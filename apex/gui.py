import base64
import binascii
import copy
import glob
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import time
import uuid
import webbrowser
import zipfile
from datetime import datetime
from threading import Timer
from typing import Any, Dict, List, Optional, Tuple

import dash
import dash_bootstrap_components as dbc
from dash import Input, Output, State, dash_table, dcc, html

from apex.account import DEFAULT_BOHRIUM_CONFIG, get_account_config_path, load_account_config, save_account_config


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8060
DEFAULT_REPORT_PORT = 8070
RETRIEVE_RUNNING_MESSAGE = "正在提取文件中..."
BLOCKED_INLINE_COMMANDS = {"gui"}
DEFAULT_SUBMIT_COMMAND = "nohup apex submit param.json -c global.json > apex.log 2>&1 &"
SUBMIT_STATUS_FILE = ".apex-submit.status"
SUBMIT_GROUP_META_FILE = ".apex-submit-group.json"
SUBMIT_RUNNING_NOTICE = "任务已提交，正在运行，详情请转到Log页面查看"
SUBMIT_BATCH_SIZE = 100
PARAM_FALLBACK_PATTERN = "*param*.json"
WORKFLOW_PROGRESS_QUERY_TIMEOUT_SECONDS = 8
WORKFLOW_QUICK_QUERY_TIMEOUT_SECONDS = 5
WORKFLOW_DETAIL_REFRESH_SECONDS = 30
WORKFLOW_DETAIL_QUERY_TIMEOUT_SECONDS = 25
WORKFLOW_QUERY_RESULT_PREFIX = "__APEX_GUI_WORKFLOW_QUERY__"
_WORKFLOW_DETAIL_CACHE: Dict[Tuple[str, str], Dict[str, Any]] = {}

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG_DIR = os.path.join(THIS_DIR, "default_config")
PROFILE_NAMES = ("lammps", "vasp", "abacus")
DEFAULT_PROFILE = "lammps"

LMP_INTERACTION_TYPE_OPTIONS = ["eam_alloy", "deepmd", "meam", "tersoff", "sw", "reaxff"]
MISSING_POTCAR_HINT = "请提交对应元素的POTCAR"
MISSING_ORB_HINT = "请提交对应元素的ORB"


