import os
import os.path
import glob
import shutil
import tempfile
import logging
import copy
import json
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


def _resolve_first_structure_file(param_path: str, structures: List[str]) -> str:
    base_dir = os.path.dirname(os.path.abspath(param_path))
    for pattern in structures:
        if os.path.isabs(pattern):
            search_patterns = [pattern]
        else:
            search_patterns = [os.path.join(base_dir, pattern), pattern]

        matches = []
        for search_pattern in search_patterns:
            matches.extend(glob.glob(search_pattern))
        matches = sorted(set(matches))
        for match in matches:
            if os.path.isdir(match):
                for candidate in ("POSCAR", "CONTCAR", "STRU"):
                    candidate_path = os.path.join(match, candidate)
                    if os.path.isfile(candidate_path):
                        return candidate_path
                nested_poscars = sorted(glob.glob(os.path.join(match, "conf_*", "POSCAR")))
                if nested_poscars:
                    return nested_poscars[0]
            elif os.path.isfile(match):
                return match
    raise RuntimeError(
        "Cannot infer interaction.type_map automatically: no structure file found "
        f"for patterns {structures} from {param_path}"
    )


def auto_fill_type_map_from_poscar(parameter_dict: dict, param_path: str) -> bool:
    interaction = parameter_dict.get("interaction")
    if not isinstance(interaction, dict):
        return False
    if interaction.get("type") in {"vasp", "abacus"}:
        return False

    current_type_map = interaction.get("type_map")
    if isinstance(current_type_map, dict) and current_type_map:
        return False
    if current_type_map not in (None, "", "auto"):
        return False

    structures = parameter_dict.get("structures", [])
    if not isinstance(structures, list) or not structures:
        raise RuntimeError(
            "Cannot infer interaction.type_map automatically because `structures` is empty"
        )

    structure_file = _resolve_first_structure_file(param_path, structures)
    interaction["type_map"] = _infer_type_map_from_structure_file(structure_file)

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
        rerun_finished = relax_param.get("interaction", {}).get("rerun_finished", True)
        skip_finished_properties = []
        finished_relax = []
        pending_relax = conf_dirs
        if rerun_finished is False:
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
            if os.path.isdir(copy_relaxation_path):
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
    make_image = wf_config.basic_config_dict["apex_image_name"]
    run_image = wf_config.basic_config_dict[f"{calculator}_image_name"]
    if not run_image:
        run_image = wf_config.basic_config_dict["run_image_name"]
    run_command = wf_config.basic_config_dict[f"{calculator}_run_command"]
    if not run_command:
        run_command = wf_config.basic_config_dict["run_command"]
    lammps_run_command = wf_config.basic_config_dict["lammps_run_command"]
    phonolammps_run_command = wf_config.basic_config_dict["phonolammps_run_command"]
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
    work_dir_list = []
    for ii in work_dirs:
        glob_list = glob.glob(os.path.abspath(ii))
        work_dir_list.extend(glob_list)
        work_dir_list.sort()
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
    else:
        raise NotADirectoryError('Empty work directory indicated, please check your argument')


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
                f"Auto-filled interaction.type_map from structure file and updated: {param_path}"
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
