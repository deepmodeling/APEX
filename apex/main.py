import argparse
import logging
from dflow.python import upload_packages
from apex import (
    __version__,
)
from .run_step import run_step
from .submit import submit_workflow
upload_packages.append(__file__)


def parse_args():
    parser = argparse.ArgumentParser(
        description=f"APEX: A scientific workflow for Alloy Properties EXplorer "
                    f"using simulations (v{__version__})\n",
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
        default='./config.json',
        help="The json file to config workflow",
    )

    parser_submit.add_argument(
        "-w", "--work",
        type=str, nargs='?',
        default='.',
        help="Working directory to be submitted",
    )

    parser_submit.add_argument(
        "-d", "--debug",
        action="store_true",
        help="Run APEX workflow via local debug mode"
    )
    parser_submit.add_argument(
        '-f', "--flow",
        choices=['relax', 'props', 'joint'],
        help="Specify type of workflow to submit: (relax | props | joint)"
    )

    ##########################################
    # Single step local test mode
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
        default='./config.json',
        help="The json file to config the dpdispatcher",
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

    if args.cmd == 'submit':
        submit_workflow(
            parameter=args.parameter,
            config_file=args.config,
            work_dir=args.work,
            flow_type=args.flow,
            is_debug=args.debug
        )

    elif args.cmd == 'test':
        run_step(
            parameter=args.parameter,
            machine_file=args.machine,
            step=args.step
        )
    else:
        raise RuntimeError(
            f"unknown command {args.command}\n{parser.print_help()}"
        )
