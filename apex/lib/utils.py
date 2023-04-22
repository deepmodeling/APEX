#!/usr/bin/env python3

from monty.serialization import loadfn
from dflow.python import upload_packages
upload_packages.append(__file__)


def return_prop_list(parameters: list) -> list:
    prop_list = []
    for ii in parameters:
        if ii.get('skip', False):
            continue
        if 'init_from_suffix' and 'output_suffix' in ii:
            #do_refine = True
            suffix = ii['output_suffix']
        elif 'reproduce' in ii and ii['reproduce']:
            #do_refine = False
            suffix = 'reprod'
        else:
            #do_refine = False
            suffix = '00'
        prop_list.append(ii['type'] + '_' + suffix)
    return prop_list


def identify_json(file: str) -> str:

    jdata = loadfn(file)
    if 'relaxation' in jdata and 'properties' not in jdata:
        flow_type = 'relax'
    elif 'properties' in jdata and 'relaxation' not in jdata:
        flow_type = 'props'
    elif 'relaxation' in jdata and 'properties' in jdata:
        flow_type = 'joint'
    else:
        raise RuntimeError('Can not recognize type of the input json file')

    interaction_type = jdata['interaction']['type']
    if interaction_type == 'vasp':
        task_type = 'vasp'
    elif interaction_type == 'abacus':
        task_type = 'abacus'
    elif interaction_type == 'deepmd' or interaction_type == 'eam_alloy':
        task_type = 'lammps'
    else:
        raise RuntimeError(f'Unsupported interaction type: {interaction_type}')

    return task_type, flow_type

def judge_flow(args) -> (str, dict):
    # identify type of flow and input parameter file
    num_args = len(args.files)
    if num_args == 1:
        task, flow = identify_json(args.files[0])
        task_type = task
        if flow == 'relax':
            flow_type = 'relax'
            relax_param = args.files[0]
            props_param = None
        elif flow == 'props':
            flow_type = 'props'
            relax_param = None
            props_param = args.files[0]
        else:
            flow_type = 'joint'
            relax_param = args.files[0]
            props_param = args.files[0]
    elif num_args == 2:
        flow_type = 'joint'
        task1, flow1 = identify_json(args.files[0])
        task2, flow2 = identify_json(args.files[1])
        if not flow1 == flow2:
            if flow1 == 'relax' and flow2 == 'props':
                relax_param = args.files[0]
                props_param = args.files[1]
            elif flow1 == 'props' and flow2 == 'relax':
                relax_param = args.files[1]
                props_param = args.files[0]
            else:
                raise RuntimeError('confusion of jason arguments provided: '
                                   'joint type of jason conflicts with another json argument')
        else:
            raise RuntimeError('Same type of input json files')
        if task1 == task2:
            task_type = task1
        else:
            raise RuntimeError('interaction types given are not matched')
    else:
        raise ValueError('A maximum of two input arguments is allowed')

    flow_info = {
        'flow_type': flow_type,
        'relax_param': relax_param,
        'props_param': props_param,
    }
    return task_type, flow_info

