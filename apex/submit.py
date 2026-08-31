import os
import os.path
import glob
import shutil
import tempfile
import logging
import copy
import json
import hashlib
import importlib.util
import re
from pathlib import Path
from typing import List
from multiprocessing import Pool
from monty.serialization import loadfn

import apex
import dpdata
import fpop
from dflow import config, s3_config

from apex.archive import archive_workdir
from apex.config import Config
from apex.flow import FlowGenerator
from apex.utils import (
    judge_flow,
    load_config_file,
    json2dict,
    copy_all_other_files,
    sepline,
    handle_prop_suffix,
    backup_path,
    apex_task_succeeded,
    all_apex_task_status_succeeded,
)


LAMMPS_PHONON_IMAGE = (
    "registry.dp.tech/dptech/dp/native/prod-16664/"
    "dpa4-phonolammps:0.0.2"
)
GPU_LAMMPS_INTERACTIONS = {"deepmd", "mace", "nep"}

DPA4_RUNTIME_KIND = "dpa4_pt2"
DPA4_RUNTIME_MODEL_PATH = (
    "/opt/dpa4-runtime/models/DPA4-alloytongqi/"
    "alloytongqi.t4-sm75.pt2"
)
DPA4_RUNTIME_MODEL_SHA256 = (
    "2614db9463f5864d80a78fec037aeae26930df2004bb9f1148a69b83c25b3daf"
)
DPA4_SOURCE_CHECKPOINT_PATH = (
    "/opt/dpa4-runtime/models/DPA4-alloytongqi/model.pt"
)
DPA4_SOURCE_CHECKPOINT_SHA256 = (
    "c84b268cc6191afc72bd2d5c001cbe526a0d2e04ebf6dbd7df021306e9abe9ad"
)
DPA4_SCASS_TYPE = "c4_m15_1 * NVIDIA T4"
DPA4_LAMMPS_RUN_COMMAND = "/usr/local/bin/dpa4-lmp -in in.lammps"
DPA4_PHONOLAMMPS_RUN_COMMAND = (
    "/usr/local/bin/dpa4-phonolammps {input_file} -c {poscar} "
    "--dim {dim} {primitive_axes}"
)
DPA4_GROUP_SIZE = 1
DPA4_POOL_SIZE = 1
DPA4_DISPATCHER_COMMAND = "python3"
DPA4_JOB_TYPE = "container"
DPA4_PLATFORM = "ali"
_HEX_SHA256_RE = re.compile(r"[0-9a-f]{64}")


