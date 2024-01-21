import os
import os.path
import glob
import shutil
import tempfile
import logging
from multiprocessing import Pool

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
    backup_path
)


@json2dict
def pack_upload_dir(
        work_dir: os.PathLike,
        upload_dir: os.PathLike,
        relax_param: dict,
        prop_param: dict,
        flow_type: str
):
    """
    Pack the necessary files and directories into temp dir and upload it to dflow
    """
    cwd = os.getcwd()
    os.chdir(work_dir)
    confs = relax_param["structures"] + prop_param["structures"]
    property_list = prop_param["properties"]
    conf_dirs = []
    for conf in confs:
        conf_dirs.extend(glob.glob(conf))
    conf_dirs = list(set(conf_dirs))
    conf_dirs.sort()
    # backup all existing property work directories
    if flow_type in ['props', 'joint']:
        for ii in conf_dirs:
            sepline(ch=ii, screen=True)
            for jj in property_list:
                do_refine, suffix = handle_prop_suffix(jj)
                if not suffix:
                    continue
                property_type = jj["type"]
                path_to_prop = os.path.join(ii, property_type + "_" + suffix)
                backup_path(path_to_prop)

    # copy necessary files and directories into temp upload directory
    conf_root_list = [conf.split('/')[0] for conf in conf_dirs]
    conf_root_list = list(set(conf_root_list))
    conf_root_list.sort()
    ignore_copy_list = conf_root_list
    ignore_copy_list.append("all_result.json")
    if flow_type in ['relax', 'joint']:
        copy_all_other_files(work_dir, upload_dir, ignore_list=ignore_copy_list)
        for ii in conf_dirs:
            build_conf_path = os.path.join(upload_dir, ii)
            copy_poscar_path = os.path.abspath(os.path.join(ii, "POSCAR"))
            target_poscar_path = os.path.join(build_conf_path, "POSCAR")
            os.makedirs(build_conf_path, exist_ok=True)
            shutil.copy(copy_poscar_path, target_poscar_path)
    elif flow_type == 'props':
        copy_all_other_files(work_dir, upload_dir, ignore_list=ignore_copy_list)
        for ii in conf_dirs:
            build_conf_path = os.path.join(upload_dir, ii)
            copy_poscar_path = os.path.abspath(os.path.join(ii, "POSCAR"))
            target_poscar_path = os.path.join(build_conf_path, "POSCAR")
            copy_relaxation_path = os.path.abspath(os.path.join(ii, "relaxation"))
            target_relaxation_path = os.path.join(build_conf_path, "relaxation")
            os.makedirs(build_conf_path, exist_ok=True)
            shutil.copy(copy_poscar_path, target_poscar_path)
            shutil.copytree(copy_relaxation_path, target_relaxation_path)
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
        print(f'Sub-process working on: {work_dir}')
        config.update(conf)
        s3_config.update(s3_conf)
        logging.basicConfig(level=logging.INFO)
    else:
        print(f'Working on: {work_dir}')

    with tempfile.TemporaryDirectory() as tmp_dir:
        logging.debug(msg=f'Temp upload directory:{tmp_dir}')
        pack_upload_dir(
            work_dir=work_dir,
            upload_dir=tmp_dir,
            relax_param=relax_param,
            prop_param=props_param,
            flow_type=flow_type
        )

        flow_id = None
        if flow_type == 'relax':
            flow_id = flow.submit_relax(
                upload_path=tmp_dir,
                download_path=work_dir,
                relax_parameter=relax_param,
                labels=labels
            )
        elif flow_type == 'props':
            flow_id = flow.submit_props(
                upload_path=tmp_dir,
                download_path=work_dir,
                props_parameter=props_param,
                labels=labels
            )
        elif flow_type == 'joint':
            flow_id = flow.submit_joint(
                upload_path=tmp_dir,
                download_path=work_dir,
                props_parameter=props_param,
                relax_parameter=relax_param,
                labels=labels
            )
    # auto archive results
    print(f'Archiving results of workflow (ID: {flow_id}) into {wf_config.database_type}...')
    archive_workdir(relax_param, props_param, wf_config, work_dir, flow_type)


def submit_workflow(
        parameter,
        config_file,
        work_dir,
        user_flow_type,
        is_debug=False,
        labels=None
):
    print('-------Submit Workflow Mode-------')
    config_dict = load_config_file(config_file)
    # config dflow_config and s3_config
    wf_config = Config(**config_dict)
    wf_config.config_dflow(wf_config.dflow_config_dict)
    wf_config.config_bohrium(wf_config.bohrium_config_dict)
    wf_config.config_s3(wf_config.dflow_s3_config_dict)
    # set pre-defined dflow debug mode settings
    if is_debug:
        tmp_work_dir = tempfile.TemporaryDirectory()
        config["mode"] = "debug"
        config["debug_workdir"] = config_dict.get("debug_workdir", tmp_work_dir.name)
        s3_config["storage_client"] = None

    # judge basic flow info from user indicated parameter files
    (run_op, calculator, flow_type,
     relax_param, props_param) = judge_flow(parameter, user_flow_type)
    print(f'Running APEX calculation via {calculator}')
    print(f'Submitting {flow_type} workflow...')
    make_image = wf_config.basic_config_dict["apex_image_name"]
    run_image = wf_config.basic_config_dict[f"{calculator}_image_name"]
    if not run_image:
        run_image = wf_config.basic_config_dict["run_image_name"]
    run_command = wf_config.basic_config_dict[f"{calculator}_run_command"]
    if not run_command:
        run_command = wf_config.basic_config_dict["run_command"]
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
        upload_python_packages=upload_python_packages
    )
    # submit the workflows
    work_dir_list = []
    for ii in work_dir:
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

    print('Completed!')
