import warnings

from monty.serialization import loadfn
from apex.core.common_equi import (make_equi, run_equi, post_equi)
from apex.core.common_prop import (make_property, run_property, post_property)
from dflow.python import upload_packages
from apex.utils import get_task_type, return_prop_list
upload_packages.append(__file__)


def run_step(parameter, step, config_file=None):
    print('-------Singel step local debug mode--------')
    param_dict = loadfn(parameter[0])
    # check input args
    json_type = get_task_type(param_dict)
    mismatch1 = step in ['make_relax', 'run_relax', 'post_relax'] and json_type == 'props'
    mismatch2 = step in ['make_post', 'run_props', 'post_props'] and json_type == 'relax'
    if mismatch1 or mismatch2:
        raise RuntimeError(
            f'mismatched indication step with type of json provided: {json_type}'
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
            if not config_file:
                raise RuntimeWarning(
                    'Miss configuration file for dpdispatcher (indicate by optional args -c).'
                    'Jobs will be running on the local shell.'
                )
                mdata = {}
            else:
                mdata = loadfn(config_file)
            run_equi(structures, inter_parameter, mdata)
        else:
            print('Posting relaxation results locally...')
            post_equi(structures, inter_parameter)

    elif step in ['make_props', 'run_props', 'post_props']:
        param = param_dict["properties"]
        if step == 'make_props':
            print('Making property tasks locally...')
            make_property(structures, inter_parameter, param)
        elif step == 'run_relax':
            print('Run property tasks locally...')
            if not config_file:
                raise RuntimeWarning(
                    'Miss configuration file for dpdispatcher (indicate by optional args -c).'
                    'Jobs will be running on the local shell.'
                )
                mdata = {}
            else:
                mdata = loadfn(config_file)
            props_list = return_prop_list(param)
            run_property(structures, inter_parameter, props_list, mdata)
        else:
            print('Posting property results locally...')
            post_property(structures, inter_parameter, param)
