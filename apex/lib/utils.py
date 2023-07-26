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
    # indentify json file type input by user
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
            if args.props or args.joint:
                raise RuntimeError('relaxation json file argument provided! Please check your jason file.')
            relax_param = args.files[0]
            props_param = None
        elif flow == 'props':
            if args.relax or args.joint:
                raise RuntimeError('property json file argument provided! Please check your jason file.')
            flow_type = 'props'
            relax_param = None
            props_param = args.files[0]
        else:
            if args.relax:
                flow_type = 'relax'
            elif args.props:
                flow_type = 'props'
            else:
                flow_type = 'joint'
            relax_param = args.files[0]
            props_param = args.files[0]

    elif num_args == 2:
        task1, flow1 = identify_json(args.files[0])
        task2, flow2 = identify_json(args.files[1])
        if not flow1 == flow2:
            if args.relax:
                flow_type = 'relax'
            elif args.props:
                flow_type = 'props'
            else:
                flow_type = 'joint'
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

    return task_type, flow_type, relax_param, props_param

def check_args_ss(args):
    num_args = len(args.files)
    if num_args == 1:
        json_type = identify_json(args.files[0])[1]
        mismatch1 = (args.make_relax or args.post_relax) and json_type == 'props'
        mismatch2 = (args.make_props or args.post_props) and json_type == 'relax'
        if mismatch1 or mismatch2:
            raise RuntimeError(f'mismatched indication step with type of json provided: {json_type}')
    else:
        raise ValueError('A maximum of one input arguments is allowed in single step mode')
