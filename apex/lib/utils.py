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


def identify_task(file: str) -> str:
    jdata = loadfn(file)
    if 'relaxation' in jdata:
        task_type = 'relax'
    elif 'properties' in jdata:
        task_type = 'props'
    else:
        raise RuntimeError('Can not recognize type of the input json file')
    return task_type
