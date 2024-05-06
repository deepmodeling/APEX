#!/usr/bin/env python3
import logging
import os
import shutil
import json
import string
import random
from typing import Type, List
from monty.serialization import loadfn
from decimal import Decimal
from dflow.python import OP
from dflow.python import upload_packages
from fpop.vasp import RunVasp
from fpop.abacus import RunAbacus
from apex.op.RunLAMMPS import RunLAMMPS
from apex.core.calculator import LAMMPS_INTER_TYPE

upload_packages.append(__file__)

MaxLength = 70
# LAMMPS_INTER_TYPE = ['deepmd', 'eam_alloy', 'meam', 'eam_fs', 'meam_spline', 'snap', 'gap', 'rann', 'mace']


# write a function to replace all '/' in the input string with '-'

def backup_path(path) -> None:
    path += "/"
    if os.path.isdir(path):
        dirname = os.path.dirname(path)
        counter = 0
        while True:
            bk_dirname = dirname + ".bk%03d" % counter
            if not os.path.isdir(bk_dirname):
                shutil.move(dirname, bk_dirname)
                break
            counter += 1


def generate_random_string(length):
    characters = string.ascii_letters + string.digits  # 包含所有字母(大写和小写)和数字
    random_string = ''.join(random.choices(characters, k=length))
    return random_string


def copy_all_other_files(
        src_dir,
        dst_dir,
        exclude_files=[],
        include_dirs=[]
) -> None:
    """
    Copies all files from the source directory to the destination directory with some files excluded
    and some directories included.

    :param src_dir: The path to the source directory.
    :param dst_dir: The path to the destination directory.
    :exclude_files: files to be ignored.
    :include_dirs: directories to be included.
    """
    if not os.path.exists(src_dir):
        raise FileNotFoundError(f"Source directory {src_dir} does not exist.")

    if not os.path.exists(dst_dir):
        os.makedirs(dst_dir)

    for item in os.listdir(src_dir):
        src_path = os.path.join(src_dir, item)
        dst_path = os.path.join(dst_dir, item)

        if os.path.isfile(src_path) and item not in exclude_files:
            shutil.copy2(src_path, dst_path)
        elif os.path.isdir(src_path) and item in include_dirs:
            shutil.copytree(src_path, dst_path)


def simplify_paths(path_list: list) -> dict:
    # only one path, return it with only basename
    if len(path_list) == 1:
        return {path_list[0]: '.../' + os.path.basename(path_list[0])}
    else:
        # Split all paths into components
        split_paths = [os.path.normpath(p).split(os.sep) for p in path_list]

        # Find common prefix
        common_prefix = os.path.commonprefix(split_paths)
        common_prefix_len = len(common_prefix)

        # Remove common prefix from each path and create dictionary
        simplified_paths_dict = {
            os.sep.join(p): '.../' + os.sep.join(p[common_prefix_len:]) if common_prefix_len else os.sep.join(p)
            for p in split_paths
        }

        return simplified_paths_dict


def is_json_file(filename):
    try:
        with open(filename, 'r') as f:
            json.load(f)
        return True
    except ValueError as e:
        return False


def load_config_file(config_file: os.PathLike) -> dict:
    try:
        config_dict = loadfn(config_file)
    except FileNotFoundError:
        logging.warning(
            msg='No global config file provided, will default all settings. '
                'You may prepare global.json under current work direction '
                'or use optional argument: -c to indicate a specific json file.'
        )
        config_dict = {}
    return config_dict


def recursive_search(directories, path='.'):
    """recursive search target directory"""
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
    elif interaction_type in LAMMPS_INTER_TYPE:
        task_type = 'lammps'
        run_op = RunLAMMPS
    else:
        raise RuntimeError(f'Unsupported interaction type: {interaction_type}')

    return task_type, run_op


def judge_flow(parameter: List[dict], specify: str) -> (Type[OP], str, str, dict, dict):
    # identify type of flow and input parameter file
    num_args = len(parameter)
    if num_args == 1:
        task, run_op = get_task_type(parameter[0])
        flow = get_flow_type(parameter[0])
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
        task1, run_op1 = get_task_type(parameter[0])
        flow1 = get_flow_type(parameter[0])
        task2, run_op2 = get_task_type(parameter[1])
        flow2 = get_flow_type(parameter[1])
        if flow1 != flow2:
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
                    'confusion of json arguments provided: '
                    'joint type of json conflicts with the other json argument'
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


def update_dict(d_base: dict, d_new: dict, depth=9999) -> None:
    depth -= 1
    if d_new is None:
        return None
    for k, v in d_new.items():
        if isinstance(v, dict) and k in d_base and isinstance(d_base[k], dict) and depth >= 0:
            update_dict(d_base[k], v, depth)
        else:
            d_base[k] = v


def convert_floats_to_decimals(obj):
    if isinstance(obj, float):
        return Decimal(str(obj))
    elif isinstance(obj, dict):
        return {k: convert_floats_to_decimals(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [convert_floats_to_decimals(x) for x in obj]
    else:
        return obj


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
        result = function(*tuple(args), **kwargs)
        return result
    return wrapper
