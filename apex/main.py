import argparse
import logging
from apex import (
    header,
    __version__,
)
from apex.run_step import run_step
from apex.submit import submit_workflow
from apex.archive import archive_result
from apex.report import report_result
from apex.retrieve import retrieve_results


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
    # Single step debug test
    parser_test = subparsers.add_parser(
        "test",
        help="Single step local test mode",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser_test.add_argument(
        "parameter", type=str, nargs=1,
        help='Json file to indicate calculation parameters'
    )
    parser_test.add_argument(
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
    parser_test.add_argument(
        "-m", "--machine",
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
        "parameter", type=str, nargs='+',
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
        submit_workflow(
            parameter=args.parameter,
            config_file=args.config,
            work_dir=args.work,
            user_flow_type=args.flow,
            is_debug=args.debug
        )
    elif args.cmd == 'test':
        run_step(
            parameter=args.parameter,
            machine_file=args.machine,
            step=args.step
        )
    elif args.cmd == 'retrieve':
        retrieve_results(
            workflow_id=args.workflow_id,
            destination=args.work,
            config_file=args.config,
        )
    elif args.cmd == 'archive':
        archive_result(
            parameter=args.parameter,
            config_file=args.config,
            work_dir=args.work,
            user_flow_type=args.flow,
            database_type=args.database,
            method=args.method,
            archive_tasks=args.tasks,
            is_result=args.result
        )
    elif args.cmd == 'report':
        report_result(
            config_file=args.config,
            path_list=args.work,
        )
    else:
        raise RuntimeError(
            f"unknown command {args.command}\n{parser.print_help()}"
        )