def _load_dpa4_profile(*, require_published: bool = True) -> dict:
    """Load the canonical bundled DPA4 profile without importing a hyphenated package."""
    profile_module_path = (
        Path(__file__).resolve().parent
        / "skills"
        / "apex-flow"
        / "scripts"
        / "dpa4_profile.py"
    )
    spec = importlib.util.spec_from_file_location(
        "apex_bundled_dpa4_profile",
        profile_module_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(
            f"Cannot load DPA4 runtime profile helper: {profile_module_path}"
        )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.load_dpa4_profile(require_published=require_published)


def _dpa4_image_name() -> str:
    """Return the immutable image identity from the canonical DPA4 profile."""
    profile = _load_dpa4_profile(require_published=True)
    image = profile["image"]
    return f"{image['ref']}@{str(image['digest']).lower()}"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_effective_lammps_interactions(
    relax_param: dict | None,
    props_param: dict | None,
    flow_type: str,
):
    """Yield every interaction that can reach the single LAMMPS run image."""
    if flow_type in {"relax", "joint"} and relax_param:
        interaction = relax_param.get("interaction")
        if isinstance(interaction, dict):
            yield "relax.interaction", interaction

    if flow_type not in {"props", "joint"} or not props_param:
        return

    base_interaction = props_param.get("interaction")
    properties = props_param.get("properties") or []
    if not properties:
        if isinstance(base_interaction, dict):
            yield "props.interaction", base_interaction
        return

    for index, prop in enumerate(properties):
        if not isinstance(prop, dict):
            continue
        overwrite = (prop.get("cal_setting") or {}).get("overwrite_interaction")
        if isinstance(overwrite, dict):
            yield (
                f"props.properties[{index}].cal_setting.overwrite_interaction",
                overwrite,
            )
        elif isinstance(base_interaction, dict):
            yield f"props.properties[{index}].interaction", base_interaction


def _has_dpa4_runtime_intent(interaction: dict) -> bool:
    return (
        "deepmd_runtime" in interaction
        or interaction.get("model_in_image") is True
        or interaction.get("model") == DPA4_RUNTIME_MODEL_PATH
        or "runtime_model_sha256" in interaction
        or "source_checkpoint" in interaction
        or "source_checkpoint_sha256" in interaction
    )


def _validate_exact_dpa4_interaction(label: str, interaction: dict) -> list[str]:
    expected = {
        "type": "deepmd",
        "deepmd_runtime": DPA4_RUNTIME_KIND,
        "model_in_image": True,
        "model": DPA4_RUNTIME_MODEL_PATH,
        "runtime_model_sha256": DPA4_RUNTIME_MODEL_SHA256,
        "source_checkpoint": DPA4_SOURCE_CHECKPOINT_PATH,
        "source_checkpoint_sha256": DPA4_SOURCE_CHECKPOINT_SHA256,
    }
    errors = []
    for key in ("runtime_model_sha256", "source_checkpoint_sha256"):
        declared = interaction.get(key)
        if not isinstance(declared, str) or not _HEX_SHA256_RE.fullmatch(declared):
            errors.append(f"{label}.{key} must be a lowercase SHA-256 hex digest")
    for key, value in expected.items():
        if interaction.get(key) != value:
            errors.append(
                f"{label}.{key} must equal {value!r} for the bundled DPA4 "
                "T4/PT2 production runtime"
            )
    type_map = interaction.get("type_map")
    if type_map != "auto":
        valid_mapping = (
            isinstance(type_map, dict)
            and bool(type_map)
            and all(
                isinstance(symbol, str)
                and bool(symbol.strip())
                and type(index) is int
                and index >= 0
                for symbol, index in type_map.items()
            )
            and set(type_map.values()) == set(range(len(type_map)))
        )
        if not valid_mapping:
            errors.append(
                f"{label}.type_map must be 'auto' before CLI expansion or a "
                "non-empty contiguous element-to-index mapping afterwards"
            )
    return errors


def _model_locations_with_sha256(
    interaction: dict,
    work_dir_list: List[os.PathLike],
    expected_sha256: str,
) -> list[str]:
    """Resolve a staged model across workdirs and return exact hash matches."""
    model = interaction.get("model")
    if not isinstance(model, str) or interaction.get("model_in_image") is True:
        return []
    model_path = Path(model).expanduser()
    candidates = (
        [model_path]
        if model_path.is_absolute()
        else [Path(work_dir) / model_path for work_dir in work_dir_list]
    )
    matches = []
    for candidate in candidates:
        try:
            if (
                candidate.is_file()
                and _sha256_file(candidate) == expected_sha256
            ):
                matches.append(str(candidate.resolve()))
        except OSError:
            continue
    return matches


def _validate_lammps_runtime_contract(
    relax_param: dict | None,
    props_param: dict | None,
    flow_type: str,
    work_dir_list: List[os.PathLike],
) -> str:
    """Validate one image-compatible runtime class for the whole workflow.

    FlowGenerator currently owns a single run image, so mixing the bundled
    DPA4/PT2 runtime with legacy interactions is never safe, including through
    property overwrite_interaction or different submitted work directories.
    """
    classifications = []
    errors = []
    for label, interaction in _iter_effective_lammps_interactions(
        relax_param, props_param, flow_type
    ):
        checkpoint_locations = _model_locations_with_sha256(
            interaction, work_dir_list, DPA4_SOURCE_CHECKPOINT_SHA256
        )
        if checkpoint_locations:
            errors.append(
                f"{label}.model is the bundled DPA4 source checkpoint in "
                f"{checkpoint_locations}; LAMMPS must receive the image-resident "
                f"T4 .pt2 runtime at {DPA4_RUNTIME_MODEL_PATH!r}, never model.pt"
            )
            classifications.append(DPA4_RUNTIME_KIND)
            continue

        staged_runtime_locations = _model_locations_with_sha256(
            interaction, work_dir_list, DPA4_RUNTIME_MODEL_SHA256
        )
        if staged_runtime_locations:
            errors.append(
                f"{label}.model resolves to the bundled DPA4 T4 .pt2 in "
                f"{staged_runtime_locations}, but the production contract "
                f"requires the image-resident path {DPA4_RUNTIME_MODEL_PATH!r} "
                "with model_in_image=true"
            )
            classifications.append(DPA4_RUNTIME_KIND)
            continue

        if _has_dpa4_runtime_intent(interaction):
            classifications.append(DPA4_RUNTIME_KIND)
            errors.extend(_validate_exact_dpa4_interaction(label, interaction))
        else:
            classifications.append("legacy")

    runtime_kinds = set(classifications)
    if DPA4_RUNTIME_KIND in runtime_kinds and "legacy" in runtime_kinds:
        errors.append(
            "A single APEX workflow cannot mix legacy LAMMPS interactions with "
            "the bundled DPA4/PT2 runtime (including relax/property overrides "
            "or submitted work directories), because it has only one run image"
        )

    if DPA4_RUNTIME_KIND in runtime_kinds:
        try:
            _dpa4_image_name()
        except RuntimeError as exc:
            errors.append(str(exc))

    if errors:
        raise RuntimeError("Invalid LAMMPS runtime contract:\n- " + "\n- ".join(errors))
    return DPA4_RUNTIME_KIND if runtime_kinds == {DPA4_RUNTIME_KIND} else "legacy"


def _uses_phonolammps(props_param: dict | None) -> bool:
    return bool(
        props_param
        and any(
            isinstance(prop, dict)
            and prop.get("type") in {"phonon", "gruneisen"}
            for prop in props_param.get("properties", [])
        )
    )


def _effective_dispatcher_machine(wf_config: Config) -> dict | None:
    """Read machine_dict after Config and dispatcher_config overrides."""
    machine = wf_config.dispatcher_config_dict.get("machine_dict")
    return machine if isinstance(machine, dict) else None


def _effective_bohrium_scass(wf_config: Config) -> str | None:
    """Read the effective dispatcher SKU after nested machine overrides."""
    machine = _effective_dispatcher_machine(wf_config)
    if not isinstance(machine, dict):
        return None
    remote_profile = machine.get("remote_profile")
    if not isinstance(remote_profile, dict):
        return None
    input_data = remote_profile.get("input_data")
    if not isinstance(input_data, dict):
        return None
    return input_data.get("scass_type")


def _effective_bohrium_image(wf_config: Config) -> str | None:
    """Read an optional nested Bohrium image override after all merges."""
    machine = _effective_dispatcher_machine(wf_config)
    if not isinstance(machine, dict):
        return None
    remote_profile = machine.get("remote_profile")
    if not isinstance(remote_profile, dict):
        return None
    input_data = remote_profile.get("input_data")
    if not isinstance(input_data, dict):
        return None
    return input_data.get("image_name")


def _validate_dpa4_execution_config(
    wf_config: Config,
    props_param: dict | None,
) -> None:
    """Require the one hardware/command profile covered by DPA4 evidence."""
    errors = []
    context_type = str(wf_config.context_type or "").lower()
    batch_type = str(wf_config.batch_type or "").lower()
    if "bohrium" not in context_type or "bohrium" not in batch_type:
        errors.append(
            "DPA4/PT2 production runs require Bohrium context_type and "
            "batch_type"
        )
    for label, value in (
        ("machine", wf_config.machine),
        ("dispatcher_config", wf_config.dispatcher_config),
        ("resources", wf_config.resources),
        ("task", wf_config.task),
    ):
        if value not in (None, {}):
            errors.append(
                f"{label} overrides are prohibited for the immutable DPA4 "
                "profile; use the generated top-level Bohrium configuration"
            )
    effective_machine = _effective_dispatcher_machine(wf_config) or {}
    effective_context = str(effective_machine.get("context_type") or "").lower()
    effective_batch = str(effective_machine.get("batch_type") or "").lower()
    if "bohrium" not in effective_context or "bohrium" not in effective_batch:
        errors.append(
            "effective dispatcher machine_dict must retain Bohrium "
            "context_type and batch_type"
        )
    if wf_config.scass_type != DPA4_SCASS_TYPE:
        errors.append(
            f"scass_type must equal {DPA4_SCASS_TYPE!r}; all other GPU/CPU "
            "SKUs are unverified"
        )
    if wf_config.job_type != DPA4_JOB_TYPE:
        errors.append(
            f"job_type must equal {DPA4_JOB_TYPE!r} so the immutable DPA4 "
            "container image is actually used"
        )
    if wf_config.platform != DPA4_PLATFORM:
        errors.append(
            f"platform must equal the qualified Bohrium value "
            f"{DPA4_PLATFORM!r}"
        )
    effective_scass = _effective_bohrium_scass(wf_config)
    if effective_scass != DPA4_SCASS_TYPE:
        errors.append(
            "effective machine.remote_profile.input_data.scass_type must "
            f"equal {DPA4_SCASS_TYPE!r}; nested machine overrides may not "
            "change the qualified GPU"
        )
    effective_image = _effective_bohrium_image(wf_config)
    if effective_image is not None:
        expected_image = _dpa4_image_name()
        if effective_image != expected_image:
            errors.append(
                "effective machine.remote_profile.input_data.image_name may "
                "be absent or equal the immutable DPA4 image "
                f"{expected_image!r}; nested image overrides are prohibited"
            )

    dispatcher = wf_config.dispatcher_config_dict
    if dispatcher.get("json_file") not in (None, ""):
        errors.append(
            "dispatcher_config.json_file is prohibited for DPA4 because it "
            "can inject machine or resource overrides after validation"
        )
    if dispatcher.get("command") != DPA4_DISPATCHER_COMMAND:
        errors.append(
            "effective dispatcher command must remain the single-process "
            f"default {DPA4_DISPATCHER_COMMAND!r}"
        )
    if dispatcher.get("remote_command") not in (None, ""):
        errors.append(
            "dispatcher remote_command is prohibited for DPA4 because it can "
            "bypass the audited one-rank wrapper"
        )

    basic = wf_config.basic_config_dict
    if basic.get("lammps_run_command") != DPA4_LAMMPS_RUN_COMMAND:
        errors.append(
            "lammps_run_command must equal the audited single-rank wrapper "
            f"{DPA4_LAMMPS_RUN_COMMAND!r}"
        )
    if _uses_phonolammps(props_param) and (
        basic.get("phonolammps_run_command")
        != DPA4_PHONOLAMMPS_RUN_COMMAND
    ):
        errors.append(
            "phonon/Gruneisen with DPA4 requires phonolammps_run_command="
            f"{DPA4_PHONOLAMMPS_RUN_COMMAND!r}"
        )
    if basic.get("group_size") != DPA4_GROUP_SIZE:
        errors.append(
            f"group_size must equal {DPA4_GROUP_SIZE} for one task per T4"
        )
    if basic.get("pool_size") != DPA4_POOL_SIZE:
        errors.append(
            f"pool_size must equal {DPA4_POOL_SIZE} for the qualified profile"
        )

    if errors:
        raise RuntimeError(
            "Invalid DPA4 execution profile:\n- " + "\n- ".join(errors)
        )


def validate_submit_paths(parameter_dicts: List[dict]) -> None:
    """
    dflow rejects structure path patterns containing '.'.
    Validate before submit and fail fast with actionable hints.
    """
    violations = []
    for idx, param in enumerate(parameter_dicts):
        structures = param.get("structures", [])
        for s_idx, structure in enumerate(structures):
            if isinstance(structure, str) and "." in structure:
                violations.append(
                    f"parameter[{idx}].structures[{s_idx}] = {structure}"
                )

    if violations:
        raise RuntimeError(
            "Invalid `apex submit` paths: dflow does not allow '.' in "
            "`structures`. "
            "Please rename the path/file and update param.json.\n"
            "Offending entries:\n- " + "\n- ".join(violations)
        )


def _select_run_image(
    calculator: str,
    props_param: dict,
    run_image: str,
    machine_type: str = None,
    runtime_contract: str = "legacy",
) -> str:
    if runtime_contract == DPA4_RUNTIME_KIND:
        if calculator != "lammps":
            raise RuntimeError("DPA4/PT2 runtime contract requires calculator=lammps")
        expected_image = _dpa4_image_name()
        if run_image != expected_image:
            logging.warning(
                "Exact bundled DPA4/PT2 contract overrides LAMMPS run image "
                "%r with immutable candidate %r.",
                run_image,
                expected_image,
            )
        return expected_image
    interaction = (props_param or {}).get("interaction", {})
    interaction_type = (
        interaction.get("type") if isinstance(interaction, dict) else None
    )
    is_cpu_machine = "_cpu" in str(machine_type or "").lower()
    if (
        calculator == "lammps"
        and interaction_type in GPU_LAMMPS_INTERACTIONS
        and not is_cpu_machine
        and props_param
        and any(
            prop.get("type") in {"phonon", "gruneisen"}
            for prop in props_param.get("properties", [])
        )
    ):
        if run_image != LAMMPS_PHONON_IMAGE:
            logging.warning(
                "GPU LAMMPS phonon/Gruneisen requires the validated "
                "phonoLAMMPS image; "
                "overriding run image %r with %r.",
                run_image,
                LAMMPS_PHONON_IMAGE,
            )
        return LAMMPS_PHONON_IMAGE
    return run_image


def _with_lammps_retry_env(run_command: str, wf_config: Config) -> str:
    if not run_command:
        return run_command
    if "APEX_LAMMPS_HEADER_RETRY" in run_command:
        return run_command
    retry_env = (
        f"APEX_LAMMPS_HEADER_RETRY={int(wf_config.lammps_header_retry_attempts)} "
        f"APEX_LAMMPS_HEADER_RETRY_DELAY={float(wf_config.lammps_header_retry_delay)} "
        f"APEX_LAMMPS_TRANSIENT_RETRY={int(wf_config.lammps_transient_retry_attempts)}"
    )
    return f"{retry_env} {run_command}"


def _infer_type_map_from_structure_file(structure_file: str) -> dict:
    structure_name = os.path.basename(structure_file)
    symbols = []
    if structure_name in {"POSCAR", "CONTCAR"}:
        from pymatgen.io.vasp import Poscar

        poscar = Poscar.from_file(structure_file)
        symbols = [str(item) for item in poscar.site_symbols]
    else:
        from pymatgen.core import Structure

        structure = Structure.from_file(structure_file)
        seen = set()
        for site in structure.sites:
            symbol = str(site.specie)
            if symbol not in seen:
                seen.add(symbol)
                symbols.append(symbol)

    if not symbols:
        raise RuntimeError(f"Cannot infer type_map from structure file: {structure_file}")
    return {symbol: idx for idx, symbol in enumerate(symbols)}


def _resolve_structure_files(param_path: str, structures: List[str]) -> List[str]:
    """Resolve every structure file used to build a complete LAMMPS type map."""
    base_dir = os.path.dirname(os.path.abspath(param_path))
    structure_files = []
    seen_files = set()

    def add_file(path: str) -> None:
        absolute_path = os.path.abspath(path)
        if absolute_path not in seen_files:
            seen_files.add(absolute_path)
            structure_files.append(absolute_path)

    for pattern in structures:
        if os.path.isabs(pattern):
            search_patterns = [pattern]
        else:
            search_patterns = [os.path.join(base_dir, pattern), pattern]

        matches = []
        seen_matches = set()
        for search_pattern in search_patterns:
            for match in sorted(glob.glob(search_pattern)):
                absolute_match = os.path.abspath(match)
                if absolute_match not in seen_matches:
                    seen_matches.add(absolute_match)
                    matches.append(absolute_match)
        for match in matches:
            if os.path.isdir(match):
                for candidate in ("POSCAR", "CONTCAR", "STRU"):
                    candidate_path = os.path.join(match, candidate)
                    if os.path.isfile(candidate_path):
                        add_file(candidate_path)
                        break
                else:
                    nested_poscars = sorted(
                        glob.glob(os.path.join(match, "conf_*", "POSCAR"))
                    )
                    for nested_poscar in nested_poscars:
                        add_file(nested_poscar)
            elif os.path.isfile(match):
                add_file(match)

    if structure_files:
        return structure_files
    raise RuntimeError(
        "Cannot infer interaction.type_map automatically: no structure file found "
        f"for patterns {structures} from {param_path}"
    )


def _infer_type_map_from_structure_files(structure_files: List[str]) -> dict:
    symbols = []
    seen = set()
    for structure_file in structure_files:
        for symbol in _infer_type_map_from_structure_file(structure_file):
            if symbol not in seen:
                seen.add(symbol)
                symbols.append(symbol)
    if not symbols:
        raise RuntimeError("Cannot infer interaction.type_map from empty structures")
    return {symbol: idx for idx, symbol in enumerate(symbols)}


def _iter_lammps_interactions_for_type_map(parameter_dict: dict):
    """Yield each distinct LAMMPS interaction that may need CLI expansion."""
    seen = set()
    interactions = [parameter_dict.get("interaction")]
    for prop in parameter_dict.get("properties") or []:
        if not isinstance(prop, dict):
            continue
        cal_setting = prop.get("cal_setting") or {}
        if isinstance(cal_setting, dict):
            interactions.append(cal_setting.get("overwrite_interaction"))

    for interaction in interactions:
        if not isinstance(interaction, dict):
            continue
        if interaction.get("type") in {"vasp", "abacus"}:
            continue
        identity = id(interaction)
        if identity in seen:
            continue
        seen.add(identity)
        yield interaction


def auto_fill_type_map_from_poscar(parameter_dict: dict, param_path: str) -> bool:
    interactions = []
    for interaction in _iter_lammps_interactions_for_type_map(parameter_dict):
        current_type_map = interaction.get("type_map")
        if (
            current_type_map is None
            or current_type_map == ""
            or current_type_map == "auto"
        ):
            interactions.append(interaction)
    if not interactions:
        return False

    structures = parameter_dict.get("structures", [])
    if not isinstance(structures, list) or not structures:
        raise RuntimeError(
            "Cannot infer interaction.type_map automatically because `structures` is empty"
        )

    structure_files = _resolve_structure_files(param_path, structures)
    type_map = _infer_type_map_from_structure_files(structure_files)
    for interaction in interactions:
        interaction["type_map"] = dict(type_map)

    with open(param_path, "w", encoding="utf-8") as fp:
        json.dump(parameter_dict, fp, indent=4)
        fp.write("\n")
    return True


def _glob_structures_in_work_dir(work_dir: os.PathLike, pattern: str) -> List[str]:
    """Resolve a structure glob the same way pack_upload_dir will use it.

    Submit can be launched from a parent directory while each workflow work_dir
    contains its own confs/. Keep returned paths relative to work_dir whenever
    possible because pack_upload_dir changes into work_dir before copying.
    """
    abs_work_dir = os.path.abspath(work_dir)
    search_pattern = pattern if os.path.isabs(pattern) else os.path.join(abs_work_dir, pattern)
    matches = []
    for match in glob.glob(search_pattern):
        abs_match = os.path.abspath(match)
        try:
            inside_work_dir = os.path.commonpath([abs_work_dir, abs_match]) == abs_work_dir
        except ValueError:
            inside_work_dir = False
        if inside_work_dir:
            matches.append(os.path.relpath(abs_match, abs_work_dir))
        else:
            matches.append(abs_match)
    return sorted(set(matches))


def _relaxation_required(relax_param: dict | None) -> bool:
    if not relax_param:
        return True
    relaxation = relax_param.get("relaxation", {})
    return relaxation.get("req_calc", True) is not False


def _stage_unrelaxed_structure_as_equilibrium(conf_dir: str, build_conf_path: str) -> None:
    """Expose a POSCAR as the equilibrium structure expected by property code."""
    source_poscar = os.path.abspath(os.path.join(conf_dir, "POSCAR"))
    if not os.path.isfile(source_poscar):
        raise RuntimeError(
            "relaxation.req_calc=false requires POSCAR in each structure directory: "
            f"{source_poscar}"
        )
    relax_task_dir = os.path.join(build_conf_path, "relaxation", "relax_task")
    os.makedirs(relax_task_dir, exist_ok=True)
    shutil.copy(source_poscar, os.path.join(relax_task_dir, "CONTCAR"))


def pack_upload_dir(
        work_dir: os.PathLike,
        upload_dir: os.PathLike,
        relax_param: dict,
        prop_param: dict,
        flow_type: str,
        exclude_upload_files: List[str],
):
    """
    Pack the necessary files and directories into temp dir and upload it to dflow
    """
    cwd = os.getcwd()
    os.chdir(work_dir)
    relax_confs = relax_param.get("structures", []) if relax_param else []
    prop_confs = prop_param.get("structures", []) if prop_param else []
    relax_prefix = relax_param["interaction"].get("potcar_prefix", None) if relax_param else None
    prop_prefix = prop_param["interaction"].get("potcar_prefix", None) if prop_param else None
    include_dirs = set()
    if relax_prefix:
        relax_prefix_base = relax_prefix.split('/')[0]
        include_dirs.add(relax_prefix_base)
    if prop_prefix:
        prop_prefix_base = prop_prefix.split('/')[0]
        include_dirs.add(prop_prefix_base)
    # Melting-point continuations may provide one restart per temperature.
    # These files are scientific inputs, so stage their containing top-level
    # directories alongside models and custom input files.
    if prop_param:
        for prop in prop_param.get("properties", []):
            if prop.get("type") not in {"melting_point", "two_phase_melting"}:
                continue
            for restart_file in prop.get("cal_setting", {}).get("restart_files", []):
                if not os.path.isabs(restart_file):
                    restart_prefix = Path(restart_file).parts[0]
                    if restart_prefix not in ("", ".", ".."):
                        include_dirs.add(restart_prefix)
    confs = relax_confs + prop_confs
    assert len(confs) > 0, "No configuration path indicated!"
    conf_dirs = []
    for conf in confs:
        conf_dirs.extend(glob.glob(conf))
    conf_dirs = list(set(conf_dirs))
    conf_dirs.sort()
    if not conf_dirs:
        os.chdir(cwd)
        raise RuntimeError(
            "No structures matched the submitted patterns under "
            f"{os.path.abspath(work_dir)}: {confs}"
        )

    def relaxation_finished(conf_path: str) -> bool:
        task_dir = os.path.join(conf_path, "relaxation", "relax_task")
        return apex_task_succeeded(task_dir)

    def property_finished(conf_path: str, properties: list) -> bool:
        # Finished only if every property that sets rerun_finished=False has
        # calculator task statuses with state=succeeded.
        all_done = True
        for prop in properties:
            rerun_finished = prop.get("rerun_finished", True)
            if rerun_finished:
                all_done = False
                break
            do_refine, suffix = handle_prop_suffix(prop)
            if not suffix:
                all_done = False
                break
            prop_dir = os.path.join(conf_path, prop["type"] + "_" + suffix)
            if not all_apex_task_status_succeeded(prop_dir):
                all_done = False
                break
        return all_done

    # Optional pruning to skip already-finished tasks before upload.
    if flow_type == 'relax' and relax_param:
        rerun_finished = relax_param.get("interaction", {}).get("rerun_finished", True)
        if rerun_finished is False:
            pruned = []
            skipped = []
            for c in conf_dirs:
                if relaxation_finished(c):
                    logging.info(f"Skip uploading finished relaxation for {c} (rerun_finished=False).")
                    skipped.append(c)
                else:
                    pruned.append(c)
            conf_dirs = pruned
            if not conf_dirs:
                raise RuntimeError("All relaxations are already finished; nothing to submit.")

    if flow_type == 'props' and prop_param:
        properties = prop_param.get("properties", [])
        finished_props = {}
        pruned = []
        for c in conf_dirs:
            done_all = property_finished(c, properties)
            if done_all:
                logging.info(f"Skip uploading finished properties for {c} (all rerun_finished=False task states succeeded).")
            else:
                pruned.append(c)
                # track per-structure finished properties
                finished_list = []
                for prop in properties:
                    if not prop.get("rerun_finished", True):
                        do_refine, suffix = handle_prop_suffix(prop)
                        if not suffix:
                            continue
                        prop_dir = os.path.join(c, prop["type"] + "_" + suffix)
                        if all_apex_task_status_succeeded(prop_dir):
                            finished_list.append(prop_dir)
                if finished_list:
                    finished_props[c] = finished_list
        conf_dirs = pruned
        if finished_props:
            prop_param["skip_finished_properties"] = [
                [c, os.path.basename(p)] for c, lst in finished_props.items() for p in lst
            ]
        if not conf_dirs and not prop_param.get("skip_finished_properties", []):
            raise RuntimeError("All properties are already finished; nothing to submit.")
    
    if flow_type == 'joint' and relax_param and prop_param:
        # Split finished vs pending relaxations so we can skip reruns while still running properties
        req_relax = _relaxation_required(relax_param)
        rerun_finished = relax_param.get("interaction", {}).get("rerun_finished", True)
        skip_finished_properties = []
        finished_relax = []
        pending_relax = conf_dirs
        if not req_relax:
            pending_relax = []
            finished_relax = conf_dirs
            relax_param["skip_finished_structures"] = finished_relax
            prop_param["pre_relaxed_structures"] = finished_relax
        elif rerun_finished is False:
            pending_relax = []
            for c in conf_dirs:
                if relaxation_finished(c):
                    finished_relax.append(c)
                else:
                    pending_relax.append(c)
            if not pending_relax:
                logging.info("All relaxations finished; joint flow will reuse existing results.")
            # keep all structures for property stage; mark which relaxations to skip
            relax_param["skip_finished_structures"] = finished_relax
            prop_param["pre_relaxed_structures"] = finished_relax
        # Detect per-structure finished properties when rerun_finished is False for that property
        properties = prop_param.get("properties", [])
        requested_property_tasks = []
        for c in conf_dirs:
            for prop in properties:
                do_refine, suffix = handle_prop_suffix(prop)
                if not suffix:
                    continue
                prop_dir_name = f"{prop['type']}_{suffix}"
                requested_property_tasks.append((c, prop_dir_name))
                if prop.get("rerun_finished", True):
                    continue
                prop_dir = os.path.join(c, prop_dir_name)
                if all_apex_task_status_succeeded(prop_dir):
                    skip_finished_properties.append([c, prop_dir_name])
        if skip_finished_properties:
            prop_param["skip_finished_properties"] = skip_finished_properties
        skipped_property_tasks = {
            (item[0], item[1])
            for item in skip_finished_properties
        }
        if not pending_relax and requested_property_tasks \
                and all(item in skipped_property_tasks for item in requested_property_tasks):
            os.chdir(cwd)
            raise RuntimeError(
                "All requested joint relaxation and property tasks are already finished; "
                "nothing to submit. Set rerun_finished=true for relaxation or at least "
                "one property if you want to resubmit."
            )
    refine_init_name_list = []
    # backup all existing property work directories
    if flow_type in ['props', 'joint']:
        property_list = prop_param["properties"]
        for ii in conf_dirs:
            sepline(ch=ii, screen=True)
            for jj in property_list:
                do_refine, suffix = handle_prop_suffix(jj)
                property_type = jj["type"]
                if not suffix:
                    continue
                if do_refine:
                    refine_init_suffix = jj['init_from_suffix']
                    refine_init_name_list.append(property_type + "_" + refine_init_suffix)
                path_to_prop = os.path.join(ii, property_type + "_" + suffix)
                # If rerun_finished is False and task states succeeded, skip backing up (keep as-is)
                if (not jj.get("rerun_finished", True)):
                    if all_apex_task_status_succeeded(path_to_prop):
                        logging.info(f"Skip backing up finished property at {path_to_prop} (rerun_finished=False, task states succeeded)")
                        continue
                backup_path(path_to_prop)

    """copy necessary files and directories into temp upload directory"""
    exclude_upload_files.append("all_result.json")
    copy_all_other_files(
        work_dir, upload_dir,
        exclude_files=exclude_upload_files,
        include_dirs=list(include_dirs)
    )
    for ii in conf_dirs:
        build_conf_path = os.path.join(upload_dir, ii)
        os.makedirs(build_conf_path, exist_ok=True)
        copy_poscar_path = os.path.abspath(os.path.join(ii, "POSCAR"))
        copy_stru_path = os.path.abspath(os.path.join(ii, "STRU"))
        if os.path.isfile(copy_poscar_path):
            target_poscar_path = os.path.join(build_conf_path, "POSCAR")
            shutil.copy(copy_poscar_path, target_poscar_path)
        if os.path.isfile(copy_stru_path):
            target_stru_path = os.path.join(build_conf_path, "STRU")
            shutil.copy(copy_stru_path, target_stru_path)
        if flow_type in ['props', 'joint']:
            copy_relaxation_path = os.path.abspath(os.path.join(ii, "relaxation"))
            target_relaxation_path = os.path.join(build_conf_path, "relaxation")
            if flow_type == 'joint' and relax_param and not _relaxation_required(relax_param):
                try:
                    _stage_unrelaxed_structure_as_equilibrium(ii, build_conf_path)
                except Exception:
                    os.chdir(cwd)
                    raise
            elif os.path.isdir(copy_relaxation_path):
                shutil.copytree(copy_relaxation_path, target_relaxation_path)
            else:
                logging.warning(f"Skip copying relaxation for {ii}: {copy_relaxation_path} not found.")
            # copy refine from init path to upload dir
            if refine_init_name_list:
                for jj in refine_init_name_list:
                    copy_init_path = os.path.abspath(os.path.join(ii, jj))
                    assert os.path.exists(copy_init_path), f'refine from init path {copy_init_path} does not exist!'
                    target_init_path = os.path.join(build_conf_path, jj)
                    shutil.copytree(copy_init_path, target_init_path)

    os.chdir(cwd)


def submit(
        flow,
        flow_type,
        work_dir,
        relax_param,
        props_param,
        wf_config,
        conf=config,
        s3_conf=s3_config,
        is_sub=False,
        labels=None,
):
    if is_sub:
        # reset dflow global config for sub-processes
        logging.info(msg=f'Sub-process working on: {work_dir}')
        config.update(conf)
        s3_config.update(s3_conf)
        logging.basicConfig(level=logging.INFO)
    else:
        logging.info(msg=f'Working on: {work_dir}')

    with tempfile.TemporaryDirectory() as tmp_dir:
        logging.debug(msg=f'Temporary upload directory:{tmp_dir}')

        # For property-only workflow, drop structures whose relaxation output is missing
        if flow_type == 'props' and props_param:
            filtered_structs = []
            missing_structs = []
            for pattern in props_param.get("structures", []):
                matches = _glob_structures_in_work_dir(work_dir, pattern)
                if not matches:
                    logging.warning(
                        f'No structure matched pattern "{pattern}" under "{work_dir}", skip.'
                    )
                    continue
                for m in matches:
                    relax_dir = os.path.join(work_dir, m, "relaxation")
                    if os.path.isdir(relax_dir):
                        filtered_structs.append(m)
                    else:
                        missing_structs.append(m)
                        logging.warning(f'Relaxation directory missing for {m}, skip property calculation on it.')
            if not filtered_structs:
                raise RuntimeError("No available relaxed structures for property workflow.")
            props_param["structures"] = filtered_structs

        pack_upload_dir(
            work_dir=work_dir,
            upload_dir=tmp_dir,
            relax_param=relax_param,
            prop_param=props_param,
            flow_type=flow_type,
            exclude_upload_files=wf_config.exclude_upload_files
        )
        cwd = os.getcwd()
        os.chdir(tmp_dir)
        flow_id = None
        flow_name = wf_config.flow_name
        submit_only = wf_config.submit_only
        if flow_type == 'relax':
            flow_id = flow.submit_relax(
                upload_path=tmp_dir,
                download_path=work_dir,
                relax_parameter=relax_param,
                submit_only=submit_only,
                name=flow_name,
                labels=labels
            )
        elif flow_type == 'props':
            flow_id = flow.submit_props(
                upload_path=tmp_dir,
                download_path=work_dir,
                props_parameter=props_param,
                submit_only=submit_only,
                name=flow_name,
                labels=labels
            )
        elif flow_type == 'joint':
            flow_id = flow.submit_joint(
                upload_path=tmp_dir,
                download_path=work_dir,
                props_parameter=props_param,
                relax_parameter=relax_param,
                submit_only=submit_only,
                name=flow_name,
                labels=labels
            )
        os.chdir(cwd)

    if not submit_only:
        # auto archive results
        print(f'Archiving results of workflow (ID: {flow_id}) into {wf_config.database_type}...')
        archive_workdir(relax_param, props_param, wf_config, work_dir, flow_type)


def submit_workflow(
    parameter_dicts: List[dict],
    config_dict: dict,
    work_dirs: List[os.PathLike],
    indicated_flow_type: str,
    flow_name: str = None,
    submit_only=False,
    is_debug=False,
    labels=None
):
    validate_submit_paths(parameter_dicts)

    # config dflow_config and s3_config
    wf_config = Config(**config_dict)
    Config.config_dflow(wf_config.dflow_config_dict)
    Config.config_bohrium(wf_config.bohrium_config_dict)
    Config.config_s3(wf_config.dflow_s3_config_dict)
    if submit_only:
        print('Submit only mode activated, no auto-retrieval of results.')
        wf_config.submit_only = True
    # set pre-defined dflow debug mode settings
    if is_debug:
        # Prefer an explicit debug_workdir from config; otherwise, try to place
        # the debug work under the configured remote_root (if any) to mimic the
        # user's desired filesystem layout; fall back to a temp dir.
        debug_dir = config_dict.get("debug_workdir")
        if not debug_dir:
            base_dir = wf_config.remote_root or os.getcwd()
            # Put artifacts in a hidden folder to avoid clutter
            debug_dir = os.path.join(base_dir, "dflow_debug")
        try:
            os.makedirs(debug_dir, exist_ok=True)
        except Exception:
            # Final fallback: system temp
            debug_dir = tempfile.mkdtemp(prefix="apex-debug-")
        config["mode"] = "debug"
        config["debug_workdir"] = debug_dir
        logging.info(f'Debug mode activated, debug work directory: {config["debug_workdir"]}')
        # Use local filesystem instead of object storage in debug
        s3_config["storage_client"] = None

    if flow_name:
        wf_config.flow_name = flow_name

    # judge basic flow info from user indicated parameter files
    (run_op, calculator, flow_type,
     relax_param, props_param) = judge_flow(parameter_dicts, indicated_flow_type)
    print(f'Running APEX calculation via {calculator}')
    print(f'Submitting {flow_type} workflow...')

    # Resolve work directories before choosing the one run image shared by all
    # generated LAMMPS steps.  Runtime identity must be uniform across them.
    work_dir_list = []
    for item in work_dirs:
        work_dir_list.extend(glob.glob(os.path.abspath(item)))
    work_dir_list = sorted(set(work_dir_list))
    if not work_dir_list:
        raise NotADirectoryError('Empty work directory indicated, please check your argument')

    # Scan every effective interaction, even when the base calculator is VASP
    # or ABACUS.  A DPA4 overwrite must never bypass the one-image calculator
    # contract by hiding under a non-LAMMPS base interaction.
    runtime_contract = _validate_lammps_runtime_contract(
        relax_param,
        props_param,
        flow_type,
        work_dir_list,
    )
    if runtime_contract == DPA4_RUNTIME_KIND:
        if calculator != "lammps":
            raise RuntimeError(
                "DPA4/PT2 overwrite_interaction cannot run in a workflow whose "
                f"base calculator is {calculator!r}; use one uniform LAMMPS "
                "interaction"
            )
        _validate_dpa4_execution_config(wf_config, props_param)

    make_image = wf_config.basic_config_dict["apex_image_name"]
    run_image = wf_config.basic_config_dict[f"{calculator}_image_name"]
    if not run_image:
        run_image = wf_config.basic_config_dict["run_image_name"]
    machine_type = (
        getattr(wf_config, "machine_type", None)
        or getattr(wf_config, "scass_type", None)
    )
    run_image = _select_run_image(
        calculator,
        props_param,
        run_image,
        machine_type=machine_type,
        runtime_contract=runtime_contract,
    )
    run_command = wf_config.basic_config_dict[f"{calculator}_run_command"]
    if not run_command:
        run_command = wf_config.basic_config_dict["run_command"]
    lammps_run_command = wf_config.basic_config_dict["lammps_run_command"]
    phonolammps_run_command = wf_config.basic_config_dict["phonolammps_run_command"]
    if runtime_contract == DPA4_RUNTIME_KIND:
        # Validation above makes this assignment an assertion of the audited
        # entry points, rather than a silent repair of an unsafe global.json.
        run_command = DPA4_LAMMPS_RUN_COMMAND
        lammps_run_command = DPA4_LAMMPS_RUN_COMMAND
        if _uses_phonolammps(props_param):
            phonolammps_run_command = DPA4_PHONOLAMMPS_RUN_COMMAND
    if calculator == "lammps":
        run_command = _with_lammps_retry_env(run_command, wf_config)
    post_image = make_image
    group_size = wf_config.basic_config_dict["group_size"]
    pool_size = wf_config.basic_config_dict["pool_size"]
    executor = wf_config.get_executor(wf_config.dispatcher_config_dict)

    # upload necessary python dependencies
    upload_python_packages = wf_config.basic_config_dict["upload_python_packages"]
    upload_python_packages.extend(list(apex.__path__))
    upload_python_packages.extend(list(fpop.__path__))
    upload_python_packages.extend(list(dpdata.__path__))
    #upload_python_packages.extend(list(phonolammps.__path__))

    flow = FlowGenerator(
        make_image=make_image,
        run_image=run_image,
        post_image=post_image,
        run_command=run_command,
        calculator=calculator,
        run_op=run_op,
        group_size=group_size,
        pool_size=pool_size,
        executor=executor,
        upload_python_packages=upload_python_packages,
        debug_mode=is_debug,
    )

    if props_param and (phonolammps_run_command or lammps_run_command):
        props_param = copy.deepcopy(props_param)
        for prop in props_param.get("properties", []):
            if prop.get("type") in {"phonon", "gruneisen"}:
                if phonolammps_run_command:
                    prop["phonolammps_run_command"] = phonolammps_run_command
            if prop.get("type") == "gruneisen" and lammps_run_command:
                prop["lammps_run_command"] = lammps_run_command

    # submit the workflows
    if len(work_dir_list) > 1:
        n_processes = len(work_dir_list)
        print(f'Submitting via {n_processes} processes...')
        pool = Pool(processes=n_processes)
        for ii in work_dir_list:
            res = pool.apply_async(
                submit,
                (flow,
                 flow_type,
                 ii,
                 relax_param,
                 props_param,
                 wf_config,
                 config,
                 s3_config,
                 True,
                 labels)
            )
        pool.close()
        pool.join()
    elif len(work_dir_list) == 1:
        submit(
            flow,
            flow_type,
            work_dir_list[0],
            relax_param,
            props_param,
            wf_config,
            labels=labels,
        )


def submit_from_args(
        parameters,
        config_file: os.PathLike,
        work_dirs,
        indicated_flow_type: str,
        flow_name: str = None,
        submit_only=False,
        is_debug=False,
        labels=None,
):
    print('-------Submit Workflow Mode-------')
    parameter_dicts = []
    for param_path in parameters:
        param_dict = loadfn(param_path)
        if auto_fill_type_map_from_poscar(param_dict, param_path):
            print(
                "Auto-filled LAMMPS type_map field(s) from all resolved "
                f"structure files and updated: {param_path}"
            )
        parameter_dicts.append(param_dict)

    label_mapping = None
    if labels:
        label_mapping = {}
        for item in labels:
            if "=" not in item:
                raise RuntimeError(f"Invalid submit label {item!r}; expected key=value")
            key, value = item.split("=", 1)
            clean_key = key.strip()
            clean_value = value.strip()
            if not clean_key or not clean_value:
                raise RuntimeError(f"Invalid submit label {item!r}; empty key/value is not allowed")
            label_mapping[clean_key] = clean_value

    submit_workflow(
        parameter_dicts=parameter_dicts,
        config_dict=load_config_file(config_file),
        work_dirs=work_dirs,
        indicated_flow_type=indicated_flow_type,
        flow_name=flow_name,
        submit_only=submit_only,
        is_debug=is_debug,
        labels=label_mapping,
    )
    print('Completed!')
