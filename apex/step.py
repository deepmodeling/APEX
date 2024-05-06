import os

from monty.serialization import loadfn
from apex.core.common_equi import (make_equi, run_equi, post_equi)
from apex.core.common_prop import (make_property, run_property, post_property)
from apex.utils import get_flow_type, load_config_file


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


def do_step_from_args(parameter: str, step: str, machine_file: os.PathLike = None):
    print('-------Singel Step Local Debug Mode--------')
    do_step(
        param_dict=loadfn(parameter),
        step=step,
        machine_dict=load_config_file(machine_file)
    )
    print('Completed!')
