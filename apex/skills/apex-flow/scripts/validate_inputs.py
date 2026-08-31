#!/usr/bin/env python3
"""
APEX Input Validator

Validates param.json and global.json before submission.

Usage:
    python validate_inputs.py --param param.json --global global.json
"""

import argparse
import copy
import glob
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path


# Valid property types
VALID_PROPERTIES = {
    "eos", "cohesive", "elastic", "surface", "vacancy",
    "interstitial", "phonon", "gamma", "gamma_surface",
    "decohesive", "finite_t_latt", "finite_t_elastic",
    "gruneisen", "annealing", "melting_point",
}

# LAMMPS-only properties
LAMMPS_ONLY_PROPERTIES = {"finite_t_elastic", "melting_point"}

# Valid LAMMPS potential types
VALID_LAMMPS_TYPES = {
    "deepmd", "mace", "nep", "gap", "snap", "rann",
    "eam_alloy", "eam_fs", "meam", "meam_spline",
}

# Valid backend types
VALID_BACKENDS = {"vasp", "abacus"} | VALID_LAMMPS_TYPES

BUNDLED_DPA4_SHA256 = (
    "c84b268cc6191afc72bd2d5c001cbe526a0d2e04ebf6dbd7df021306e9abe9ad"
)
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
    """Load the canonical profile shared by generation and validation."""
    profile_module_path = Path(__file__).resolve().parent / "dpa4_profile.py"
    spec = importlib.util.spec_from_file_location(
        "apex_skill_bundled_dpa4_profile",
        profile_module_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(
            f"Cannot load DPA4 runtime profile helper: {profile_module_path}"
        )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.load_dpa4_profile(require_published=require_published)


def _dpa4_image_name() -> str | None:
    try:
        profile = _load_dpa4_profile(require_published=True)
    except RuntimeError:
        return None
    image = profile["image"]
    return f"{image['ref']}@{str(image['digest']).lower()}"


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_effective_interactions(param_config: dict):
    base = param_config.get("interaction")
    properties = param_config.get("properties") or []
    if not properties:
        if isinstance(base, dict):
            yield "interaction", base
        return
    for index, prop in enumerate(properties):
        if not isinstance(prop, dict):
            continue
        overwrite = (prop.get("cal_setting") or {}).get("overwrite_interaction")
        if isinstance(overwrite, dict):
            yield (
                f"properties[{index}].cal_setting.overwrite_interaction",
                overwrite,
            )
        elif isinstance(base, dict):
            yield f"properties[{index}].interaction", base


def _dpa4_intent(interaction: dict) -> bool:
    return (
        "deepmd_runtime" in interaction
        or interaction.get("model_in_image") is True
        or interaction.get("model") == DPA4_RUNTIME_MODEL_PATH
        or "runtime_model_sha256" in interaction
        or "source_checkpoint" in interaction
        or "source_checkpoint_sha256" in interaction
    )


def _uses_dpa4_phonolammps(param_config: dict) -> bool:
    return any(
        isinstance(prop, dict)
        and prop.get("type") in {"phonon", "gruneisen"}
        for prop in param_config.get("properties", []) or []
    )


def _nested_scass(machine: dict | None) -> str | None:
    if not isinstance(machine, dict):
        return None
    remote_profile = machine.get("remote_profile")
    if not isinstance(remote_profile, dict):
        return None
    input_data = remote_profile.get("input_data")
    if not isinstance(input_data, dict):
        return None
    return input_data.get("scass_type")


def _nested_image_name(machine: dict | None) -> str | None:
    if not isinstance(machine, dict):
        return None
    remote_profile = machine.get("remote_profile")
    if not isinstance(remote_profile, dict):
        return None
    input_data = remote_profile.get("input_data")
    if not isinstance(input_data, dict):
        return None
    return input_data.get("image_name")


def _validate_dpa4_global_contract(
    param_config: dict,
    global_config: dict | None,
    expected_image: str | None,
) -> list[str]:
    """Validate image, hardware, grouping, and audited entry points."""
    if not isinstance(global_config, dict):
        return [
            "DPA4/PT2 requires global.json so the exact image, T4 SKU, and "
            "single-rank wrappers can be verified"
        ]

    errors = []
    if expected_image is not None and (
        global_config.get("lammps_image_name") != expected_image
    ):
        errors.append(
            "DPA4 lammps_image_name must equal the published immutable image "
            f"{expected_image!r}"
        )

    context_type = str(global_config.get("context_type") or "").lower()
    batch_type = str(global_config.get("batch_type") or "").lower()
    if "bohrium" not in context_type or "bohrium" not in batch_type:
        errors.append(
            "DPA4/PT2 production validation requires Bohrium context_type "
            "and batch_type"
        )
    if global_config.get("scass_type") != DPA4_SCASS_TYPE:
        errors.append(
            f"DPA4 scass_type must equal {DPA4_SCASS_TYPE!r}; other T4 SKUs, "
            "CPU, and non-T4 GPUs are unverified"
        )
    if global_config.get("job_type", DPA4_JOB_TYPE) != DPA4_JOB_TYPE:
        errors.append(
            f"DPA4 job_type must equal {DPA4_JOB_TYPE!r} so the immutable "
            "container image is used"
        )
    if global_config.get("platform", DPA4_PLATFORM) != DPA4_PLATFORM:
        errors.append(
            f"DPA4 platform must equal the qualified Bohrium value "
            f"{DPA4_PLATFORM!r}"
        )

    for label in ("machine", "dispatcher_config", "resources", "task"):
        value = global_config.get(label)
        if value not in (None, {}):
            errors.append(
                f"DPA4 {label} overrides are prohibited; use the generated "
                "top-level Bohrium profile without nested dispatcher/resource "
                "configuration"
            )

    machine = global_config.get("machine")
    if isinstance(machine, dict):
        nested_scass = _nested_scass(machine)
        if nested_scass is not None and nested_scass != DPA4_SCASS_TYPE:
            errors.append(
                "machine.remote_profile.input_data.scass_type may not "
                "override the qualified DPA4 T4 SKU"
            )
        for key in ("context_type", "batch_type"):
            value = machine.get(key)
            if value is not None and "bohrium" not in str(value).lower():
                errors.append(
                    f"machine.{key} may not override the DPA4 Bohrium profile"
                )
        nested_image = _nested_image_name(machine)
        if nested_image is not None and nested_image != expected_image:
            errors.append(
                "machine.remote_profile.input_data.image_name may be absent "
                "or equal the published immutable DPA4 image; nested image "
                "overrides are prohibited"
            )

    dispatcher = global_config.get("dispatcher_config")
    if isinstance(dispatcher, dict) and dispatcher.get("json_file") not in (
        None,
        "",
    ):
        errors.append(
            "DPA4 dispatcher_config.json_file is prohibited because it can "
            "inject machine or resource overrides after validation"
        )
    if isinstance(dispatcher, dict) and "machine_dict" in dispatcher:
        dispatcher_machine = dispatcher.get("machine_dict")
        nested_scass = _nested_scass(dispatcher_machine)
        if nested_scass != DPA4_SCASS_TYPE:
            errors.append(
                "dispatcher_config.machine_dict must explicitly retain "
                f"scass_type={DPA4_SCASS_TYPE!r}"
            )
        for key in ("context_type", "batch_type"):
            value = (
                dispatcher_machine.get(key)
                if isinstance(dispatcher_machine, dict)
                else None
            )
            if not isinstance(value, str) or "bohrium" not in value.lower():
                errors.append(
                    "dispatcher_config.machine_dict must retain Bohrium "
                    f"{key}"
                )
        nested_image = _nested_image_name(dispatcher_machine)
        if nested_image is not None and nested_image != expected_image:
            errors.append(
                "dispatcher_config.machine_dict remote image_name may be "
                "absent or equal the published immutable DPA4 image"
            )

    effective_dispatcher_command = global_config.get(
        "dispatcher_command", DPA4_DISPATCHER_COMMAND
    )
    effective_remote_command = global_config.get("dispatcher_remote_command")
    if isinstance(dispatcher, dict):
        if "command" in dispatcher:
            effective_dispatcher_command = dispatcher.get("command")
        if "remote_command" in dispatcher:
            effective_remote_command = dispatcher.get("remote_command")
    if effective_dispatcher_command != DPA4_DISPATCHER_COMMAND:
        errors.append(
            "DPA4 effective dispatcher command must remain the single-process "
            f"default {DPA4_DISPATCHER_COMMAND!r}"
        )
    if effective_remote_command not in (None, ""):
        errors.append(
            "DPA4 dispatcher remote_command is prohibited because it can "
            "bypass the audited one-rank wrapper"
        )

    if global_config.get("lammps_run_command") != DPA4_LAMMPS_RUN_COMMAND:
        errors.append(
            "DPA4 lammps_run_command must equal the audited single-rank "
            f"wrapper {DPA4_LAMMPS_RUN_COMMAND!r}"
        )
    if _uses_dpa4_phonolammps(param_config) and (
        global_config.get("phonolammps_run_command")
        != DPA4_PHONOLAMMPS_RUN_COMMAND
    ):
        errors.append(
            "DPA4 phonon/Gruneisen requires phonolammps_run_command="
            f"{DPA4_PHONOLAMMPS_RUN_COMMAND!r}"
        )
    if type(global_config.get("group_size")) is not int or (
        global_config.get("group_size") != DPA4_GROUP_SIZE
    ):
        errors.append(
            f"DPA4 group_size must equal {DPA4_GROUP_SIZE} for one task per T4"
        )
    if type(global_config.get("pool_size")) is not int or (
        global_config.get("pool_size") != DPA4_POOL_SIZE
    ):
        errors.append(
            f"DPA4 pool_size must equal {DPA4_POOL_SIZE}"
        )
    return errors

# Bare PATH-based VASP executables are unreliable in Bohrium VASP images.
_BARE_VASP_RUN_RE = re.compile(
    r"^\s*mpirun\b.*\bvasp_(?:std|gam|ncl)\s*$", re.IGNORECASE
)


def _resolve_under_base(path_str: str, base_dir: Path) -> Path:
    path = Path(path_str).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path


def _elements_from_poscar(poscar_path: Path) -> list:
    """Best-effort VASP5 species line parse (no pymatgen required)."""
    try:
        lines = poscar_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    if len(lines) < 6:
        return []
    # VASP 5+: line index 5 is element symbols when tokens start with a letter
    tokens = lines[5].split()
    if tokens and all(tok and tok[0].isalpha() for tok in tokens):
        return tokens
    return []


def _iter_structure_poscars(param_config: dict, base_dir: Path) -> list:
    """Resolve structures patterns to POSCAR/CONTCAR/STRU files."""
    found = []
    for pattern in param_config.get("structures", []) or []:
        search = pattern if os.path.isabs(pattern) else str(base_dir / pattern)
        matches = sorted(glob.glob(search))
        if not matches and not any(ch in pattern for ch in "*?[]"):
            # literal directory that exists but was not glob-expanded
            candidate = _resolve_under_base(pattern, base_dir)
            if candidate.exists():
                matches = [str(candidate)]
        for match in matches:
            path = Path(match)
            if path.is_file():
                found.append(path)
                continue
            if path.is_dir():
                for name in ("POSCAR", "CONTCAR", "STRU"):
                    nested = path / name
                    if nested.is_file():
                        found.append(nested)
                        break
                else:
                    found.extend(sorted(path.glob("conf_*/POSCAR")))
    return found


def collect_structure_elements(param_config: dict, base_dir: Path) -> set:
    elements = set()
    for poscar in _iter_structure_poscars(param_config, base_dir):
        elements.update(_elements_from_poscar(poscar))
    return elements


def resolve_vasp_potcar_file(prefix: Path, potcar_entry: str) -> tuple:
    """Resolve POTCAR file the same way APEX VASP.py opens it.

    Returns (path_or_None, hint_for_agent).
    APEX uses ``os.path.join(prefix, potcars[element])`` as a file path.
    """
    direct = (prefix / potcar_entry).expanduser()
    if direct.is_file():
        return direct.resolve(), None
    nested = (prefix / potcar_entry / "POTCAR").expanduser()
    if nested.is_file():
        return None, (
            f"found potpaw-style file at '{nested}', but APEX opens "
            f"join(potcar_prefix, potcars[el]) as a file — set potcars "
            f"entry to '{potcar_entry}/POTCAR' (not '{potcar_entry}')"
        )
    return None, None


def validate_vasp_potcars(
    interaction: dict, base_dir: Path, required_elements: set = None
) -> tuple:
    """Check potcar_prefix accessibility and required POTCAR files."""
    errors = []
    warnings = []

    prefix_raw = interaction.get("potcar_prefix")
    potcars = interaction.get("potcars") or {}
    if not prefix_raw:
        errors.append(
            "VASP: 'potcar_prefix' missing — ask the user for a readable "
            "POTCAR library path"
        )
        return errors, warnings
    if not potcars:
        errors.append("VASP: 'potcars' mapping missing")
        return errors, warnings

    prefix = _resolve_under_base(str(prefix_raw), base_dir)
    # Absolute host libraries are invisible inside Bohrium/dflow containers.
    if Path(str(prefix_raw)).expanduser().is_absolute():
        errors.append(
            f"VASP: potcar_prefix is an absolute host path ({prefix_raw}). "
            "Bohrium/dflow cannot see paths like /share/PAW_PBE after upload. "
            "Copy POTCAR_<Element> files into the job root and set "
            "potcar_prefix to '.' (or re-run generate_config.py create)."
        )
        return errors, warnings
    if not prefix.exists():
        errors.append(
            f"VASP: potcar_prefix not accessible (path does not exist): {prefix}"
        )
        return errors, warnings
    if not os.access(prefix, os.R_OK):
        errors.append(f"VASP: potcar_prefix exists but is not readable: {prefix}")
        return errors, warnings
    if not prefix.is_dir():
        warnings.append(
            f"VASP: potcar_prefix is not a directory ({prefix}); "
            "continuing file checks anyway"
        )

    check_elements = set(required_elements or [])
    check_elements.update(potcars.keys())
    if not check_elements:
        warnings.append(
            "VASP: no structure elements parsed and potcars is empty — "
            "cannot verify POTCAR files"
        )
        return errors, warnings

    missing = []
    for element in sorted(check_elements):
        if element not in potcars:
            missing.append(f"{element} (no potcars mapping)")
            continue
        entry = potcars[element]
        path, hint = resolve_vasp_potcar_file(prefix, entry)
        if path is None:
            expected = prefix / entry
            if hint:
                missing.append(f"{element} → {hint}")
            else:
                missing.append(f"{element} → missing file {expected}")

    if missing:
        errors.append(
            "VASP: POTCAR location unusable or incomplete for required "
            f"elements under '{prefix}'. Missing/invalid:\n  - "
            + "\n  - ".join(missing)
            + "\nAsk the user to confirm the correct POTCAR library path "
            "and potpaw folder names."
        )
    else:
        mapped = ", ".join(
            f"{el}:{potcars[el]}" for el in sorted(check_elements) if el in potcars
        )
        warnings.append(f"VASP: POTCAR files OK ({mapped})")

    return errors, warnings


def _incar_has_kspacing(text: str) -> bool:
    return bool(re.search(r"(?im)^\s*KSPACING\s*=", text))


def _input_has_kspacing(text: str) -> bool:
    return bool(re.search(r"(?im)^\s*kspacing\b", text))


def _parse_incar_values(text: str) -> dict:
    """Parse simple INCAR assignments, including semicolon-separated tags."""
    values = {}
    for match in re.finditer(
        r"(?im)(?:^|;)\s*([A-Za-z][A-Za-z0-9_]*)\s*=\s*([^;!#\n]+)",
        text,
    ):
        values[match.group(1).upper()] = match.group(2).strip()
    return values


def _parse_vasp_bool(value, default=False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    normalized = str(value).strip().strip(".").lower()
    if normalized in {"true", "t", "yes", "y", "1"}:
        return True
    if normalized in {"false", "f", "no", "n", "0"}:
        return False
    raise ValueError(f"invalid VASP boolean value: {value!r}")


def _positive_incar_int(values: dict, key: str, errors: list):
    raw = values.get(key)
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        errors.append(f"VASP: {key} must be a positive integer, got {raw!r}")
        return None
    if value <= 0:
        errors.append(f"VASP: {key} must be a positive integer, got {value}")
        return None
    return value


def _effective_vasp_sampling(prop: dict, incar_values: dict):
    cal_setting = prop.get("cal_setting") or {}
    kspacing = cal_setting.get(
        "kspacing", cal_setting.get("KSPACING", incar_values.get("KSPACING"))
    )
    kgamma = cal_setting.get(
        "kgamma", cal_setting.get("KGAMMA", incar_values.get("KGAMMA"))
    )
    if kspacing is None:
        return None, None
    if isinstance(kspacing, list):
        spacing = [float(value) for value in kspacing]
    else:
        spacing = float(kspacing)
    return spacing, _parse_vasp_bool(kgamma, default=False)


def _kpoint_summary(
    poscar: Path, kspacing, kgamma: bool, supercell_size=None
) -> dict:
    from apex.core.calculator.lib import vasp_utils

    sampling_poscar = poscar
    temporary_dir = None
    if supercell_size is not None:
        factors = [int(value) for value in supercell_size]
        if len(factors) != 3 or any(value <= 0 for value in factors):
            raise ValueError(
                f"invalid VASP supercell factors: {supercell_size!r}"
            )
        lines = poscar.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines()
        if len(lines) < 5:
            raise ValueError(f"unreadable POSCAR: {poscar}")
        for line_index, factor in zip(range(2, 5), factors):
            vector = [float(value) for value in lines[line_index].split()[:3]]
            lines[line_index] = " ".join(
                f"{factor * value:.16g}" for value in vector
            )
        temporary_dir = tempfile.TemporaryDirectory()
        sampling_poscar = Path(temporary_dir.name) / "POSCAR"
        sampling_poscar.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        text = vasp_utils.make_kspacing_kpoints(
            str(sampling_poscar), kspacing, kgamma
        )
    finally:
        if temporary_dir is not None:
            temporary_dir.cleanup()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 4:
        raise RuntimeError("APEX generated an unreadable KPOINTS payload")
    return {
        "style": lines[2],
        "grid": [int(value) for value in lines[3].split()[:3]],
    }


def _property_supercell_size(prop: dict):
    prop_type = prop.get("type")
    if prop_type in {"vacancy", "interstitial"}:
        return prop.get("supercell", [1, 1, 1])
    if prop_type in {"phonon", "gruneisen", "finite_t_latt", "annealing", "melting_point"}:
        return prop.get("supercell_size", [2, 2, 2])
    return prop.get("supercell_size")


def _collect_vasp_sampling_reports(
    param_config: dict,
    base_dir: Path,
    incar_values: dict,
    gamma_reports: list,
) -> tuple:
    """Collect representative grids; runtime still decides from each KPOINTS."""
    reports = list(gamma_reports)
    errors = []
    structure_paths = [
        path for path in _iter_structure_poscars(param_config, base_dir)
        if path.name != "STRU"
    ]

    relaxation = param_config.get("relaxation")
    if (
        isinstance(relaxation, dict)
        and relaxation.get("req_calc", True) is not False
    ):
        try:
            kspacing, kgamma = _effective_vasp_sampling(
                relaxation, incar_values
            )
            for structure_path in structure_paths:
                reports.append(
                    {
                        "label": f"relaxation for {structure_path}",
                        "kpoints": (
                            None if kspacing is None
                            else _kpoint_summary(
                                structure_path, kspacing, kgamma
                            )
                        ),
                    }
                )
        except Exception as exc:
            errors.append(
                f"VASP: cannot verify relaxation KPOINTS: {exc}"
            )

    for prop_index, prop in enumerate(param_config.get("properties", [])):
        if not isinstance(prop, dict) or prop.get("req_calc", True) is False:
            continue
        if prop.get("type") in {"gamma", "gamma_surface"}:
            continue
        try:
            kspacing, kgamma = _effective_vasp_sampling(prop, incar_values)
            supercell_size = _property_supercell_size(prop)
            for structure_path in structure_paths:
                reports.append(
                    {
                        "label": (
                            f"properties[{prop_index}] {prop.get('type')} "
                            f"for {structure_path}"
                        ),
                        "kpoints": (
                            None if kspacing is None
                            else _kpoint_summary(
                                structure_path,
                                kspacing,
                                kgamma,
                                supercell_size=supercell_size,
                            )
                        ),
                    }
                )
        except Exception as exc:
            errors.append(
                f"VASP: cannot verify properties[{prop_index}] KPOINTS: {exc}"
            )
    return reports, errors


def validate_dft_kspacing(
    param_config: dict, base_dir: Path, interaction: dict
) -> tuple:
    """Require VASP KSPACING / ABACUS kspacing (or K_POINTS) before submit."""
    errors = []
    warnings = []
    int_type = interaction.get("type")

    if int_type == "vasp":
        incar_name = interaction.get("incar") or "INCAR"
        incar_path = _resolve_under_base(incar_name, base_dir)
        cal_setting = {}
        relaxation = param_config.get("relaxation") or {}
        if isinstance(relaxation, dict):
            cal_setting = relaxation.get("cal_setting") or {}
        has_cal_kspacing = "kspacing" in cal_setting or "KSPACING" in cal_setting
        if incar_path.is_file():
            text = incar_path.read_text(encoding="utf-8", errors="replace")
            if not _incar_has_kspacing(text) and not has_cal_kspacing:
                errors.append(
                    "VASP: INCAR (or relaxation.cal_setting) must set KSPACING; "
                    "APEX auto-generates KPOINTS from it and will fail without it"
                )
            elif not re.search(r"(?im)^\s*KGAMMA\s*=", text):
                warnings.append(
                    "VASP: KGAMMA not set in INCAR "
                    "(recommend True=Gamma or False=Monkhorst-Pack)"
                )
        elif not has_cal_kspacing:
            errors.append(
                f"VASP: INCAR not found at '{incar_path}' and "
                "cal_setting has no kspacing/KSPACING"
            )

    elif int_type == "abacus":
        input_name = interaction.get("incar") or "INPUT"
        input_path = _resolve_under_base(input_name, base_dir)
        cal_setting = {}
        relaxation = param_config.get("relaxation") or {}
        if isinstance(relaxation, dict):
            cal_setting = relaxation.get("cal_setting") or {}
        has_k_points = "K_POINTS" in cal_setting
        if input_path.is_file():
            text = input_path.read_text(encoding="utf-8", errors="replace")
            if not _input_has_kspacing(text) and not has_k_points:
                errors.append(
                    "ABACUS: INPUT must set kspacing (1/Bohr), or set "
                    "cal_setting.K_POINTS like [nx,ny,nz,0,0,0]; "
                    "APEX writes KPT from these"
                )
        elif not has_k_points:
            warnings.append(
                f"ABACUS: INPUT not found at '{input_path}'; ensure "
                "kspacing or cal_setting.K_POINTS is set before submit"
            )

    return errors, warnings


def validate_vasp_run_command(global_config: dict) -> tuple:
    """Enforce Bohrium-safe VASP run_command when vasp_run_command is set."""
    errors = []
    warnings = []
    cmd = global_config.get("vasp_run_command")
    if not cmd:
        return errors, warnings
    if not isinstance(cmd, str):
        errors.append("'vasp_run_command' must be a string")
        return errors, warnings

    bare = _BARE_VASP_RUN_RE.match(cmd)
    missing_setvars = "setvars.sh" not in cmd
    missing_ulimit = "ulimit" not in cmd
    missing_abs = "/opt/vasp" not in cmd and "vasp.5" not in cmd

    if bare or (missing_setvars and missing_abs):
        errors.append(
            "VASP: vasp_run_command must use the Bohrium template "
            '(source /opt/intel/oneapi/setvars.sh && ulimit -s unlimited && '
            "mpirun -n <RANKS> /opt/vasp.5.4.4/bin/<vasp_std|vasp_gam>); "
            "a bare PATH-based VASP executable is not allowed"
        )
    else:
        if missing_setvars:
            errors.append(
                "VASP: vasp_run_command must source "
                "/opt/intel/oneapi/setvars.sh"
            )
        if missing_ulimit:
            warnings.append(
                "VASP: vasp_run_command should include "
                "'ulimit -s unlimited'"
            )
        if missing_abs:
            warnings.append(
                "VASP: prefer an absolute vasp_std/vasp_gam binary path "
                "in vasp_run_command"
            )

    return errors, warnings


def _read_vasp_incar_values(interaction: dict, base_dir: Path) -> tuple:
    incar_name = interaction.get("incar") or "INCAR"
    incar_path = _resolve_under_base(str(incar_name), base_dir)
    if not incar_path.is_file():
        return {}, incar_path
    text = incar_path.read_text(encoding="utf-8", errors="replace")
    return _parse_incar_values(text), incar_path


def _vasp_command_details(global_config: dict) -> dict:
    command = (
        global_config.get("vasp_run_command")
        or global_config.get("run_command")
        or ""
    )
    executable_match = re.search(
        r"(?:^|[/\s])(?P<name>vasp_(?:std|gam))(?=$|[\s\"'])",
        command,
        re.IGNORECASE,
    )
    ranks_match = re.search(
        r"\bmpirun\b[^;&|\n]*?(?:-n|-np)\s+(?P<ranks>\d+)",
        command,
        re.IGNORECASE,
    )
    scass_match = re.search(
        r"\bc(?P<cores>\d+)_", str(global_config.get("scass_type") or "")
    )
    return {
        "command": command,
        "executable": (
            executable_match.group("name").lower()
            if executable_match else None
        ),
        "ranks": int(ranks_match.group("ranks")) if ranks_match else None,
        "scass_cores": (
            int(scass_match.group("cores")) if scass_match else None
        ),
    }


def _uses_bohrium_backend(global_config: dict) -> bool:
    """Return whether the global configuration selects Bohrium execution."""
    context_values = (
        global_config.get("context_type"),
        global_config.get("batch_type"),
    )
    return (
        global_config.get("dflow_host") == "https://workflows.deepmodeling.com"
        or isinstance(global_config.get("bohrium_config"), dict)
        or any(
            isinstance(value, str) and "bohrium" in value.lower()
            for value in context_values
        )
    )


def validate_vasp_parallel_settings(
    param_config: dict,
    global_config: dict,
    base_dir: Path,
    gamma_reports: list,
) -> tuple:
    """Validate executable, MPI, and INCAR parallel settings together."""
    errors = []
    warnings = []
    interaction = param_config.get("interaction") or {}
    incar_values, incar_path = _read_vasp_incar_values(interaction, base_dir)
    details = _vasp_command_details(global_config)

    executable = details["executable"]
    if executable is None:
        errors.append(
            "VASP: cannot parse vasp_std/vasp_gam from vasp_run_command"
        )
    ranks = details["ranks"]
    if ranks is None:
        errors.append(
            "VASP: cannot parse a positive MPI rank count from "
            "'mpirun -n <RANKS>'"
        )

    scass_cores = details["scass_cores"]
    if (
        _uses_bohrium_backend(global_config)
        and ranks is not None
        and scass_cores is not None
        and ranks != scass_cores
    ):
        errors.append(
            f"VASP: MPI ranks ({ranks}) must match Bohrium CPU count "
            f"from scass_type ({scass_cores})"
        )

    ncore = _positive_incar_int(incar_values, "NCORE", errors)
    npar = _positive_incar_int(incar_values, "NPAR", errors)
    kpar = _positive_incar_int(incar_values, "KPAR", errors)
    if "NCORE" not in incar_values:
        warnings.append(
            f"VASP: NCORE is not set in '{incar_path}'; choose it explicitly "
            "after considering MPI ranks and KPAR"
        )
    if ncore is not None and npar is not None:
        errors.append("VASP: do not set NCORE and NPAR at the same time")

    effective_kpar = 1 if kpar is None else kpar
    if ranks is not None:
        if ranks % effective_kpar:
            errors.append(
                f"VASP: KPAR={effective_kpar} must divide MPI ranks={ranks}"
            )
        elif ncore is not None:
            ranks_per_kgroup = ranks // effective_kpar
            if ranks_per_kgroup % ncore:
                errors.append(
                    f"VASP: NCORE={ncore} must divide "
                    f"MPI ranks/KPAR={ranks_per_kgroup}"
                )

    sampling_reports, sampling_errors = _collect_vasp_sampling_reports(
        param_config, base_dir, incar_values, gamma_reports
    )
    errors.extend(sampling_errors)
    gamma_only_reports = [
        report for report in sampling_reports
        if report.get("kpoints")
        and str(report["kpoints"].get("style", "")).lower() == "gamma"
        and report["kpoints"].get("grid") == [1, 1, 1]
    ]
    if gamma_only_reports and effective_kpar != 1:
        labels = ", ".join(
            report.get("label", "VASP task") for report in gamma_only_reports
        )
        errors.append(
            "VASP: Gamma-centered 1x1x1 tasks are executed with vasp_gam "
            f"and require KPAR=1; detected KPAR={effective_kpar} for {labels}"
        )

    return errors, warnings


def validate_global(global_config: dict) -> list:
    """Validate global.json configuration."""
    errors = []
    warnings = []

    context_type = str(global_config.get("context_type", "")).lower()
    batch_type = str(global_config.get("batch_type", "")).lower()
    if "openapi" in {context_type, batch_type}:
        for key in ("batch_type", "context_type", "machine_type", "image_address"):
            if not global_config.get(key):
                errors.append(f"OpenAPI: missing non-empty '{key}' in global.json")

        project_id = global_config.get("project_id")
        if type(project_id) is not int or project_id <= 0:
            errors.append(
                "OpenAPI: project_id must be a positive unquoted JSON integer"
            )

        access_key = global_config.get("access_key")
        if not isinstance(access_key, str) or not access_key.strip():
            errors.append("OpenAPI: missing non-empty access_key")

        bohrium_config = global_config.get("bohrium_config")
        if not isinstance(bohrium_config, dict):
            errors.append("OpenAPI: missing bohrium_config object")
        else:
            nested_id = bohrium_config.get("project_id")
            if type(nested_id) is not int or nested_id <= 0:
                errors.append(
                    "OpenAPI: bohrium_config.project_id must be a positive "
                    "JSON integer"
                )
            elif nested_id != project_id:
                errors.append("OpenAPI: project_id values must match")
            nested_key = bohrium_config.get("access_key")
            if not isinstance(nested_key, str) or not nested_key.strip():
                errors.append("OpenAPI: missing bohrium_config.access_key")

        dflow_config = global_config.get("dflow_config")
        if not isinstance(dflow_config, dict):
            errors.append("OpenAPI: missing dflow_config object")
        else:
            if dflow_config.get("namespace") != "dflow":
                errors.append("OpenAPI: dflow_config.namespace must be 'dflow'")
            if dflow_config.get("token") != "":
                errors.append("OpenAPI: dflow_config.token must be an empty string")

        lammps_image = global_config.get("lammps_image_name")
        image_address = global_config.get("image_address")
        if lammps_image and image_address != lammps_image:
            errors.append("OpenAPI: image_address and lammps_image_name must match")
        if not global_config.get("dispatcher_image"):
            errors.append("OpenAPI: missing dispatcher_image")

        run_commands = (
            "lammps_run_command", "abacus_run_command", "vasp_run_command"
        )
        if not any(global_config.get(key) for key in run_commands):
            errors.append("OpenAPI: missing calculator run command in global.json")

        rc_errors, rc_warnings = validate_vasp_run_command(global_config)
        errors.extend(rc_errors)
        warnings.extend(rc_warnings)
        return errors, warnings

    machine = global_config.get("machine")
    machine = machine if isinstance(machine, dict) else {}
    is_bohrium = _uses_bohrium_backend(global_config)

    if is_bohrium:
        for key in ("batch_type", "context_type"):
            if not global_config.get(key):
                errors.append(f"Missing '{key}' in global.json")

        program_id = global_config.get("program_id")
        if program_id is not None and (
            not isinstance(program_id, int) or isinstance(program_id, bool)
        ):
            errors.append(
                "'program_id' must be an unquoted JSON integer, not a string; "
                "use `apex account` or regenerate global.json"
            )
        elif isinstance(program_id, int) and program_id <= 0:
            errors.append("'program_id' must be a positive integer")

        bohrium_config = global_config.get("bohrium_config")
        ticket_mode = isinstance(bohrium_config, dict)
        if ticket_mode:
            project_id = bohrium_config.get("project_id")
            if not isinstance(project_id, int) or isinstance(project_id, bool):
                errors.append(
                    "'bohrium_config.project_id' must be a JSON integer, "
                    "not a quoted string"
                )
            elif project_id <= 0:
                errors.append(
                    "'bohrium_config.project_id' must be a positive integer"
                )
            elif isinstance(program_id, int) and not isinstance(program_id, bool):
                if project_id != program_id:
                    errors.append(
                        "'program_id' and 'bohrium_config.project_id' must match"
                    )

            ticket = bohrium_config.get("ticket")
            if not isinstance(ticket, str) or not ticket.strip():
                errors.append(
                    "Missing non-empty 'bohrium_config.ticket'; regenerate "
                    "global.json with generate_config.py"
                )
            if program_id is None:
                errors.append(
                    "Ticket-based Bohrium config requires integer 'program_id'"
                )
        else:
            warnings.append(
                "Bohrium direct-submit profile uses credentials from "
                "`apex account`; verify them with `apex account --show`"
            )

        remote_profile = machine.get("remote_profile")
        if isinstance(remote_profile, dict) and "program_id" in remote_profile:
            nested_id = remote_profile["program_id"]
            if not isinstance(nested_id, int) or isinstance(nested_id, bool):
                errors.append(
                    "'machine.remote_profile.program_id' must be a JSON "
                    "integer, not a quoted string"
                )
            elif program_id is not None and nested_id != program_id:
                errors.append(
                    "'machine.remote_profile.program_id' must match "
                    "'program_id'"
                )

        if not global_config.get("scass_type"):
            errors.append("Missing 'scass_type' in global.json")

        run_commands = (
            "lammps_run_command", "abacus_run_command", "vasp_run_command"
        )
        if not any(global_config.get(key) for key in run_commands):
            errors.append(
                "Missing calculator run command in global.json "
                "(expected one of lammps_run_command, abacus_run_command, "
                "vasp_run_command)"
            )

        rc_errors, rc_warnings = validate_vasp_run_command(global_config)
        errors.extend(rc_errors)
        warnings.extend(rc_warnings)
    else:
        # Local debug and DPDispatcher cluster modes do not use Bohrium auth.
        top_batch = global_config.get("batch_type")
        nested_batch = machine.get("batch_type")
        batch_type = nested_batch or top_batch
        if not batch_type:
            if not machine:
                errors.append(
                    "Missing local execution configuration: set top-level "
                    "'batch_type' or provide 'machine.batch_type'"
                )
            else:
                errors.append("Missing 'machine.batch_type'")

        if (
            isinstance(batch_type, str)
            and batch_type.lower() not in {"shell"}
            and "resources" not in global_config
        ):
            warnings.append("No 'resources' section - will use defaults")

        if "run_command" not in global_config:
            errors.append("Missing 'run_command' in global.json")

        if re.search(r"<[^>]+>", json.dumps(global_config)):
            errors.append(
                "Replace all <...> placeholders in local/cluster global.json"
            )

    return errors, warnings


def validate_interaction(interaction: dict) -> list:
    """Validate interaction configuration."""
    errors = []
    warnings = []

    if "type" not in interaction:
        errors.append("Missing 'interaction.type'")
        return errors, warnings

    int_type = interaction["type"]

    if int_type not in VALID_BACKENDS:
        errors.append(f"Unknown interaction type: '{int_type}'")
        return errors, warnings

    # LAMMPS-specific checks
    if int_type in VALID_LAMMPS_TYPES:
        if "model" not in interaction:
            errors.append(f"LAMMPS potential '{int_type}' requires 'model' field")
        if "type_map" not in interaction:
            errors.append(f"LAMMPS potential '{int_type}' requires 'type_map' field")
        if "model_in_image" in interaction and not isinstance(
            interaction["model_in_image"], bool
        ):
            errors.append("interaction.model_in_image must be a boolean")
        runtime = interaction.get("deepmd_runtime")
        if runtime is not None and runtime != DPA4_RUNTIME_KIND:
            errors.append(
                f"interaction.deepmd_runtime must equal {DPA4_RUNTIME_KIND!r}"
            )
        if (
            runtime == DPA4_RUNTIME_KIND
            and int_type != "deepmd"
        ):
            errors.append("deepmd_runtime=dpa4_pt2 requires interaction.type=deepmd")

    # ABACUS-specific checks
    elif int_type == "abacus":
        if "potcars" not in interaction:
            errors.append("ABACUS requires 'potcars' (pseudopotential mapping)")
        if "orb_files" not in interaction:
            warnings.append("ABACUS: 'orb_files' not specified (required for LCAO)")

    # VASP-specific checks (filesystem checks run in validate_vasp_potcars)
    elif int_type == "vasp":
        if "potcars" not in interaction:
            errors.append("VASP requires 'potcars' mapping")
        if "potcar_prefix" not in interaction:
            errors.append("VASP requires 'potcar_prefix' (POTCAR library path)")

    return errors, warnings


def validate_bundled_dpa4_runtime(
    param_config: dict,
    global_config: dict | None,
    base_dir: Path,
) -> tuple[list[str], list[str]]:
    """Require the exact image-resident, T4-only DPA4 production contract."""
    interactions = list(_iter_effective_interactions(param_config))
    if not interactions:
        return [], []

    errors = []
    kinds = []
    expected_fields = {
        "type": "deepmd",
        "deepmd_runtime": DPA4_RUNTIME_KIND,
        "model_in_image": True,
        "model": DPA4_RUNTIME_MODEL_PATH,
        "runtime_model_sha256": DPA4_RUNTIME_MODEL_SHA256,
        "source_checkpoint": DPA4_SOURCE_CHECKPOINT_PATH,
        "source_checkpoint_sha256": BUNDLED_DPA4_SHA256,
    }

    for label, interaction in interactions:
        local_checkpoint = False
        model = interaction.get("model")
        if isinstance(model, str) and interaction.get("model_in_image") is not True:
            model_path = _resolve_under_base(model, base_dir)
            try:
                local_checkpoint = (
                    model_path.is_file()
                    and _sha256_path(model_path) == BUNDLED_DPA4_SHA256
                )
            except OSError:
                local_checkpoint = False

        if local_checkpoint:
            kinds.append(DPA4_RUNTIME_KIND)
            errors.append(
                f"{label}.model is the bundled DPA4 source checkpoint; "
                f"LAMMPS must use image-resident {DPA4_RUNTIME_MODEL_PATH!r}, "
                "never model.pt"
            )
            continue

        if not _dpa4_intent(interaction):
            kinds.append("legacy")
            continue

        kinds.append(DPA4_RUNTIME_KIND)
        for key in ("runtime_model_sha256", "source_checkpoint_sha256"):
            declared = interaction.get(key)
            if not isinstance(declared, str) or not _HEX_SHA256_RE.fullmatch(declared):
                errors.append(
                    f"{label}.{key} must be a lowercase SHA-256 hex digest"
                )
        for key, expected in expected_fields.items():
            if interaction.get(key) != expected:
                errors.append(
                    f"{label}.{key} must equal {expected!r} for the bundled "
                    "DPA4 T4/PT2 runtime"
                )
        type_map = interaction.get("type_map")
        valid_type_map = type_map == "auto" or (
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
        if not valid_type_map:
            errors.append(
                f"{label}.type_map must be 'auto' before CLI expansion or a "
                "non-empty contiguous element-to-index mapping afterwards"
            )

    kind_set = set(kinds)
    if DPA4_RUNTIME_KIND in kind_set and "legacy" in kind_set:
        errors.append(
            "A single APEX parameter set cannot mix legacy LAMMPS interactions "
            "with bundled DPA4/PT2 (including overwrite_interaction)"
        )

    if DPA4_RUNTIME_KIND in kind_set:
        base_interaction = param_config.get("interaction")
        if (
            not isinstance(base_interaction, dict)
            or base_interaction.get("type") not in VALID_LAMMPS_TYPES
        ):
            errors.append(
                "DPA4/PT2 overwrite_interaction requires a LAMMPS base "
                "interaction; VASP/ABACUS base calculators cannot execute the "
                "DPA4 runtime"
            )
        expected_image = _dpa4_image_name()
        if expected_image is None:
            errors.append(
                "DPA4 image identity is not finalized: replace "
                "__DPA4_IMAGE_REF__ and __DPA4_IMAGE_DIGEST__ before submission"
            )
        errors.extend(
            _validate_dpa4_global_contract(
                param_config,
                global_config,
                expected_image,
            )
        )

    return errors, []


def validate_gamma_settings(prop: dict, prefix: str) -> tuple:
    """Mirror the public validation rules in ``gamma_slab.py``."""
    errors = []
    warnings = []
    parent_lattice = prop.get("parent_lattice")
    if parent_lattice is not None and (
        not isinstance(parent_lattice, str)
        or parent_lattice.strip().lower() not in {"bcc", "fcc", "hcp"}
    ):
        errors.append(
            f"{prefix}: parent_lattice must be one of bcc, fcc, or hcp"
        )
    supercell = prop.get("supercell_size", [1, 1, 5])
    if not isinstance(supercell, (list, tuple)) or len(supercell) != 3:
        errors.append(f"{prefix}: gamma supercell_size must contain 3 values")
    else:
        for index, value in enumerate(supercell[:2]):
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value <= 0
            ):
                errors.append(
                    f"{prefix}: gamma supercell_size[{index}] "
                    "must be a positive integer"
                )
        plane_count = supercell[2]
        if (
            isinstance(plane_count, bool)
            or not isinstance(plane_count, (int, float))
            or not math.isfinite(float(plane_count))
            or plane_count <= 0
        ):
            errors.append(
                f"{prefix}: gamma supercell_size[2] must be a positive "
                "finite number of Miller-plane spacings"
            )

    min_height = prop.get("min_slab_height")
    if min_height is None:
        warnings.append(
            f"{prefix}: min_slab_height is not set; confirm the generated "
            "material thickness before submission"
        )
    elif (
        isinstance(min_height, bool)
        or not isinstance(min_height, (int, float))
        or not math.isfinite(float(min_height))
        or min_height <= 0
    ):
        errors.append(f"{prefix}: min_slab_height must be a positive finite number")

    max_atoms = prop.get("max_atoms")
    if max_atoms is None:
        warnings.append(
            f"{prefix}: max_atoms is not set; confirm the generated atom "
            "count before submission"
        )
    elif (
        not isinstance(max_atoms, int)
        or isinstance(max_atoms, bool)
        or max_atoms <= 0
    ):
        errors.append(f"{prefix}: max_atoms must be a positive integer")

    min_distance = prop.get("min_distance", 0.2)
    if (
        isinstance(min_distance, bool)
        or not isinstance(min_distance, (int, float))
        or not math.isfinite(float(min_distance))
        or min_distance < 0
    ):
        errors.append(f"{prefix}: min_distance must be non-negative and finite")

    vacuum_size = prop.get("vacuum_size", 20)
    if (
        isinstance(vacuum_size, bool)
        or not isinstance(vacuum_size, (int, float))
        or not math.isfinite(float(vacuum_size))
        or vacuum_size < 0
    ):
        errors.append(f"{prefix}: vacuum_size must be non-negative and finite")

    require_orthogonal = prop.get(
        "require_orthogonal_cell", prop.get("orthogonalize_cell", False)
    )
    if not isinstance(require_orthogonal, bool):
        errors.append(f"{prefix}: require_orthogonal_cell must be a boolean")
    if (
        "require_orthogonal_cell" in prop
        and "orthogonalize_cell" in prop
        and prop["require_orthogonal_cell"] != prop["orthogonalize_cell"]
    ):
        errors.append(
            f"{prefix}: require_orthogonal_cell and orthogonalize_cell disagree"
        )

    if prop.get("type") == "gamma":
        n_steps = prop.get("n_steps", 10)
        if (
            not isinstance(n_steps, int)
            or isinstance(n_steps, bool)
            or n_steps <= 0
        ):
            errors.append(f"{prefix}: n_steps must be a positive integer")
        displacement_points = prop.get("displacement_points")
        if displacement_points is not None and (
            not isinstance(displacement_points, list)
            or not displacement_points
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or not 0.0 <= float(value) <= 1.0
                for value in displacement_points
            )
            or len(set(displacement_points)) != len(displacement_points)
            or 0.0 not in displacement_points
        ):
            errors.append(
                f"{prefix}: displacement_points must include 0 and contain "
                "unique finite values in [0, 1]"
            )
    return errors, warnings


def validate_properties(
    properties: list, interaction_type: str, base_dir: Path = None
) -> list:
    """Validate property configurations."""
    errors = []
    warnings = []

    if not properties:
        errors.append("No properties defined")
        return errors, warnings

    for i, prop in enumerate(properties):
        prefix = f"properties[{i}]"

        if "type" not in prop:
            errors.append(f"{prefix}: missing 'type' field")
            continue

        prop_type = prop["type"]
        if prop_type not in VALID_PROPERTIES:
            errors.append(f"{prefix}: unknown property type '{prop_type}'")
            continue

        cal_setting = prop.get("cal_setting", {})
        if (
            prop_type != "melting_point"
            and isinstance(cal_setting, dict)
            and "restart_files" in cal_setting
        ):
            errors.append(
                f"{prefix}: cal_setting.restart_files is supported only by "
                "melting_point; finite_t_latt and other properties do not "
                "forward restart.coexistence.start"
            )

        # Check LAMMPS-only constraint
        if prop_type in LAMMPS_ONLY_PROPERTIES:
            if interaction_type in ("vasp", "abacus"):
                errors.append(
                    f"{prefix}: '{prop_type}' is LAMMPS-only, "
                    f"but backend is '{interaction_type}'"
                )

        # Property-specific validation
        if prop_type == "eos":
            for key in ("vol_start", "vol_end", "vol_step"):
                if key not in prop:
                    errors.append(f"{prefix}: EOS requires '{key}'")

        elif prop_type == "cohesive":
            for key in ("latt_start", "latt_end", "latt_step"):
                if key not in prop:
                    errors.append(f"{prefix}: cohesive requires '{key}'")

        elif prop_type == "surface":
            for key in ("min_slab_size", "min_vacuum_size"):
                if key not in prop:
                    errors.append(f"{prefix}: surface requires '{key}'")

        elif prop_type == "decohesive":
            if "miller_index" not in prop:
                errors.append(f"{prefix}: decohesive requires 'miller_index'")
            if "min_slab_size" not in prop:
                errors.append(f"{prefix}: decohesive requires 'min_slab_size'")

        elif prop_type == "gruneisen":
            mesh = prop.get("MESH")
            if mesh is None:
                mesh = [20, 20, 20]
            if (
                not isinstance(mesh, (list, tuple))
                or len(mesh) != 3
                or any(
                    not isinstance(value, int)
                    or isinstance(value, bool)
                    or value <= 0
                    for value in mesh
                )
            ):
                errors.append(
                    f"{prefix}: gruneisen 'MESH' must contain 3 positive integers"
                )
            if "volume_strains" not in prop:
                errors.append(f"{prefix}: gruneisen requires 'volume_strains'")
            else:
                vs = prop["volume_strains"]
                if 0.0 not in vs:
                    errors.append(f"{prefix}: gruneisen 'volume_strains' must include 0.0")
                if len(vs) < 3:
                    errors.append(f"{prefix}: gruneisen needs ≥3 volume_strains points")
            if "temperatures" not in prop:
                errors.append(f"{prefix}: gruneisen requires 'temperatures'")

        elif prop_type == "finite_t_elastic":
            cal_setting = prop.get("cal_setting", {})
            method = cal_setting.get("method", "paired_langevin")
            if method != "paired_langevin":
                errors.append(
                    f"{prefix}: finite_t_elastic only supports method='paired_langevin'"
                )

        elif prop_type == "melting_point":
            method = str(prop.get("method", "two_phase")).lower().replace("-", "_")
            if method not in {
                "two_phase", "coexistence", "two_phase_coexistence",
                "direct_coexistence",
            }:
                errors.append(
                    f"{prefix}: melting_point only supports method='two_phase'"
                )
            supercell = prop.get("supercell_size", [1, 1, 1])
            if (
                not isinstance(supercell, (list, tuple))
                or len(supercell) != 3
                or any(
                    not isinstance(value, int)
                    or isinstance(value, bool)
                    or value <= 0
                    for value in supercell
                )
            ):
                errors.append(
                    f"{prefix}: supercell_size must contain 3 positive integers"
                )
            cal = prop.get("cal_setting", {})
            temperatures = cal.get("temperature")
            if not isinstance(temperatures, list) or not temperatures:
                errors.append(
                    f"{prefix}: cal_setting.temperature must be a non-empty list"
                )
            elif any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or value <= 0
                for value in temperatures
            ):
                errors.append(f"{prefix}: all temperatures must be positive finite numbers")
            axis = cal.get("interface_axis", "z")
            if axis not in {"x", "y", "z"}:
                errors.append(f"{prefix}: interface_axis must be x, y, or z")
            liquid_fraction = cal.get("liquid_fraction", 0.5)
            if (
                isinstance(liquid_fraction, bool)
                or not isinstance(liquid_fraction, (int, float))
                or not 0.1 <= float(liquid_fraction) <= 0.9
            ):
                errors.append(
                    f"{prefix}: liquid_fraction must be between 0.1 and 0.9"
                )
            replicas = cal.get("replicas", 1)
            if (
                not isinstance(replicas, int)
                or isinstance(replicas, bool)
                or replicas < 1
            ):
                errors.append(f"{prefix}: replicas must be a positive integer")
            restart_files = cal.get("restart_files")
            if restart_files is not None:
                if not isinstance(restart_files, list):
                    errors.append(
                        f"{prefix}: restart_files must be a list with one "
                        "entry per temperature"
                    )
                elif isinstance(temperatures, list) and len(restart_files) != len(
                    temperatures
                ):
                    errors.append(
                        f"{prefix}: restart_files must contain exactly one "
                        "entry per temperature"
                    )
                else:
                    invalid_paths = [
                        path for path in restart_files
                        if not isinstance(path, str) or not path.strip()
                    ]
                    if invalid_paths:
                        errors.append(
                            f"{prefix}: restart_files entries must be "
                            "non-empty paths"
                        )
                    elif base_dir is not None:
                        missing = [
                            path for path in restart_files
                            if not (
                                Path(path)
                                if Path(path).is_absolute()
                                else base_dir / path
                            ).is_file()
                        ]
                        if missing:
                            errors.append(
                                f"{prefix}: melting restart file(s) not found: "
                                + ", ".join(missing)
                            )
            for key in (
                "premelt_steps", "conditioning_steps", "production_steps",
                "dump_step", "thermo_step", "restart_interval",
            ):
                value = cal.get(key, {
                    "premelt_steps": 5000,
                    "conditioning_steps": 5000,
                    "production_steps": 100000,
                    "dump_step": 100,
                    "thermo_step": 100,
                    "restart_interval": 10000,
                }[key])
                if (
                    not isinstance(value, int)
                    or isinstance(value, bool)
                    or value <= 0
                ):
                    errors.append(
                        f"{prefix}: melting_point {key} must be a positive integer"
                    )

        if prop_type in {"gamma", "gamma_surface"}:
            gamma_errors, gamma_warnings = validate_gamma_settings(prop, prefix)
            errors.extend(gamma_errors)
            warnings.extend(gamma_warnings)
            plane = prop.get("plane_miller")
            direction = prop.get("slip_direction")
            if not plane or not direction:
                errors.append(
                    f"{prefix}: {prop_type} requires plane_miller and slip_direction"
                )
            elif not isinstance(plane, (list, tuple)) or not isinstance(
                direction, (list, tuple)
            ):
                errors.append(
                    f"{prefix}: plane_miller and slip_direction must be sequences"
                )
            elif len(plane) != len(direction):
                errors.append(
                    f"{prefix}: plane_miller and slip_direction dimensions differ"
                )
            elif not 3 <= len(plane) <= 4:
                errors.append(
                    f"{prefix}: plane_miller and slip_direction require "
                    "3 or 4 components"
                )
            elif not all(
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(float(value))
                for value in list(plane) + list(direction)
            ):
                errors.append(
                    f"{prefix}: plane_miller and slip_direction must be "
                    "finite numeric vectors"
                )
            elif not any(float(value) != 0 for value in plane) or not any(
                float(value) != 0 for value in direction
            ):
                errors.append(
                    f"{prefix}: plane_miller and slip_direction must be "
                    "non-zero vectors"
                )
            elif not math.isclose(
                sum(p * d for p, d in zip(plane, direction)),
                0.0,
                abs_tol=1.0e-10,
            ):
                errors.append(
                    f"{prefix}: slip_direction must lie on plane_miller"
                )

        if prop_type == "gamma_surface":
            for key in ("n_steps_x", "n_steps_y"):
                value = prop.get(key, prop.get("n_steps", 10))
                if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                    errors.append(f"{prefix}: {key} must be a positive integer")
            closed_loop = prop.get("closed_loop", False)
            if not isinstance(closed_loop, bool):
                errors.append(f"{prefix}: closed_loop must be a boolean")
            if closed_loop is True and (
                prop.get("slip_length") is not None
                or prop.get("slip_length_y") is not None
            ):
                errors.append(
                    f"{prefix}: closed_loop cannot be combined with "
                    "slip_length/slip_length_y"
                )

    return errors, warnings


