#!/usr/bin/env python3
import os
from typing import Type
from monty.serialization import loadfn
from dflow.python import OP
from dflow.python import upload_packages
from fpop.vasp import RunVasp
from fpop.abacus import RunAbacus
from apex.op.RunLAMMPS import RunLAMMPS

upload_packages.append(__file__)

MaxLength = 70


def recursive_search(directories, path='.'):
    # list all directions
    items = os.listdir(path)
    directories_in_path = [
        i for i in items if os.path.isdir(os.path.join(path, i)) and not i.startswith('.')
    ]

    # check if target work direction is found
    if set(directories) <= set(directories_in_path):
        return os.path.abspath(path)

    # recursive search in next direction
    if len(directories_in_path) == 1:
        return recursive_search(directories, os.path.join(path, directories_in_path[0]))

    # return False for failure
    return False


def handle_prop_suffix(parameter: dict):
    if parameter.get('skip', False):
        return None, None
    if 'init_from_suffix' and 'output_suffix' in parameter:
        do_refine = True
        suffix = parameter['output_suffix']
    elif 'reproduce' in parameter and parameter['reproduce']:
        do_refine = False
        suffix = 'reprod'
    elif 'suffix' in parameter and parameter['suffix']:
        do_refine = False
        suffix = str(parameter['suffix'])
    else:
        do_refine = False
        suffix = '00'
    return do_refine, suffix


def return_prop_list(parameters: list) -> list:
    prop_list = []
    for ii in parameters:
        _, suffix = handle_prop_suffix(ii)
        if not suffix:
            continue
        prop_list.append(ii['type'] + '_' + suffix)
    return prop_list


def get_flow_type(d: dict) -> str:
    if 'relaxation' in d and 'properties' not in d:
        flow_type = 'relax'
    elif 'properties' in d and 'relaxation' not in d:
        flow_type = 'props'
    elif 'relaxation' in d and 'properties' in d:
        flow_type = 'joint'
    else:
        raise RuntimeError('Can not recognize type of the input json file')
    return flow_type


def get_task_type(d: dict) -> (str, Type[OP]):
    interaction_type = d['interaction']['type']
    if interaction_type == 'vasp':
        task_type = 'vasp'
        run_op = RunVasp
    elif interaction_type == 'abacus':
        task_type = 'abacus'
        run_op = RunAbacus
    elif interaction_type in ['deepmd', 'eam_alloy']:
        task_type = 'lammps'
        run_op = RunLAMMPS
    else:
        raise RuntimeError(f'Unsupported interaction type: {interaction_type}')

    return task_type, run_op


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


def sepline(ch="-", sp="-", screen=False):
    r"""
    seperate the output by '-'
    """
    ch.center(MaxLength, sp)


def update_dict(d1: dict, d2: dict) -> None:
    if d2 is None:
        return None
    for k, v in d2.items():
        if isinstance(v, dict) and k in d1 and isinstance(d1[k], dict):
            update_dict(d1[k], v)
        else:
            d1[k] = v


def json2dict(function):
    def wrapper(*args, **kwargs):
        # try to convert json to dict for arguments passed as args
        args = list(args)
        for ii in range(len(args)):
            if isinstance(args[ii], os.PathLike) or isinstance(args[ii], str):
                try:
                    args[ii] = loadfn(args[ii])
                except Exception:
                    pass
        # try to convert json to dict for arguments passed as kwargs
        for k, v in kwargs.items():
            if isinstance(v, os.PathLike) or isinstance(v, str):
                try:
                    kwargs[k] = loadfn(v)
                except Exception:
                    pass
        function(*tuple(args), **kwargs)
    return wrapper
