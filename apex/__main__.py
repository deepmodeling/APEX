#!/usr/bin/env python

from dflow import config, s3_config
from dflow.plugins import bohrium
from dflow.plugins.bohrium import TiefblueClient
from monty.serialization import loadfn

debug_mode = loadfn("global.json").get("debug_mode", False)
if debug_mode:
    config["mode"] = "debug"
    config["debug_copy_method"] = "copy"
    s3_storage_client = None
else:
    dflow_host = loadfn("global.json").get("dflow_host", "https://127.0.0.1:2746")
    config["host"] = dflow_host
    k8s_api_server = loadfn("global.json").get("k8s_api_server", "https://127.0.0.1:2746")
    config["k8s_api_server"] = k8s_api_server
    username = loadfn("global.json").get("email", None)
    bohrium.config["username"] = username
    password = loadfn("global.json").get("password", None)
    bohrium.config["password"] = password
    program_id = loadfn("global.json").get("program_id", None)
    bohrium.config["program_id"] = program_id
    s3_repo_key = loadfn("global.json").get("s3_repo_key", None)
    s3_config["repo_key"] = s3_repo_key
    s3_storage_client = loadfn("global.json").get("s3_storage_client", None)

import argparse
from apex.VASP_flow import VASPFlow
from apex.LAMMPS_flow import LAMMPSFlow
from apex.lib.utils import judge_flow
from apex.lib.utils import check_args_ss
from apex.core.common_equi import (make_equi, post_equi)
from apex.core.common_prop import (make_property, post_property)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('files', type=str, nargs='+',
                        help='Input indicating json files')
    parser.add_argument('-r', "--relax", help="Submit relaxation workflow",
                        action="store_true")
    parser.add_argument('-p', "--props", help="Submit core test workflow",
                        action="store_true")
    parser.add_argument('-j', "--joint", help="Submit relaxation followed by core test joint workflow",
                        action="store_true")
    parser.add_argument('-mr', "--make_relax", help="Run make relaxation step locally",
                        action="store_true")
    parser.add_argument('-pr', "--post_relax", help="Run post relaxation step locally",
                        action="store_true")
    parser.add_argument('-mp', "--make_props", help="Run make properties step locally",
                        action="store_true")
    parser.add_argument('-pp', "--post_props", help="Run post properties step locally",
                        action="store_true")
    args = parser.parse_args()

    # check args
    provided_args = sum([args.relax, args.props, args.joint,
                         args.make_relax, args.post_relax,
                         args.make_props, args.post_props])
    if provided_args > 1:
        parser.error("Only one optional argument is allowed.")

    return args

def run_flow(args):
    args = parse_args()

    task_type, flow_type, relax_param, props_param = judge_flow(args)

    if task_type == 'abacus':
        print('Simulation via ABACUS')
        tf = ABACUSFlow(flow_type, relax_param, props_param)
    elif task_type == 'vasp':
        print('Simulation via VASP')
        tf = VASPFlow(flow_type, relax_param, props_param)
    elif task_type == 'lammps':
        print('Simulation via LAMMPS')
        tf = LAMMPSFlow(flow_type, relax_param, props_param)

    if flow_type == 'relax':
        print('Submitting relaxation workflow...')
    elif flow_type == 'props':
        print('Submitting property test workflow...')
    else:
        print('Submitting relaxation & property test joint workflow...')

    tf.init_steps()
    tf.generate_flow()


def run_step(args):
    print('-------Singel step local debug mode--------')
    param_argv = args.files[0]
    structures = loadfn(param_argv)["structures"]
    inter_parameter = loadfn(param_argv)["interaction"]
    if args.make_relax or args.post_relax:
        parameter = loadfn(param_argv)["relaxation"]
        if args.make_relax:
            print('Making relaxation tasks locally...')
            make_equi(structures, inter_parameter, parameter)
        else:
            print('Posting relaxation results locally...')
            post_equi(structures, inter_parameter)
    else:
        parameter = loadfn(param_argv)["properties"]
        if args.make_props:
            print('Making properties tasks locally...')
            make_property(structures, inter_parameter, parameter)
        else:
            print('Posting properties results locally...')
            post_property(structures, inter_parameter, parameter)


def main():
    args = parse_args()
    if (args.make_relax or args.post_relax
        or args.make_props or args.post_props):
        check_args_ss(args)
        run_step(args)
    else:
        if s3_storage_client == "TiefblueClient":
            s3_config["storage_client"] = TiefblueClient()
        else:
            s3_config["storage_client"] = s3_storage_client

        run_flow(args)

if __name__ == '__main__':
    main()
