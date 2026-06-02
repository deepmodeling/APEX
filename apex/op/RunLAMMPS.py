import datetime
import os, subprocess, logging, time
from pathlib import Path
from monty.serialization import dumpfn, loadfn
from dflow.python import (
    OP,
    OPIO,
    OPIOSign,
    Artifact,
    upload_packages
)

upload_packages.append(__file__)


class RunLAMMPS(OP):
    """
    class for LAMMPS calculation
    """
    def __init__(self, infomode=1):
        self.infomode = infomode

    @classmethod
    def get_input_sign(cls):
        return OPIOSign({
            'input_lammps': Artifact(Path),
            'run_command': str
        })

    @classmethod
    def get_output_sign(cls):
        return OPIOSign({
            'backward_dir': Artifact(Path, sub_path=False)
        })

    @classmethod
    def _cleanup_model_links(cls, task_dir):
        task_path = Path(task_dir)
        inter_json = task_path / "inter.json"
        if not inter_json.exists():
            return

        try:
            inter_param = loadfn(inter_json)
        except Exception as exc:
            logging.warning(f"Failed to load inter.json for symlink cleanup: {exc}")
            return

        model_spec = inter_param.get("model", [])
        if isinstance(model_spec, str):
            model_list = [model_spec]
        elif isinstance(model_spec, list):
            model_list = model_spec
        else:
            model_list = []

        for model in model_list:
            link_candidates = {task_path / model, task_path / Path(model).name}
            for link_path in link_candidates:
                if link_path.is_symlink() and not link_path.exists():
                    link_path.unlink()

    @classmethod
    def _utc_now(cls) -> str:
        return datetime.datetime.now(datetime.timezone.utc).isoformat()

    @classmethod
    def _classify_exit_code(cls, exit_code: int) -> dict:
        if exit_code == 0:
            return {
                "state": "succeeded",
                "reason": "command_exit_zero",
                "message": "Command completed successfully.",
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
        if exit_code in (130, 143):
            return {
                "state": "failed",
                "reason": "terminated",
                "message": f"Command was terminated by signal-like exit code {exit_code}.",
            }
        if exit_code == 137:
            return {
                "state": "failed",
                "reason": "killed_or_oom",
                "message": "Command was killed with exit code 137, commonly SIGKILL/OOM/preemption.",
            }
        if exit_code > 128:
            return {
                "state": "failed",
                "reason": "signal_exit",
                "message": f"Command exited with code {exit_code}, likely signal {exit_code - 128}.",
            }
        return {
            "state": "failed",
            "reason": "nonzero_exit",
            "message": f"Command exited with non-zero code {exit_code}.",
        }

    @classmethod
    def _write_task_status(
        cls,
        status_file: Path,
        *,
        exit_code: int,
        cmd: str,
        elapsed: float,
        started_at: str,
        finished_at: str,
        debug_log: str = ".debug.log",
        attempts: int = 1,
        retry_reason: str | None = None,
    ):
        status = cls._classify_exit_code(exit_code)
        payload = {
            **status,
            "exit_code": int(exit_code),
            "run_command": cmd,
            "started_at": started_at,
            "finished_at": finished_at,
            "elapsed_seconds": elapsed,
            "debug_log": debug_log,
            "attempts": int(attempts),
        }
        if retry_reason:
            payload["retry_reason"] = retry_reason
        dumpfn(
            payload,
            status_file,
            indent=4,
        )

    @classmethod
    def _append_debug(cls, debug_file: Path, text: str):
        with open(debug_file, "a") as fp:
            fp.write(text)
            if not text.endswith("\n"):
                fp.write("\n")

    @classmethod
    def _safe_cmd(cls, cmd: str, timeout: int = 10) -> str:
        try:
            out = subprocess.check_output(
                cmd,
                shell=True,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                text=True,
            )
            return out.rstrip()
        except Exception as exc:
            return f"<unavailable: {exc}>"

    @classmethod
    def _tail_file(cls, path: Path, n_lines: int = 80) -> str:
        if not path.exists():
            return f"{path.name}: missing"
        try:
            with open(path, "r", errors="replace") as fp:
                lines = fp.readlines()
            return "".join(lines[-n_lines:]).rstrip()
        except Exception as exc:
            return f"{path.name}: unable to read: {exc}"

    @classmethod
    def _directory_inventory(cls, task_dir: Path, max_entries: int = 300) -> str:
        entries = []
        for path in sorted(task_dir.rglob("*")):
            rel = path.relative_to(task_dir)
            if len(rel.parts) > 3:
                continue
            if path.is_dir():
                continue
            if path.is_symlink():
                entries.append(f"{rel}: symlink -> {os.readlink(path)} exists={path.exists()}")
            else:
                entries.append(f"{rel}: size={path.stat().st_size}")
            if len(entries) >= max_entries:
                entries.append(f"<truncated after {max_entries} entries>")
                break
        return "\n".join(entries) if entries else "<empty task directory>"

    @classmethod
    def _metadata_summary(cls, task_dir: Path) -> str:
        lines = []
        for path in sorted(task_dir.glob("*.json")):
            try:
                data = loadfn(path)
            except Exception as exc:
                lines.append(f"{path.name}: unable to parse JSON: {exc}")
                continue
            if isinstance(data, dict):
                keys = [
                    "type",
                    "property",
                    "method",
                    "cal_type",
                    "role",
                    "pair_id",
                    "strain_component",
                    "strain_value",
                    "temperature",
                    "exit_code",
                ]
                summary = {key: data[key] for key in keys if key in data}
                lines.append(f"{path.name}: {summary}")
            else:
                lines.append(f"{path.name}: {type(data).__name__}")
        return "\n".join(lines) if lines else "<no JSON metadata>"

    @classmethod
    def _log_candidates(cls, task_dir: Path) -> list[Path]:
        names = [
            ".debug.stderr",
            ".debug.stdout",
            "outlog",
            "errlog",
            "log.lammps",
            "run.log",
            "main.log",
            "log",
            "apex_task_status.json",
        ]
        candidates = [task_dir / name for name in names]
        candidates.extend(sorted(task_dir.glob("*.log")))
        candidates.extend(sorted(task_dir.glob("log.*")))
        deduped = []
        seen = set()
        for path in candidates:
            if path in seen:
                continue
            seen.add(path)
            deduped.append(path)
        return deduped

    @classmethod
    def _is_lammps_header_only_log(cls, path: Path) -> bool:
        if not path.is_file():
            return False
        try:
            lines = [
                line.strip()
                for line in path.read_text(errors="replace").splitlines()
                if line.strip()
            ]
        except Exception:
            return False
        return len(lines) == 1 and lines[0].startswith("LAMMPS (")

    @classmethod
    def _is_header_only_lammps_failure(cls, task_dir: Path, exit_code: int) -> bool:
        if exit_code == 0:
            return False
        if any((task_dir / name).exists() for name in ["CONTCAR", "dump.relax", "stress_timeseries.txt"]):
            return False
        return (
            cls._is_lammps_header_only_log(task_dir / "log.lammps")
            or cls._is_lammps_header_only_log(task_dir / "outlog")
        )

    @classmethod
    def _archive_retry_file(cls, path: Path, attempt: int):
        if not path.exists():
            return
        target = path.with_name(f"{path.name}.attempt{attempt}")
        try:
            if target.exists():
                target.unlink()
            path.rename(target)
        except Exception as exc:
            logging.warning(f"Could not archive retry file {path}: {exc}")

    @classmethod
    def _prepare_retry(cls, task_dir: Path, attempt: int):
        for name in ["log.lammps", "outlog", "errlog", "run.log", "dump.relax", "stress_timeseries.txt"]:
            cls._archive_retry_file(task_dir / name, attempt)

    @classmethod
    def _resource_snapshot(cls) -> str:
        lines = []
        lines.append("$ date")
        lines.append(cls._safe_cmd("date -Is"))
        lines.append("$ pwd")
        lines.append(os.getcwd())
        lines.append("$ df -h .")
        lines.append(cls._safe_cmd("df -h ."))
        lines.append("$ free -h || vm_stat")
        lines.append(cls._safe_cmd("free -h || vm_stat"))
        lines.append("$ nvidia-smi")
        lines.append(cls._safe_cmd("nvidia-smi --query-gpu=index,name,memory.total,memory.used,utilization.gpu --format=csv,noheader,nounits || nvidia-smi", timeout=5))
        return "\n".join(lines)

    @classmethod
    def _write_initial_debug(cls, debug_file: Path, task_dir: Path, cmd: str):
        selected_env = [
            "PATH",
            "LD_LIBRARY_PATH",
            "OMP_NUM_THREADS",
            "CUDA_VISIBLE_DEVICES",
            "DP_INFER_BATCH_SIZE",
        ]
        cls._append_debug(debug_file, "# APEX LAMMPS debug log\n")
        cls._append_debug(debug_file, "## Command\n")
        cls._append_debug(debug_file, cmd)
        cls._append_debug(debug_file, "\n## Environment\n")
        for key in selected_env:
            cls._append_debug(debug_file, f"{key}={os.environ.get(key, '')}")
        cls._append_debug(debug_file, "\n## Limits\n")
        cls._append_debug(debug_file, cls._safe_cmd("ulimit -a"))
        cls._append_debug(debug_file, "\n## Initial resources\n")
        cls._append_debug(debug_file, cls._resource_snapshot())
        cls._append_debug(debug_file, "\n## Metadata summary\n")
        cls._append_debug(debug_file, cls._metadata_summary(task_dir))
        cls._append_debug(debug_file, "\n## Initial file inventory\n")
        cls._append_debug(debug_file, cls._directory_inventory(task_dir))

    @classmethod
    def _write_final_debug(cls, debug_file: Path, task_dir: Path, exit_code: int, elapsed: float):
        cls._append_debug(debug_file, "\n## Final status\n")
        cls._append_debug(debug_file, f"exit_code={exit_code}")
        cls._append_debug(debug_file, f"elapsed_seconds={elapsed:.3f}")
        cls._append_debug(debug_file, "\n## Final resources\n")
        cls._append_debug(debug_file, cls._resource_snapshot())
        cls._append_debug(debug_file, "\n## Final file inventory\n")
        cls._append_debug(debug_file, cls._directory_inventory(task_dir))
        cls._append_debug(debug_file, "\n## Log tails\n")
        for path in cls._log_candidates(task_dir):
            cls._append_debug(debug_file, f"\n### {path.name}\n")
            cls._append_debug(debug_file, cls._tail_file(path))

    @classmethod
    def _run_command(cls, cmd: str, task_dir: Path) -> int:
        stdout_path = task_dir / ".debug.stdout"
        stderr_path = task_dir / ".debug.stderr"
        stdout_path.touch()
        stderr_path.touch()
        return int(subprocess.call(cmd, shell=True))

    @OP.exec_sign_check
    def execute(self, op_in: OPIO) -> OPIO:
        cwd = os.getcwd()
        task_dir = Path(op_in["input_lammps"]).resolve()
        status_file = task_dir / "apex_task_status.json"

        try:
            os.chdir(task_dir)
            if os.path.exists("run_command"):
                with open("run_command", 'r') as f:
                    cmd = f.read()
            else:
                cmd = op_in["run_command"]
            cmd = str(cmd).strip()

            debug_file = task_dir / ".debug.log"
            if not cmd:
                now = self._utc_now()
                self._append_debug(debug_file, "# APEX LAMMPS debug log\n")
                self._append_debug(debug_file, "## Command\n<empty>")
                self._write_task_status(
                    status_file,
                    exit_code=127,
                    cmd=cmd,
                    elapsed=0.0,
                    started_at=now,
                    finished_at=now,
                )
                self._write_final_debug(debug_file, task_dir, 127, 0.0)
                self._cleanup_model_links(task_dir)
                return OPIO({"backward_dir": op_in["input_lammps"]})
            self._write_initial_debug(debug_file, task_dir, cmd)
            started_at = self._utc_now()
            start = time.time()
            retry_reason = None
            attempts = 1
            max_attempts = int(os.environ.get("APEX_LAMMPS_HEADER_RETRY", "2"))
            max_attempts = max(1, max_attempts)
            exit_code = self._run_command(cmd, task_dir)
            while (
                attempts < max_attempts
                and self._is_header_only_lammps_failure(task_dir, exit_code)
            ):
                retry_reason = "header_only_lammps_log_after_nonzero_exit"
                self._append_debug(
                    debug_file,
                    f"\n## Retry {attempts + 1}\n"
                    f"Retrying LAMMPS because exit_code={exit_code} and log.lammps/outlog "
                    "contains only the LAMMPS header.",
                )
                self._prepare_retry(task_dir, attempts)
                time.sleep(float(os.environ.get("APEX_LAMMPS_HEADER_RETRY_DELAY", "5")))
                attempts += 1
                exit_code = self._run_command(cmd, task_dir)
            elapsed = time.time() - start
            finished_at = self._utc_now()
            self._write_task_status(
                status_file,
                exit_code=exit_code,
                cmd=cmd,
                elapsed=elapsed,
                started_at=started_at,
                finished_at=finished_at,
                attempts=attempts,
                retry_reason=retry_reason,
            )
            self._write_final_debug(debug_file, task_dir, exit_code, elapsed)
            if exit_code == 0:
                logging.info("Call Lammps command successfully!")
            else:
                logging.warning(f"Call Lammps command failed with exit code: {exit_code}")

            self._cleanup_model_links(task_dir)
        finally:
            os.chdir(cwd)

        op_out = OPIO({
            "backward_dir": op_in["input_lammps"]
        })
        return op_out
