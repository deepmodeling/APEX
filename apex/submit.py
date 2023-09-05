import os
from typing import Type
from dflow import config, s3_config
from dflow.python import upload_packages, OP
from monty.serialization import loadfn
from fpop.vasp import RunVasp
from fpop.abacus import RunAbacus
from apex.op.RunLAMMPS import RunLAMMPS
from apex.utils import get_task_type, get_flow_type
from .config import Config
from .flow import FlowFactory
upload_packages.append(__file__)


def judge_flow(parameter, specify) -> (Type[OP], str, str, dict, dict):
    # identify type of flow and input parameter file
    num_args = len(parameter)
    if num_args == 1:
        task, run_op = get_task_type(loadfn(parameter[0]))
        flow = get_flow_type(loadfn(parameter[0]))
        task_type = task
        if flow == 'relax':
            flow_type = 'relax'
            if specify in ['props', 'joint']:
                raise RuntimeError(
                    'relaxation json file argument provided! Please check your jason file.'
                )
            relax_param = parameter[0]
            props_param = None
        elif flow == 'props':
            if specify in ['relax', 'joint']:
                raise RuntimeError(
                    'property test json file argument provided! Please check your jason file.'
                )
            flow_type = 'props'
            relax_param = None
            props_param = parameter[0]
        else:
            if specify == 'relax':
                flow_type = 'relax'
            elif specify == 'props':
                flow_type = 'props'
            else:
                flow_type = 'joint'
            relax_param = parameter[0]
            props_param = parameter[0]

    elif num_args == 2:
        task1, run_op1 = get_task_type(loadfn(parameter[0]))
        flow1 = get_flow_type(loadfn(parameter[0]))
        task2, run_op2 = get_task_type(loadfn(parameter[1]))
        flow2 = get_flow_type(loadfn(parameter[1]))
        if not flow1 == flow2:
            if specify == 'relax':
                flow_type = 'relax'
            elif specify == 'props':
                flow_type = 'props'
            else:
                flow_type = 'joint'
            if flow1 == 'relax' and flow2 == 'props':
                relax_param = parameter[0]
                props_param = parameter[1]
            elif flow1 == 'props' and flow2 == 'relax':
                relax_param = parameter[1]
                props_param = parameter[0]
            else:
                raise RuntimeError(
                    'confusion of jason arguments provided: '
                    'joint type of jason conflicts with the other json argument'
                )
        else:
            raise RuntimeError('Same type of input json files')
        if task1 == task2:
            task_type = task1
            run_op = run_op1
        else:
            raise RuntimeError('interaction types given are not matched')
    else:
        raise ValueError('A maximum of two input arguments is allowed')

    return run_op, task_type, flow_type, relax_param, props_param


def submit_workflow(parameter,
                    config_file,
                    specify,
                    is_debug=False):
    try:
        global_config = loadfn(config_file)
    except FileNotFoundError:
        FileNotFoundError(
            'Please prepare global.json under current work direction '
            'or use optional argument: -c to indicate a specific json file.'
        )
    # config dflow_config and s3_config
    wf_config = Config(global_config)
    wf_config.config_dflow(wf_config.dflow_config)
    wf_config.config_bohrium(wf_config.bohrium_config)
    wf_config.config_s3(wf_config.dflow_s3_config)
    # set debug mode
    if is_debug:
        config["mode"] = "debug"
        config["debug_copy_method"] = "copy"
        s3_config["storage_client"] = None
    # judge basic flow info from user indicated parameter files
    (run_op, calculator, flow_type,
     relax_param, props_param) = judge_flow(parameter, specify)

    make_image = wf_config.basic_config["apex_image_name"]
    run_image = wf_config.basic_config[f"{calculator}_image_name"]
    run_command = wf_config.basic_config[f"{calculator}_run_command"]
    post_image = make_image
    executor = wf_config.get_executor(wf_config.dispatcher_config)
    upload_python_packages = wf_config.basic_config["upload_python_packages"]
    
    flow_factory = FlowFactory(
        make_image=make_image,
        run_image=run_image,
        post_image=post_image,
        run_command=run_command,
        calculator=calculator,
        run_op=run_op,
        executor=executor,
        upload_python_packages=upload_python_packages
    )

    if flow_type == 'relax':
        flow_factory.submit_relax(
            work_path=os.getcwd(),
            parameter=relax_param
        )
    elif flow_type == 'props':
        flow_factory.submit_props(
            work_path=os.getcwd(),
            parameter=props_param
        )
    elif flow_type == 'joint':
        flow_factory.submit_joint(
            work_path=os.getcwd(),
            props_param=props_param,
            relax_param=relax_param
        )