def _gamma_task_count(prop: dict) -> int:
    if prop.get("type") == "gamma":
        if prop.get("displacement_points") is not None:
            return len(prop["displacement_points"])
        return int(prop.get("n_steps", 10)) + 1
    n_steps_x = int(prop.get("n_steps_x", prop.get("n_steps", 10)))
    n_steps_y = int(prop.get("n_steps_y", n_steps_x))
    return (n_steps_x + 1) * (n_steps_y + 1)


def preflight_gamma_structures(
    param_config: dict, base_dir: Path
) -> tuple:
    """Generate one representative slab per structure and Gamma property."""
    reports = []
    errors = []
    warnings = []
    properties = [
        (index, prop)
        for index, prop in enumerate(param_config.get("properties", []))
        if isinstance(prop, dict)
        and prop.get("type") in {"gamma", "gamma_surface"}
    ]
    if not properties:
        return reports, errors, warnings

    structure_paths = _iter_structure_poscars(param_config, base_dir)
    if not structure_paths:
        errors.append(
            "Gamma preflight: no local POSCAR/CONTCAR structure was resolved"
        )
        return reports, errors, warnings

    interaction = param_config.get("interaction") or {}
    if interaction.get("type") == "abacus":
        warnings.append(
            "Gamma preflight: representative STRU generation is not performed; "
            "run apex preview before submission"
        )
        return reports, errors, warnings

    incar_values = {}
    if interaction.get("type") == "vasp":
        incar_values, _ = _read_vasp_incar_values(interaction, base_dir)

    from pymatgen.core.structure import Structure
    from apex.core.property.Gamma import Gamma
    from apex.core.property.GammaSurface import GammaSurface

    for structure_path in structure_paths:
        if structure_path.name == "STRU":
            warnings.append(
                f"Gamma preflight: skipped unsupported structure file "
                f"'{structure_path}'"
            )
            continue
        try:
            parent_atom_count = len(Structure.from_file(structure_path))
        except Exception as exc:
            errors.append(
                f"Gamma preflight: cannot read '{structure_path}': {exc}"
            )
            continue

        for prop_index, original_prop in properties:
            prop = copy.deepcopy(original_prop)
            prop_type = prop["type"]
            label = (
                f"properties[{prop_index}] {prop_type} "
                f"for {structure_path}"
            )
            prop["reproduce"] = False
            for key in (
                "init_from_suffix",
                "output_suffix",
                "init_data_path",
                "start_confs_path",
            ):
                prop.pop(key, None)
            if prop_type == "gamma":
                prop["n_steps"] = 1
                if prop.get("displacement_points") is not None:
                    prop["displacement_points"] = [0.0]
            else:
                prop["n_steps_x"] = 1
                prop["n_steps_y"] = 1

            previous_cwd = Path.cwd()
            try:
                with tempfile.TemporaryDirectory(
                    prefix="apex-gamma-preflight-"
                ) as temp_name:
                    root = Path(temp_name)
                    equi = root / "relaxation" / "relax_task"
                    work = root / prop_type
                    equi.mkdir(parents=True)
                    shutil.copy2(structure_path, equi / "CONTCAR")
                    (equi / "result.json").write_text(
                        "{}\n", encoding="utf-8"
                    )

                    if prop_type == "gamma":
                        task_paths = Gamma(
                            prop, interaction
                        ).make_confs(str(work), str(equi))
                    else:
                        task_paths = GammaSurface(
                            prop, interaction
                        ).make_confs(
                            str(work),
                            str(equi),
                            require_relaxation_result=False,
                        )
                    if not task_paths:
                        raise RuntimeError("no representative task was generated")

                    metadata_path = work / "slab_generation.json"
                    metadata = json.loads(
                        metadata_path.read_text(encoding="utf-8")
                    )
                    task_poscar = Path(task_paths[0]) / "POSCAR"
                    kpoints = None
                    if interaction.get("type") == "vasp":
                        kspacing, kgamma = _effective_vasp_sampling(
                            original_prop, incar_values
                        )
                        if kspacing is not None:
                            kpoints = _kpoint_summary(
                                task_poscar, kspacing, kgamma
                            )

                    min_height = original_prop.get("min_slab_height")
                    if (
                        min_height is not None
                        and metadata["slab_height"] + 1.0e-10
                        < float(min_height)
                    ):
                        raise RuntimeError(
                            f"generated material thickness "
                            f"{metadata['slab_height']:.6f} A is below "
                            f"min_slab_height={float(min_height):.6f} A"
                        )

                    reports.append(
                        {
                            "label": label,
                            "property_index": prop_index,
                            "property_type": prop_type,
                            "structure": str(structure_path),
                            "parent_atom_count": parent_atom_count,
                            "atom_count": metadata["atom_count"],
                            "slab_height": metadata["slab_height"],
                            "effective_plane_spacings": metadata[
                                "effective_plane_spacings"
                            ],
                            "oriented_cell_repeats": metadata[
                                "oriented_cell_repeats"
                            ],
                            "minimum_pair_distance": metadata[
                                "minimum_pair_distance"
                            ],
                            "expected_task_count": _gamma_task_count(
                                original_prop
                            ),
                            "kpoints": kpoints,
                        }
                    )
            except Exception as exc:
                errors.append(f"Gamma preflight failed for {label}: {exc}")
            finally:
                os.chdir(previous_cwd)

    if not reports and not errors:
        errors.append("Gamma preflight did not produce a representative slab")
    return reports, errors, warnings


