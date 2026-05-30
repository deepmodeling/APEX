import argparse
import json
import os
import subprocess
import sys
import traceback
from typing import List, Sequence


def _append_log(log_file: str, message: str) -> None:
    os.makedirs(os.path.dirname(log_file) or ".", exist_ok=True)
    with open(log_file, "a", encoding="utf-8") as log_fp:
        log_fp.write(message)
        log_fp.flush()


def _write_status(status_file: str, code: int) -> None:
    os.makedirs(os.path.dirname(status_file) or ".", exist_ok=True)
    with open(status_file, "w", encoding="utf-8") as fp:
        fp.write(str(code))


def run_submit_group(meta_path: str, log_file: str, status_file: str) -> int:
    exit_codes: List[int] = []
    try:
        with open(meta_path, "r", encoding="utf-8") as fp:
            meta = json.load(fp)
        jobs = meta.get("submit_jobs", [])
        _append_log(log_file, f"[apex-gui] submit group {meta.get('group_id', '')} start\n")
        procs = []
        with open(log_file, "a", encoding="utf-8") as log_fp:
            for job in jobs:
                args = [
                    sys.executable,
                    "-m",
                    "apex",
                    "submit",
                    job["param_file"],
                    "-c",
                    job["global_file"],
                    "-s",
                    "-n",
                    job["workflow_name"],
                ]
                for label in job.get("labels", []):
                    args.extend(["-l", label])
                log_fp.write(
                    f"[apex-gui] launch batch {job.get('batch_index')}/{job.get('batch_total')}: {' '.join(args)}\n"
                )
                log_fp.flush()
                procs.append(
                    (
                        job,
                        subprocess.Popen(
                            args,
                            stdout=log_fp,
                            stderr=subprocess.STDOUT,
                            cwd=job["workdir"],
                            text=True,
                        ),
                    )
                )
            for job, proc in procs:
                code = proc.wait()
                exit_codes.append(code)
                log_fp.write(
                    f"[apex-gui] batch {job.get('batch_index')}/{job.get('batch_total')} exited with code {code}\n"
                )
                log_fp.flush()
        final_code = 0 if all(code == 0 for code in exit_codes) else 1
        _append_log(log_file, f"[apex-gui] submit group done with code {final_code}\n")
    except Exception:
        final_code = 1
        _append_log(log_file, traceback.format_exc())
    _write_status(status_file, final_code)
    return final_code


def run_retrieve_group(
    workdir: str,
    global_file: str,
    log_file: str,
    status_file: str,
    workflow_ids: Sequence[str],
) -> int:
    codes: List[int] = []
    try:
        os.makedirs(os.path.dirname(log_file) or ".", exist_ok=True)
        with open(log_file, "a", encoding="utf-8") as log_fp:
            for idx, workflow_id in enumerate(workflow_ids, start=1):
                log_fp.write(f"[apex-gui] retrieve workflow {idx}/{len(workflow_ids)}: {workflow_id}\n")
                log_fp.flush()
                args = [
                    sys.executable,
                    "-m",
                    "apex",
                    "retrieve",
                    "-i",
                    workflow_id,
                    "-w",
                    workdir,
                    "-c",
                    global_file,
                ]
                codes.append(
                    subprocess.call(
                        args,
                        stdout=log_fp,
                        stderr=subprocess.STDOUT,
                        cwd=workdir,
                        text=True,
                    )
                )
        final_code = 0 if all(code == 0 for code in codes) else 1
    except Exception:
        final_code = 1
        _append_log(log_file, traceback.format_exc())
    _write_status(status_file, final_code)
    return final_code


def main(argv: Sequence[str] = None) -> int:
    parser = argparse.ArgumentParser(description="Background helpers for apex gui.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    submit_parser = subparsers.add_parser("submit-group")
    submit_parser.add_argument("meta_path")
    submit_parser.add_argument("log_file")
    submit_parser.add_argument("status_file")

    retrieve_parser = subparsers.add_parser("retrieve")
    retrieve_parser.add_argument("workdir")
    retrieve_parser.add_argument("global_file")
    retrieve_parser.add_argument("log_file")
    retrieve_parser.add_argument("status_file")
    retrieve_parser.add_argument("workflow_ids", nargs="+")

    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command == "submit-group":
        return run_submit_group(args.meta_path, args.log_file, args.status_file)
    return run_retrieve_group(
        args.workdir,
        args.global_file,
        args.log_file,
        args.status_file,
        args.workflow_ids,
    )


if __name__ == "__main__":
    raise SystemExit(main())
