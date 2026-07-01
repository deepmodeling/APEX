from __future__ import annotations

from pathlib import Path
from typing import Any

from monty.serialization import loadfn


HEADER_ONLY_RETRY_REASON = "header_only_lammps_log_after_nonzero_exit"
REMOTE_LAMMPS_STARTUP_FAILURE = "remote_lammps_startup_failure"


def is_lammps_header_only_text(text: str) -> bool:
    lines = [line.strip() for line in str(text).splitlines() if line.strip()]
    return len(lines) == 1 and lines[0].startswith("LAMMPS (")


def is_lammps_header_only_log(path: Path | str) -> bool:
    log_path = Path(path)
    if not log_path.is_file():
        return False
    try:
        return is_lammps_header_only_text(log_path.read_text(errors="replace"))
    except Exception:
        return False


def is_header_only_lammps_failure(task_dir: Path | str, exit_code: int | None) -> bool:
    if exit_code in (None, 0):
        return False
    task_path = Path(task_dir)
    if any((task_path / name).exists() for name in ["CONTCAR", "dump.relax", "stress_timeseries.txt"]):
        return False
    return (
        is_lammps_header_only_log(task_path / "log.lammps")
        or is_lammps_header_only_log(task_path / "outlog")
    )


def classify_lammps_exit_code(exit_code: int | None, *, remote_startup: bool = False) -> dict[str, Any]:
    if exit_code is None:
        return {
            "state": "failed",
            "reason": "unknown_failure",
            "message": "Command exit code is unavailable.",
        }
    if exit_code == 0:
        return {
            "state": "succeeded",
            "reason": "command_exit_zero",
            "message": "Command completed successfully.",
        }
    if remote_startup:
        return {
            "state": "failed",
            "reason": REMOTE_LAMMPS_STARTUP_FAILURE,
            "message": "LAMMPS exited non-zero after writing only the startup header.",
        }
    if exit_code == 124:
        return {
            "state": "failed",
            "reason": "timeout",
            "message": "Command exited with timeout code 124.",
        }
    if exit_code == 126:
        return {
            "state": "failed",
            "reason": "command_not_executable",
            "message": "Command was found but could not be executed.",
        }
    if exit_code == 127:
        return {
            "state": "failed",
            "reason": "command_not_found",
            "message": "Command executable was not found.",
        }
    if exit_code == 137:
        return {
            "state": "failed",
            "reason": "killed_or_oom",
            "message": "Command was killed with exit code 137, commonly SIGKILL/OOM/preemption.",
        }
    if exit_code in (130, 143):
        return {
            "state": "failed",
            "reason": "terminated",
            "message": f"Command was terminated by signal-like exit code {exit_code}.",
        }
    if exit_code > 128:
        return {
            "state": "failed",
            "reason": "killed_or_oom",
            "message": f"Command exited with code {exit_code}, likely signal {exit_code - 128}.",
        }
    return {
        "state": "failed",
        "reason": "nonzero_lammps_error",
        "message": f"Command exited with non-zero code {exit_code}.",
    }


def classify_apex_task_status(status: Any, task_dir: Path | str | None = None) -> dict[str, Any]:
    if not isinstance(status, dict):
        return {
            "state": "failed",
            "reason": "invalid_task_status",
            "message": "apex_task_status.json is missing or is not a JSON object.",
            "exit_code": None,
        }
    exit_code = status.get("exit_code")
    try:
        exit_code = int(exit_code) if exit_code is not None else None
    except (TypeError, ValueError):
        exit_code = None
    remote_startup = (
        status.get("reason") == REMOTE_LAMMPS_STARTUP_FAILURE
        or status.get("retry_reason") == HEADER_ONLY_RETRY_REASON
    )
    if task_dir is not None:
        remote_startup = remote_startup or is_header_only_lammps_failure(task_dir, exit_code)
    classified = classify_lammps_exit_code(exit_code, remote_startup=remote_startup)
    if status.get("state") != "succeeded" and classified["state"] == "succeeded":
        classified = {
            "state": "failed",
            "reason": "invalid_task_status",
            "message": "Task status is failed but exit_code is zero.",
        }
    return {
        **classified,
        "exit_code": exit_code,
        "original_reason": status.get("reason"),
        "retry_reason": status.get("retry_reason"),
    }


def load_and_classify_task_status(status_path: Path | str) -> dict[str, Any]:
    path = Path(status_path)
    try:
        status = loadfn(path)
    except Exception as exc:
        return {
            "state": "failed",
            "reason": "invalid_task_status",
            "message": f"Could not parse apex_task_status.json: {exc}",
            "exit_code": None,
        }
    return classify_apex_task_status(status, path.parent)