def print_gamma_preflight_reports(reports: list):
    if not reports:
        return
    print("Gamma preflight:")
    for report in reports:
        kpoints = report.get("kpoints")
        kpoint_text = (
            "not available"
            if not kpoints
            else f"{kpoints['style']} {kpoints['grid']}"
        )
        print(f"  {report['label']}")
        print(
            "    atoms(parent/final)="
            f"{report['parent_atom_count']}/{report['atom_count']}; "
            f"thickness={report['slab_height']:.6f} A; "
            f"plane spacings={report['effective_plane_spacings']:.8g}; "
            f"layers={report['oriented_cell_repeats']}; "
            f"minimum distance={report['minimum_pair_distance']:.6f} A; "
            f"expected tasks={report['expected_task_count']}; "
            f"KPOINTS={kpoint_text}"
        )


def validate_structures(param_config: dict, base_dir: Path) -> list:
    """Validate structure paths."""
    errors = []
    warnings = []

    structures = param_config.get("structures", [])
    if not structures:
        errors.append("No 'structures' defined in param.json")
        return errors, warnings

    for s in structures:
        struct_path = base_dir / s
        if not struct_path.exists():
            warnings.append(f"Structure path not found: {s} (may exist in container)")

    return errors, warnings


def main():
    parser = argparse.ArgumentParser(
        description="Validate APEX configuration files"
    )
    parser.add_argument("--param", "-p", required=True,
                        help="Path to param.json")
    parser.add_argument("--global", "-g", dest="global_json",
                        help="Path to global.json")
    parser.add_argument("--strict", action="store_true",
                        help="Treat warnings as errors")

    args = parser.parse_args()

    all_errors = []
    all_warnings = []
    global_config = None
    gamma_reports = []

    # Load param.json
    param_path = Path(args.param)
    if not param_path.exists():
        print(f"ERROR: param.json not found: {param_path}", file=sys.stderr)
        sys.exit(1)

    with open(param_path) as f:
        param_config = json.load(f)

    # Validate global.json if provided
    if args.global_json:
        global_path = Path(args.global_json)
        if not global_path.exists():
            all_errors.append(f"global.json not found: {global_path}")
        else:
            with open(global_path) as f:
                global_config = json.load(f)
            errors, warnings = validate_global(global_config)
            all_errors.extend(errors)
            all_warnings.extend(warnings)

    # Validate interaction
    interaction = param_config.get("interaction", {})
    base_dir = param_path.parent
    if not interaction:
        all_errors.append("Missing 'interaction' in param.json")
    else:
        errors, warnings = validate_interaction(interaction)
        all_errors.extend(errors)
        all_warnings.extend(warnings)

    # Validate properties
    properties = param_config.get("properties", [])
    interaction_type = interaction.get("type", "unknown")
    # Relaxation-only parameter files intentionally omit ``properties`` (or
    # may leave it empty).  Keep rejecting an empty property-only input, but
    # do not turn a valid relaxation flow into a validation failure.
    if properties or not isinstance(param_config.get("relaxation"), dict):
        errors, warnings = validate_properties(
            properties, interaction_type, base_dir=base_dir
        )
    else:
        errors, warnings = [], []
    all_errors.extend(errors)
    all_warnings.extend(warnings)

    for label, overwrite in _iter_effective_interactions(param_config):
        if label == "interaction" or label.endswith(".interaction"):
            continue
        errors, warnings = validate_interaction(overwrite)
        all_errors.extend(f"{label}: {error}" for error in errors)
        all_warnings.extend(f"{label}: {warning}" for warning in warnings)

    errors, warnings = validate_bundled_dpa4_runtime(
        param_config, global_config, base_dir
    )
    all_errors.extend(errors)
    all_warnings.extend(warnings)

    # Validate structures
    errors, warnings = validate_structures(param_config, base_dir)
    all_errors.extend(errors)
    all_warnings.extend(warnings)

    # Build representative Gamma slabs before any remote submission.
    if any(
        isinstance(prop, dict)
        and prop.get("type") in {"gamma", "gamma_surface"}
        for prop in properties
    ):
        reports, errors, warnings = preflight_gamma_structures(
            param_config, base_dir
        )
        gamma_reports.extend(reports)
        all_errors.extend(errors)
        all_warnings.extend(warnings)

    # VASP POTCAR filesystem check (prefix + per-element files)
    if interaction.get("type") == "vasp":
        required_elements = collect_structure_elements(param_config, base_dir)
        errors, warnings = validate_vasp_potcars(
            interaction, base_dir, required_elements=required_elements
        )
        all_errors.extend(errors)
        # "POTCAR files OK" is informational; keep as warning only when useful
        for w in warnings:
            if w.startswith("VASP: POTCAR files OK"):
                print(f"  {w}")
            else:
                all_warnings.append(w)

    # DFT k-spacing (VASP KSPACING / ABACUS kspacing)
    if interaction.get("type") in ("vasp", "abacus"):
        errors, warnings = validate_dft_kspacing(
            param_config, base_dir, interaction
        )
        all_errors.extend(errors)
        all_warnings.extend(warnings)

    if interaction.get("type") == "vasp" and global_config is not None:
        errors, warnings = validate_vasp_parallel_settings(
            param_config, global_config, base_dir, gamma_reports
        )
        all_errors.extend(errors)
        all_warnings.extend(warnings)

    # VASP image is license-gated: require an explicit user-provided image.
    if interaction.get("type") == "vasp" and global_config is not None:
        image = global_config.get("vasp_image_name")
        if not isinstance(image, str) or not image.strip():
            all_errors.append(
                "VASP: missing 'vasp_image_name' in global.json. "
                "VASP is commercial — do not invent a default image; "
                "ask the user to confirm a license and provide the image"
            )
        elif any(
            token in image.lower()
            for token in ("user-provided", "your_", "example", "<", "todo")
        ):
            all_errors.append(
                "VASP: 'vasp_image_name' looks like a placeholder. "
                "Replace it with a user-confirmed licensed VASP image"
            )

    # Report
    print_gamma_preflight_reports(gamma_reports)
    if all_warnings:
        print("WARNINGS:", file=sys.stderr)
        for w in all_warnings:
            print(f"  ⚠ {w}", file=sys.stderr)

    if all_errors:
        print("\nERRORS:", file=sys.stderr)
        for e in all_errors:
            print(f"  ✗ {e}", file=sys.stderr)
        print(f"\nValidation FAILED: {len(all_errors)} error(s)", file=sys.stderr)
        sys.exit(1)
    elif args.strict and all_warnings:
        print(f"\nValidation FAILED (strict mode): {len(all_warnings)} warning(s)",
              file=sys.stderr)
        sys.exit(1)
    else:
        print(f"✓ Validation PASSED ({len(all_warnings)} warnings)")
        # Print summary
        print(f"  Backend: {interaction_type}")
        print(f"  Properties: {[p.get('type') for p in properties]}")
        print(f"  Structures: {param_config.get('structures', [])}")
        if global_config and "program_id" in global_config:
            program_id = global_config["program_id"]
            project_id = global_config.get(
                "bohrium_config", {}
            ).get("project_id")
            print("  Hard project ID type check:")
            print(
                f"    program_id={program_id!r} "
                f"type={type(program_id).__name__}"
            )
            print(
                f"    bohrium_config.project_id={project_id!r} "
                f"type={type(project_id).__name__}"
            )
        elif global_config and str(
            global_config.get("context_type", "")
        ).lower() == "openapi":
            project_id = global_config.get("project_id")
            nested_id = global_config.get(
                "bohrium_config", {}
            ).get("project_id")
            print("  Hard OpenAPI project ID type check:")
            print(
                f"    project_id={project_id!r} "
                f"type={type(project_id).__name__}"
            )
            print(
                f"    bohrium_config.project_id={nested_id!r} "
                f"type={type(nested_id).__name__}"
            )


if __name__ == "__main__":
    main()
