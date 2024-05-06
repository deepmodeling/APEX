import argparse
import logging
import os
import datetime
from typing import List

from dflow import (
    Workflow,
    query_workflows,
    download_artifact
)

from apex import (
    header,
    __version__,
)
from apex.config import Config
from apex.step import do_step_from_args
from apex.submit import submit_from_args
from apex.archive import archive_from_args
from apex.report import report_from_args
from apex.utils import load_config_file


def parse_args():
    parser = argparse.ArgumentParser(
        description=f"APEX: A scientific workflow for Alloy Properties EXplorer "
                    f"using simulations (v{__version__})\n"
                    f"Type 'apex -h' for help.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    subparsers = parser.add_subparsers(title="Valid subcommands", dest="cmd")
    # version
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"APEX v{__version__}"
    )

    ##########################################
    # Submit
    parser_submit = subparsers.add_parser(
        "submit",
        help="Submit an APEX workflow",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser_submit.add_argument(
        "parameter", type=str, nargs='+',
        help='Json files to indicate calculation parameters'
    )
    parser_submit.add_argument(
        "-c", "--config",
        type=str, nargs='?',
        default='./global.json',
        help="The json file to config workflow",
    )
    parser_submit.add_argument(
        "-w", "--work",
        type=str, nargs='+',
        default='.',
        help="(Optional) Work directories to be submitted",
    )
    parser_submit.add_argument(
        "-d", "--debug",
        action="store_true",
        help="(Optional) Run APEX workflow via local debug mode"
    )
    parser_submit.add_argument(
        "-s", "--submit_only",
        action="store_true",
        help="(Optional) Submit workflow only without automatic result retrieving"
    )
    parser_submit.add_argument(
        '-f', "--flow",
        choices=['relax', 'props', 'joint'],
        help="(Optional) Specify type of workflow to submit: (relax | props | joint)"
    )
    parser_submit.add_argument(
        "-n", "--name",
        type=str, default=None,
        help="(Optional) Specify name of the workflow",
    )

    ##########################################
    # Do single step locally
    parser_do = subparsers.add_parser(
        "do",
        help="Run single step locally independent from workflow",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser_do.add_argument(
        "parameter", type=str,
        help='Json file to indicate calculation parameters'
    )
    parser_do.add_argument(
        "step",
        type=str,
        choices=[
            'make_relax', 'run_relax', 'post_relax',
            'make_props', 'run_props', 'post_props'
        ],
        help="Specify step name to be tested: "
             "(make_relax | run_relax | post_relax |"
             " make_props | run_props | post_props)"
    )
    parser_do.add_argument(
        "-c", "--config",
        type=str, nargs='?',
        default='./global.json',
        help="The json file to config the dpdispatcher",
    )

    ##########################################
    # Retrieve artifacts manually
    parser_retrieve = subparsers.add_parser(
        "retrieve",
        help="Retrieve results of an workflow with key provided manually",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser_retrieve.add_argument(
        "-i", "--id", type=str, default=None,
        help='Workflow ID to be retrieved'
    )
    parser_retrieve.add_argument(
        "-w", "--work", type=str, default='.',
        help='Target work directory to be retrieved'
    )
    parser_retrieve.add_argument(
        "-c", "--config",
        type=str, nargs='?',
        default='./global.json',
        help="The json file to config workflow",
    )

    ##########################################
    ### dflow operations
    # list workflows
    parser_list = subparsers.add_parser(
        "list",
        help="List workflows",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser_list.add_argument(
        "-l",
        "--label",
        type=str,
        default=None,
        help="query by labels",
    )
    parser_list.add_argument(
        "-c", "--config",
        type=str, nargs='?',
        default='./global.json',
        help="The json file to config workflow",
    )
    # get workflow
    parser_get = subparsers.add_parser(
        "get",
        help="Get a workflow",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser_get.add_argument(
        "-i", "--id", type=str, default=None,
        help='Workflow ID to get'
    )
    parser_get.add_argument(
        "-w", "--work", type=str, default='.',
        help='Target work directory to get'
    )
    parser_get.add_argument(
        "-c", "--config",
        type=str, nargs='?',
        default='./global.json',
        help="The json file to config workflow",
    )
    # get steps of workflow
    parser_getsteps = subparsers.add_parser(
        "getsteps",
        help="Get steps from a workflow",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser_getsteps.add_argument("ID", help="the workflow ID.")
    parser_getsteps.add_argument(
        "-n",
        "--name",
        type=str,
        default=None,
        help="query by name",
    )
    parser_getsteps.add_argument(
        "-k",
        "--key",
        type=str,
        default=None,
        help="query by key",
    )
    parser_getsteps.add_argument(
        "-p",
        "--phase",
        type=str,
        default=None,
        help="query by phase",
    )
    parser_getsteps.add_argument(
        "-i",
        "--id",
        type=str,
        default=None,
        help="workflow ID to query",
    )
    parser_getsteps.add_argument(
        "-w", "--work", type=str, default='.',
        help='Target work directory'
    )
    parser_getsteps.add_argument(
        "-t",
        "--type",
        type=str,
        default=None,
        help="query by type",
    )
    parser_getsteps.add_argument(
        "-c", "--config",
        type=str, nargs='?',
        default='./global.json',
        help="The json file to config workflow",
    )
    #  getkeys of workflow
    parser_getkeys = subparsers.add_parser(
        "getkeys",
        help="Get keys of steps from a workflow",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser_getkeys.add_argument(
        "-i", "--id", type=str, default=None,
        help='Workflow ID to get keys'
    )
    parser_getkeys.add_argument(
        "-w", "--work", type=str, default='.',
        help='Target work directory get keys'
    )
    parser_getkeys.add_argument(
        "-c", "--config",
        type=str, nargs='?',
        default='./global.json',
        help="The json file to config workflow",
    )
    # delete workflow
    parser_delete = subparsers.add_parser(
        "delete",
        help="Delete a workflow",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser_delete.add_argument(
        "-i", "--id", type=str, default=None,
        help='Workflow ID to delete'
    )
    parser_delete.add_argument(
        "-w", "--work", type=str, default='.',
        help='Target work directory delete'
    )
    parser_delete.add_argument(
        "-c", "--config",
        type=str, nargs='?',
        default='./global.json',
        help="The json file to config workflow",
    )
    # resubmit workflow
    parser_resubmit = subparsers.add_parser(
        "resubmit",
        help="Resubmit a workflow",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser_resubmit.add_argument(
        "-i", "--id", type=str, default=None,
        help='Workflow ID to resubmit'
    )
    parser_resubmit.add_argument(
        "-w", "--work", type=str, default='.',
        help='Target work directory to resubmit'
    )
    parser_resubmit.add_argument(
        "-c", "--config",
        type=str, nargs='?',
        default='./global.json',
        help="The json file to config workflow",
    )
    # retry workflow
    parser_retry = subparsers.add_parser(
        "retry",
        help="Retry a workflow",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser_retry.add_argument(
        "-i", "--id", type=str, default=None,
        help='Workflow ID to retry'
    )
    parser_retry.add_argument(
        "-w", "--work", type=str, default='.',
        help='Target work directory to retry'
    )
    parser_retry.add_argument(
        "-s",
        "--step",
        type=str,
        default=None,
        help="retry a step in a running workflow with step ID (experimental)",
    )
    # resume workflow
    parser_resume = subparsers.add_parser(
        "resume",
        help="Resume a workflow",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser_resume.add_argument(
        "-i", "--id", type=str, default=None,
        help='Workflow ID to resume'
    )
    parser_resume.add_argument(
        "-w", "--work", type=str, default='.',
        help='Target work directory to resume'
    )
    parser_resume.add_argument(
        "-c", "--config",
        type=str, nargs='?',
        default='./global.json',
        help="The json file to config workflow",
    )
    # stop workflow
    parser_stop = subparsers.add_parser(
        "stop",
        help="Stop a workflow",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser_stop.add_argument(
        "-i", "--id", type=str, default=None,
        help='Workflow ID to stop'
    )
    parser_stop.add_argument(
        "-w", "--work", type=str, default='.',
        help='Target work directory to stop'
    )
    parser_stop.add_argument(
        "-c", "--config",
        type=str, nargs='?',
        default='./global.json',
        help="The json file to config workflow",
    )
    # suspend workflow
    parser_suspend = subparsers.add_parser(
        "suspend",
        help="Suspend a workflow",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser_suspend.add_argument(
        "-i", "--id", type=str, default=None,
        help='Workflow ID to suspend'
    )
    parser_suspend.add_argument(
        "-w", "--work", type=str, default='.',
        help='Target work directory to suspend'
    )
    parser_suspend.add_argument(
        "-c", "--config",
        type=str, nargs='?',
        default='./global.json',
        help="The json file to config workflow",
    )
    # terminate workflow
    parser_terminate = subparsers.add_parser(
        "terminate",
        help="Terminate a workflow",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser_terminate.add_argument(
        "-i", "--id", type=str, default=None,
        help='Workflow ID to terminate'
    )
    parser_terminate.add_argument(
        "-w", "--work", type=str, default='.',
        help='Target work directory to terminate'
    )
    parser_terminate.add_argument(
        "-c", "--config",
        type=str, nargs='?',
        default='./global.json',
        help="The json file to config workflow",
    )

    ##########################################
    # Archive results
    parser_archive = subparsers.add_parser(
        "archive",
        help="Archive test results to local or database",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser_archive.add_argument(
        "json", type=str, nargs='+',
        help='Json files to indicate calculation parameters '
             'or result json files that will be directly archived to database when -r flag is raised'
    )
    parser_archive.add_argument(
        "-c", "--config",
        type=str, nargs='?',
        default='./global.json',
        help="The json file of global config",
    )
    parser_archive.add_argument(
        "-w", "--work",
        type=str, nargs='+',
        default='.',
        help="(Optional) Working directory",
    )
    parser_archive.add_argument(
        '-f', "--flow",
        choices=['relax', 'props', 'joint'],
        help="(Optional) Specify type of workflow's results to archive: (relax | props | joint)"
    )
    parser_archive.add_argument(
        '-d', "--database",
        choices=['local', 'mongodb', 'dynamodb'],
        help="(Optional) Specify type of database: (local | mongodb | dynamodb)"
    )
    parser_archive.add_argument(
        '-m', "--method",
        choices=['sync', 'record'],
        help="(Optional) Specify archive method: (sync | record)"
    )
    parser_archive.add_argument(
        "-t", "--tasks",
        action="store_true",
        help="Whether to archive running details of each task (default: False)"
    )
    parser_archive.add_argument(
        "-r", "--result",
        action="store_true",
        help="(Optional) whether to treat json files as results and archive them directly to database",
    )

    ##########################################
    # Report results
    parser_report = subparsers.add_parser(
        "report",
        help="Run result visualization report app",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser_report.add_argument(
        "-c", "--config",
        type=str, nargs='?',
        default='./global.json',
        help="The json file of global config",
    )
    parser_report.add_argument(
        "-w", "--work",
        type=str, nargs='+',
        default='.',
        help="(Optional) Working directory or json file path to be reported",
    )

    parsed_args = parser.parse_args()
    # print help if no parser
    if not parsed_args.cmd:
        parser.print_help()

    return parser, parsed_args


def config_dflow(config_file: os.PathLike) -> None:
    # config dflow_config and s3_config
    config_dict = load_config_file(config_file)
    wf_config = Config(**config_dict)
    Config.config_dflow(wf_config.dflow_config_dict)
    Config.config_bohrium(wf_config.bohrium_config_dict)
    Config.config_s3(wf_config.dflow_s3_config_dict)


def format_print_table(t: List[List[str]]):
    ncol = len(t[0])
    maxlen = [0] * ncol
    for row in t:
        for i, s in enumerate(row):
            if len(str(s)) > maxlen[i]:
                maxlen[i] = len(str(s))
    for row in t:
        for i, s in enumerate(row):
            print(str(s) + " " * (maxlen[i]-len(str(s))+3), end="")
        print()


def format_time_delta(td: datetime.timedelta) -> str:
    if td.days > 0:
        return "%dd" % td.days
    elif td.seconds >= 3600:
        return "%dh" % (td.seconds // 3600)
    else:
        return "%ds" % td.seconds


def get_id_from_record(work_dir: os.PathLike, operation_name: str = None) -> str:
    logging.info(msg='No workflow_id is provided, will employ the latest workflow')
    workflow_log = os.path.join(work_dir, '.workflow.log')
    assert os.path.isfile(workflow_log), \
        'No workflow_id is provided and no .workflow.log file found in work_dir'
    with open(workflow_log, 'r') as f:
        try:
            last_record = f.readlines()[-1]
        except IndexError:
            raise RuntimeError('No workflow_id is provided and .workflow.log file is empty!')
    workflow_id = last_record.split('\t')[0]
    assert workflow_id, 'No workflow ID for operation!'
    logging.info(msg=f'Operating on workflow ID: {workflow_id}')
    if operation_name:
        modified_record = last_record.split('\t')
        modified_record[1] = operation_name
        modified_record[2] = datetime.datetime.now().isoformat()
        with open(workflow_log, 'a') as f:
            f.write('\t'.join(modified_record))
    return workflow_id


def main():
    # logging
    logging.basicConfig(level=logging.INFO)
    # parse args
    parser, args = parse_args()
    if args.cmd == 'submit':
        header()
        submit_from_args(
            parameters=args.parameter,
            config_file=args.config,
            work_dirs=args.work,
            indicated_flow_type=args.flow,
            flow_name=args.name,
            submit_only=args.submit_only,
            is_debug=args.debug
        )
    elif args.cmd == "list":
            config_dflow(args.config)
            if args.label is not None:
                labels = {}
                for label in args.label.split(","):
                    key, value = label.split("=")
                    labels[key] = value
            else:
                labels = None
            wfs = query_workflows(labels=labels)
            t = [["NAME", "STATUS", "AGE", "DURATION"]]
            for wf in wfs:
                tc = datetime.datetime.strptime(wf.metadata.creationTimestamp,
                                                "%Y-%m-%dT%H:%M:%SZ")
                age = format_time_delta(datetime.datetime.now() - tc)
                dur = format_time_delta(wf.get_duration())
                t.append([wf.id, wf.status.phase, age, dur])
            format_print_table(t)
    elif args.cmd == "get":
        config_dflow(args.config)
        wf_id = args.id
        if not wf_id:
            wf_id = get_id_from_record(args.work, 'get')
        wf = Workflow(id=wf_id)
        info = wf.query()
        t = []
        t.append(["Name:", info.id])
        t.append(["Status:", info.status.phase])
        t.append(["Created:", info.metadata.creationTimestamp])
        t.append(["Started:", info.status.startedAt])
        t.append(["Finished:", info.status.finishedAt])
        t.append(["Duration", format_time_delta(info.get_duration())])
        t.append(["Progress:", info.status.progress])
        format_print_table(t)
        print()
        steps = info.get_step()
        t = [["STEP", "ID", "KEY", "TYPE", "PHASE", "DURATION"]]
        for step in steps:
            if step.type in ["StepGroup"]:
                continue
            key = step.key if step.key is not None else ""
            dur = format_time_delta(step.get_duration())
            t.append([step.displayName, step.id, key, step.type, step.phase,
                      dur])
        format_print_table(t)
    elif args.cmd == "getsteps":
        config_dflow(args.config)
        wf_id = args.ID
        name = args.name
        key = args.key
        phase = args.phase
        id = args.id
        type = args.type
        if name is not None:
            name = name.split(",")
        if key is not None:
            key = key.split(",")
        if phase is not None:
            phase = phase.split(",")
        if id is not None:
            id = id.split(",")
        if type is not None:
            type = type.split(",")
        wf = Workflow(id=wf_id)
        if key is not None:
            steps = wf.query_step_by_key(key, name, phase, id, type)
        else:
            steps = wf.query_step(name, key, phase, id, type)
        for step in steps:
            if step.type in ["StepGroup"]:
                continue
            key = step.key if step.key is not None else ""
            dur = format_time_delta(step.get_duration())
            t = []
            t.append(["Step:", step.displayName])
            t.append(["ID:", step.id])
            t.append(["Key:", key])
            t.append(["Type:", step.type])
            t.append(["Phase:", step.phase])
            format_print_table(t)
            if hasattr(step, "outputs"):
                if hasattr(step.outputs, "parameters"):
                    print("Output parameters:")
                    for name, par in step.outputs.parameters.items():
                        if name[:6] == "dflow_":
                            continue
                        print("%s: %s" % (name, par.value))
                    print()
                if hasattr(step.outputs, "artifacts"):
                    print("Output artifacts:")
                    for name, art in step.outputs.artifacts.items():
                        if name[:6] == "dflow_" or name == "main-logs":
                            continue
                        key = ""
                        if hasattr(art, "s3"):
                            key = art.s3.key
                        elif hasattr(art, "oss"):
                            key = art.oss.key
                        print("%s: %s" % (name, key))
                    print()
            print()
    elif args.cmd == "getkeys":
        config_dflow(args.config)
        wf_id = args.id
        if not wf_id:
            wf_id = get_id_from_record(args.work, 'getkeys')
        wf = Workflow(id=wf_id)
        keys = wf.query_keys_of_steps()
        print("\n".join(keys))
    elif args.cmd == "delete":
        config_dflow(args.config)
        wf_id = args.id
        if not wf_id:
            wf_id = get_id_from_record(args.work, 'delete')
        wf = Workflow(id=wf_id)
        wf.delete()
        print(f'Workflow deleted! (ID: {wf.id}, UID: {wf.uid})')
    elif args.cmd == "resubmit":
        config_dflow(args.config)
        wf_id = args.id
        if not wf_id:
            wf_id = get_id_from_record(args.work, 'resubmit')
        wf = Workflow(id=wf_id)
        wf.resubmit()
        print(f'Workflow resubmitted... (ID: {wf.id}, UID: {wf.uid})')
    elif args.cmd == "resume":
        config_dflow(args.config)
        wf_id = args.id
        if not wf_id:
            wf_id = get_id_from_record(args.work, 'resume')
        wf = Workflow(id=wf_id)
        wf.resume()
        print(f'Workflow resumed... (ID: {wf.id}, UID: {wf.uid})')
    elif args.cmd == "retry":
        config_dflow(args.config)
        wf_id = args.id
        if not wf_id:
            wf_id = get_id_from_record(args.work, 'retry')
        wf = Workflow(id=wf_id)
        if args.step is not None:
            wf.retry_steps(args.step.split(","))
        else:
            wf.retry()
        print(f'Workflow retried... (ID: {wf.id}, UID: {wf.uid})')
    elif args.cmd == "stop":
        config_dflow(args.config)
        wf_id = args.id
        if not wf_id:
            wf_id = get_id_from_record(args.work, 'stop')
        wf = Workflow(id=wf_id)
        wf.stop()
        print(f'Workflow stopped! (ID: {wf.id}, UID: {wf.uid})')
    elif args.cmd == "suspend":
        config_dflow(args.config)
        wf_id = args.id
        if not wf_id:
            wf_id = get_id_from_record(args.work, 'suspend')
        wf = Workflow(id=wf_id)
        wf.suspend()
        print(f'Workflow suspended... (ID: {wf.id}, UID: {wf.uid})')
    elif args.cmd == "terminate":
        config_dflow(args.config)
        wf_id = args.id
        if not wf_id:
            wf_id = get_id_from_record(args.work, 'terminate')
        wf = Workflow(id=wf_id)
        wf.terminate()
    elif args.cmd == 'retrieve':
        config_dflow(args.config)
        wf_id = args.id
        if not wf_id:
            wf_id = get_id_from_record(args.work, 'retrieve')
        wf = Workflow(id=wf_id)
        work_dir = args.work
        all_keys = wf.query_keys_of_steps()
        wf_info = wf.query()
        download_keys = [key for key in all_keys if key.split('-')[0] == 'propertycal' or key == 'relaxationcal']
        task_left = len(download_keys)
        print(f'Retrieving {task_left} workflow results {wf_id} to {work_dir}')

        for key in download_keys:
            step = wf_info.get_step(key=key)[0]
            task_left -= 1
            if step['phase'] == 'Succeeded':
                logging.info(f"Retrieving {key}...({task_left} more left)")
                download_artifact(
                    artifact=step.outputs.artifacts['retrieve_path'],
                    path=work_dir
                )
            else:
                logging.warning(f"Step {key} with status: {step['phase']} will be skipping...({task_left} more left)")
    elif args.cmd == 'do':
        header()
        do_step_from_args(
            parameter=args.parameter,
            machine_file=args.config,
            step=args.step
        )
    elif args.cmd == 'archive':
        archive_from_args(
            parameters=args.json,
            config_file=args.config,
            work_dirs=args.work,
            indicated_flow_type=args.flow,
            database_type=args.database,
            method=args.method,
            archive_tasks=args.tasks,
            is_result=args.result
        )
    elif args.cmd == 'report':
        header()
        report_from_args(
            config_file=args.config,
            path_list=args.work,
        )
    else:
        raise RuntimeError(
            f"unknown command {args.cmd}\n{parser.print_help()}"
        )
