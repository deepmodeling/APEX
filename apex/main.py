import argparse
import logging
from apex import (
    header,
    __version__,
)
from apex.run import run_step_from_args
from apex.submit import submit_from_args
from apex.archive import archive_from_args
from apex.report import report_from_args
from apex.retrieve import retrieve_from_args


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
        '-f', "--flow",
        choices=['relax', 'props', 'joint'],
        help="(Optional) Specify type of workflow to submit: (relax | props | joint)"
    )

    ##########################################
    # Run single step locally
    parser_run = subparsers.add_parser(
        "run",
        help="Run single step locally mode",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser_run.add_argument(
        "parameter", type=str,
        help='Json file to indicate calculation parameters'
    )
    parser_run.add_argument(
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
    parser_run.add_argument(
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
        "workflow_id", type=str,
        help='Workflow ID to be downloaded'
    )
    parser_retrieve.add_argument(
        "-w", "--work", type=str, default='./',
        help='destination work directory to be downloaded to'
    )
    parser_retrieve.add_argument(
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


def main():
    # logging
    logging.basicConfig(level=logging.INFO)
    # parse args
    parser, args = parse_args()
    header()
    if args.cmd == 'submit':
        submit_from_args(
            parameters=args.parameter,
            config_file=args.config,
            work_dirs=args.work,
            indicated_flow_type=args.flow,
            is_debug=args.debug
        )
    elif args.cmd == 'run':
        run_step_from_args(
            parameter=args.parameter,
            machine_file=args.config,
            step=args.step
        )
    elif args.cmd == 'retrieve':
        retrieve_from_args(
            workflow_id=args.workflow_id,
            destination=args.work,
            config_file=args.config,
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
        report_from_args(
            config_file=args.config,
            path_list=args.work,
        )
    else:
        raise RuntimeError(
            f"unknown command {args.command}\n{parser.print_help()}"
        )
