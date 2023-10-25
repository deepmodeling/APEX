#!/usr/bin/env python3
from typing import Type
from monty.serialization import loadfn
from dflow.python import OP
from dflow.python import upload_packages
from fpop.vasp import RunVasp
from fpop.abacus import RunAbacus
from .op.RunLAMMPS import RunLAMMPS

upload_packages.append(__file__)

MaxLength = 70


def return_prop_list(parameters: list) -> list:
    prop_list = []
    for ii in parameters:
        if ii.get('skip', False):
            continue
        if 'init_from_suffix' and 'output_suffix' in ii:
            # do_refine = True
            suffix = ii['output_suffix']
        elif 'reproduce' in ii and ii['reproduce']:
            # do_refine = False
            suffix = 'reprod'
        elif 'suffix' in ii and ii['suffix']:
            suffix = str(ii['suffix'])
        else:
            # do_refine = False
            suffix = '00'
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
