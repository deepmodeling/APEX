import argparse
import re
import logging
import os
import datetime
import time
from typing import List

from dflow import (
    Workflow,
    query_workflows,
    download_artifact,
    config,
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
    parser_submit.add_argument(
        "-l", "--label",
        action="append",
        default=None,
        help="(Optional) Workflow label in key=value form; may be provided multiple times.",
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
    parser_retrieve.add_argument(
        "-d", "--debug",
        action="store_true",
        help="Retrieve failed-step diagnostic artifacts in dflow debug mode",
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
    parser_retry.add_argument(
        "-c", "--config",
        type=str, nargs='?',
        default='./global.json',
        help="The json file to config workflow",
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
    parser_report.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not automatically open the report in a browser",
    )
    parser_report.add_argument(
        "-H", "--host",
        type=str,
        default="127.0.0.1",
        help="Host for the report Dash server",
    )
    parser_report.add_argument(
        "-p", "--port",
        type=int,
        default=8070,
        help="Port for the report Dash server",
    )

    ##########################################
    # RSS
    parser_rss = subparsers.add_parser(
        "rss",
        help="Generate RSS structures from an rss.json config",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser_rss.add_argument(
        "rss_json", type=str,
        help="Path to rss json config file",
    )

    ##########################################
    # Preview GIFs
    parser_preview = subparsers.add_parser(
        "preview",
        help="Generate preview GIFs from param_props JSON files",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser_preview.add_argument(
        "parameters",
        type=str,
        nargs='+',
        help="param_props JSON files, e.g. param_props_gamma*.json",
    )
    parser_preview.add_argument(
        "--gif-fps",
        type=int,
        default=8,
        help="GIF frames per second",
    )
    parser_preview.add_argument(
        "--gif-dpi",
        type=int,
        default=140,
        help="GIF rendering DPI",
    )
    parser_preview.add_argument(
        "--gif-padding",
        type=float,
        default=0.30,
        help="Relative x/y padding ratio around the global bounds",
    )
    parser_preview.add_argument(
        "--gif-xshift",
        type=float,
        default=0.0,
        help="Shift the rendered viewport horizontally by a fraction of the data span",
    )
    parser_preview.add_argument(
        "--gif-yshift",
        type=float,
        default=0.0,
        help="Shift the rendered viewport vertically by a fraction of the data span; positive values move the structure downward",
    )

    ##########################################
    # GUI
    parser_gui = subparsers.add_parser(
        "gui",
        help="Launch a web-based graphical interface for common APEX commands",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser_gui.add_argument(
        "-H", "--host",
        type=str,
        default="127.0.0.1",
        help="Host address used by the Dash GUI server",
    )
    parser_gui.add_argument(
        "-p", "--port",
        type=int,
        default=8060,
        help="Port used by the Dash GUI server",
    )
    parser_gui.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not automatically open a browser window",
    )

    ##########################################
    # Account
    parser_account = subparsers.add_parser(
        "account",
        help="Manage default Bohrium account and cloud config",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser_account.add_argument(
        "--show",
        action="store_true",
        help="Show saved default account config",
    )
    parser_account.add_argument(
        "--reset",
        action="store_true",
        help="Remove saved default account config",
    )
    parser_account.add_argument(
        "--non-interactive",
        action="store_true",
        help="Do not ask for input when no account fields are passed",
    )
    parser_account.add_argument(
        "--file",
        type=str,
        default=None,
        help="Custom path for account config file",
    )
    parser_account.add_argument("--dflow-host", dest="dflow_host", type=str, default=None)
    parser_account.add_argument("--k8s-api-server", dest="k8s_api_server", type=str, default=None)
    parser_account.add_argument("--batch-type", dest="batch_type", type=str, default=None)
    parser_account.add_argument("--context-type", dest="context_type", type=str, default=None)
    parser_account.add_argument("--email", type=str, default=None)
    parser_account.add_argument("--password", type=str, default=None)
    parser_account.add_argument("--program-id", dest="program_id", type=int, default=None)
    parser_account.add_argument("--apex-image-name", dest="apex_image_name", type=str, default=None)

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


def _parse_workflow_log_record(line: str) -> dict | None:
    parts = line.rstrip("\n").split("\t")
    if len(parts) < 4:
        return None
    return {
        "workflow_id": parts[0].strip(),
        "operation": parts[1].strip(),
        "timestamp": parts[2].strip(),
        "workdir": parts[3].strip(),
        "workflow_uid": parts[4].strip() if len(parts) > 4 else "",
    }


def _format_workflow_log_record(record: dict) -> str:
    return "\t".join(
        [
            str(record.get("workflow_id", "")).strip(),
            str(record.get("operation", "")).strip(),
            str(record.get("timestamp", "")).strip(),
            str(record.get("workdir", "")).strip(),
            str(record.get("workflow_uid", "")).strip(),
        ]
    ).rstrip("\t") + "\n"


def _looks_like_workflow_uid(value: str) -> bool:
    return bool(
        re.fullmatch(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
            str(value or "").strip(),
        )
    )


def _load_workflow_log_records(work_dir: os.PathLike) -> list[dict]:
    workflow_log = os.path.join(work_dir, '.workflow.log')
    assert os.path.isfile(workflow_log), \
        'No workflow_id is provided and no .workflow.log file found in work_dir'
    with open(workflow_log, 'r', encoding='utf-8', errors='replace') as f:
        records = [_parse_workflow_log_record(line) for line in f if line.strip()]
    records = [record for record in records if record]
    if not records:
        raise RuntimeError('No workflow_id is provided and .workflow.log file is empty!')
    return records


def _resolve_workflow_reference(
        work_dir: os.PathLike,
        workflow_id: str | None = None,
        operation_name: str | None = None,
) -> tuple[str, str]:
    records = _load_workflow_log_records(work_dir)
    selected_record = None
    clean_workflow_id = str(workflow_id or "").strip()
    if clean_workflow_id:
        for record in reversed(records):
            if clean_workflow_id in {
                record.get("workflow_id", ""),
                record.get("workflow_uid", ""),
            }:
                selected_record = record
                break
        if selected_record is None:
            if _looks_like_workflow_uid(clean_workflow_id):
                return "", clean_workflow_id
            return clean_workflow_id, ""
        if (
            _looks_like_workflow_uid(clean_workflow_id)
            and clean_workflow_id == selected_record.get("workflow_uid", "").strip()
        ):
            resolved_uid = selected_record.get("workflow_uid", "").strip()
            logging.info(msg=f'Operating on workflow UID: {resolved_uid}')
            if operation_name:
                updated_record = dict(selected_record)
                updated_record["operation"] = operation_name
                updated_record["timestamp"] = datetime.datetime.now().isoformat()
                workflow_log = os.path.join(work_dir, '.workflow.log')
                with open(workflow_log, 'a', encoding='utf-8') as f:
                    f.write(_format_workflow_log_record(updated_record))
            return "", resolved_uid
    else:
        logging.info(msg='No workflow_id is provided, will employ the latest workflow')
        selected_record = records[-1]

    resolved_id = selected_record.get("workflow_id", "").strip()
    resolved_uid = selected_record.get("workflow_uid", "").strip()
    assert resolved_id, 'No workflow ID for operation!'
    logging.info(
        msg=(
            f'Operating on workflow ID: {resolved_id}'
            + (f' (UID: {resolved_uid})' if resolved_uid else '')
        )
    )
    if operation_name:
        updated_record = dict(selected_record)
        updated_record["operation"] = operation_name
        updated_record["timestamp"] = datetime.datetime.now().isoformat()
        workflow_log = os.path.join(work_dir, '.workflow.log')
        with open(workflow_log, 'a', encoding='utf-8') as f:
            f.write(_format_workflow_log_record(updated_record))
    return resolved_id, resolved_uid


def get_id_from_record(work_dir: os.PathLike, operation_name: str = None) -> str:
    workflow_id, _ = _resolve_workflow_reference(work_dir, operation_name=operation_name)
    return workflow_id


def _format_workflow_query_error(wf_id: str, exc: Exception) -> str | None:
    text = str(exc)
    lower_text = text.lower()
    status = getattr(exc, "status", None)
    is_not_found = (
        status == 404
        or "(404)" in text
        or "reason: not found" in lower_text
        or '"not found"' in lower_text
    )
    is_workflow_query = (
        "workflow" in lower_text
        and ("not found" in lower_text or str(wf_id) in text)
    )
    if not (is_not_found and is_workflow_query):
        return None

    return (
        f"Workflow {wf_id!r} was not found by dflow/Argo.\n"
        "The workflow may have been deleted, may not have been archived, or the "
        "current config may point to a different dflow host/project/namespace.\n"
        "Check the workflow ID in .workflow.log or pass the expected ID with -i. "
        "If the ID is correct, verify the -c config file uses the same Bohrium/"
        "dflow account and project that submitted the workflow."
    )


def _workflow_query_not_found_message(wf_ref: str, exc: Exception) -> str | None:
    return _format_workflow_query_error(wf_ref, exc)


def _build_workflow_handle(workflow_id: str, workflow_uid: str = "", prefer_uid: bool = False) -> Workflow:
    clean_uid = str(workflow_uid or "").strip()
    clean_id = str(workflow_id or "").strip()
    if (prefer_uid or not clean_id) and clean_uid:
        return Workflow(uid=clean_uid)
    return Workflow(id=clean_id)


def _run_with_workflow_fallback(
        workflow_id: str,
        workflow_uid: str,
        action,
):
    clean_id = str(workflow_id or "").strip()
    clean_uid = str(workflow_uid or "").strip()

    if not clean_id and clean_uid:
        wf_uid = _build_workflow_handle(clean_id, clean_uid, prefer_uid=True)
        try:
            return action(wf_uid, clean_uid, True)
        except Exception as exc:
            message = _workflow_query_not_found_message(clean_uid, exc)
            if message:
                raise SystemExit(message) from None
            raise

    wf = _build_workflow_handle(clean_id, clean_uid, prefer_uid=False)
    try:
        return action(wf, clean_id, False)
    except Exception as exc:
        if not clean_uid:
            message = _workflow_query_not_found_message(clean_id, exc)
            if message:
                raise SystemExit(message) from None
            raise
        if _workflow_query_not_found_message(clean_id, exc) is None:
            raise
        logging.warning(
            "Workflow %r was not found by name; retrying by UID %s.",
            clean_id,
            clean_uid,
        )

    wf_uid = _build_workflow_handle(clean_id, clean_uid, prefer_uid=True)
    try:
        return action(wf_uid, clean_uid, True)
    except Exception as exc:
        message = _workflow_query_not_found_message(clean_uid, exc)
        if message:
            raise SystemExit(
                message
                + f"\nA fallback query by UID {clean_uid!r} was also attempted and failed."
            ) from None
        raise


def _query_keys_of_steps(wf: Workflow) -> List[str]:
    return wf.query_keys_of_steps()


def _query_workflow(wf: Workflow):
    return wf.query()


def _query_keys_of_steps_or_exit(wf: Workflow, wf_id: str) -> List[str]:
    try:
        return _query_keys_of_steps(wf)
    except Exception as exc:
        message = _format_workflow_query_error(wf_id, exc)
        if message:
            raise SystemExit(message) from None
        raise


def _query_workflow_or_exit(wf: Workflow, wf_id: str):
    try:
        return _query_workflow(wf)
    except Exception as exc:
        message = _format_workflow_query_error(wf_id, exc)
        if message:
            raise SystemExit(message) from None
        raise


_ARTIFACT_NOT_IN_STORAGE = "the artifact does not exist in the storage"
_TRANSIENT_DOWNLOAD_MARKERS = (
    "connection",
    "connect",
    "timeout",
    "timed out",
    "temporarily unavailable",
    "network",
    "name resolution",
    "dns",
    "reset by peer",
    "remote disconnected",
    "broken pipe",
    "ssl",
    "proxy",
)


def _is_missing_artifact_error(exc: Exception) -> bool:
    return _ARTIFACT_NOT_IN_STORAGE in str(exc).lower()


def _is_transient_download_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in _TRANSIENT_DOWNLOAD_MARKERS)


def _download_artifact_with_retry(artifact, path, retries: int = 3, delay: int = 10):
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            return download_artifact(artifact=artifact, path=path)
        except Exception as exc:
            last_exc = exc
            if _is_missing_artifact_error(exc) or not _is_transient_download_error(exc):
                raise RuntimeError(f"Artifact download failed without retry: {exc}") from exc
            if attempt >= retries:
                break
            logging.warning(
                "Artifact download failed (%s/%s): %s. Retrying in %ss...",
                attempt,
                retries,
                exc,
                delay,
            )
            time.sleep(delay)
    raise RuntimeError(
        f"Artifact download failed after {retries} attempt(s): {last_exc}"
    ) from last_exc


def _resolve_cli_workflow_reference(
        work_dir: os.PathLike | None,
        workflow_id: str | None,
        operation_name: str | None = None,
) -> tuple[str, str]:
    clean_workflow_id = str(workflow_id or "").strip()
    if work_dir:
        workflow_log = os.path.join(work_dir, '.workflow.log')
        if clean_workflow_id and not os.path.isfile(workflow_log):
            if _looks_like_workflow_uid(clean_workflow_id):
                return "", clean_workflow_id
            return clean_workflow_id, ""
        return _resolve_workflow_reference(
            work_dir=work_dir,
            workflow_id=clean_workflow_id or None,
            operation_name=operation_name,
        )
    return clean_workflow_id, ""


def _safe_get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _safe_parameter_value(step, name):
    inputs = _safe_get(step, "inputs", {}) or {}
    parameters = _safe_get(inputs, "parameters", {}) or {}
    parameter = _safe_get(parameters, name)
    if parameter is None:
        return None
    value = _safe_get(parameter, "value", parameter)
    return str(value) if value not in (None, "") else None


def _directory_has_entries(path: str) -> bool:
    if not os.path.isdir(path):
        return False
    try:
        with os.scandir(path) as entries:
            return any(True for _ in entries)
    except OSError:
        return False


def _retrieve_existing_result_dir(step, key: str, work_dir: str):
    if str(key).startswith("propertycal-"):
        path_to_prop = _safe_parameter_value(step, "path_to_prop")
        if path_to_prop:
            target = os.path.join(work_dir, path_to_prop)
            return target if _directory_has_entries(target) else None
        return None

    if str(key).startswith("relaxcal-") or key == "relaxationcal":
        flow_id = _safe_parameter_value(step, "flow_id")
        if flow_id:
            target = os.path.join(work_dir, flow_id, "relaxation")
            return target if _directory_has_entries(target) else None
    return None


def _get_step_artifacts(step):
    outputs = _safe_get(step, "outputs")
    if outputs is None:
        return {}
    return _safe_get(outputs, "artifacts", {}) or {}


def _sanitize_path_token(text: str) -> str:
    cleaned = "".join(ch if (ch.isalnum() or ch in "._-") else "-" for ch in str(text))
    return cleaned.strip("-") or "unknown"


def _collect_step_with_children(wf_info, root_step):
    all_steps = [root_step]
    queue = [root_step]
    seen = set()
    while queue:
        step = queue.pop(0)
        step_id = _safe_get(step, "id")
        if not step_id or step_id in seen:
            continue
        seen.add(step_id)
        try:
            children = wf_info.get_step(parent_id=step_id, sort_by_generation=True)
        except Exception:
            children = []
        all_steps.extend(children)
        queue.extend(children)
    return all_steps


def _is_retrievable_result_step_key(key: str) -> bool:
    prefix = str(key).split("-")[0]
    return prefix in {"propertycal", "relaxcal"} or key == "relaxationcal"


def _should_retrieve_failure_artifacts(debug_requested: bool = False) -> bool:
    return bool(debug_requested or config.get("mode") == "debug")


def _download_failure_artifacts_for_step(wf_info, root_step, key, work_dir):
    preferred_names = {
        "main-logs",
        "main_logs",
        "backward_dir",
        "retrieve_path",
        "output_all",
        "output_work_path",
        "task_paths",
    }
    related_steps = _collect_step_with_children(wf_info, root_step)
    downloaded = 0
    seen = set()
    for step in related_steps:
        step_id = _safe_get(step, "id", "step")
        step_name = _safe_get(step, "displayName", _safe_get(step, "name", step_id))
        artifacts = _get_step_artifacts(step)
        for art_name, artifact in artifacts.items():
            if art_name.startswith("dflow_"):
                continue
            if art_name not in preferred_names:
                continue
            key_tuple = (str(step_id), str(art_name))
            if key_tuple in seen:
                continue
            seen.add(key_tuple)

            target_dir = os.path.join(
                work_dir,
                ".failed-artifacts",
                _sanitize_path_token(key),
                _sanitize_path_token(step_name),
                _sanitize_path_token(art_name),
            )
            os.makedirs(target_dir, exist_ok=True)
            if _directory_has_entries(target_dir):
                logging.info(
                    "Skip retrieving failure artifact %s for step %s (%s) "
                    "because %s already contains files.",
                    art_name,
                    step_name,
                    key,
                    target_dir,
                )
                continue
            try:
                _download_artifact_with_retry(artifact=artifact, path=target_dir)
                downloaded += 1
            except Exception as exc:
                logging.warning(
                    "Failed to download artifact %s for step %s (%s): %s",
                    art_name,
                    step_name,
                    key,
                    exc,
                )
    return downloaded


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
            is_debug=args.debug,
            labels=args.label,
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
        wf_id, wf_uid = _resolve_cli_workflow_reference(args.work, args.id, 'get')
        info = _run_with_workflow_fallback(
            wf_id,
            wf_uid,
            lambda wf, _wf_ref, _used_uid: _query_workflow(wf),
        )
        t = []
        t.append(["Name:", info.id])
        if getattr(info, "uid", None):
            t.append(["UID:", info.uid])
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
        try:
            if key is not None:
                steps = wf.query_step_by_key(key, name, phase, id, type)
            else:
                steps = wf.query_step(name, key, phase, id, type)
        except Exception as exc:
            message = _format_workflow_query_error(wf_id, exc)
            if message:
                raise SystemExit(message) from None
            raise
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
        wf_id, wf_uid = _resolve_cli_workflow_reference(args.work, args.id, 'getkeys')
        keys = _run_with_workflow_fallback(
            wf_id,
            wf_uid,
            lambda wf, _wf_ref, _used_uid: _query_keys_of_steps(wf),
        )
        print("\n".join(keys))
    elif args.cmd == "delete":
        config_dflow(args.config)
        wf_id, wf_uid = _resolve_cli_workflow_reference(args.work, args.id, 'delete')
        wf = _build_workflow_handle(wf_id, wf_uid)
        wf.delete()
        print(f'Workflow deleted! (ID: {wf.id}, UID: {wf.uid})')
    elif args.cmd == "resubmit":
        config_dflow(args.config)
        wf_id, wf_uid = _resolve_cli_workflow_reference(args.work, args.id, 'resubmit')
        wf = _build_workflow_handle(wf_id, wf_uid)
        wf.resubmit()
        print(f'Workflow resubmitted... (ID: {wf.id}, UID: {wf.uid})')
    elif args.cmd == "resume":
        config_dflow(args.config)
        wf_id, wf_uid = _resolve_cli_workflow_reference(args.work, args.id, 'resume')
        wf = _build_workflow_handle(wf_id, wf_uid)
        wf.resume()
        print(f'Workflow resumed... (ID: {wf.id}, UID: {wf.uid})')
    elif args.cmd == "retry":
        config_dflow(args.config)
        wf_id, wf_uid = _resolve_cli_workflow_reference(args.work, args.id, 'retry')
        wf = _build_workflow_handle(wf_id, wf_uid)
        if args.step is not None:
            wf.retry_steps(args.step.split(","))
        else:
            wf.retry()
        print(f'Workflow retried... (ID: {wf.id}, UID: {wf.uid})')
    elif args.cmd == "stop":
        config_dflow(args.config)
        wf_id, wf_uid = _resolve_cli_workflow_reference(args.work, args.id, 'stop')
        wf = _build_workflow_handle(wf_id, wf_uid)
        wf.stop()
        print(f'Workflow stopped! (ID: {wf.id}, UID: {wf.uid})')
    elif args.cmd == "suspend":
        config_dflow(args.config)
        wf_id, wf_uid = _resolve_cli_workflow_reference(args.work, args.id, 'suspend')
        wf = _build_workflow_handle(wf_id, wf_uid)
        wf.suspend()
        print(f'Workflow suspended... (ID: {wf.id}, UID: {wf.uid})')
    elif args.cmd == "terminate":
        config_dflow(args.config)
        wf_id, wf_uid = _resolve_cli_workflow_reference(args.work, args.id, 'terminate')
        wf = _build_workflow_handle(wf_id, wf_uid)
        wf.terminate()
    elif args.cmd == 'retrieve':
        config_dflow(args.config)
        wf_id, wf_uid = _resolve_cli_workflow_reference(args.work, args.id, 'retrieve')
        work_dir = args.work
        all_keys, wf_info, query_ref = _run_with_workflow_fallback(
            wf_id,
            wf_uid,
            lambda wf, _wf_ref, _used_uid: (
                _query_keys_of_steps(wf),
                _query_workflow(wf),
                _wf_ref,
            ),
        )
        download_keys = [key for key in all_keys if _is_retrievable_result_step_key(key)]
        task_left = len(download_keys)
        print(f'Retrieving {task_left} workflow results {query_ref} to {work_dir}')

        for index, key in enumerate(download_keys, start=1):
            step = wf_info.get_step(key=key)[0]
            task_left -= 1
            phase = step['phase']
            print(f"Retrieving result {index}/{len(download_keys)}: {key}", flush=True)
            if phase == 'Succeeded':
                existing_result_dir = _retrieve_existing_result_dir(step, key, work_dir)
                if existing_result_dir:
                    logging.info(
                        "Skip retrieving %s because %s already contains files.",
                        key,
                        existing_result_dir,
                    )
                    continue
                logging.info(f"Retrieving {key}...({task_left} more left)")
                try:
                    _download_artifact_with_retry(
                        artifact=step.outputs.artifacts['retrieve_path'],
                        path=work_dir
                    )
                except Exception as exc:
                    logging.warning(f"Retrieve {key} failed: {exc}")
            else:
                if not _should_retrieve_failure_artifacts(args.debug):
                    logging.warning(
                        f"Step {key} with status: {phase} is not Succeeded; "
                        f"skip failed-artifact retrieval because debug mode is not enabled. "
                        f"({task_left} more left)"
                    )
                    continue
                logging.warning(
                    f"Step {key} with status: {phase} is not Succeeded; "
                    f"trying to retrieve failure artifacts in debug mode...({task_left} more left)"
                )
                downloaded = _download_failure_artifacts_for_step(
                    wf_info=wf_info,
                    root_step=step,
                    key=key,
                    work_dir=work_dir,
                )
                if downloaded == 0:
                    logging.warning(f"No retrievable failure artifacts found for {key}")
                else:
                    logging.info(
                        f"Retrieved {downloaded} failure artifact groups for {key} "
                        f"under {os.path.join(work_dir, '.failed-artifacts')}"
                    )
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
            open_browser=not args.no_browser,
            host=args.host,
            port=args.port,
        )
    elif args.cmd == 'gui':
        header()
        from apex.gui import gui_from_args

        gui_from_args(
            host=args.host,
            port=args.port,
            open_browser=not args.no_browser
        )
    elif args.cmd == 'account':
        from apex.account import account_from_args

        account_from_args(args)
    elif args.cmd == 'rss':
        from apex.rss import rss_from_args

        rss_from_args(args.rss_json)
    elif args.cmd == 'preview':
        from apex.preview import preview_from_args

        preview_from_args(args)
    else:
        raise RuntimeError(
            f"unknown command {args.cmd}\n{parser.print_help()}"
        )
