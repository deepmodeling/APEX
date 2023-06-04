#!/usr/bin/env python

from dflow import config, s3_config
from dflow.plugins import bohrium
from dflow.plugins.bohrium import TiefblueClient
from monty.serialization import loadfn

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
s3_config["storage_client"] = s3_storage_client

import argparse
from apex.VASP_flow import VASPFlow
from apex.LAMMPS_flow import LAMMPSFlow
from apex.ABACUS_flow import ABACUSFlow
from apex.lib.utils import judge_flow


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('files', type=str, nargs='+',
                        help='Input indicating json files')
    parser.add_argument("--relax", help="Submit relaxation workflow",
                        action="store_true")
    parser.add_argument("--props", help="Submit property test workflow",
                        action="store_true")
    parser.add_argument("--joint", help="Submit relaxation followed by property test joint workflow",
                        action="store_true")
    args = parser.parse_args()
    return args


def main():
    args = parse_args()

    task_type, flow_info = judge_flow(args)
    flow_type = flow_info['flow_type']
    if flow_type == 'relax':
        print('Submitting relaxation workflow...')
    elif flow_type == 'props':
        print('Submitting property test workflow...')
    else:
        print('Submitting relaxation & property test joint workflow...')

    if task_type == 'abacus':
        print('Simulation via ABACUS')
        tf = ABACUSFlow(flow_info)
    elif task_type == 'vasp':
        print('Simulation via VASP')
        tf = VASPFlow(flow_info)
    elif task_type == 'lammps':
        print('Simulation via LAMMPS')
        tf = LAMMPSFlow(flow_info)
    tf.init_steps()
    tf.generate_flow()

if __name__ == '__main__':
    main()