def _load_json_file(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _json_dump_text(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, indent=4, ensure_ascii=False)


def _profile_dir(profile: str) -> str:
    profile_name = profile if profile in PROFILE_NAMES else DEFAULT_PROFILE
    return os.path.join(DEFAULT_CONFIG_DIR, profile_name)


def _load_profile_global(profile: str) -> Dict[str, Any]:
    return _load_json_file(os.path.join(_profile_dir(profile), "global.json"))


def _load_profile_param_template(profile: str) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    for filename in (
        "param_structure.json",
        os.path.join("param_interaction", "param_interaction.json"),
        "param_relax.json",
        "param_props.json",
    ):
        part = _load_json_file(os.path.join(_profile_dir(profile), filename))
        if isinstance(part, dict):
            payload.update(copy.deepcopy(part))
    return payload


def _extract_property_types(param_template: Dict[str, Any]) -> List[str]:
    ordered: List[str] = []
    for item in param_template.get("properties", []):
        if not isinstance(item, dict):
            continue
        ptype = item.get("type")
        if ptype and ptype not in ordered:
            ordered.append(ptype)
    return ordered


def _extract_selected_properties(param_template: Dict[str, Any]) -> List[str]:
    selected: List[str] = []
    for item in param_template.get("properties", []):
        if not isinstance(item, dict):
            continue
        ptype = item.get("type")
        if ptype and item.get("req_calc") and ptype not in selected:
            selected.append(ptype)
    return selected


def _extract_requested_properties(param_payload: Dict[str, Any]) -> List[str]:
    selected: List[str] = []
    for item in param_payload.get("properties", []):
        if not isinstance(item, dict):
            continue
        ptype = item.get("type")
        if ptype and item.get("req_calc", True) is not False and ptype not in selected:
            selected.append(ptype)
    return selected


def _extract_structure_defaults(param_template: Dict[str, Any]) -> List[str]:
    structures = param_template.get("structures", [])
    if not isinstance(structures, list):
        return []
    return [str(item).strip() for item in structures if str(item).strip()]


def _extract_interaction_defaults(param_template: Dict[str, Any]) -> Tuple[str, str, List[str]]:
    interaction = param_template.get("interaction")
    if not isinstance(interaction, dict):
        return "eam_alloy", "", []
    interaction_type = interaction.get("type") or "eam_alloy"
    interaction_model_obj = interaction.get("model")
    if isinstance(interaction_model_obj, list):
        interaction_model = ", ".join(str(item) for item in interaction_model_obj)
    else:
        interaction_model = interaction_model_obj or ""
    type_map = interaction.get("type_map")
    if isinstance(type_map, dict):
        elements = list(type_map.keys())
    else:
        elements = []
    return interaction_type, interaction_model, elements


def _interaction_path_key(profile: str) -> str:
    return "input" if profile == "abacus" else "incar"


def _extract_interaction_incar(param_template: Dict[str, Any], profile: str = DEFAULT_PROFILE) -> str:
    interaction = param_template.get("interaction")
    if not isinstance(interaction, dict):
        return ""
    key = _interaction_path_key(profile)
    if key == "input":
        return interaction.get("input") or interaction.get("incar") or ""
    return interaction.get("incar") or ""


def _extract_potcar_rows(param_template: Dict[str, Any]) -> List[Tuple[str, str]]:
    interaction = param_template.get("interaction")
    if not isinstance(interaction, dict):
        return []
    potcars = interaction.get("potcars")
    if not isinstance(potcars, dict):
        return []
    return [(str(ele), _strip_parenthetical_suffix(str(path))) for ele, path in potcars.items()]


def _extract_orb_rows(param_template: Dict[str, Any]) -> List[Tuple[str, str]]:
    interaction = param_template.get("interaction")
    if not isinstance(interaction, dict):
        return []
    orb_files = interaction.get("orb_files")
    if not isinstance(orb_files, dict):
        return []
    return [(str(ele), _strip_parenthetical_suffix(str(path))) for ele, path in orb_files.items()]


def _strip_parenthetical_suffix(text: str) -> str:
    return re.sub(r"\s*\([^)]*\)\s*$", "", (text or "").strip())


def _interaction_type_options_for_profile(profile: str, default_type: str) -> List[Dict[str, str]]:
    if profile == "lammps":
        options = list(LMP_INTERACTION_TYPE_OPTIONS)
        if default_type and default_type not in options:
            options.insert(0, default_type)
    elif profile in {"vasp", "abacus"}:
        options = [profile]
        if default_type and default_type not in options:
            options.insert(0, default_type)
    else:
        options = list(LMP_INTERACTION_TYPE_OPTIONS)
    return [{"label": item, "value": item} for item in options]


def _rows_to_mapping(keys: List[str], values: List[str]) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for key, value in zip(keys, values):
        k = (key or "").strip()
        v = _strip_parenthetical_suffix(value or "")
        if v.startswith(MISSING_POTCAR_HINT) or v.startswith(MISSING_ORB_HINT):
            continue
        if k and v and k not in mapping:
            mapping[k] = v
    return mapping


def _interaction_table_columns_for_profile(profile: str) -> List[Dict[str, str]]:
    base = [
        {"id": "element", "name": "Element"},
        {"id": "potcar", "name": "POTCAR"},
    ]
    if profile == "abacus":
        base.append({"id": "orb_file", "name": "ORB file"})
    return base


def _interaction_table_rows_from_template(profile: str, param_template: Dict[str, Any]) -> List[Dict[str, str]]:
    potcar_map = dict(_extract_potcar_rows(param_template))
    orb_map = dict(_extract_orb_rows(param_template))
    ordered_elements = list(potcar_map.keys())
    for ele in orb_map.keys():
        if ele not in ordered_elements:
            ordered_elements.append(ele)

    rows: List[Dict[str, str]] = []
    for ele in ordered_elements:
        row = {
            "element": ele,
            "potcar": potcar_map.get(ele, ""),
        }
        if profile == "abacus":
            row["orb_file"] = orb_map.get(ele, "")
        rows.append(row)

    if not rows:
        rows.append({"element": "", "potcar": "", **({"orb_file": ""} if profile == "abacus" else {})})
    return rows


def _resolve_first_structure_file_for_gui(workdir: str, structures: List[str]) -> str:
    abs_workdir = _normalize_workdir(workdir)
    for pattern in structures or []:
        clean_pattern = (pattern or "").strip()
        if not clean_pattern:
            continue
        search_pattern = clean_pattern if os.path.isabs(clean_pattern) else os.path.join(abs_workdir, clean_pattern)
        for match in sorted(set(glob.glob(search_pattern))):
            if os.path.isfile(match):
                if os.path.basename(match) == "POSCAR":
                    return match
                continue
            if os.path.isdir(match):
                for candidate in ("POSCAR", "CONTCAR", "STRU"):
                    candidate_path = os.path.join(match, candidate)
                    if os.path.isfile(candidate_path):
                        return candidate_path
                nested = sorted(glob.glob(os.path.join(match, "**", "POSCAR"), recursive=True))
                if nested:
                    return nested[0]
    return ""


def _extract_elements_from_selected_structure(workdir: str, structures: List[str]) -> List[str]:
    structure_file = _resolve_first_structure_file_for_gui(workdir, structures)
    if not structure_file or os.path.basename(structure_file) != "POSCAR":
        return []
    try:
        from pymatgen.io.vasp import Poscar

        return [str(symbol) for symbol in Poscar.from_file(structure_file).site_symbols]
    except Exception:
        return []


def _workdir_files(workdir: str, preferred_subdir: str = "") -> List[str]:
    abs_workdir = _normalize_workdir(workdir)
    candidates: List[str] = []
    for root, dirnames, filenames in os.walk(abs_workdir):
        dirnames[:] = sorted(name for name in dirnames if not name.startswith("."))
        rel_root = os.path.relpath(root, abs_workdir)
        for filename in sorted(filenames):
            if filename.startswith("."):
                continue
            rel_path = filename if rel_root == "." else os.path.join(rel_root, filename)
            rel_path = rel_path.replace(os.path.sep, "/")
            candidates.append(rel_path)
    if preferred_subdir:
        preferred_prefix = preferred_subdir.strip("/").replace(os.path.sep, "/") + "/"
        candidates.sort(key=lambda item: (0 if item.startswith(preferred_prefix) else 1, len(item), item.lower()))
    else:
        candidates.sort(key=lambda item: (len(item), item.lower()))
    return candidates


def _suffix_token_match(path: str, element: str) -> bool:
    basename = os.path.basename(path)
    tokens = [tok for tok in re.split(r"[._-]+", basename) if tok]
    return bool(tokens) and tokens[-1].lower() == element.lower()


def _prefix_token_match(path: str, element: str) -> bool:
    basename = os.path.basename(path)
    tokens = [tok for tok in re.split(r"[._-]+", basename) if tok]
    return bool(tokens) and tokens[0].lower() == element.lower()


def _autodetect_interaction_rows(profile: str, workdir: str, structures: List[str], param_template: Dict[str, Any]) -> List[Dict[str, str]]:
    if profile not in {"vasp", "abacus"}:
        return _interaction_table_rows_from_template(profile, param_template)

    elements = _extract_elements_from_selected_structure(workdir, structures)
    if not elements:
        return _interaction_table_rows_from_template(profile, param_template)

    preferred_dir = "vasp_input" if profile == "vasp" else "abacus_input"
    files = _workdir_files(workdir, preferred_subdir=preferred_dir)
    rows: List[Dict[str, str]] = []
    for element in elements:
        if profile == "vasp":
            potcar = next((os.path.basename(path) for path in files if _suffix_token_match(path, element)), "")
            rows.append({"element": element, "potcar": potcar or MISSING_POTCAR_HINT})
        else:
            potcar = next(
                (
                    os.path.basename(path)
                    for path in files
                    if _prefix_token_match(path, element) and not os.path.basename(path).lower().endswith(".orb")
                ),
                "",
            )
            orb_file = next(
                (
                    os.path.basename(path)
                    for path in files
                    if _prefix_token_match(path, element) and os.path.basename(path).lower().endswith(".orb")
                ),
                "",
            )
            rows.append(
                {
                    "element": element,
                    "potcar": potcar or MISSING_POTCAR_HINT,
                    "orb_file": orb_file or MISSING_ORB_HINT,
                }
            )
    return rows


def _interaction_editor_label(profile: str) -> str:
    if profile == "vasp":
        return "INCAR 编辑区"
    if profile == "abacus":
        return "INPUT 编辑区"
    return "INCAR/INPUT 编辑区"


def _interaction_path_label(profile: str) -> str:
    return "interaction.input" if profile == "abacus" else "interaction.incar (会自动去掉括号备注)"


def _interaction_path_placeholder(profile: str) -> str:
    return "abacus_input/INPUT" if profile == "abacus" else "vasp_input/INCAR"


def _load_profile_incar_content(profile: str, param_template: Dict[str, Any]) -> str:
    incar_rel = _strip_parenthetical_suffix(_extract_interaction_incar(param_template, profile))
    if not incar_rel:
        return ""
    source_path = os.path.join(_profile_dir(profile), "param_interaction", incar_rel)
    try:
        with open(source_path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _build_feedback(message: str, ok: bool = False) -> Dict[str, Any]:
    return {
        "ok": ok,
        "message": message,
        "command": "",
        "returncode": "",
        "stdout": "",
        "stderr": "",
        "finished_at": datetime.now().isoformat(timespec="seconds"),
    }


def _is_retrieve_feedback(payload: Any) -> bool:
    return isinstance(payload, dict) and payload.get("operation") == "retrieve" and bool(payload.get("status_file"))


def _retrieve_state_is_active(state_payload: Any) -> bool:
    if not isinstance(state_payload, dict) or state_payload.get("status") != "running":
        return False
    status_file = state_payload.get("status_file") or ""
    if not status_file:
        return True
    if not os.path.isfile(status_file):
        return True
    try:
        with open(status_file, "r", encoding="utf-8") as f:
            return not f.read().strip()
    except OSError:
        return True


def _resolve_triggered_id():
    if hasattr(dash, "ctx") and dash.ctx.triggered_id is not None:
        return dash.ctx.triggered_id
    triggered = dash.callback_context.triggered
    if not triggered:
        return None
    return triggered[0]["prop_id"].split(".")[0]


def _run_apex_command(arguments: List[str], cwd: Optional[str] = None) -> Dict[str, Any]:
    command = [sys.executable, "-m", "apex", *arguments]
    try:
        completed = subprocess.run(
            command,
            cwd=cwd or os.getcwd(),
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception as exc:
        return {
            "ok": False,
            "message": f"Failed to launch command: {exc}",
            "command": " ".join(shlex.quote(token) for token in command),
            "returncode": "",
            "stdout": "",
            "stderr": str(exc),
            "finished_at": datetime.now().isoformat(timespec="seconds"),
        }

    return {
        "ok": completed.returncode == 0,
        "message": "Command finished successfully." if completed.returncode == 0 else "Command finished with errors.",
        "command": " ".join(shlex.quote(token) for token in command),
        "returncode": str(completed.returncode),
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
        "finished_at": datetime.now().isoformat(timespec="seconds"),
    }


def _run_apex_command_in_background(
    arguments: List[str],
    cwd: Optional[str] = None,
    log_file: str = "apex-advanced.log",
) -> Dict[str, Any]:
    command = [sys.executable, "-m", "apex", *arguments]
    display_cmd = " ".join(shlex.quote(token) for token in command)
    shell_cmd = f"nohup {display_cmd} > {shlex.quote(log_file)} 2>&1 & echo $!"
    try:
        completed = subprocess.run(
            ["bash", "-lc", shell_cmd],
            cwd=cwd or os.getcwd(),
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception as exc:
        return {
            "ok": False,
            "message": f"Failed to launch background command: {exc}",
            "command": shell_cmd,
            "returncode": "",
            "stdout": "",
            "stderr": str(exc),
            "finished_at": datetime.now().isoformat(timespec="seconds"),
        }

    pid = completed.stdout.strip().splitlines()[-1] if completed.stdout.strip() else ""
    message = f"Background command started. Log file: {log_file}."
    if pid:
        message += f" PID: {pid}."
    return {
        "ok": completed.returncode == 0,
        "message": message if completed.returncode == 0 else "Background command failed to start.",
        "command": shell_cmd,
        "returncode": str(completed.returncode),
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
        "finished_at": datetime.now().isoformat(timespec="seconds"),
    }


def _start_retrieve_in_background(workdir: str, workflow_id: str, global_file: str) -> Dict[str, Any]:
    log_file = os.path.join(workdir, "apex-retrieve.log")
    status_file = os.path.join(workdir, ".apex-retrieve.status")
    workflow_ids = _parse_workflow_ids(workflow_id)
    if not workflow_ids:
        return {
            "ok": False,
            "message": "Workflow ID is required for retrieve.",
            "command": "",
            "returncode": "",
            "stdout": "",
            "stderr": "",
            "finished_at": datetime.now().isoformat(timespec="seconds"),
        }
    command = [
        sys.executable,
        "-m",
        "apex.gui_background",
        "retrieve",
        workdir,
        global_file,
        log_file,
        status_file,
    ] + workflow_ids
    display_cmd = " ".join(shlex.quote(token) for token in command)
    shell_cmd = (
        f"rm -f {shlex.quote(status_file)}; "
        f"({display_cmd} > {shlex.quote(log_file)} 2>&1; "
        f"code=$?; printf \"%s\" \"$code\" > {shlex.quote(status_file)}) & echo $!"
    )
    try:
        completed = subprocess.run(
            ["bash", "-lc", shell_cmd],
            cwd=workdir,
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception as exc:
        return {
            "ok": False,
            "message": f"Failed to launch retrieve: {exc}",
            "command": shell_cmd,
            "returncode": "",
            "stdout": "",
            "stderr": str(exc),
            "finished_at": datetime.now().isoformat(timespec="seconds"),
        }

    pid = completed.stdout.strip().splitlines()[-1] if completed.stdout.strip() else ""
    return {
        "ok": completed.returncode == 0,
        "message": RETRIEVE_RUNNING_MESSAGE if completed.returncode == 0 else "Retrieve failed to start.",
        "operation": "retrieve",
        "command": shell_cmd,
        "returncode": str(completed.returncode),
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "pid": pid,
        "workdir": workdir,
        "workflow_id": _format_workflow_ids(workflow_ids),
        "workflow_ids": workflow_ids,
        "global_file": global_file,
        "log_file": log_file,
        "status_file": status_file,
    }


def _advanced_report_args(arguments: List[str], report_port: int = DEFAULT_REPORT_PORT) -> List[str]:
    normalized = list(arguments)
    if not normalized or normalized[0] != "report":
        return normalized

    if "--no-browser" not in normalized:
        normalized.append("--no-browser")
    has_port = any(
        arg == "-p"
        or arg == "--port"
        or arg.startswith("--port=")
        for arg in normalized
    )
    if not has_port:
        normalized.extend(["--port", str(report_port)])
    return normalized


def _format_feedback(payload: Dict[str, Any]) -> str:
    if not payload:
        return "Click any action button to run an APEX command."

    lines = [f"[{payload.get('finished_at', '')}] {'SUCCESS' if payload.get('ok') else 'FAILED'}"]

    message = payload.get("message")
    if message:
        lines.append(message)
    report_url = str(payload.get("report_url") or "").strip()
    if report_url:
        lines.append(f"Report URL: {report_url}")
    command = payload.get("command")
    if command:
        lines.extend(["", f"$ {command}"])
    returncode = payload.get("returncode")
    if returncode != "":
        lines.append(f"Return code: {returncode}")

    stdout = payload.get("stdout")
    stderr = payload.get("stderr")
    if stdout:
        lines.extend(["", "[stdout]", stdout])
    if stderr:
        lines.extend(["", "[stderr]", stderr])
    if not stdout and not stderr:
        lines.extend(["", "(No command output)"])

    return "\n".join(lines)


def _report_started_children(report_url: str) -> List[Any]:
    clean_url = str(report_url or "").strip()
    if not clean_url:
        return ["Retrieve finished; report started."]
    return [
        "Retrieve finished; report started. ",
        html.A(clean_url, href=clean_url, target="_blank", rel="noreferrer"),
    ]


def _completed_retrieve_state(
    workdir: str,
    workflow_id: str,
    workflow_ids: Optional[List[str]],
    global_file: str,
    param_file: str,
    text: Any,
    log_file: str = "",
    status_file: str = "",
    report_url: str = "",
) -> Dict[str, Any]:
    return {
        "status": "done",
        "workdir": workdir,
        "workflow_id": workflow_id,
        "workflow_ids": workflow_ids or _parse_workflow_ids(workflow_id),
        "global_file": global_file,
        "param_file": param_file,
        "log_file": log_file,
        "status_file": status_file,
        "completed_text": text,
        "report_url": report_url,
        "pending_report": False,
    }


def _has_local_reportable_results(workdir: str) -> bool:
    target_workdir = _normalize_workdir(workdir)
    if os.path.isfile(os.path.join(target_workdir, "all_result.json")):
        return True
    for root, _dirs, files in os.walk(target_workdir):
        if "result.json" in files:
            return True
    return False


def _read_log_tail(log_path: str = "apex.log", max_lines: int = 400, workdir: Optional[str] = None) -> str:
    if not os.path.isabs(log_path):
        log_path = os.path.join(_normalize_workdir(workdir or os.getcwd()), log_path)
    if not os.path.isfile(log_path):
        return f"{log_path} not found yet. Run submit first."
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError as exc:
        return f"Failed to read {log_path}: {exc}"
    return "".join(lines[-max_lines:]) if lines else f"{log_path} is empty."


def _parse_retrieve_progress_from_log(log_text: str) -> Optional[Tuple[int, str, str]]:
    total = None
    current = 0
    current_key = ""
    for line in log_text.splitlines():
        total_match = re.search(r"Retrieving\s+(\d+)\s+workflow results", line)
        if total_match:
            total = int(total_match.group(1))
        progress_match = re.search(r"Retrieving result\s+(\d+)/(\d+):\s*(.+)", line)
        if progress_match:
            current = int(progress_match.group(1))
            total = int(progress_match.group(2))
            current_key = progress_match.group(3).strip()

    if not total:
        return None

    percent = int(round((current / total) * 100)) if total > 0 else 100
    percent = max(0, min(percent, 99 if current < total else 100))
    label = f"{percent}%"
    if current_key:
        text = f"{RETRIEVE_RUNNING_MESSAGE} {current}/{total}: {current_key}"
    else:
        text = f"{RETRIEVE_RUNNING_MESSAGE} 0/{total}"
    return percent, label, text


def _retrieve_log_matches_workflow(log_file: str, workflow_id: str) -> bool:
    workflow_id = (workflow_id or "").strip()
    if not workflow_id:
        return True
    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            for _ in range(80):
                line = f.readline()
                if not line:
                    break
                match = re.search(r"Retrieving\s+\d+\s+workflow results\s+(\S+)\s+to\b", line)
                if match:
                    return match.group(1) == workflow_id
    except OSError:
        return False
    return False


def _parse_extra_elements(raw_text: str) -> List[str]:
    if not raw_text:
        return []
    normalized = raw_text.replace(",", " ").replace(";", " ").replace("\n", " ")
    return [token.strip() for token in normalized.split() if token.strip()]


def _merge_dict_values(base: Dict[str, Any], updates: Optional[Dict[str, Any]]) -> None:
    if not updates:
        return
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge_dict_values(base[key], value)
        else:
            base[key] = value


def _load_account_state(account_path: Optional[str] = None) -> Dict[str, Any]:
    path_obj = get_account_config_path(account_path)
    raw_config = load_account_config(path_obj)
    merged = copy.deepcopy(DEFAULT_BOHRIUM_CONFIG)
    if isinstance(raw_config, dict):
        _merge_dict_values(merged, raw_config)
    program_id = merged.get("program_id")
    return {
        "path": str(path_obj),
        "email": str(merged.get("email") or ""),
        "program_id": str(program_id) if program_id not in (None, "") else "",
        "password_set": bool(merged.get("password")),
    }


def _render_account_summary(account_state: Dict[str, Any]) -> str:
    email = account_state.get("email") or "(未设置)"
    program_id = account_state.get("program_id") or "(未设置)"
    password_status = "已设置 (隐藏)" if account_state.get("password_set") else "未设置"
    config_path = account_state.get("path") or "(unknown)"
    return "\n".join(
        [
            f"Config path: {config_path}",
            f"Email: {email}",
            f"Program ID: {program_id}",
            f"Password: {password_status}",
        ]
    )


def _brief_feedback(payload: Dict[str, Any]) -> str:
    if not payload:
        return ""
    status = "SUCCESS" if payload.get("ok") else "FAILED"
    message = payload.get("message") or ""
    ts = payload.get("finished_at") or datetime.now().isoformat(timespec="seconds")
    return f"[{ts}] {status} {message}"


def _save_account_overwrite(
    email: str,
    password: str,
    program_id_text: str,
    account_path: Optional[str] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    path_obj = get_account_config_path(account_path)
    raw_config = load_account_config(path_obj)
    merged = copy.deepcopy(DEFAULT_BOHRIUM_CONFIG)
    if isinstance(raw_config, dict):
        _merge_dict_values(merged, raw_config)

    updates_applied: List[str] = []
    clean_email = (email or "").strip()
    clean_password = (password or "").strip()
    clean_program_id = (program_id_text or "").strip()

    if clean_email:
        merged["email"] = clean_email
        updates_applied.append("email")
    if clean_password:
        merged["password"] = clean_password
        updates_applied.append("password")
    if clean_program_id:
        try:
            merged["program_id"] = int(clean_program_id)
        except ValueError:
            current_state = _load_account_state(account_path)
            return _build_feedback("program_id 必须是整数"), current_state
        updates_applied.append("program_id")

    save_account_config(merged, path_obj)
    new_state = _load_account_state(account_path)
    updated_fields = ", ".join(updates_applied) if updates_applied else "none (kept existing values)"
    message = f"Account saved to {new_state.get('path')}. Updated: {updated_fields}."
    return _build_feedback(message, ok=True), new_state


DEFAULT_PARAM_TEMPLATE = _load_profile_param_template(DEFAULT_PROFILE)
DEFAULT_STRUCTURE_PATHS = _extract_structure_defaults(DEFAULT_PARAM_TEMPLATE)
DEFAULT_PROPERTY_TYPES = _extract_property_types(DEFAULT_PARAM_TEMPLATE)
DEFAULT_SELECTED_PROPERTIES = _extract_selected_properties(DEFAULT_PARAM_TEMPLATE)
DEFAULT_INTERACTION_TYPE, DEFAULT_INTERACTION_MODEL, _DEFAULT_INTERACTION_ELEMENTS = _extract_interaction_defaults(
    DEFAULT_PARAM_TEMPLATE
)
DEFAULT_ACCOUNT_STATE = _load_account_state()
DEFAULT_SUBMIT_STATE = {
    "workdir": os.getcwd(),
    "workflow_id": "",
    "workflow_ids": [],
    "workflow_group_id": "",
    "workflow_group_size": 0,
    "workflow_group_total_confs": 0,
    "global_file": "global.json",
    "param_file": "param.json",
}


def _build_param_payload(
    profile: str,
    with_relax: bool,
    selected_properties: List[str],
    interaction_type: str,
    interaction_model: str,
    selected_structures: List[str] = None,
    element_slots: List[str] = None,
    interaction_incar: str = "",
    interaction_rows: List[Dict[str, str]] = None,
    base_template: Dict[str, Any] = None,
) -> Dict[str, Any]:
    template = base_template if isinstance(base_template, dict) and base_template else DEFAULT_PARAM_TEMPLATE
    payload = copy.deepcopy(template)
    payload["structures"] = [item.strip() for item in (selected_structures or []) if isinstance(item, str) and item.strip()]

    if with_relax:
        payload["relaxation"] = copy.deepcopy(template.get("relaxation", {}))
    else:
        payload.pop("relaxation", None)

    selected_set = set(selected_properties or [])
    kept_properties = []
    for item in payload.get("properties", []):
        if not isinstance(item, dict):
            continue
        ptype = item.get("type")
        if ptype in selected_set:
            item["req_calc"] = True
            kept_properties.append(item)
    payload["properties"] = kept_properties

    interaction_rows = interaction_rows or []
    potcar_map = _rows_to_mapping(
        [row.get("element", "") if isinstance(row, dict) else "" for row in interaction_rows],
        [row.get("potcar", "") if isinstance(row, dict) else "" for row in interaction_rows],
    )
    orb_map = _rows_to_mapping(
        [row.get("element", "") if isinstance(row, dict) else "" for row in interaction_rows],
        [row.get("orb_file", "") if isinstance(row, dict) else "" for row in interaction_rows],
    )
    interaction_from_template = payload.get("interaction")
    interaction_payload = copy.deepcopy(interaction_from_template) if isinstance(interaction_from_template, dict) else {}
    template_type = interaction_payload.get("type") if isinstance(interaction_payload, dict) else None
    effective_type = (interaction_type or "").strip() or interaction_payload.get("type") or (
        profile if profile in {"vasp", "abacus"} else "eam_alloy"
    )
    interaction_payload["type"] = effective_type

    if profile == "lammps":
        model_text = (interaction_model or "").strip()
        if model_text:
            if "," in model_text:
                interaction_payload["model"] = [item.strip() for item in model_text.split(",") if item.strip()]
            else:
                interaction_payload["model"] = model_text
        elif "model" in interaction_payload and interaction_payload.get("model") and effective_type == template_type:
            pass
        elif effective_type == "eam_alloy":
            interaction_payload["model"] = "Al.eam.alloy"
        else:
            interaction_payload.pop("model", None)

        interaction_payload["type_map"] = "auto"
        interaction_payload.pop("incar", None)
        interaction_payload.pop("potcars", None)
        interaction_payload.pop("potcar_prefix", None)
    else:
        interaction_payload.pop("model", None)
        interaction_payload.pop("type_map", None)
        path_key = _interaction_path_key(profile)
        if interaction_incar.strip():
            interaction_payload[path_key] = interaction_incar.strip()
        interaction_payload.pop("incar" if path_key == "input" else "input", None)
        if potcar_map:
            interaction_payload["potcars"] = potcar_map
        elif "potcars" in interaction_payload and isinstance(interaction_payload.get("potcars"), dict):
            pass
        else:
            interaction_payload.pop("potcars", None)
        if profile == "abacus":
            if orb_map:
                interaction_payload["orb_files"] = orb_map
            elif "orb_files" in interaction_payload and isinstance(interaction_payload.get("orb_files"), dict):
                pass
            else:
                interaction_payload.pop("orb_files", None)

    payload["interaction"] = interaction_payload

    return payload


def _parse_param_editor_payload(raw_text: str, fallback_template: Dict[str, Any]) -> Dict[str, Any]:
    try:
        parsed = json.loads(raw_text or "")
    except json.JSONDecodeError:
        return copy.deepcopy(fallback_template)
    return copy.deepcopy(parsed) if isinstance(parsed, dict) else copy.deepcopy(fallback_template)


def _property_items_by_type(items: List[Any]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        ptype = item.get("type")
        if ptype:
            grouped.setdefault(str(ptype), []).append(item)
    return grouped


def _patch_properties(
    payload: Dict[str, Any],
    template: Dict[str, Any],
    selected_properties: List[str],
) -> None:
    existing_by_type = _property_items_by_type(payload.get("properties", []))
    template_by_type = _property_items_by_type(template.get("properties", []))
    next_properties = []
    used_counts: Dict[str, int] = {}
    for ptype in selected_properties or []:
        ptype = str(ptype)
        idx = used_counts.get(ptype, 0)
        candidates = existing_by_type.get(ptype) or template_by_type.get(ptype) or []
        if idx < len(candidates):
            item = copy.deepcopy(candidates[idx])
        elif candidates:
            item = copy.deepcopy(candidates[-1])
        else:
            item = {"type": ptype}
        item["type"] = ptype
        item["req_calc"] = True
        next_properties.append(item)
        used_counts[ptype] = idx + 1
    payload["properties"] = next_properties


def _patch_interaction(
    payload: Dict[str, Any],
    profile: str,
    interaction_type: str,
    interaction_model: str,
    interaction_incar: str,
    interaction_rows: List[Dict[str, str]],
) -> None:
    interaction_rows = interaction_rows or []
    potcar_map = _rows_to_mapping(
        [row.get("element", "") if isinstance(row, dict) else "" for row in interaction_rows],
        [row.get("potcar", "") if isinstance(row, dict) else "" for row in interaction_rows],
    )
    orb_map = _rows_to_mapping(
        [row.get("element", "") if isinstance(row, dict) else "" for row in interaction_rows],
        [row.get("orb_file", "") if isinstance(row, dict) else "" for row in interaction_rows],
    )
    current = payload.get("interaction")
    interaction_payload = copy.deepcopy(current) if isinstance(current, dict) else {}
    effective_type = (interaction_type or "").strip() or interaction_payload.get("type") or (
        profile if profile in {"vasp", "abacus"} else "eam_alloy"
    )
    interaction_payload["type"] = effective_type

    if profile == "lammps":
        model_text = (interaction_model or "").strip()
        if model_text:
            if "," in model_text:
                interaction_payload["model"] = [item.strip() for item in model_text.split(",") if item.strip()]
            else:
                interaction_payload["model"] = model_text
        elif effective_type != "eam_alloy":
            interaction_payload.pop("model", None)
        interaction_payload["type_map"] = "auto"
        for key in ("input", "incar", "potcars", "potcar_prefix", "orb_files"):
            interaction_payload.pop(key, None)
    else:
        interaction_payload.pop("model", None)
        interaction_payload.pop("type_map", None)
        path_key = _interaction_path_key(profile)
        if (interaction_incar or "").strip():
            interaction_payload[path_key] = interaction_incar.strip()
        interaction_payload.pop("incar" if path_key == "input" else "input", None)
        if potcar_map:
            interaction_payload["potcars"] = potcar_map
        if profile == "abacus" and orb_map:
            interaction_payload["orb_files"] = orb_map

    payload["interaction"] = interaction_payload


def _patch_param_payload(
    current_text: str,
    triggered_id: str,
    profile: str,
    template: Dict[str, Any],
    relax_check: List[str],
    properties_check: List[str],
    structures_value: List[str],
    interaction_type: str,
    interaction_model: str,
    interaction_incar: str,
    interaction_rows: List[Dict[str, str]],
) -> Dict[str, Any]:
    payload = _parse_param_editor_payload(current_text, template)
    if triggered_id == "submit-structures":
        payload["structures"] = [item.strip() for item in (structures_value or []) if isinstance(item, str) and item.strip()]
    elif triggered_id == "submit-properties-check":
        _patch_properties(payload, template, properties_check or [])
    elif triggered_id == "submit-relax-check":
        if "relax" in (relax_check or []):
            payload.setdefault("relaxation", copy.deepcopy(template.get("relaxation", {})))
        else:
            payload.pop("relaxation", None)
    elif triggered_id in {
        "submit-interaction-type",
        "submit-interaction-model",
        "submit-interaction-incar",
        "submit-interaction-table",
    }:
        _patch_interaction(
            payload=payload,
            profile=profile,
            interaction_type=interaction_type,
            interaction_model=interaction_model,
            interaction_incar=interaction_incar,
            interaction_rows=interaction_rows or [],
        )
    return payload


def _normalize_workdir(workdir: str) -> str:
    clean = (workdir or "").strip()
    return os.path.abspath(clean or os.getcwd())


def _resolve_file_path(workdir: str, filename: str) -> str:
    clean = (filename or "").strip()
    if not clean:
        return ""
    if os.path.isabs(clean):
        return clean
    return os.path.join(workdir, clean)


def _list_workdir_file_options(workdir: str, current_value: str = "") -> List[Dict[str, str]]:
    target_dir = _normalize_workdir(workdir)
    options: List[Dict[str, str]] = []
    seen = set()

    if os.path.isdir(target_dir):
        for root, dirnames, filenames in os.walk(target_dir):
            dirnames[:] = sorted(name for name in dirnames if not name.startswith("."))
            rel_root = os.path.relpath(root, target_dir)
            for filename in sorted(filenames):
                if filename.startswith("."):
                    continue
                rel_path = filename if rel_root == "." else os.path.join(rel_root, filename)
                rel_path = rel_path.replace(os.path.sep, "/")
                options.append({"label": rel_path, "value": rel_path})
                seen.add(rel_path)

    clean_current = (current_value or "").strip()
    if clean_current and clean_current not in seen:
        options.insert(0, {"label": f"{clean_current} (current)", "value": clean_current})
    return options


def _is_param_fallback_filename(path: str) -> bool:
    name = os.path.basename(path or "")
    return "param" in name and name.endswith(".json")


def _read_text_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _find_param_fallback_file(workdir: str, preferred_files: Optional[List[str]] = None) -> Tuple[str, str]:
    target_dir = _normalize_workdir(workdir)
    candidates: List[str] = []

    for rel_path in preferred_files or []:
        clean_rel = str(rel_path or "").replace("\\", "/").strip("/")
        if not _is_param_fallback_filename(clean_rel):
            continue
        abs_path = _resolve_file_path(target_dir, clean_rel)
        if os.path.isfile(abs_path):
            candidates.append(clean_rel)

    if not candidates and os.path.isdir(target_dir):
        for abs_path in glob.glob(os.path.join(target_dir, PARAM_FALLBACK_PATTERN)):
            if os.path.isfile(abs_path):
                candidates.append(os.path.basename(abs_path))

    if not candidates:
        return "", ""

    unique_candidates = sorted(set(candidates), key=lambda rel: (-os.path.getmtime(_resolve_file_path(target_dir, rel)), rel))
    selected = unique_candidates[0]
    return selected, _read_text_file(_resolve_file_path(target_dir, selected))


def _param_controls_from_text(param_text: str, profile: str, workdir: str) -> Optional[Dict[str, Any]]:
    try:
        payload = json.loads(param_text or "{}")
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    profile = profile if profile in PROFILE_NAMES else DEFAULT_PROFILE
    structures = _extract_structure_defaults(payload)
    property_types = _extract_property_types(payload)
    property_values = _extract_requested_properties(payload)
    interaction_type, interaction_model, _interaction_elements = _extract_interaction_defaults(payload)
    interaction_incar = _extract_interaction_incar(payload, profile)
    interaction_rows = _interaction_table_rows_from_template(profile, payload)

    return {
        "structures_options": _list_structure_path_options(workdir, structures),
        "structures_value": structures,
        "relax_value": ["relax"] if "relaxation" in payload else [],
        "property_options": [{"label": name, "value": name} for name in property_types],
        "property_value": property_values,
        "interaction_type_options": _interaction_type_options_for_profile(profile, interaction_type),
        "interaction_type": interaction_type,
        "interaction_model_options": _list_workdir_file_options(workdir, interaction_model),
        "interaction_model": interaction_model,
        "interaction_incar": interaction_incar,
        "interaction_rows": interaction_rows,
    }


def _param_control_output_values(workdir: str, param_text: str, profile: str) -> Tuple[Any, ...]:
    controls = _param_controls_from_text(param_text, profile, workdir)
    if not controls:
        return (dash.no_update,) * 11
    return (
        controls["structures_options"],
        controls["structures_value"],
        controls["relax_value"],
        controls["property_options"],
        controls["property_value"],
        controls["interaction_type_options"],
        controls["interaction_type"],
        controls["interaction_model_options"],
        controls["interaction_model"],
        controls["interaction_incar"],
        controls["interaction_rows"],
    )


def _is_structure_candidate_dir(abs_dir: str) -> bool:
    for marker in ("POSCAR", "CONTCAR", "STRU"):
        if os.path.isfile(os.path.join(abs_dir, marker)):
            return True
    for marker in ("POSCAR", "CONTCAR", "STRU"):
        if glob.glob(os.path.join(abs_dir, "conf_*", marker)):
            return True
    return False


def _structure_wildcard_options(structure_paths: List[str]) -> List[Dict[str, str]]:
    groups: Dict[Tuple[str, str], List[str]] = {}
    for rel_path in structure_paths:
        parent, name = os.path.split(rel_path)
        match = re.match(r"^(.*?)(\d+)$", name)
        if not match:
            continue
        prefix = match.group(1)
        if not prefix:
            continue
        groups.setdefault((parent, prefix), []).append(rel_path)

    options = []
    for (parent, prefix), members in sorted(groups.items()):
        if len(members) < 2:
            continue
        wildcard = f"{prefix}*"
        rel_wildcard = f"{parent}/{wildcard}" if parent else wildcard
        options.append({"label": rel_wildcard, "value": rel_wildcard})
    return options


def _list_structure_path_options(workdir: str, current_values: Optional[List[str]] = None) -> List[Dict[str, str]]:
    target_dir = _normalize_workdir(workdir)
    options: List[Dict[str, str]] = []
    seen = set()
    structure_paths: List[str] = []

    if os.path.isdir(target_dir):
        for root, dirnames, _filenames in os.walk(target_dir):
            dirnames[:] = sorted(name for name in dirnames if not name.startswith("."))
            rel_root = os.path.relpath(root, target_dir)
            if rel_root == ".":
                continue
            rel_path = rel_root.replace(os.path.sep, "/")
            if "." in rel_path:
                continue
            if _is_structure_candidate_dir(root):
                structure_paths.append(rel_path)

    for wildcard_option in _structure_wildcard_options(structure_paths):
        if wildcard_option["value"] not in seen:
            options.append(wildcard_option)
            seen.add(wildcard_option["value"])

    for rel_path in structure_paths:
        if rel_path not in seen:
            options.append({"label": rel_path, "value": rel_path})
            seen.add(rel_path)

    for current in current_values or []:
        clean_current = (current or "").strip()
        if clean_current and clean_current not in seen:
            options.insert(0, {"label": f"{clean_current} (current)", "value": clean_current})
            seen.add(clean_current)
    return options


def _save_uploaded_files(contents: Any, filenames: Any, workdir: str, target_subdir: str = "confs") -> List[str]:
    if not contents:
        return []

    if isinstance(contents, str):
        content_items = [contents]
    else:
        content_items = list(contents)

    if isinstance(filenames, str):
        filename_items = [filenames]
    else:
        filename_items = list(filenames or [])

    if len(filename_items) != len(content_items):
        raise ValueError("Uploaded file metadata is incomplete.")

    target_dir = _normalize_workdir(workdir)
    if not os.path.isdir(target_dir):
        raise FileNotFoundError(f"Workdir does not exist: {target_dir}")
    upload_root = os.path.join(target_dir, target_subdir)
    os.makedirs(upload_root, exist_ok=True)

    saved_files: List[str] = []

    def _safe_join_under(root_path: str, rel_unix_path: str) -> str:
        normalized = rel_unix_path.replace("\\", "/")
        path_parts = [part for part in normalized.split("/") if part and part != "."]
        if not path_parts or any(part == ".." for part in path_parts):
            raise ValueError(f"Unsafe path in uploaded archive: {rel_unix_path}")
        candidate = os.path.join(root_path, *path_parts)
        root_real = os.path.realpath(root_path)
        candidate_real = os.path.realpath(candidate)
        if os.path.commonpath([root_real, candidate_real]) != root_real:
            raise ValueError(f"Unsafe path in uploaded archive: {rel_unix_path}")
        return candidate

    def _extract_archive(payload: bytes, raw_filename: str, upload_root_path: str) -> List[str]:
        extracted_rel_paths: List[str] = []
        lowered = raw_filename.lower()
        if lowered.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(payload)) as zf:
                for info in zf.infolist():
                    rel_name = info.filename.replace("\\", "/")
                    if info.is_dir() or not rel_name.strip():
                        continue
                    target_path = _safe_join_under(upload_root_path, rel_name)
                    os.makedirs(os.path.dirname(target_path), exist_ok=True)
                    with zf.open(info, "r") as src, open(target_path, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    extracted_rel_paths.append(
                        os.path.relpath(target_path, target_dir).replace(os.path.sep, "/")
                    )
            return extracted_rel_paths

        if lowered.endswith(".tar") or lowered.endswith(".tar.gz") or lowered.endswith(".tgz"):
            with tarfile.open(fileobj=io.BytesIO(payload), mode="r:*") as tf:
                for member in tf.getmembers():
                    rel_name = member.name.replace("\\", "/")
                    if not member.isfile() or not rel_name.strip():
                        continue
                    target_path = _safe_join_under(upload_root_path, rel_name)
                    os.makedirs(os.path.dirname(target_path), exist_ok=True)
                    src = tf.extractfile(member)
                    if src is None:
                        continue
                    with src, open(target_path, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    extracted_rel_paths.append(
                        os.path.relpath(target_path, target_dir).replace(os.path.sep, "/")
                    )
            return extracted_rel_paths

        return []

    for raw_content, raw_name in zip(content_items, filename_items):
        raw_filename = (raw_name or "").strip().replace("\\", "/")
        path_parts = [part for part in raw_filename.split("/") if part]
        if not path_parts:
            raise ValueError("Uploaded filename is empty.")
        if any(part in {".", ".."} for part in path_parts):
            raise ValueError(f"Unsafe uploaded filename: {raw_name}")
        rel_path = os.path.join(*path_parts)
        filename = path_parts[-1]
        if "," not in raw_content:
            raise ValueError(f"Invalid upload payload for {filename}.")

        _header, encoded = raw_content.split(",", 1)
        try:
            payload = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError(f"Invalid base64 payload for {filename}: {exc}") from exc

        extracted = _extract_archive(payload, filename, upload_root)
        if extracted:
            saved_files.extend(extracted)
            continue

        target_path = os.path.join(upload_root, rel_path)
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        with open(target_path, "wb") as f:
            f.write(payload)
        saved_files.append(os.path.join(target_subdir, rel_path).replace(os.path.sep, "/"))

    return saved_files


def _read_latest_workflow_id(workdir: str) -> str:
    log_path = os.path.join(workdir, ".workflow.log")
    if not os.path.isfile(log_path):
        return ""
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return ""
    if not lines:
        return ""
    record = lines[-1].strip()
    if not record:
        return ""
    return record.split("\t")[0].strip()


def _parse_workflow_ids(value: Any) -> List[str]:
    if isinstance(value, list):
        raw_items = value
    else:
        raw_items = re.split(r"[\s,]+", str(value or ""))
    workflow_ids: List[str] = []
    for item in raw_items:
        clean = str(item or "").strip()
        if clean and clean not in workflow_ids:
            workflow_ids.append(clean)
    return workflow_ids


def _format_workflow_ids(workflow_ids: List[str]) -> str:
    return ", ".join([item for item in workflow_ids if item])


def _workflow_log_records(workdir: str) -> List[Dict[str, str]]:
    log_path = os.path.join(workdir, ".workflow.log")
    if not os.path.isfile(log_path):
        return []
    records: List[Dict[str, str]] = []
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 4:
                    continue
                records.append(
                    {
                        "workflow_id": parts[0].strip(),
                        "operation": parts[1].strip(),
                        "timestamp": parts[2].strip(),
                        "workdir": parts[3].strip(),
                        "workflow_uid": parts[4].strip() if len(parts) > 4 else "",
                    }
                )
    except OSError:
        return []
    return records


def _count_workflow_log_records(workdir: str) -> int:
    return len(_workflow_log_records(workdir))


def _submit_meta_path(workdir: str) -> str:
    return os.path.join(_normalize_workdir(workdir), SUBMIT_GROUP_META_FILE)


def _load_submit_group_metadata(workdir: str) -> Dict[str, Any]:
    meta_path = _submit_meta_path(workdir)
    if not os.path.isfile(meta_path):
        return {}
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_submit_group_metadata(workdir: str, payload: Dict[str, Any]) -> None:
    meta_path = _submit_meta_path(workdir)
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=4, ensure_ascii=False)
        f.write("\n")


def _sanitize_workflow_label_value(value: str, fallback: str = "apex-gui") -> str:
    clean = re.sub(r"[^a-z0-9.-]+", "-", str(value or "").strip().lower()).strip("-.")
    return clean[:63] or fallback


def _build_submit_group_id() -> str:
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    suffix = uuid.uuid4().hex[:8]
    return f"apex-gui-{timestamp}-{suffix}"


def _derive_batch_param_filename(param_file: str, index: int, total: int) -> str:
    base, ext = os.path.splitext(param_file)
    suffix = f".batch-{index:03d}-of-{total:03d}"
    if ext:
        return f"{base}{suffix}{ext}"
    return f"{param_file}{suffix}.json"


def _resolve_structures_for_submit(workdir: str, structures: List[str]) -> List[str]:
    try:
        from apex.submit import _glob_structures_in_work_dir
    except Exception as exc:
        raise RuntimeError(f"Failed to load submit structure resolver: {exc}") from exc

    resolved: List[str] = []
    for pattern in structures or []:
        matches = _glob_structures_in_work_dir(workdir, pattern)
        for match in matches:
            if match not in resolved:
                resolved.append(match)
    return resolved


def _build_submit_batches(
    workdir: str,
    param_payload: Dict[str, Any],
    param_file: str,
    batch_size: int = SUBMIT_BATCH_SIZE,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    resolved_structures = _resolve_structures_for_submit(workdir, param_payload.get("structures", []))
    if not resolved_structures:
        raise RuntimeError(
            f"No structures matched the submitted patterns under {workdir}: "
            f"{param_payload.get('structures', [])}"
        )

    batches: List[Dict[str, Any]] = []
    total_batches = max(1, (len(resolved_structures) + batch_size - 1) // batch_size)
    for batch_index in range(total_batches):
        batch_structures = resolved_structures[batch_index * batch_size:(batch_index + 1) * batch_size]
        batch_payload = copy.deepcopy(param_payload)
        batch_payload["structures"] = batch_structures
        batch_file = param_file if total_batches == 1 else _derive_batch_param_filename(param_file, batch_index + 1, total_batches)
        batches.append(
            {
                "index": batch_index + 1,
                "param_file": batch_file,
                "structures": batch_structures,
                "payload": batch_payload,
            }
        )
    return batches, resolved_structures


def _merge_workflow_ids(*groups: Any) -> List[str]:
    merged: List[str] = []
    for group in groups:
        for workflow_id in _parse_workflow_ids(group):
            if workflow_id not in merged:
                merged.append(workflow_id)
    return merged


def _discover_group_workflow_ids(workdir: str, meta: Dict[str, Any]) -> List[str]:
    if not meta:
        return []
    records = _workflow_log_records(workdir)
    start_index = int(meta.get("workflow_log_line_start", 0) or 0)
    expected = int(meta.get("expected_batches", 0) or 0)
    normalized_workdir = _normalize_workdir(workdir)
    discovered: List[str] = []
    for record in records[start_index:]:
        if _normalize_workdir(record.get("workdir", "")) != normalized_workdir:
            continue
        workflow_id = record.get("workflow_id", "").strip()
        if workflow_id and workflow_id not in discovered:
            discovered.append(workflow_id)
        if expected and len(discovered) >= expected:
            break
    return discovered


def _sync_submit_group_metadata_ids(workdir: str, meta: Dict[str, Any]) -> Dict[str, Any]:
    if not meta:
        return {}
    discovered = _discover_group_workflow_ids(workdir, meta)
    stored = _parse_workflow_ids(meta.get("workflow_ids", []))
    merged = _merge_workflow_ids(stored, discovered)
    if merged != stored:
        meta = copy.deepcopy(meta)
        meta["workflow_ids"] = merged
        _save_submit_group_metadata(workdir, meta)
    return meta


def _tail_text_file(path: str, max_chars: int = 1600) -> str:
    if not path or not os.path.isfile(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError:
        return ""
    return content[-max_chars:].strip()


def _summarize_step_progress(steps: List[Any]) -> Dict[str, int]:
    total = 0
    running = 0
    finished = 0
    for step in steps or []:
        if isinstance(step, dict):
            step_type = step.get("type")
            phase = str(step.get("phase", ""))
        else:
            step_type = getattr(step, "type", "")
            phase = str(getattr(step, "phase", ""))
        if step_type == "StepGroup":
            continue
        total += 1
        phase_norm = phase.lower()
        if phase_norm in {"succeeded", "skipped", "omitted"}:
            finished += 1
        elif phase_norm in {"running", "pending"}:
            running += 1
    return {"total": total, "running": running, "finished": finished}


def _step_key(step: Any) -> str:
    if isinstance(step, dict):
        return str(step.get("key") or "")
    return str(getattr(step, "key", "") or "")


def _step_phase(step: Any) -> str:
    if isinstance(step, dict):
        return str(step.get("phase") or "")
    return str(getattr(step, "phase", "") or "")


def _step_parameter(step: Any, name: str) -> str:
    inputs = step.get("inputs", {}) if isinstance(step, dict) else getattr(step, "inputs", None)
    parameters = inputs.get("parameters", {}) if isinstance(inputs, dict) else getattr(inputs, "parameters", {})
    if isinstance(parameters, dict):
        item = parameters.get(name)
        if isinstance(item, dict):
            return str(item.get("value") or "")
        return str(getattr(item, "value", item) or "")
    for item in parameters or []:
        if isinstance(item, dict) and item.get("name") == name:
            return str(item.get("value") or "")
        if getattr(item, "name", None) == name:
            return str(getattr(item, "value", "") or "")
    return ""


def _conf_id_from_step(step: Any) -> str:
    key = _step_key(step)
    if key.startswith("relaxcal-"):
        return _step_parameter(step, "flow_id") or key[len("relaxcal-"):]
    if key.startswith("propertycal-"):
        path_to_prop = _step_parameter(step, "path_to_prop")
        if path_to_prop:
            return os.path.dirname(path_to_prop).replace(os.path.sep, "/")
        flow_id = _step_parameter(step, "flow_id")
        if flow_id and "-" in flow_id:
            return "-".join(flow_id.split("-")[:-2]) or flow_id
        return key[len("propertycal-"):]
    return ""


def _summarize_conf_progress(steps: List[Any]) -> Dict[str, int]:
    conf_phases: Dict[str, List[str]] = {}
    for step in steps or []:
        conf_id = _conf_id_from_step(step)
        if not conf_id:
            continue
        conf_phases.setdefault(conf_id, []).append(_step_phase(step).lower())

    finished = 0
    running = 0
    failed = 0
    for phases in conf_phases.values():
        if any(phase in {"failed", "error"} for phase in phases):
            failed += 1
        elif all(phase in {"succeeded", "skipped", "omitted"} for phase in phases):
            finished += 1
        else:
            running += 1
    return {
        "conf_total": len(conf_phases),
        "conf_running": running,
        "conf_finished": finished,
        "conf_failed": failed,
    }


def _query_workflow_progress(workflow_id: str, config_file: str) -> Tuple[Dict[str, int], str, str]:
    try:
        from dflow import Workflow
    except Exception as exc:
        raise RuntimeError(f"dflow is unavailable: {exc}") from exc

    _configure_dflow_from_config(config_file)

    wf = Workflow(id=workflow_id)
    info = wf.query()
    steps = info.get_step()
    counts = _summarize_step_progress(steps)
    counts.update(_summarize_conf_progress(steps))
    workflow_phase = str(getattr(getattr(info, "status", None), "phase", "Unknown"))
    progress_text = str(getattr(getattr(info, "status", None), "progress", ""))
    return counts, workflow_phase, progress_text


def _configure_dflow_from_config(config_file: str) -> None:
    from apex.config import Config
    from apex.utils import load_config_file

    config_dict = load_config_file(config_file)
    wf_config = Config(**config_dict)
    Config.config_dflow(wf_config.dflow_config_dict)
    Config.config_bohrium(wf_config.bohrium_config_dict)
    Config.config_s3(wf_config.dflow_s3_config_dict)


def _query_workflow_phase_progress(workflow_id: str, config_file: str) -> Tuple[str, str]:
    try:
        from dflow import Workflow
    except Exception as exc:
        raise RuntimeError(f"dflow is unavailable: {exc}") from exc

    _configure_dflow_from_config(config_file)
    wf = Workflow(id=workflow_id)
    info = wf.query(fields=["status.phase", "status.progress"])
    status = getattr(info, "status", None)
    workflow_phase = str(getattr(status, "phase", "Unknown"))
    progress_text = str(getattr(status, "progress", "") or "")
    return workflow_phase, progress_text


def _workflow_query_subprocess_code() -> str:
    return (
        "import json, sys\n"
        "from apex.gui import (\n"
        "    WORKFLOW_QUERY_RESULT_PREFIX,\n"
        "    _query_workflow_phase_progress,\n"
        "    _query_workflow_progress,\n"
        ")\n"
        "kind, workflow_id, config_file = sys.argv[1:4]\n"
        "try:\n"
        "    if kind == 'phase':\n"
        "        payload = _query_workflow_phase_progress(workflow_id, config_file)\n"
        "    elif kind == 'detail':\n"
        "        payload = _query_workflow_progress(workflow_id, config_file)\n"
        "    else:\n"
        "        raise ValueError(f'unknown workflow query kind: {kind}')\n"
        "    result = {'ok': True, 'payload': payload}\n"
        "except BaseException as exc:\n"
        "    result = {'ok': False, 'error': f'{type(exc).__name__}: {exc}'}\n"
        "print(WORKFLOW_QUERY_RESULT_PREFIX + json.dumps(result))\n"
    )


def _parse_workflow_query_subprocess_output(stdout: str, stderr: str, returncode: int):
    for line in reversed((stdout or "").splitlines()):
        if line.startswith(WORKFLOW_QUERY_RESULT_PREFIX):
            payload = json.loads(line[len(WORKFLOW_QUERY_RESULT_PREFIX):])
            if payload.get("ok"):
                return payload.get("payload")
            raise RuntimeError(payload.get("error") or "workflow query failed")
    tail = (stderr or stdout or "").strip()
    if len(tail) > 1000:
        tail = tail[-1000:]
    raise RuntimeError(f"workflow query exited without a result (code {returncode}): {tail}")


def _workflow_query_command(kind: str, workflow_id: str, config_file: str) -> List[str]:
    return [
        sys.executable,
        "-c",
        _workflow_query_subprocess_code(),
        kind,
        workflow_id,
        config_file,
    ]


def _run_query_subprocess_with_timeout(kind: str, workflow_id: str, config_file: str, timeout: int):
    try:
        completed = subprocess.run(
            _workflow_query_command(kind, workflow_id, config_file),
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(f"workflow progress query timed out after {timeout}s") from exc
    return _parse_workflow_query_subprocess_output(
        completed.stdout,
        completed.stderr,
        completed.returncode,
    )


def _query_workflow_phase_progress_with_timeout(
        workflow_id: str,
        config_file: str,
        timeout: int = WORKFLOW_QUICK_QUERY_TIMEOUT_SECONDS,
) -> Tuple[str, str]:
    return tuple(_run_query_subprocess_with_timeout(
        "phase",
        workflow_id,
        config_file,
        timeout,
    ))


def _query_workflow_progress_with_timeout(
        workflow_id: str,
        config_file: str,
        timeout: int = WORKFLOW_PROGRESS_QUERY_TIMEOUT_SECONDS,
) -> Tuple[Dict[str, int], str, str]:
    payload = _run_query_subprocess_with_timeout(
        "detail",
        workflow_id,
        config_file,
        timeout,
    )
    counts, workflow_phase, progress_text = payload
    return counts, workflow_phase, progress_text


def _query_many_workflow_phase_progress(workflow_ids: List[str], config_file: str) -> Dict[str, Tuple[str, str]]:
    results: Dict[str, Tuple[str, str]] = {}
    for workflow_id in workflow_ids:
        results[workflow_id] = _query_workflow_phase_progress(workflow_id, config_file)
    return results


def _query_many_workflow_phase_progress_with_timeout(
        workflow_ids: List[str],
        config_file: str,
        timeout: Optional[int] = None,
) -> Dict[str, Tuple[str, str]]:
    safe_ids = _parse_workflow_ids(workflow_ids)
    if not safe_ids:
        return {}
    payload = (
        "import json, sys\n"
        "from apex.gui import WORKFLOW_QUERY_RESULT_PREFIX, _query_many_workflow_phase_progress\n"
        "workflow_ids = json.loads(sys.argv[1])\n"
        "config_file = sys.argv[2]\n"
        "try:\n"
        "    result = {'ok': True, 'payload': _query_many_workflow_phase_progress(workflow_ids, config_file)}\n"
        "except BaseException as exc:\n"
        "    result = {'ok': False, 'error': f'{type(exc).__name__}: {exc}'}\n"
        "print(WORKFLOW_QUERY_RESULT_PREFIX + json.dumps(result))\n"
    )
    try:
        completed = subprocess.run(
            [sys.executable, "-c", payload, json.dumps(safe_ids), config_file],
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout or max(WORKFLOW_QUICK_QUERY_TIMEOUT_SECONDS, min(20, len(safe_ids) * 3)),
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(f"workflow progress query timed out after {timeout or max(WORKFLOW_QUICK_QUERY_TIMEOUT_SECONDS, min(20, len(safe_ids) * 3))}s") from exc
    raw_payload = _parse_workflow_query_subprocess_output(
        completed.stdout,
        completed.stderr,
        completed.returncode,
    )
    if not isinstance(raw_payload, dict):
        return {}
    return {
        workflow_id: tuple(value)
        for workflow_id, value in raw_payload.items()
        if workflow_id in safe_ids and isinstance(value, (list, tuple)) and len(value) == 2
    }


def _workflow_progress_percent(progress_text: str) -> int:
    match = re.search(r"(\d+)\s*/\s*(\d+)", progress_text or "")
    if not match:
        return 0
    current = int(match.group(1))
    total = int(match.group(2))
    if total <= 0:
        return 0
    return max(0, min(100, int(round((current / total) * 100))))


def _workflow_progress_fraction(progress_text: str) -> Tuple[int, int]:
    match = re.search(r"(\d+)\s*/\s*(\d+)", progress_text or "")
    if not match:
        return 0, 0
    current = int(match.group(1))
    total = int(match.group(2))
    if total <= 0:
        return 0, 0
    return current, total


def _aggregate_progress_fraction(progress_texts: List[str]) -> Tuple[int, int, int]:
    current_total = 0
    grand_total = 0
    for progress_text in progress_texts or []:
        current, total = _workflow_progress_fraction(progress_text)
        current_total += current
        grand_total += total
    if grand_total <= 0:
        return 0, 0, 0
    percent = max(0, min(100, int(round((current_total / grand_total) * 100))))
    return current_total, grand_total, percent


def _workflow_detail_cache_key(workflow_id: str, config_file: str) -> Tuple[str, str]:
    return workflow_id, os.path.abspath(config_file)


def _start_workflow_detail_query(entry: Dict[str, Any], workflow_id: str, config_file: str, now: float) -> None:
    process = subprocess.Popen(
        _workflow_query_command("detail", workflow_id, config_file),
        cwd=os.getcwd(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    entry["process"] = process
    entry["started_at"] = now
    entry["last_start_at"] = now
    entry["running"] = True


def _poll_workflow_detail_cache(entry: Dict[str, Any], now: float) -> None:
    process = entry.get("process")
    if process is None:
        return
    if process.poll() is None:
        started_at = entry.get("started_at", now)
        if now - started_at > WORKFLOW_DETAIL_QUERY_TIMEOUT_SECONDS:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
            entry["error"] = f"detail query timed out after {WORKFLOW_DETAIL_QUERY_TIMEOUT_SECONDS}s"
            entry["running"] = False
            entry.pop("process", None)
        return
    try:
        stdout, stderr = process.communicate(timeout=1)
        payload = _parse_workflow_query_subprocess_output(stdout, stderr, process.returncode)
        counts, workflow_phase, raw_progress = payload
        entry["counts"] = counts
        entry["workflow_phase"] = workflow_phase
        entry["raw_progress"] = raw_progress
        entry["updated_at"] = now
        entry.pop("error", None)
    except Exception as exc:
        entry["error"] = str(exc)
    entry["running"] = False
    entry.pop("process", None)


def _get_workflow_detail_cache(workflow_id: str, config_file: str, now: Optional[float] = None) -> Dict[str, Any]:
    timestamp = time.time() if now is None else now
    key = _workflow_detail_cache_key(workflow_id, config_file)
    entry = _WORKFLOW_DETAIL_CACHE.setdefault(key, {})
    _poll_workflow_detail_cache(entry, timestamp)
    last_start_at = entry.get("last_start_at", 0.0)
    if not entry.get("running") and timestamp - last_start_at >= WORKFLOW_DETAIL_REFRESH_SECONDS:
        _start_workflow_detail_query(entry, workflow_id, config_file, timestamp)
    return entry


def _build_submit_shell_command(meta_path: str, cwd: str) -> Tuple[str, str]:
    log_file = os.path.join(cwd, "apex.log")
    status_file = os.path.join(cwd, SUBMIT_STATUS_FILE)
    submit_inner = (
        f"{shlex.quote(sys.executable)} -m apex.gui_background submit-group "
        f"{shlex.quote(meta_path)} {shlex.quote(log_file)} {shlex.quote(status_file)}"
    )
    display_cmd = f"nohup {submit_inner} > /dev/null 2>&1 &"
    shell_cmd = (
        f"rm -f {shlex.quote(status_file)}; "
        f"nohup bash -lc {shlex.quote(submit_inner)} >/dev/null 2>&1 & echo $!"
    )
    return shell_cmd, display_cmd


def _run_submit_in_background(meta_path: str, cwd: Optional[str] = None) -> Dict[str, Any]:
    target_cwd = cwd or os.getcwd()
    shell_cmd, display_cmd = _build_submit_shell_command(meta_path, target_cwd)
    try:
        completed = subprocess.run(
            ["bash", "-lc", shell_cmd],
            cwd=target_cwd,
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception as exc:
        return {
            "ok": False,
            "message": f"Failed to start background submit: {exc}",
            "command": display_cmd,
            "returncode": "",
            "stdout": "",
            "stderr": str(exc),
            "finished_at": datetime.now().isoformat(timespec="seconds"),
        }

    pid = completed.stdout.strip().splitlines()[-1] if completed.stdout.strip() else ""
    message = "Submit started in background."
    if pid:
        message += f" PID: {pid}."
    message += f" Submit metadata: {os.path.basename(meta_path)}. Log file: apex.log"

    return {
        "ok": completed.returncode == 0,
        "message": message if completed.returncode == 0 else "Background submit failed to start.",
        "operation": "submit",
        "command": display_cmd,
        "returncode": str(completed.returncode),
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "status_file": os.path.join(target_cwd, SUBMIT_STATUS_FILE),
    }


def _ensure_default_interaction_files(
    profile: str,
    param_payload: Dict[str, Any],
    incar_content: str = None,
    workdir: Optional[str] = None,
) -> List[str]:
    created_paths: List[str] = []
    interaction = param_payload.get("interaction")
    if not isinstance(interaction, dict):
        return created_paths

    interaction_key = _interaction_path_key(profile)
    incar_value = interaction.get(interaction_key) or interaction.get("incar")
    if isinstance(incar_value, str) and incar_value.strip():
        clean_incar = _strip_parenthetical_suffix(incar_value)
        interaction[interaction_key] = clean_incar
        if interaction_key == "input":
            interaction.pop("incar", None)
        target_path = os.path.join(_normalize_workdir(workdir or os.getcwd()), clean_incar)
        parent_dir = os.path.dirname(target_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)

        if incar_content is not None:
            with open(target_path, "w", encoding="utf-8") as f:
                if incar_content and not incar_content.endswith("\n"):
                    f.write(f"{incar_content}\n")
                else:
                    f.write(incar_content or "")
            created_paths.append(clean_incar)
        else:
            source_path = os.path.join(_profile_dir(profile), "param_interaction", clean_incar)
            if os.path.isfile(source_path) and not os.path.exists(target_path):
                shutil.copyfile(source_path, target_path)
                created_paths.append(clean_incar)

    for map_key in ("potcars", "orb_files"):
        value_map = interaction.get(map_key)
        if not isinstance(value_map, dict):
            continue
        interaction[map_key] = {
            str(k): _strip_parenthetical_suffix(str(v)) for k, v in value_map.items()
        }

    return created_paths


def _parse_submit_payloads(submit_global_editor: str, submit_param_editor: str) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    try:
        global_payload = json.loads(submit_global_editor or "{}")
    except json.JSONDecodeError as exc:
        return {}, {}, _build_feedback(f"global.json JSON 格式错误: {exc}")

    try:
        param_payload = json.loads(submit_param_editor or "{}")
    except json.JSONDecodeError as exc:
        return {}, {}, _build_feedback(f"param.json JSON 格式错误: {exc}")

    if not isinstance(global_payload, dict):
        return {}, {}, _build_feedback("global.json 必须是 JSON object")
    if not isinstance(param_payload, dict):
        return {}, {}, _build_feedback("param.json 必须是 JSON object")

    try:
        from apex.submit import validate_submit_paths

        validate_submit_paths([param_payload])
    except Exception as exc:
        return {}, {}, _build_feedback(str(exc))

    return global_payload, param_payload, {}


def _write_submit_json_files(
    global_payload: Dict[str, Any],
    param_payload: Dict[str, Any],
    workdir: str,
    global_filename: str,
    param_filename: str,
) -> Tuple[str, str]:
    global_path = _resolve_file_path(workdir, global_filename)
    param_path = _resolve_file_path(workdir, param_filename)
    if not global_path or not param_path:
        raise RuntimeError("global/param filename cannot be empty")

    global_parent = os.path.dirname(global_path)
    param_parent = os.path.dirname(param_path)
    if global_parent:
        os.makedirs(global_parent, exist_ok=True)
    if param_parent:
        os.makedirs(param_parent, exist_ok=True)

    with open(global_path, "w", encoding="utf-8") as f:
        json.dump(global_payload, f, indent=4, ensure_ascii=False)
        f.write("\n")
    with open(param_path, "w", encoding="utf-8") as f:
        json.dump(param_payload, f, indent=4, ensure_ascii=False)
        f.write("\n")
    return global_path, param_path


def _cleanup_reset_logs(workdir: str, filenames: Optional[List[str]] = None) -> List[str]:
    target_dir = _normalize_workdir(workdir)
    removed: List[str] = []
    meta = _load_submit_group_metadata(target_dir)
    base_param_file = str(meta.get("param_file", "") or "")
    batch_param_files = [
        item.get("param_file", "")
        for item in meta.get("submit_jobs", [])
        if isinstance(item, dict) and item.get("param_file") and item.get("param_file") != base_param_file
    ]
    for name in filenames or ["dpdispatcher.log", ".workflow.log", "apex.log", "apex-report.log", ".apex-submit.status",".apex-retrieve.status","apex-retrieve.log"]:
        target_path = os.path.join(target_dir, name)
        if os.path.isfile(target_path):
            os.remove(target_path)
            removed.append(name)
    meta_path = _submit_meta_path(target_dir)
    if os.path.isfile(meta_path):
        os.remove(meta_path)
        removed.append(os.path.basename(meta_path))
    for rel_path in batch_param_files:
        target_path = _resolve_file_path(target_dir, rel_path)
        if target_path and os.path.isfile(target_path):
            os.remove(target_path)
            removed.append(rel_path)
    return removed


def _run_report_in_background(
    config_file: str,
    report_target: str,
    cwd: str,
    port: int = DEFAULT_REPORT_PORT,
) -> Dict[str, Any]:
    apex_bin = shutil.which("apex")
    if apex_bin:
        report_inner = (
            f"{shlex.quote(apex_bin)} report --no-browser -c {shlex.quote(config_file)} "
            f"-w {shlex.quote(report_target)} --port {port}"
        )
        display_cmd = (
            f"nohup apex report --no-browser -c {shlex.quote(config_file)} "
            f"-w {shlex.quote(report_target)} --port {port} > apex-report.log 2>&1 &"
        )
    else:
        report_inner = (
            f"{shlex.quote(sys.executable)} -m apex report --no-browser -c {shlex.quote(config_file)} "
            f"-w {shlex.quote(report_target)} --port {port}"
        )
        display_cmd = f"nohup {report_inner} > apex-report.log 2>&1 &"
    shell_cmd = f"nohup {report_inner} > apex-report.log 2>&1 & echo $!"

    try:
        completed = subprocess.run(
            ["bash", "-lc", shell_cmd],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception as exc:
        return {
            "ok": False,
            "message": f"Failed to start background report: {exc}",
            "command": display_cmd,
            "returncode": "",
            "stdout": "",
            "stderr": str(exc),
            "finished_at": datetime.now().isoformat(timespec="seconds"),
        }

    pid = completed.stdout.strip().splitlines()[-1] if completed.stdout.strip() else ""
    message = f"Report app started in background on port {port}."
    if pid:
        message += f" PID: {pid}."
    message += " Log file: apex-report.log"
    report_url = f"http://{DEFAULT_HOST}:{port}"
    return {
        "ok": completed.returncode == 0,
        "message": message if completed.returncode == 0 else "Background report failed to start.",
        "command": display_cmd,
        "returncode": str(completed.returncode),
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "report_url": report_url,
    }


def _run_local_archive(workdir: str, global_file: str, param_file: str) -> Tuple[bool, str]:
    target_workdir = _normalize_workdir(workdir)
    all_result_path = os.path.join(target_workdir, "all_result.json")

    resolved_global = _resolve_file_path(target_workdir, global_file or "global.json")
    resolved_param = _resolve_file_path(target_workdir, param_file or "param.json")
    if not resolved_param or not os.path.isfile(resolved_param):
        return False, f"param file not found: {resolved_param or param_file or 'param.json'}"
    if not resolved_global or not os.path.isfile(resolved_global):
        return False, f"global file not found: {resolved_global or global_file or 'global.json'}"

    try:
        from apex.archive import archive_workdir
        from apex.config import Config
        from apex.utils import judge_flow, load_config_file
        from monty.serialization import loadfn
    except Exception as exc:
        return False, f"failed to load local archive helpers: {exc}"

    try:
        param_dict = loadfn(resolved_param)
        config_dict = load_config_file(resolved_global)
        cfg = Config(**config_dict)
        cfg.database_type = "local"
        _run_op, _calculator, flow_type, relax_param, props_param = judge_flow([param_dict], None)
        archive_workdir(
            relax_param=relax_param,
            props_param=props_param,
            config=cfg,
            work_dir=target_workdir,
            flow_type=flow_type,
        )
    except Exception as exc:
        return False, str(exc)

    if not os.path.isfile(all_result_path):
        return False, "archive_workdir completed but all_result.json is still missing"
    return True, all_result_path


def _ensure_local_all_result(workdir: str, global_file: str, param_file: str) -> Tuple[bool, str]:
    target_workdir = _normalize_workdir(workdir)
    all_result_path = os.path.join(target_workdir, "all_result.json")
    if os.path.isfile(all_result_path):
        return True, all_result_path
    return _run_local_archive(target_workdir, global_file, param_file)


def _run_archive_and_report_pipeline(workdir: str, global_file: str, param_file: str) -> Dict[str, Any]:
    ok, payload = _run_local_archive(workdir, global_file, param_file)
    if not ok:
        return _build_feedback(f"Local archive failed: {payload}")
    all_result_path = payload

    report_feedback = _run_report_in_background(global_file, workdir, cwd=workdir)
    if not report_feedback.get("ok"):
        report_feedback["message"] = f"Report failed. {report_feedback.get('message', '')}".strip()
        return report_feedback

    report_feedback["message"] = (
        f"Local archive + report completed. all_result.json: {all_result_path}. "
        f"{report_feedback.get('message', '')}"
    )
    return report_feedback


def _run_finalize_pipeline(workdir: str, workflow_id: str, global_file: str) -> Dict[str, Any]:
    retrieve_feedback = _run_apex_command(
        ["retrieve", "-i", workflow_id, "-w", workdir, "-c", global_file],
        cwd=workdir,
    )
    if not retrieve_feedback.get("ok"):
        retrieve_feedback["message"] = f"Retrieve failed. {retrieve_feedback.get('message', '')}".strip()
        return retrieve_feedback

    report_feedback = _run_archive_and_report_pipeline(workdir, global_file, "param.json")
    if report_feedback.get("ok"):
        report_feedback["message"] = report_feedback["message"].replace(
            "Local archive + report completed.",
            "Retrieve + local archive + report completed.",
            1,
        )
    return report_feedback


def _finalize_retrieve_status(state_payload: Dict[str, Any]) -> Tuple[int, str, bool, Any, Dict[str, Any], Dict[str, Any]]:
    retrieve_state = None
    if isinstance(state_payload, dict):
        if state_payload.get("status") == "running":
            retrieve_state = state_payload
        elif state_payload.get("status") == "done":
            return (
                100,
                "100%",
                False,
                state_payload.get("completed_text") or "Retrieve finished.",
                state_payload,
                dash.no_update,
            )
        else:
            retrieve_state = state_payload.get("retrieve")
    if not isinstance(retrieve_state, dict) or retrieve_state.get("status") != "running":
        workdir = state_payload.get("workdir") if isinstance(state_payload, dict) else ""
        workflow_id = state_payload.get("workflow_id") if isinstance(state_payload, dict) else ""
        log_file = os.path.join(_normalize_workdir(workdir or os.getcwd()), "apex-retrieve.log")
        if os.path.isfile(log_file):
            log_text = _read_log_tail(log_file, max_lines=200, workdir=None)
            if _retrieve_log_matches_workflow(log_file, workflow_id):
                progress = _parse_retrieve_progress_from_log(log_text)
                if progress:
                    value, label, text = progress
                    return value, label, True, text, state_payload, dash.no_update
        return 0, "0%", False, "Retrieve 未运行", {}, dash.no_update

    status_file = retrieve_state.get("status_file") or ""
    log_file = retrieve_state.get("log_file") or ""
    workdir = retrieve_state.get("workdir") or state_payload.get("workdir") or os.getcwd()
    global_file = retrieve_state.get("global_file") or state_payload.get("global_file") or "global.json"
    param_file = retrieve_state.get("param_file") or state_payload.get("param_file") or "param.json"
    pending_report = bool(retrieve_state.get("pending_report"))
    if not status_file or not os.path.isfile(status_file):
        log_text = _read_log_tail(log_file, max_lines=200, workdir=None) if log_file else ""
        progress = _parse_retrieve_progress_from_log(log_text)
        if progress:
            value, label, text = progress
            return value, label, True, text, retrieve_state, dash.no_update
        return 5, "Retrieving", True, RETRIEVE_RUNNING_MESSAGE, retrieve_state, dash.no_update

    try:
        with open(status_file, "r", encoding="utf-8") as f:
            return_code = f.read().strip()
    except OSError as exc:
        feedback = _build_feedback(f"Retrieve status read failed: {exc}")
        return 0, "Failed", False, "Retrieve 状态读取失败", {}, feedback

    if return_code != "0":
        log_tail = _read_log_tail(log_file, max_lines=120, workdir=None) if log_file else ""
        feedback = _build_feedback("Retrieve failed.")
        feedback["command"] = retrieve_state.get("command", "")
        feedback["returncode"] = return_code
        feedback["stderr"] = log_tail
        return 100, "Failed", False, "Retrieve failed. See apex-retrieve.log.", {}, feedback

    if not pending_report:
        completed_state = _completed_retrieve_state(
            workdir=workdir,
            workflow_id=retrieve_state.get("workflow_id", ""),
            workflow_ids=retrieve_state.get("workflow_ids", []),
            global_file=global_file,
            param_file=param_file,
            text="Retrieve finished.",
            log_file=log_file,
            status_file=status_file,
        )
        return 100, "Done", False, "Retrieve finished.", completed_state, dash.no_update

    report_feedback = _run_archive_and_report_pipeline(workdir, global_file, param_file)
    if not report_feedback.get("ok"):
        completed_state = _completed_retrieve_state(
            workdir=workdir,
            workflow_id=retrieve_state.get("workflow_id", ""),
            workflow_ids=retrieve_state.get("workflow_ids", []),
            global_file=global_file,
            param_file=param_file,
            text="Retrieve finished; report failed.",
            log_file=log_file,
            status_file=status_file,
        )
        return 100, "Done", False, "Retrieve finished; report failed.", completed_state, report_feedback

    report_feedback["message"] = report_feedback["message"].replace(
        "Local archive + report completed.",
        "Retrieve + local archive + report completed.",
        1,
    )
    completed_text = _report_started_children(report_feedback.get("report_url", ""))
    completed_state = _completed_retrieve_state(
        workdir=workdir,
        workflow_id=retrieve_state.get("workflow_id", ""),
        workflow_ids=retrieve_state.get("workflow_ids", []),
        global_file=global_file,
        param_file=param_file,
        text=completed_text,
        log_file=log_file,
        status_file=status_file,
        report_url=report_feedback.get("report_url", ""),
    )
    return 100, "100%", False, completed_text, completed_state, report_feedback


DEFAULT_GLOBAL_EDITOR_TEXT = _json_dump_text(_load_profile_global(DEFAULT_PROFILE))
DEFAULT_INTERACTION_ROWS = _interaction_table_rows_from_template(DEFAULT_PROFILE, DEFAULT_PARAM_TEMPLATE)
DEFAULT_INTERACTION_INCAR_CONTENT = _load_profile_incar_content(DEFAULT_PROFILE, DEFAULT_PARAM_TEMPLATE)
DEFAULT_PARAM_EDITOR_TEXT = _json_dump_text(
    _build_param_payload(
        profile=DEFAULT_PROFILE,
        selected_structures=DEFAULT_STRUCTURE_PATHS,
        with_relax="relaxation" in DEFAULT_PARAM_TEMPLATE,
        selected_properties=DEFAULT_SELECTED_PROPERTIES,
        interaction_type=DEFAULT_INTERACTION_TYPE,
        interaction_model=DEFAULT_INTERACTION_MODEL,
        interaction_incar=_extract_interaction_incar(DEFAULT_PARAM_TEMPLATE),
        interaction_rows=DEFAULT_INTERACTION_ROWS,
        base_template=DEFAULT_PARAM_TEMPLATE,
    )
)


class ApexGuiApp:
    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, open_browser: bool = True):
        self.host = host
        self.port = port
        self.open_browser = open_browser
        dbc_css = "https://cdn.jsdelivr.net/gh/AnnMarieW/dash-bootstrap-templates/dbc.min.css"
        self.app = dash.Dash(
            __name__,
            external_stylesheets=[dbc.themes.MATERIA, dbc_css],
            suppress_callback_exceptions=True,
        )
        self.app.title = "APEX GUI"
        self.app.layout = self._build_layout()
        self._register_callbacks()

    @staticmethod
    def _build_submit_tab() -> dbc.Tab:
        initial_workdir = os.getcwd()
        initial_param_file, initial_param_text = _find_param_fallback_file(initial_workdir)
        if not initial_param_file:
            initial_param_file = "param.json"
            initial_param_text = DEFAULT_PARAM_EDITOR_TEXT
        initial_controls = _param_controls_from_text(initial_param_text, DEFAULT_PROFILE, initial_workdir) or {}
        initial_property_options = initial_controls.get(
            "property_options",
            [{"label": name, "value": name} for name in DEFAULT_PROPERTY_TYPES],
        )
        initial_property_values = initial_controls.get("property_value", DEFAULT_SELECTED_PROPERTIES)
        initial_structures = initial_controls.get("structures_value", DEFAULT_STRUCTURE_PATHS)
        initial_structure_options = initial_controls.get(
            "structures_options",
            _list_structure_path_options(initial_workdir, DEFAULT_STRUCTURE_PATHS),
        )
        initial_relax_value = initial_controls.get("relax_value", ["relax"])
        initial_interaction_type = initial_controls.get("interaction_type", DEFAULT_INTERACTION_TYPE)
        initial_interaction_options = initial_controls.get(
            "interaction_type_options",
            _interaction_type_options_for_profile(DEFAULT_PROFILE, DEFAULT_INTERACTION_TYPE),
        )
        initial_interaction_model = initial_controls.get("interaction_model", DEFAULT_INTERACTION_MODEL)
        initial_interaction_model_options = initial_controls.get(
            "interaction_model_options",
            _list_workdir_file_options(initial_workdir, DEFAULT_INTERACTION_MODEL),
        )
        initial_interaction_incar = initial_controls.get(
            "interaction_incar",
            _extract_interaction_incar(DEFAULT_PARAM_TEMPLATE, DEFAULT_PROFILE),
        )
        initial_interaction_rows = initial_controls.get("interaction_rows", DEFAULT_INTERACTION_ROWS)

        return dbc.Tab(
            label="Submit",
            children=[
                dbc.Row(
                    [
                        dbc.Col(
                            [
                                html.H5("基础设置"),
                                dbc.Label("计算软件"),
                                dcc.Dropdown(
                                    id="submit-profile",
                                    clearable=False,
                                    value=DEFAULT_PROFILE,
                                    options=[
                                        {"label": "LAMMPS", "value": "lammps"},
                                        {"label": "VASP", "value": "vasp"},
                                        {"label": "ABACUS", "value": "abacus"},
                                    ],
                                ),
                                html.Br(),
                                dbc.Label("是否计算 Relax"),
                                dcc.Checklist(
                                    id="submit-relax-check",
                                    options=[{"label": "启用 relaxation", "value": "relax"}],
                                    value=initial_relax_value,
                                    inputStyle={"marginRight": "6px", "marginLeft": "10px"},
                                    labelStyle={"display": "inline-block"},
                                ),
                                html.Br(),
                                dbc.Label("Properties 勾选"),
                                dcc.Checklist(
                                    id="submit-properties-check",
                                    options=initial_property_options,
                                    value=initial_property_values,
                                    inputStyle={"marginRight": "6px", "marginLeft": "10px"},
                                    labelStyle={"display": "inline-block", "marginRight": "12px"},
                                ),
                                html.Br(),
                                html.Div(
                                    id="submit-interaction-type-block",
                                    children=[
                                        dbc.Label("interaction.type"),
                                        dcc.Dropdown(
                                            id="submit-interaction-type",
                                            clearable=False,
                                            value=initial_interaction_type,
                                            options=initial_interaction_options,
                                        ),
                                    ],
                                ),
                                html.Br(),
                                html.Div(
                                    id="submit-lammps-interaction-block",
                                    style={"display": "block"},
                                    children=[
                                        dbc.Label("interaction.model"),
                                        dcc.Dropdown(
                                            id="submit-interaction-model",
                                            options=initial_interaction_model_options,
                                            value=initial_interaction_model,
                                            placeholder="选择当前 Workdir 中的模型文件",
                                            clearable=True,
                                            searchable=True,
                                        ),
                                        html.Small("从当前 Workdir 选择模型文件。上传后列表会自动刷新。", className="text-muted"),
                                    ],
                                ),
                                html.Div(
                                    id="submit-electronic-interaction-block",
                                    style={"display": "none"},
                                    children=[
                                        dbc.Label("interaction 列表（动态增删行）"),
                                        dbc.Row(
                                            [
                                                dbc.Col(
                                                    dbc.Button("加一行", id="submit-row-add", color="secondary", className="me-2"),
                                                    width="auto",
                                                ),
                                                dbc.Col(
                                                    dbc.Button("删一行", id="submit-row-del", color="secondary"),
                                                    width="auto",
                                                ),
                                            ],
                                            className="g-2 mb-2",
                                        ),
                                        html.Small(
                                            "VASP: Element + POTCAR；ABACUS: Element + POTCAR + ORB",
                                            className="text-muted",
                                        ),
                                        dash_table.DataTable(
                                            id="submit-interaction-table",
                                            columns=_interaction_table_columns_for_profile(DEFAULT_PROFILE),
                                            data=initial_interaction_rows,
                                            editable=True,
                                            row_deletable=True,
                                            style_data_conditional=[
                                                {
                                                    "if": {"filter_query": f'{{potcar}} = "{MISSING_POTCAR_HINT}"', "column_id": "potcar"},
                                                    "color": "#7a7a7a",
                                                },
                                                {
                                                    "if": {"filter_query": f'{{orb_file}} = "{MISSING_ORB_HINT}"', "column_id": "orb_file"},
                                                    "color": "#7a7a7a",
                                                },
                                            ],
                                            style_table={"overflowX": "auto"},
                                            style_cell={"textAlign": "left", "padding": "6px"},
                                        ),
                                    ],
                                ),
                                html.Br(),
                                dbc.Label("工作目录 (Workdir)"),
                                dbc.Input(
                                    id="submit-workdir",
                                    value=os.getcwd(),
                                    placeholder="/path/to/workdir",
                                ),
                                html.Br(),
                                dbc.Label("structures"),
                                dcc.Dropdown(
                                    id="submit-structures",
                                    options=initial_structure_options,
                                    value=initial_structures,
                                    placeholder="选择结构目录",
                                    multi=True,
                                    searchable=True,
                                ),
                                html.Small("从当前 Workdir 选择结构目录, 请注意不要选择文件", className="text-muted"),
                                html.Br(),
                                dbc.Label("上传结构"),
                                dcc.Upload(
                                    id="submit-structure-upload",
                                    multiple=True,
                                    children=html.Div(
                                        [
                                            "拖拽结构文件/文件夹到这里，或 ",
                                            html.A("点击选择文件"),
                                        ]
                                    ),
                                    style={
                                        "width": "100%",
                                        "minHeight": "72px",
                                        "lineHeight": "72px",
                                        "borderWidth": "1px",
                                        "borderStyle": "dashed",
                                        "borderRadius": "6px",
                                        "textAlign": "center",
                                        "backgroundColor": "#fafafa",
                                    },
                                ),
                                html.Small(
                                    "结构文件会上传到当前 Workdir 下的 confs/；支持目录拖拽，或上传 zip/tar(.gz/.tgz) 自动解压。",
                                    className="text-muted",
                                ),
                                html.Br(),
                                dbc.Label("上传文件"),
                                dcc.Upload(
                                    id="submit-file-upload",
                                    multiple=True,
                                    children=html.Div(
                                        [
                                            "拖拽文件/文件夹到这里，或 ",
                                            html.A("点击选择文件"),
                                        ]
                                    ),
                                    style={
                                        "width": "100%",
                                        "minHeight": "72px",
                                        "lineHeight": "72px",
                                        "borderWidth": "1px",
                                        "borderStyle": "dashed",
                                        "borderRadius": "6px",
                                        "textAlign": "center",
                                        "backgroundColor": "#fafafa",
                                    },
                                ),
                                html.Small(
                                    "普通文件会上传到当前 Working Directory；支持目录拖拽，或上传 zip/tar(.gz/.tgz) 自动解压。",
                                    className="text-muted",
                                ),
                                html.Br(),
                                dbc.Label("提交参数文件名"),
                                dbc.Input(
                                    id="submit-param-file",
                                    value=initial_param_file,
                                    placeholder="param.json",
                                ),
                                html.Br(),
                                dbc.Label("全局配置文件名"),
                                dbc.Input(
                                    id="submit-global-file",
                                    value="global.json",
                                    placeholder="global.json",
                                ),
                                html.Br(),
                                dbc.Label("Workflow ID(s) (留空时自动从 .workflow.log 读取，可填多个，逗号分隔)"),
                                dbc.Input(
                                    id="submit-workflow-id",
                                    value="",
                                    placeholder="例如: wf-xxxx, wf-yyyy",
                                ),
                                html.Br(),
                                dbc.Button("Reset", id="submit-reset", color="secondary", className="me-2"),
                                dbc.Button("Submit", id="submit-run", color="primary", className="me-2"),
                                dbc.Button("Report", id="submit-finalize", color="success"),
                            ],
                            md=5,
                        ),
                        dbc.Col(
                            [
                                html.H5("Advanced Setting"),
                                dbc.Label("global.json 编辑区"),
                                dcc.Textarea(
                                    id="submit-global-editor",
                                    value=DEFAULT_GLOBAL_EDITOR_TEXT,
                                    style={"width": "100%", "height": "220px", "fontFamily": "monospace"},
                                ),
                                html.Div(
                                    id="submit-incar-right-block",
                                    style={"display": "none"},
                                    children=[
                                        html.Br(),
                                        dbc.Label(id="submit-interaction-path-label", children=_interaction_path_label(DEFAULT_PROFILE)),
                                        dbc.Input(
                                            id="submit-interaction-incar",
                                            value=initial_interaction_incar,
                                            placeholder=_interaction_path_placeholder(DEFAULT_PROFILE),
                                        ),
                                        html.Br(),
                                        dbc.Label(id="submit-incar-editor-title", children=_interaction_editor_label(DEFAULT_PROFILE)),
                                        dcc.Textarea(
                                            id="submit-incar-content",
                                            value=DEFAULT_INTERACTION_INCAR_CONTENT,
                                            style={"width": "100%", "height": "180px", "fontFamily": "monospace"},
                                        ),
                                    ],
                                ),
                                html.Br(),
                                dbc.Label("param.json 编辑区"),
                                dcc.Textarea(
                                    id="submit-param-editor",
                                    value=initial_param_text,
                                    style={"width": "100%", "height": "300px", "fontFamily": "monospace"},
                                ),
                            ],
                            md=7,
                        ),
                    ],
                    className="g-3",
                ),
                html.Br(),
                dbc.Row(
                    [
                        dbc.Col(
                            [
                                html.H6("Workflow 进度"),
                                dbc.Progress(id="submit-progress-bar", value=0, max=100, striped=True, animated=True),
                                html.Div(id="submit-progress-text", className="mt-2 text-muted"),
                                html.Div(id="submit-progress-stats", className="text-muted"),
                                html.H6("Retrieve 进度", className="mt-3"),
                                dbc.Progress(id="retrieve-progress-bar", value=0, max=100, striped=True, animated=False),
                                html.Div(id="retrieve-progress-text", className="mt-2 text-muted"),
                                dcc.Interval(id="submit-progress-interval", interval=5000, n_intervals=0),
                            ],
                            md=12,
                        )
                    ]
                ),
                html.Br(),
                dbc.Label("运行命令"),
                html.Pre(
                    id="submit-command-preview",
                    children=DEFAULT_SUBMIT_COMMAND,
                    style={"backgroundColor": "#f5f5f5", "padding": "10px", "borderRadius": "4px"},
                ),
            ],
        )

    @staticmethod
    def _build_manage_tab() -> dbc.Tab:
        return dbc.Tab(
            label="Log",
            children=[
                dbc.Row(
                    [
                        dbc.Col(
                            [
                                dbc.Button("刷新日志", id="manage-log-refresh", color="primary", className="me-2"),
                                dcc.Interval(id="manage-log-interval", interval=3000, n_intervals=0),
                                html.Small("每 3 秒自动刷新一次", className="text-muted"),
                            ],
                            md=12,
                        ),
                    ]
                ),
                html.Br(),
                html.Pre(
                    id="manage-log-content",
                    style={
                        "whiteSpace": "pre-wrap",
                        "backgroundColor": "#0b0b0b",
                        "color": "#cfe6cf",
                        "padding": "12px",
                        "borderRadius": "6px",
                        "maxHeight": "560px",
                        "overflowY": "auto",
                    },
                    children=_read_log_tail(),
                ),
            ],
        )

    @staticmethod
    def _build_advanced_tab() -> dbc.Tab:
        return dbc.Tab(
            label="Advanced",
            children=[
                html.P(
                    "Run any APEX command tail. Available options :'submit', 'do', 'retrieve', 'list', 'get', 'getsteps', 'getkeys', 'delete', 'resubmit', 'retry', 'resume', 'stop', 'suspend', 'terminate', 'archive', 'report', 'rss', 'preview', 'gui', 'account'; Example: submit param_joint.json -c global_bohrium.json",
                    className="text-muted",
                ),
                dbc.Textarea(
                    id="advanced-command",
                    placeholder="submit param_joint.json -c global.json",
                    style={"height": "120px"},
                ),
                html.Br(),
                dbc.Button("Run advanced command", id="advanced-run", color="warning"),
                html.Div(
                    "Safety note: `gui` are blocked here to avoid nested Dash servers.",
                    className="text-muted mt-2",
                ),
            ],
        )

    @staticmethod
    def _build_account_tab() -> dbc.Tab:
        return dbc.Tab(
            label="Account",
            children=[
                html.P("底层对应 `apex account`，密码仅支持覆盖保存，不会在界面显示。", className="text-muted"),
                dbc.Row(
                    [
                        dbc.Col(
                            [
                                dbc.Label("Bohrium Email / 用户名"),
                                dbc.Input(
                                    id="account-email",
                                    value=DEFAULT_ACCOUNT_STATE.get("email", ""),
                                    placeholder="you@example.com",
                                ),
                                html.Br(),
                                dbc.Label("Program ID"),
                                dbc.Input(
                                    id="account-program-id",
                                    value=DEFAULT_ACCOUNT_STATE.get("program_id", ""),
                                    placeholder="例如 1234",
                                ),
                                html.Br(),
                                dbc.Label("Password (留空表示保持当前密码不变)"),
                                dbc.Input(
                                    id="account-password",
                                    type="password",
                                    value="",
                                    placeholder="输入新密码以覆盖",
                                ),
                                html.Br(),
                                dbc.Button("刷新", id="account-refresh", color="secondary", className="me-2"),
                                dbc.Button("覆盖保存", id="account-save", color="primary"),
                            ],
                            md=6,
                        ),
                        dbc.Col(
                            [
                                dbc.Label("当前账号信息"),
                                html.Pre(
                                    id="account-summary",
                                    children=_render_account_summary(DEFAULT_ACCOUNT_STATE),
                                    style={"backgroundColor": "#f5f5f5", "padding": "10px", "borderRadius": "4px"},
                                ),
                                dbc.Label("操作结果"),
                                html.Pre(
                                    id="account-feedback",
                                    children="",
                                    style={
                                        "whiteSpace": "pre-wrap",
                                        "backgroundColor": "#111",
                                        "color": "#f5f5f5",
                                        "padding": "10px",
                                        "borderRadius": "4px",
                                        "minHeight": "80px",
                                    },
                                ),
                            ],
                            md=6,
                        ),
                    ],
                    className="g-3",
                ),
            ],
        )

    def _build_layout(self) -> dbc.Container:
        return dbc.Container(
            [
                html.H2("APEX Graphical Interface", className="mt-3"),
                dcc.Store(id="command-result"),
                dcc.Store(id="submit-state", data=DEFAULT_SUBMIT_STATE),
                dcc.Store(id="retrieve-state", data={}),
                dcc.ConfirmDialog(id="submit-confirm-dialog"),
                dbc.Tabs(
                    [
                        self._build_submit_tab(),
                        self._build_manage_tab(),
                        self._build_advanced_tab(),
                        self._build_account_tab(),
                    ],
                    className="mb-3",
                ),
                html.Hr(),
                html.H4("Command Output"),
                html.Pre(
                    id="command-output",
                    style={
                        "whiteSpace": "pre-wrap",
                        "backgroundColor": "#111",
                        "color": "#f5f5f5",
                        "padding": "14px",
                        "borderRadius": "6px",
                        "maxHeight": "480px",
                        "overflowY": "auto",
                    },
                    children="Click any action button to run an APEX command.",
                ),
            ],
            fluid=True,
        )

    def _register_callbacks(self) -> None:
        @self.app.callback(
            Output("submit-global-editor", "value"),
            Output("submit-relax-check", "value"),
            Output("submit-properties-check", "options"),
            Output("submit-properties-check", "value"),
            Output("submit-interaction-type-block", "style"),
            Output("submit-interaction-type", "options"),
            Output("submit-interaction-type", "value"),
            Output("submit-lammps-interaction-block", "style"),
            Output("submit-electronic-interaction-block", "style"),
            Output("submit-incar-right-block", "style"),
            Output("submit-interaction-path-label", "children"),
            Output("submit-interaction-incar", "placeholder"),
            Output("submit-interaction-incar", "value"),
            Output("submit-incar-editor-title", "children"),
            Output("submit-incar-content", "value"),
            Output("submit-interaction-table", "columns"),
            Input("submit-profile", "value"),
        )
        def _sync_profile_defaults(profile):
            param_template = _load_profile_param_template(profile)
            prop_types = _extract_property_types(param_template)
            prop_selected = _extract_selected_properties(param_template)
            interaction_type, _interaction_model, _interaction_elements = _extract_interaction_defaults(param_template)
            interaction_incar = _extract_interaction_incar(param_template)
            interaction_incar_content = _load_profile_incar_content(profile, param_template)
            global_payload = _load_profile_global(profile)
            prop_options = [{"label": name, "value": name} for name in prop_types]
            interaction_options = _interaction_type_options_for_profile(profile, interaction_type)
            relax_value = ["relax"] if "relaxation" in param_template else []
            interaction_type_style = {"display": "block"} if profile == "lammps" else {"display": "none"}
            lammps_style = {"display": "block"} if profile == "lammps" else {"display": "none"}
            electronic_style = {"display": "none"} if profile == "lammps" else {"display": "block"}
            table_columns = _interaction_table_columns_for_profile(profile)

            return (
                _json_dump_text(global_payload),
                relax_value,
                prop_options,
                prop_selected,
                interaction_type_style,
                interaction_options,
                interaction_type,
                lammps_style,
                electronic_style,
                electronic_style,
                _interaction_path_label(profile),
                _interaction_path_placeholder(profile),
                _extract_interaction_incar(param_template, profile),
                _interaction_editor_label(profile),
                interaction_incar_content,
                table_columns,
            )

        @self.app.callback(
            Output("submit-interaction-table", "data"),
            Input("submit-profile", "value"),
            Input("submit-workdir", "value"),
            Input("submit-structures", "value"),
            Input("command-result", "data"),
            Input("submit-row-add", "n_clicks"),
            Input("submit-row-del", "n_clicks"),
            State("submit-interaction-table", "data"),
            State("submit-interaction-table", "columns"),
            prevent_initial_call=True,
        )
        def _update_interaction_table(profile, submit_workdir, structures_value, _command_result, _add_clicks, _del_clicks, current_data, columns):
            triggered_id = _resolve_triggered_id()
            profile = profile if profile in PROFILE_NAMES else DEFAULT_PROFILE

            if triggered_id == "command-result":
                return current_data or []

            if triggered_id in {"submit-profile", "submit-workdir", "submit-structures"}:
                template = _load_profile_param_template(profile)
                if profile in {"vasp", "abacus"}:
                    return _autodetect_interaction_rows(
                        profile,
                        submit_workdir or os.getcwd(),
                        structures_value or _extract_structure_defaults(template),
                        template,
                    )
                return _interaction_table_rows_from_template(profile, template)

            rows = copy.deepcopy(current_data or [])
            columns = columns or _interaction_table_columns_for_profile(profile)
            column_ids = [col.get("id") for col in columns if isinstance(col, dict)]
            blank_row = {key: "" for key in column_ids}

            if triggered_id == "submit-row-add":
                rows.append(blank_row)
            elif triggered_id == "submit-row-del":
                if rows:
                    rows.pop()

            if not rows:
                rows = [blank_row]
            return rows

        @self.app.callback(
            Output("submit-structures", "options"),
            Output("submit-structures", "value"),
            Input("submit-workdir", "value"),
            Input("command-result", "data"),
            Input("submit-profile", "value"),
            State("submit-structures", "value"),
            prevent_initial_call=False,
        )
        def _refresh_structure_options(submit_workdir, _command_result, submit_profile, current_structures):
            triggered_id = _resolve_triggered_id()
            workdir = _normalize_workdir(submit_workdir)
            template = _load_profile_param_template(submit_profile if submit_profile in PROFILE_NAMES else DEFAULT_PROFILE)
            template_structures = _extract_structure_defaults(template)
            current_value = template_structures if triggered_id == "submit-profile" else (current_structures or template_structures)
            options = _list_structure_path_options(workdir, current_value)
            if triggered_id == "command-result":
                return options, dash.no_update
            return options, current_value

        @self.app.callback(
            Output("submit-interaction-model", "options"),
            Output("submit-interaction-model", "value"),
            Input("submit-workdir", "value"),
            Input("command-result", "data"),
            Input("submit-profile", "value"),
            State("submit-interaction-model", "value"),
            prevent_initial_call=False,
        )
        def _refresh_interaction_model_options(submit_workdir, _command_result, submit_profile, current_model):
            triggered_id = _resolve_triggered_id()
            workdir = _normalize_workdir(submit_workdir)
            template = _load_profile_param_template(submit_profile if submit_profile in PROFILE_NAMES else DEFAULT_PROFILE)
            _interaction_type, template_model, _interaction_elements = _extract_interaction_defaults(template)
            current_value = template_model if triggered_id == "submit-profile" else (current_model or template_model)
            options = _list_workdir_file_options(workdir, current_value)
            return options, current_value

        @self.app.callback(
            Output("submit-param-file", "value", allow_duplicate=True),
            Output("submit-param-editor", "value", allow_duplicate=True),
            Output("submit-structures", "options", allow_duplicate=True),
            Output("submit-structures", "value", allow_duplicate=True),
            Output("submit-relax-check", "value", allow_duplicate=True),
            Output("submit-properties-check", "options", allow_duplicate=True),
            Output("submit-properties-check", "value", allow_duplicate=True),
            Output("submit-interaction-type", "options", allow_duplicate=True),
            Output("submit-interaction-type", "value", allow_duplicate=True),
            Output("submit-interaction-model", "options", allow_duplicate=True),
            Output("submit-interaction-model", "value", allow_duplicate=True),
            Output("submit-interaction-incar", "value", allow_duplicate=True),
            Output("submit-interaction-table", "data", allow_duplicate=True),
            Input("submit-workdir", "value"),
            State("submit-profile", "value"),
            prevent_initial_call=True,
        )
        def _load_param_fallback_from_workdir(submit_workdir, submit_profile):
            workdir = _normalize_workdir(submit_workdir)
            param_file, param_text = _find_param_fallback_file(workdir)
            if not param_file:
                return (dash.no_update,) * 13
            return (
                param_file,
                param_text,
                *_param_control_output_values(workdir, param_text, submit_profile),
            )

        @self.app.callback(
            Output("submit-param-editor", "value"),
            Input("submit-profile", "value"),
            Input("submit-reset", "n_clicks"),
            Input("submit-relax-check", "value"),
            Input("submit-properties-check", "value"),
            Input("submit-structures", "value"),
            Input("submit-interaction-type", "value"),
            Input("submit-interaction-model", "value"),
            Input("submit-interaction-incar", "value"),
            Input("submit-interaction-table", "data"),
            State("submit-profile", "value"),
            State("submit-param-editor", "value"),
            prevent_initial_call=True,
        )
        def _generate_param_editor(
            profile_input,
            _reset_clicks,
            relax_check,
            properties_check,
            structures_value,
            interaction_type,
            interaction_model,
            interaction_incar,
            interaction_table_rows,
            profile_state,
            current_param_text,
        ):
            triggered_id = _resolve_triggered_id()
            profile = profile_state or profile_input or DEFAULT_PROFILE
            param_template = _load_profile_param_template(profile)

            if triggered_id in {"submit-profile", "submit-reset"}:
                init_interaction_type, init_interaction_model, _init_elements = _extract_interaction_defaults(param_template)
                init_structures = _extract_structure_defaults(param_template)
                payload = _build_param_payload(
                    profile=profile,
                    selected_structures=init_structures,
                    with_relax="relaxation" in param_template,
                    selected_properties=_extract_selected_properties(param_template),
                    interaction_type=init_interaction_type,
                    interaction_model=init_interaction_model,
                    interaction_incar=_extract_interaction_incar(param_template),
                    interaction_rows=_interaction_table_rows_from_template(profile, param_template),
                    base_template=param_template,
                )
                return _json_dump_text(payload)

            payload = _patch_param_payload(
                current_text=current_param_text or "",
                triggered_id=triggered_id,
                profile=profile,
                template=param_template,
                relax_check=relax_check or [],
                properties_check=properties_check or [],
                structures_value=structures_value or [],
                interaction_type=interaction_type or "",
                interaction_model=interaction_model or "",
                interaction_incar=interaction_incar or "",
                interaction_rows=interaction_table_rows or [],
            )
            return _json_dump_text(payload)

        @self.app.callback(
            Output("command-result", "data"),
            Output("submit-confirm-dialog", "displayed"),
            Output("submit-confirm-dialog", "message"),
            Output("submit-state", "data"),
            Output("submit-workflow-id", "value"),
            Input("submit-reset", "n_clicks"),
            Input("submit-run", "n_clicks"),
            Input("submit-finalize", "n_clicks"),
            Input("submit-confirm-dialog", "submit_n_clicks"),
            Input("advanced-run", "n_clicks"),
            State("submit-profile", "value"),
            State("submit-global-editor", "value"),
            State("submit-param-editor", "value"),
            State("submit-incar-content", "value"),
            State("submit-workdir", "value"),
            State("submit-global-file", "value"),
            State("submit-param-file", "value"),
            State("submit-workflow-id", "value"),
            State("submit-state", "data"),
            State("retrieve-state", "data"),
            State("advanced-command", "value"),
            prevent_initial_call=True,
        )
        def _handle_command(
            _reset_clicks,
            _submit_clicks,
            _finalize_clicks,
            _submit_confirm_clicks,
            _advanced_clicks,
            submit_profile,
            submit_global_editor,
            submit_param_editor,
            submit_incar_content,
            submit_workdir,
            submit_global_file,
            submit_param_file,
            submit_workflow_id,
            submit_state,
            retrieve_state,
            advanced_command,
        ):
            triggered_id = _resolve_triggered_id()
            default_confirm_message = "检测到已存在 apex.log，是否确认重新提交？"
            profile = submit_profile if submit_profile in PROFILE_NAMES else DEFAULT_PROFILE
            workdir = _normalize_workdir(submit_workdir)
            global_file = (submit_global_file or "global.json").strip()
            param_file = (submit_param_file or "param.json").strip()
            current_workflow_id = (submit_workflow_id or "").strip()

            state_payload = copy.deepcopy(submit_state) if isinstance(submit_state, dict) else copy.deepcopy(DEFAULT_SUBMIT_STATE)
            state_payload.update(
                {
                    "workdir": workdir,
                    "global_file": global_file,
                    "param_file": param_file,
                }
            )

            if not os.path.isdir(workdir):
                feedback = _build_feedback(f"Workdir does not exist: {workdir}")
                return feedback, False, default_confirm_message, state_payload, current_workflow_id

            if not global_file or not param_file:
                feedback = _build_feedback("global/param 文件名不能为空")
                return feedback, False, default_confirm_message, state_payload, current_workflow_id

            if triggered_id == "submit-reset":
                removed_logs = _cleanup_reset_logs(workdir)
                state_payload["workflow_id"] = ""
                state_payload["workflow_ids"] = []
                state_payload["workflow_group_id"] = ""
                state_payload["workflow_group_size"] = 0
                state_payload["workflow_group_total_confs"] = 0
                if removed_logs:
                    message = f"Reset completed. Removed files in {workdir}: " + ", ".join(removed_logs)
                else:
                    message = f"Reset completed. No log files removed in {workdir}."
                return _build_feedback(message=message, ok=True), False, default_confirm_message, state_payload, ""

            if triggered_id in {"submit-run", "submit-confirm-dialog"}:
                global_payload, param_payload, parse_feedback = _parse_submit_payloads(
                    submit_global_editor,
                    submit_param_editor,
                )
                if parse_feedback:
                    return parse_feedback, False, default_confirm_message, state_payload, current_workflow_id

                created_files = _ensure_default_interaction_files(
                    profile,
                    param_payload,
                    incar_content=submit_incar_content,
                    workdir=workdir,
                )
                try:
                    batch_specs, resolved_structures = _build_submit_batches(workdir, param_payload, param_file)
                except Exception as exc:
                    return _build_feedback(f"Submit preparation failed: {exc}"), False, default_confirm_message, state_payload, current_workflow_id
                _write_submit_json_files(global_payload, param_payload, workdir, global_file, param_file)
                batch_param_files: List[str] = []
                for batch_spec in batch_specs:
                    batch_file = batch_spec["param_file"]
                    if batch_file == param_file:
                        continue
                    _write_submit_json_files(global_payload, batch_spec["payload"], workdir, global_file, batch_file)
                    batch_param_files.append(batch_file)
                workflow_log_line_start = _count_workflow_log_records(workdir)
                group_id = _build_submit_group_id()
                workdir_label = _sanitize_workflow_label_value(os.path.basename(workdir) or "workdir")
                total_batches = len(batch_specs)
                group_meta = {
                    "group_id": group_id,
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                    "workdir": workdir,
                    "global_file": global_file,
                    "param_file": param_file,
                    "batch_size": SUBMIT_BATCH_SIZE,
                    "total_confs": len(resolved_structures),
                    "expected_batches": total_batches,
                    "workflow_log_line_start": workflow_log_line_start,
                    "workflow_ids": [],
                    "submit_jobs": [],
                }
                base_workflow_name = f"apex-gui-{workdir_label}-{group_id[-8:]}"
                for batch_spec in batch_specs:
                    batch_index = batch_spec["index"]
                    batch_tag = f"{batch_index:03d}-of-{total_batches:03d}"
                    labels = [
                        f"apex_gui_group={group_id}",
                        f"apex_gui_workdir={workdir_label}",
                        f"apex_gui_batch={batch_tag}",
                        "apex_gui_source=gui",
                    ]
                    workflow_name = f"{base_workflow_name}-batch-{batch_index:03d}"
                    group_meta["submit_jobs"].append(
                        {
                            "batch_index": batch_index,
                            "batch_total": total_batches,
                            "param_file": batch_spec["param_file"],
                            "global_file": global_file,
                            "workflow_name": workflow_name,
                            "labels": labels,
                            "workdir": workdir,
                            "conf_count": len(batch_spec["structures"]),
                        }
                    )
                _save_submit_group_metadata(workdir, group_meta)
                state_payload["workflow_id"] = ""
                state_payload["workflow_ids"] = []
                state_payload["workflow_group_id"] = group_id
                state_payload["workflow_group_size"] = total_batches
                state_payload["workflow_group_total_confs"] = len(resolved_structures)

            if triggered_id == "submit-run":
                if os.path.exists(os.path.join(workdir, "apex.log")):
                    warning = _build_feedback(
                        "Detected existing apex.log. Please confirm resubmission.",
                        ok=False,
                    )
                    return warning, True, default_confirm_message, state_payload, current_workflow_id

                run_feedback = _run_submit_in_background(_submit_meta_path(workdir), cwd=workdir)
                if run_feedback.get("ok"):
                    batch_count = state_payload.get("workflow_group_size", 0)
                    conf_count = state_payload.get("workflow_group_total_confs", 0)
                    run_feedback["message"] = (
                        f"{run_feedback.get('message', '').rstrip()}\n"
                        f"{SUBMIT_RUNNING_NOTICE}\n"
                        f"Detected {conf_count} confs; split into {batch_count} workflow batch(es) with at most {SUBMIT_BATCH_SIZE} confs each."
                    )
                if created_files:
                    extra_line = "Auto-created default files: " + ", ".join(created_files)
                    run_feedback["message"] = f"{run_feedback.get('message', '').rstrip()}\n{extra_line}"
                if batch_param_files:
                    run_feedback["message"] = (
                        f"{run_feedback.get('message', '').rstrip()}\n"
                        f"Batch param files: {', '.join(batch_param_files)}"
                    )
                return run_feedback, False, default_confirm_message, state_payload, ""

            if triggered_id == "submit-confirm-dialog":
                run_feedback = _run_submit_in_background(_submit_meta_path(workdir), cwd=workdir)
                if run_feedback.get("ok"):
                    batch_count = state_payload.get("workflow_group_size", 0)
                    conf_count = state_payload.get("workflow_group_total_confs", 0)
                    run_feedback["message"] = (
                        f"{run_feedback.get('message', '').rstrip()}\n"
                        f"{SUBMIT_RUNNING_NOTICE}\n"
                        f"Detected {conf_count} confs; split into {batch_count} workflow batch(es) with at most {SUBMIT_BATCH_SIZE} confs each."
                    )
                if created_files:
                    extra_line = "Auto-created default files: " + ", ".join(created_files)
                    run_feedback["message"] = f"{run_feedback.get('message', '').rstrip()}\n{extra_line}"
                if batch_param_files:
                    run_feedback["message"] = (
                        f"{run_feedback.get('message', '').rstrip()}\n"
                        f"Batch param files: {', '.join(batch_param_files)}"
                    )
                return run_feedback, False, default_confirm_message, state_payload, ""

            if triggered_id == "submit-finalize":
                workflow_ids = _merge_workflow_ids(
                    current_workflow_id,
                    state_payload.get("workflow_ids", []),
                    state_payload.get("workflow_id", ""),
                )
                if not workflow_ids:
                    meta = _sync_submit_group_metadata_ids(workdir, _load_submit_group_metadata(workdir))
                    workflow_ids = _merge_workflow_ids(workflow_ids, meta.get("workflow_ids", []))
                if not workflow_ids:
                    workflow_ids = _merge_workflow_ids(_read_latest_workflow_id(workdir))
                workflow_id_text = _format_workflow_ids(workflow_ids)
                if not workflow_ids:
                    if (
                        isinstance(retrieve_state, dict)
                        and retrieve_state.get("status") == "done"
                        and _normalize_workdir(retrieve_state.get("workdir") or workdir) == workdir
                    ) or _has_local_reportable_results(workdir):
                        report_feedback = _run_archive_and_report_pipeline(workdir, global_file, param_file)
                        return report_feedback, False, default_confirm_message, state_payload, current_workflow_id
                    feedback = _build_feedback("Workflow ID is required for report. Fill it or submit first.")
                    return feedback, False, default_confirm_message, state_payload, ""
                if _retrieve_state_is_active(retrieve_state):
                    active_workflow_id = ""
                    if isinstance(retrieve_state, dict):
                        active_workflow_id = retrieve_state.get("workflow_id", "") or workflow_id_text
                    feedback = _build_feedback(
                        f"Retrieve is already running for workflow {active_workflow_id}. "
                        "Report will start automatically after retrieve finishes.",
                        ok=True,
                    )
                    feedback["operation"] = "report-request"
                    feedback["workdir"] = workdir
                    feedback["workflow_id"] = active_workflow_id or workflow_id_text
                    feedback["workflow_ids"] = workflow_ids
                    feedback["global_file"] = global_file
                    feedback["param_file"] = param_file
                    feedback["pending_report"] = True
                    state_payload["workflow_id"] = active_workflow_id or workflow_id_text
                    state_payload["workflow_ids"] = workflow_ids
                    return feedback, False, default_confirm_message, state_payload, active_workflow_id or workflow_id_text

                if (
                    isinstance(retrieve_state, dict)
                    and retrieve_state.get("status") == "done"
                    and _normalize_workdir(retrieve_state.get("workdir") or workdir) == workdir
                ) or _has_local_reportable_results(workdir):
                    report_feedback = _run_archive_and_report_pipeline(workdir, global_file, param_file)
                    state_payload["workflow_id"] = workflow_id_text
                    state_payload["workflow_ids"] = workflow_ids
                    return report_feedback, False, default_confirm_message, state_payload, workflow_id_text

                final_feedback = _start_retrieve_in_background(
                    workdir=workdir,
                    workflow_id=workflow_id_text,
                    global_file=global_file,
                )
                final_feedback["param_file"] = param_file
                final_feedback["pending_report"] = True
                state_payload["workflow_id"] = workflow_id_text
                state_payload["workflow_ids"] = workflow_ids
                return final_feedback, False, default_confirm_message, state_payload, workflow_id_text

            if triggered_id == "advanced-run":
                if not advanced_command or not advanced_command.strip():
                    return _build_feedback("Please provide a command tail."), False, default_confirm_message, state_payload, current_workflow_id
                try:
                    advanced_args = shlex.split(advanced_command.strip())
                except ValueError as exc:
                    return _build_feedback(f"Command parse error: {exc}"), False, default_confirm_message, state_payload, current_workflow_id
                if advanced_args and advanced_args[0] == "apex":
                    advanced_args = advanced_args[1:]
                if not advanced_args:
                    return _build_feedback("Please provide arguments after `apex`."), False, default_confirm_message, state_payload, current_workflow_id
                if advanced_args[0] in BLOCKED_INLINE_COMMANDS:
                    return (
                        _build_feedback(
                            f"`apex {advanced_args[0]}` is blocked in Advanced mode to avoid nested Dash apps."
                        ),
                        False,
                        default_confirm_message,
                        state_payload,
                        current_workflow_id,
                    )
                if advanced_args[0] == "report":
                    report_args = _advanced_report_args(advanced_args)
                    return (
                        _run_apex_command_in_background(
                            report_args,
                            cwd=workdir,
                            log_file="apex-report.log",
                        ),
                        False,
                        default_confirm_message,
                        state_payload,
                        current_workflow_id,
                    )
                return _run_apex_command(advanced_args, cwd=workdir), False, default_confirm_message, state_payload, current_workflow_id

            return _build_feedback("No action detected."), False, default_confirm_message, state_payload, current_workflow_id

        @self.app.callback(
            Output("submit-progress-bar", "value"),
            Output("submit-progress-bar", "label"),
            Output("submit-progress-text", "children"),
            Output("submit-progress-stats", "children"),
            Output("submit-state", "data", allow_duplicate=True),
            Output("submit-workflow-id", "value", allow_duplicate=True),
            Input("submit-progress-interval", "n_intervals"),
            Input("command-result", "data"),
            Input("submit-workflow-id", "value"),
            Input("submit-workdir", "value"),
            Input("submit-global-file", "value"),
            State("submit-state", "data"),
            prevent_initial_call=True,
        )
        def _refresh_submit_progress(
            _n_intervals,
            _command_result,
            submit_workflow_id,
            submit_workdir,
            submit_global_file,
            submit_state,
        ):
            state_payload = copy.deepcopy(submit_state) if isinstance(submit_state, dict) else copy.deepcopy(DEFAULT_SUBMIT_STATE)
            workdir = _normalize_workdir(submit_workdir or state_payload.get("workdir") or os.getcwd())
            state_payload["workdir"] = workdir
            global_file = (submit_global_file or state_payload.get("global_file") or "global.json").strip()
            state_payload["global_file"] = global_file
            meta = _sync_submit_group_metadata_ids(workdir, _load_submit_group_metadata(workdir))
            if meta:
                state_payload["workflow_group_id"] = meta.get("group_id", "")
                state_payload["workflow_group_size"] = int(meta.get("expected_batches", 0) or 0)
                state_payload["workflow_group_total_confs"] = int(meta.get("total_confs", 0) or 0)

            workflow_ids = _merge_workflow_ids(
                submit_workflow_id,
                state_payload.get("workflow_ids", []),
                state_payload.get("workflow_id", ""),
                meta.get("workflow_ids", []) if meta else [],
            )
            if not workflow_ids:
                workflow_ids = _merge_workflow_ids(_read_latest_workflow_id(workdir))
            workflow_id_text = _format_workflow_ids(workflow_ids)
            if workflow_ids:
                state_payload["workflow_ids"] = workflow_ids
                state_payload["workflow_id"] = workflow_id_text

            if not workflow_ids:
                updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                status_path = os.path.join(workdir, SUBMIT_STATUS_FILE)
                status_code = _tail_text_file(status_path, max_chars=32)
                if status_code and status_code != "0":
                    log_tail = _tail_text_file(os.path.join(workdir, "apex.log"))
                    stats = "Submit failed before writing .workflow.log."
                    if log_tail:
                        stats += f"\n\napex.log tail:\n{log_tail}"
                    return (
                        0,
                        "0%",
                        f"提交失败，未生成 workflow id | Last updated: {updated_at}",
                        stats,
                        state_payload,
                        "",
                    )
                if os.path.isfile(os.path.join(workdir, "apex.log")) and not status_code:
                    return (
                        0,
                        "0%",
                        f"正在提交，等待 workflow id 生成 | Last updated: {updated_at}",
                        "Submit process is still running or uploading inputs. Check apex.log for live output.",
                        state_payload,
                        "",
                    )
                if meta:
                    pending = int(meta.get("expected_batches", 0) or 0)
                    total_confs = int(meta.get("total_confs", 0) or 0)
                    return (
                        0,
                        "0%",
                        f"批量提交已准备完成，等待 workflow id 注册 | Group: {meta.get('group_id', '')} | Last updated: {updated_at}",
                        f"Confs: {total_confs} | Expected workflows: {pending} | Registered: 0",
                        state_payload,
                        "",
                    )
                return (
                    0,
                    "0%",
                    f"暂无 workflow id（提交后会自动识别） | Last updated: {updated_at}",
                    "Total: 0 | Running: 0 | Finished: 0",
                    state_payload,
                    "",
                )

            config_path = _resolve_file_path(workdir, global_file)
            updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if not os.path.isfile(config_path):
                return (
                    0,
                    "0%",
                    f"缺少配置文件: {config_path} | Last updated: {updated_at}",
                    "Total: 0 | Running: 0 | Finished: 0",
                    state_payload,
                    workflow_id_text,
                )

            try:
                phase_results = _query_many_workflow_phase_progress_with_timeout(workflow_ids, config_path)
            except TimeoutError as exc:
                return (
                    0,
                    "0%",
                    f"远端进度查询超时: {exc} | Last updated: {updated_at}",
                    "Total: 0 | Running: 0 | Finished: 0",
                    state_payload,
                    workflow_id_text,
                )
            except Exception as exc:
                return (
                    0,
                    "0%",
                    f"无法查询 workflow 进度: {exc} | Last updated: {updated_at}",
                    "Total: 0 | Running: 0 | Finished: 0",
                    state_payload,
                    workflow_id_text,
                )
            workflow_phases: Dict[str, int] = {}
            progress_texts: List[str] = []
            for workflow_id in workflow_ids:
                workflow_phase, raw_progress = phase_results.get(workflow_id, ("Unknown", ""))
                workflow_phases[workflow_phase] = workflow_phases.get(workflow_phase, 0) + 1
                if raw_progress:
                    progress_texts.append(raw_progress)

            current_total, grand_total, percent = _aggregate_progress_fraction(progress_texts)
            group_id = state_payload.get("workflow_group_id", "")
            registered = len(workflow_ids)
            expected = int(state_payload.get("workflow_group_size", 0) or 0)
            total_confs = int(state_payload.get("workflow_group_total_confs", 0) or 0)
            phase_bits = [f"{phase} {count}" for phase, count in sorted(workflow_phases.items())]
            scope_text = f"Workflows {registered}"
            if expected:
                scope_text += f"/{expected}"
            phase_text = f"{scope_text}"
            if group_id:
                phase_text = f"Group {group_id} | {phase_text}"
            phase_text += f" | Last updated: {updated_at}"
            if phase_bits:
                phase_text += " | " + ", ".join(phase_bits)
            if grand_total:
                phase_text += f" | Progress: {current_total}/{grand_total}"

            stats_totals = {
                "total": 0,
                "running": 0,
                "finished": 0,
                "conf_total": 0,
                "conf_running": 0,
                "conf_finished": 0,
                "conf_failed": 0,
            }
            detail_refreshing = 0
            detail_errors: List[str] = []
            detail_update_times: List[float] = []
            for workflow_id in workflow_ids:
                detail_cache = _get_workflow_detail_cache(workflow_id, config_path)
                counts = detail_cache.get("counts")
                if isinstance(counts, dict):
                    for key in stats_totals:
                        stats_totals[key] += int(counts.get(key, 0) or 0)
                    updated_stamp = detail_cache.get("updated_at")
                    if isinstance(updated_stamp, (int, float)):
                        detail_update_times.append(float(updated_stamp))
                elif detail_cache.get("error"):
                    detail_errors.append(f"{workflow_id}: {detail_cache['error']}")
                if detail_cache.get("running"):
                    detail_refreshing += 1

            if stats_totals["total"] or stats_totals["conf_total"]:
                stats = (
                    f"Steps: Total {stats_totals['total']} | Running {stats_totals['running']} | Finished {stats_totals['finished']}"
                    f" | Confs: Total {stats_totals['conf_total']} | Running {stats_totals['conf_running']} | "
                    f"Finished {stats_totals['conf_finished']} | Failed {stats_totals['conf_failed']}"
                )
                if total_confs:
                    stats += f" | Planned confs {total_confs}"
                if detail_update_times:
                    stats += " | Details updated: " + datetime.fromtimestamp(max(detail_update_times)).strftime("%H:%M:%S")
                if detail_refreshing:
                    stats += f" | Refreshing details for {detail_refreshing} workflow(s)..."
            elif detail_errors:
                stats = "Steps/Confs: detail query failed: " + "; ".join(detail_errors[:3])
            elif detail_refreshing:
                stats = f"Steps/Confs: querying details in background for {detail_refreshing} workflow(s)..."
            else:
                stats = "Steps/Confs: detail query pending."
            stats += f"\nWorkflow IDs: {workflow_id_text}"
            return percent, f"{percent}%", phase_text, stats, state_payload, workflow_id_text

        @self.app.callback(
            Output("retrieve-progress-bar", "value"),
            Output("retrieve-progress-bar", "label"),
            Output("retrieve-progress-bar", "animated"),
            Output("retrieve-progress-text", "children"),
            Output("retrieve-state", "data", allow_duplicate=True),
            Output("command-result", "data", allow_duplicate=True),
            Input("submit-progress-interval", "n_intervals"),
            State("retrieve-state", "data"),
            State("submit-workdir", "value"),
            State("submit-workflow-id", "value"),
            prevent_initial_call=True,
        )
        def _refresh_retrieve_progress(_n_intervals, retrieve_state, submit_workdir, submit_workflow_id):
            state_payload = copy.deepcopy(retrieve_state) if isinstance(retrieve_state, dict) else {}
            if submit_workdir and not state_payload.get("workdir"):
                state_payload["workdir"] = _normalize_workdir(submit_workdir)
            if submit_workflow_id and not state_payload.get("workflow_id"):
                state_payload["workflow_id"] = submit_workflow_id.strip()
            if submit_workflow_id and not state_payload.get("workflow_ids"):
                state_payload["workflow_ids"] = _parse_workflow_ids(submit_workflow_id)
            value, label, animated, text, next_state, feedback = _finalize_retrieve_status(state_payload)
            return value, label, animated, text, next_state, feedback

        @self.app.callback(
            Output("account-email", "value"),
            Output("account-program-id", "value"),
            Output("account-password", "value"),
            Output("account-summary", "children"),
            Output("account-feedback", "children"),
            Input("account-refresh", "n_clicks"),
            Input("account-save", "n_clicks"),
            State("account-email", "value"),
            State("account-program-id", "value"),
            State("account-password", "value"),
            prevent_initial_call=True,
        )
        def _handle_account(_refresh_clicks, _save_clicks, email_value, program_id_value, password_value):
            triggered_id = _resolve_triggered_id()
            if triggered_id == "account-save":
                feedback, account_state = _save_account_overwrite(
                    email=email_value or "",
                    password=password_value or "",
                    program_id_text=program_id_value or "",
                )
            else:
                account_state = _load_account_state()
                feedback = _build_feedback("Account info refreshed.", ok=True)
            return (
                account_state.get("email", ""),
                account_state.get("program_id", ""),
                "",
                _render_account_summary(account_state),
                _brief_feedback(feedback),
            )

        @self.app.callback(Output("command-output", "children"), Input("command-result", "data"))
        def _render_output(payload):
            return _format_feedback(payload)

        @self.app.callback(
            Output("retrieve-state", "data", allow_duplicate=True),
            Input("command-result", "data"),
            State("submit-state", "data"),
            State("retrieve-state", "data"),
            prevent_initial_call=True,
        )
        def _sync_retrieve_state(payload, submit_state, retrieve_state):
            if not isinstance(payload, dict):
                return dash.no_update
            if payload.get("operation") == "report-request":
                current_state = copy.deepcopy(retrieve_state) if isinstance(retrieve_state, dict) else {}
                submit_payload = submit_state if isinstance(submit_state, dict) else {}
                current_state["workdir"] = payload.get("workdir") or current_state.get("workdir") or submit_payload.get("workdir") or os.getcwd()
                current_state["workflow_id"] = payload.get("workflow_id") or current_state.get("workflow_id") or submit_payload.get("workflow_id") or ""
                current_state["workflow_ids"] = payload.get("workflow_ids") or current_state.get("workflow_ids") or submit_payload.get("workflow_ids") or []
                current_state["global_file"] = payload.get("global_file") or current_state.get("global_file") or submit_payload.get("global_file") or "global.json"
                current_state["param_file"] = payload.get("param_file") or current_state.get("param_file") or submit_payload.get("param_file") or "param.json"
                current_state["pending_report"] = True
                return current_state
            if not _is_retrieve_feedback(payload):
                return dash.no_update
            state_payload = submit_state if isinstance(submit_state, dict) else {}
            return {
                "status": "running",
                "workdir": payload.get("workdir") or state_payload.get("workdir") or os.getcwd(),
                "workflow_id": payload.get("workflow_id") or state_payload.get("workflow_id") or "",
                "workflow_ids": payload.get("workflow_ids") or state_payload.get("workflow_ids") or [],
                "global_file": payload.get("global_file") or state_payload.get("global_file") or "global.json",
                "param_file": payload.get("param_file") or state_payload.get("param_file") or "param.json",
                "log_file": payload.get("log_file", ""),
                "status_file": payload.get("status_file", ""),
                "command": payload.get("command", ""),
                "pending_report": bool(payload.get("pending_report")),
            }

        @self.app.callback(
            Output("command-result", "data", allow_duplicate=True),
            Input("submit-structure-upload", "contents"),
            State("submit-structure-upload", "filename"),
            State("submit-workdir", "value"),
            prevent_initial_call=True,
        )
        def _handle_structure_upload(upload_contents, upload_filenames, submit_workdir):
            workdir = _normalize_workdir(submit_workdir)
            try:
                saved_files = _save_uploaded_files(upload_contents, upload_filenames, workdir, target_subdir="confs")
            except Exception as exc:
                return _build_feedback(f"Structure upload failed: {exc}")

            if not saved_files:
                return _build_feedback("No uploaded structure files received.")

            return _build_feedback(
                f"Uploaded {len(saved_files)} structure file(s) to {os.path.join(workdir, 'confs')}: " + ", ".join(saved_files),
                ok=True,
            )

        @self.app.callback(
            Output("command-result", "data", allow_duplicate=True),
            Output("submit-param-file", "value", allow_duplicate=True),
            Output("submit-param-editor", "value", allow_duplicate=True),
            Output("submit-structures", "options", allow_duplicate=True),
            Output("submit-structures", "value", allow_duplicate=True),
            Output("submit-relax-check", "value", allow_duplicate=True),
            Output("submit-properties-check", "options", allow_duplicate=True),
            Output("submit-properties-check", "value", allow_duplicate=True),
            Output("submit-interaction-type", "options", allow_duplicate=True),
            Output("submit-interaction-type", "value", allow_duplicate=True),
            Output("submit-interaction-model", "options", allow_duplicate=True),
            Output("submit-interaction-model", "value", allow_duplicate=True),
            Output("submit-interaction-incar", "value", allow_duplicate=True),
            Output("submit-interaction-table", "data", allow_duplicate=True),
            Input("submit-file-upload", "contents"),
            State("submit-file-upload", "filename"),
            State("submit-workdir", "value"),
            State("submit-profile", "value"),
            prevent_initial_call=True,
        )
        def _handle_file_upload(upload_contents, upload_filenames, submit_workdir, submit_profile):
            workdir = _normalize_workdir(submit_workdir)
            try:
                saved_files = _save_uploaded_files(upload_contents, upload_filenames, workdir, target_subdir="")
            except Exception as exc:
                return (_build_feedback(f"File upload failed: {exc}"),) + (dash.no_update,) * 13

            if not saved_files:
                return (_build_feedback("No uploaded files received."),) + (dash.no_update,) * 13

            param_file, param_text = _find_param_fallback_file(workdir, preferred_files=saved_files)
            param_message = ""
            if param_file:
                param_message = f" Using {param_file} as submit parameter file."

            feedback = _build_feedback(
                f"Uploaded {len(saved_files)} file(s) to {workdir}: " + ", ".join(saved_files) + param_message,
                ok=True,
            )
            if not param_file:
                return (feedback,) + (dash.no_update,) * 13
            return (
                feedback,
                param_file,
                param_text,
                *_param_control_output_values(workdir, param_text, submit_profile),
            )

        @self.app.callback(
            Output("manage-log-content", "children"),
            Input("manage-log-refresh", "n_clicks"),
            Input("manage-log-interval", "n_intervals"),
            State("submit-workdir", "value"),
        )
        def _update_manage_log(_clicks, _n_intervals, submit_workdir):
            return _read_log_tail(workdir=submit_workdir)

    def run(self) -> None:
        host_for_browser = "127.0.0.1" if self.host in {"0.0.0.0", "::"} else self.host
        url = f"http://{host_for_browser}:{self.port}/"
        if self.open_browser:
            Timer(1.0, lambda: webbrowser.open(url)).start()
        print(f"APEX GUI server running at {url}")
        self.app.run(host=self.host, port=self.port, debug=False, use_reloader=False)


def gui_from_args(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, open_browser: bool = True) -> None:
    print("-------APEX GUI Mode-------")
    ApexGuiApp(host=host, port=port, open_browser=open_browser).run()
