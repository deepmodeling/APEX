import os

from monty.serialization import loadfn
from apex.core.common_equi import (make_equi, run_equi, post_equi)
from apex.core.common_prop import (make_property, run_property, post_property)
from apex.utils import get_flow_type, load_config_file
from apex.archive import archive_workdir
from apex.config import Config


def do_step(param_dict: dict, step: str, machine_dict: dict = None):
    # check input args
    json_type = get_flow_type(param_dict)
    mismatch1 = step in ['make_relax', 'run_relax', 'post_relax'] and json_type == 'props'
    mismatch2 = step in ['make_props', 'run_props', 'post_props'] and json_type == 'relax'
    if mismatch1 or mismatch2:
        raise RuntimeError(
            f'mismatched indication step ({step}) with type of json provided ({json_type})'
        )
    structures = param_dict["structures"]
    inter_parameter = param_dict["interaction"]
    if step in ['make_relax', 'run_relax', 'post_relax']:
        param = param_dict["relaxation"]
        if step == 'make_relax':
            print('Making relaxation tasks locally...')
            make_equi(structures, inter_parameter, param)
        elif step == 'run_relax':
            print('Run relaxation tasks locally...')
            if not machine_dict:
                raise RuntimeWarning(
                    'Miss configuration file for dpdispatcher (indicate by optional args -c).'
                    'Jobs will be running on the local shell.'
                )
                mdata = {}
            else:
                mdata = machine_dict
            run_equi(structures, inter_parameter, mdata)
        else:
            print('Posting relaxation results locally...')
            post_equi(structures, inter_parameter)
            # Auto-archive to generate/update all_result.json locally (parity with `apex submit`)
            try:
                cfg = Config(**(machine_dict or {}))
                archive_workdir(
                    relax_param=param_dict,
                    props_param=None,
                    config=cfg,
                    work_dir=os.getcwd(),
                    flow_type='relax'
                )
            except Exception as e:  # non-fatal; keep local post succeeding
                print(f"[archive] skipped: {e}")

    elif step in ['make_props', 'run_props', 'post_props']:
        param = param_dict["properties"]
        if step == 'make_props':
            print('Making property tasks locally...')
            make_property(structures, inter_parameter, param)
        elif step == 'run_props':
            print('Run property tasks locally...')
            if not machine_dict:
                raise RuntimeWarning(
                    'Miss configuration file for dpdispatcher (indicate by optional args -c).'
                    'Jobs will be running on the local shell.'
                )
                mdata = {}
            else:
                mdata = machine_dict
            run_property(structures, inter_parameter, param, mdata)
        else:
            print('Posting property results locally...')
            post_property(structures, inter_parameter, param)
            # Auto-archive to generate/update all_result.json locally (parity with `apex submit`)
            try:
                cfg = Config(**(machine_dict or {}))
                archive_workdir(
                    relax_param=None,
                    props_param=param_dict,
                    config=cfg,
                    work_dir=os.getcwd(),
                    flow_type='props'
                )
            except Exception as e:  # non-fatal; keep local post succeeding
                print(f"[archive] skipped: {e}")


def do_step_from_args(parameter: str, step: str, machine_file: os.PathLike = None):
    print('-------Singel Step Local Debug Mode--------')
    param_dict = loadfn(parameter)
    mcfg = load_config_file(machine_file)
    if step in ['make', 'run', 'post']:
        _do_step_combined(param_dict, step, mcfg)
    else:
        do_step(
            param_dict=param_dict,
            step=step,
            machine_dict=mcfg
        )
    print('Completed!')


def _do_step_combined(param_dict: dict, step: str, machine_dict: dict = None):
    """
    Combined local steps that auto-detect json type and sequence:
    - make:   relax -> props (for joint), otherwise corresponding only
    - run:    relax -> props (for joint), otherwise corresponding only
    - post:   relax -> props (for joint), otherwise corresponding only, then archive once
    """
    flow = get_flow_type(param_dict)  # 'relax' | 'props' | 'joint'
    structures = param_dict.get('structures')
    inter_parameter = param_dict.get('interaction')

    # Helper to get mdata or warn
    def _mdata_or_warn():
        if not machine_dict:
            raise RuntimeWarning(
                'Miss configuration file for dpdispatcher (indicate by optional args -c). '
                'Jobs will be running on the local shell.'
            )
        return machine_dict

    # make
    if step == 'make':
        if flow in ['relax', 'joint']:
            make_equi(structures, inter_parameter, param_dict['relaxation'])
        if flow in ['props', 'joint']:
            make_property(structures, inter_parameter, param_dict['properties'])
        return

    # run
    if step == 'run':
        mdata = _mdata_or_warn()
        if flow in ['relax', 'joint']:
            run_equi(structures, inter_parameter, mdata)
        if flow in ['props', 'joint']:
            run_property(structures, inter_parameter, param_dict['properties'], mdata)
        return

    # post
    if step == 'post':
        if flow in ['relax', 'joint']:
            post_equi(structures, inter_parameter)
        if flow in ['props', 'joint']:
            post_property(structures, inter_parameter, param_dict['properties'])
        # archive once for combined post
        try:
            cfg = Config(**(machine_dict or {}))
            archive_workdir(
                relax_param=param_dict if flow in ['relax', 'joint'] else None,
                props_param=param_dict if flow in ['props', 'joint'] else None,
                config=cfg,
                work_dir=os.getcwd(),
                flow_type=flow,
            )
        except Exception as e:
            print(f"[archive] skipped: {e}")
