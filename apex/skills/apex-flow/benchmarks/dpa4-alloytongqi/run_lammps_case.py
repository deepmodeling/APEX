#!/usr/bin/env python3
"""Run one generated LAMMPS case and capture fail-closed evidence."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Sequence

from benchmark_lib import (
    BENCHMARK_ID,
    SCHEMA_VERSION,
    capture_runtime_environment,
    describe_file,
    detect_fatal_output,
    identity_reasons,
    missing_package_reasons,
    resolve_lammps_plugin_path,
    run_monitored,
    runtime_model_identity,
    single_rank_command_reasons,
    t4_environment_reasons_for_device,
    utc_now,
    write_json_new,
)


RESULT_RE = re.compile(r"^BENCH_RESULT\s+(?P<fields>.+)$", re.MULTILINE)


def _parse_result_line(stdout: str) -> dict[str, Any]:
    matches = list(RESULT_RE.finditer(stdout))
    if len(matches) != 1:
        raise ValueError(f"expected exactly one BENCH_RESULT line, found {len(matches)}")
    values: dict[str, float] = {}
    for token in matches[0].group("fields").split():
        if "=" not in token:
            raise ValueError(f"malformed BENCH_RESULT token: {token!r}")
        key, raw = token.split("=", 1)
        value = float(raw)
        if not math.isfinite(value):
            raise ValueError(f"non-finite BENCH_RESULT value: {key}={raw}")
        values[key] = value
    required = {"natoms", "pe", "pxx", "pyy", "pzz", "pxy", "pxz", "pyz"}
    missing = sorted(required - set(values))
    if missing:
        raise ValueError(f"BENCH_RESULT missing fields: {', '.join(missing)}")
    natoms = int(values.pop("natoms"))
    if natoms <= 0:
        raise ValueError(f"invalid atom count: {natoms}")
    return {
        "natoms": natoms,
        "energy_eV": values.pop("pe"),
        "stress_bar": values,
    }


def _parse_force_dump(path: Path, expected_atoms: int) -> list[dict[str, Any]]:
    lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    headers = [i for i, line in enumerate(lines) if line.startswith("ITEM: ATOMS ")]
    if not headers:
        raise ValueError("forces.dump has no ITEM: ATOMS block")
    start = headers[-1]
    columns = lines[start].split()[2:]
    required = ("id", "type", "x", "y", "z", "fx", "fy", "fz")
    if any(name not in columns for name in required):
        raise ValueError(f"forces.dump columns are incomplete: {columns}")
    indices = {name: columns.index(name) for name in required}
    records = []
    for line in lines[start + 1 :]:
        if line.startswith("ITEM:"):
            break
        tokens = line.split()
        if not tokens:
            continue
        try:
            record = {
                "id": int(tokens[indices["id"]]),
                "type": int(tokens[indices["type"]]),
            }
            for name in ("x", "y", "z", "fx", "fy", "fz"):
                value = float(tokens[indices[name]])
                if not math.isfinite(value):
                    raise ValueError(f"non-finite {name} for atom {record['id']}")
                record[name] = value
        except (IndexError, ValueError) as exc:
            raise ValueError(f"invalid forces.dump row: {line!r}: {exc}") from exc
        records.append(record)
    records.sort(key=lambda item: item["id"])
    if len(records) != expected_atoms:
        raise ValueError(
            f"forces.dump atom count {len(records)} != BENCH_RESULT {expected_atoms}"
        )
    if [item["id"] for item in records] != list(range(1, expected_atoms + 1)):
        raise ValueError("forces.dump atom ids are not contiguous and one-based")
    return records


def _prepare_output(
    case_dir: Path, runtime_model: Path, output: Path, mode: str, device: str
) -> tuple[Path, Path, Path]:
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"refusing to overwrite non-empty evidence directory: {output}")
    output.mkdir(parents=True, exist_ok=True)
    input_source = case_dir / f"in.{mode}.lammps"
    data_source = case_dir / "structure.data"
    for source in (input_source, data_source):
        if not source.is_file():
            raise FileNotFoundError(source)
    input_target = output / input_source.name
    data_target = output / data_source.name
    runtime_target = output / f"runtime.{device}.pt2"
    shutil.copy2(input_source, input_target)
    shutil.copy2(data_source, data_target)
    shutil.copy2(runtime_model, runtime_target)
    return input_target, data_target, runtime_target


def _load_command(raw: str) -> list[str]:
    value = json.loads(raw)
    if not isinstance(value, list) or not value or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ValueError("--command-json must be a non-empty JSON string array")
    return value


def _checkpoint_probe(
    checkpoint: Path, env: dict[str, str], timeout: float, cwd: Path
) -> dict[str, Any]:
    command = [
        "dp",
        "--pt",
        "show",
        str(checkpoint),
        "type-map",
        "descriptor",
        "fitting-net",
        "size",
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
        return {
            "command": command,
            "exit_code": completed.returncode,
            "timed_out": False,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
    except FileNotFoundError as exc:
        return {
            "command": command,
            "exit_code": None,
            "timed_out": False,
            "stdout": "",
            "stderr": f"FileNotFoundError: {exc}",
        }
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        return {
            "command": command,
            "exit_code": None,
            "timed_out": True,
            "stdout": stdout,
            "stderr": stderr,
        }


def run(args: argparse.Namespace) -> dict[str, Any]:
    case_dir = args.case_dir.resolve()
    checkpoint = args.checkpoint.resolve()
    runtime_model = args.runtime_model.resolve()
    if not checkpoint.is_file():
        raise FileNotFoundError(checkpoint)
    if not runtime_model.is_file():
        raise FileNotFoundError(runtime_model)
    if runtime_model.suffix.lower() != ".pt2":
        raise ValueError("--runtime-model must be a device-specific .pt2 file")
    input_path, data_path, runtime_target = _prepare_output(
        case_dir, runtime_model, args.output, args.mode, args.device
    )
    base_command = _load_command(args.command_json)
    command = [
        *base_command,
        "-in",
        input_path.name,
        "-var",
        "runtime_model",
        runtime_target.name,
    ]
    child_env = os.environ.copy()
    child_env["OMP_NUM_THREADS"] = "1"
    child_env["DP_INTRA_OP_PARALLELISM_THREADS"] = "1"
    child_env["DP_INTER_OP_PARALLELISM_THREADS"] = "1"
    if args.device == "cpu":
        child_env["CUDA_VISIBLE_DEVICES"] = ""
    else:
        child_env["CUDA_VISIBLE_DEVICES"] = args.gpu_index
    plugin_path, plugins, plugin_issues = resolve_lammps_plugin_path(
        args.lammps_plugin_path, os.environ.get("LAMMPS_PLUGIN_PATH")
    )
    if plugin_path:
        child_env["LAMMPS_PLUGIN_PATH"] = plugin_path

    identity, identity_issues = identity_reasons(
        checkpoint, args.image_ref, args.image_digest
    )
    runtime_identity, runtime_issues = runtime_model_identity(
        runtime_target, args.device, args.output
    )
    identity["runtime_model_pt2"] = runtime_identity
    environment = capture_runtime_environment()
    environment["selected_environment"] = {
        key: child_env.get(key)
        for key in (
            "CONDA_DEFAULT_ENV",
            "CUDA_VISIBLE_DEVICES",
            "LAMMPS_PLUGIN_PATH",
            "OMP_NUM_THREADS",
            "DP_INTRA_OP_PARALLELISM_THREADS",
            "DP_INTER_OP_PARALLELISM_THREADS",
        )
    }
    environment["lammps_plugins"] = plugins
    setup_failures = single_rank_command_reasons(
        base_command, {"lmp", "lmp_mpi", "lmp_serial", "lammps", "dpa4-lmp"}
    )
    setup_failures.extend(plugin_issues)
    setup_failures.extend(
        t4_environment_reasons_for_device(
            environment, args.gpu_index, args.device
        )
    )
    started_at = utc_now()
    start = time.monotonic()
    checkpoint_probe = _checkpoint_probe(
        checkpoint, child_env, min(args.timeout, 120.0), args.output
    )
    checkpoint_probe_stdout = args.output / "dp_show.stdout.log"
    checkpoint_probe_stderr = args.output / "dp_show.stderr.log"
    checkpoint_probe_stdout.write_text(checkpoint_probe["stdout"], encoding="utf-8")
    checkpoint_probe_stderr.write_text(checkpoint_probe["stderr"], encoding="utf-8")
    execution_error = None
    if (
        setup_failures
        or checkpoint_probe["exit_code"] != 0
        or checkpoint_probe["timed_out"]
    ):
        execution_error = "LAMMPS skipped because setup or checkpoint probe did not pass"
        execution = {
            "exit_code": None,
            "timed_out": False,
            "stdout": "",
            "stderr": execution_error,
            "gpu_process_samples": [],
            "root_pid": None,
        }
    else:
        try:
            execution = run_monitored(command, args.output, child_env, args.timeout)
        except (FileNotFoundError, OSError) as exc:
            execution_error = f"{type(exc).__name__}: {exc}"
            execution = {
                "exit_code": None,
                "timed_out": False,
                "stdout": "",
                "stderr": execution_error,
                "gpu_process_samples": [],
                "root_pid": None,
            }
    # Successful DeepMD .pt2 GPU loading is explicit runtime evidence emitted
    # by the backend.  Keep the high-frequency nvidia-smi samples as the
    # primary process proof, but retain this independent fallback for tiny
    # run-0 jobs that can finish between process-table samples.
    gpu_backend_loaded = bool(
        re.search(
            r"load model .*\.pt2 to gpu\s+\d+",
            execution.get("stdout", "") + "\n" + execution.get("stderr", ""),
            re.IGNORECASE,
        )
    )
    duration = time.monotonic() - start
    finished_at = utc_now()
    stdout_path = args.output / "stdout.log"
    stderr_path = args.output / "stderr.log"
    stdout_path.write_text(execution["stdout"], encoding="utf-8")
    stderr_path.write_text(execution["stderr"], encoding="utf-8")

    failure_reasons: list[str] = list(setup_failures)
    inconclusive_reasons: list[str] = []
    if checkpoint_probe["timed_out"]:
        failure_reasons.append("DP_SHOW_TIMEOUT")
    if checkpoint_probe["exit_code"] != 0:
        failure_reasons.append("DP_SHOW_NONZERO_EXIT_CODE")
    failure_reasons.extend(
        f"DP_SHOW:{reason}"
        for reason in detect_fatal_output(
            checkpoint_probe["stdout"], checkpoint_probe["stderr"]
        )
    )
    if execution_error:
        failure_reasons.append("COMMAND_NOT_STARTED")
    if execution["timed_out"]:
        failure_reasons.append("COMMAND_TIMEOUT")
    if execution["exit_code"] != 0:
        failure_reasons.append("NONZERO_EXIT_CODE")
    failure_reasons.extend(detect_fatal_output(execution["stdout"], execution["stderr"]))
    for reason in identity_issues:
        if reason == "CHECKPOINT_SHA256_MISMATCH":
            failure_reasons.append(reason)
        else:
            inconclusive_reasons.append(reason)
    failure_reasons.extend(runtime_issues)
    inconclusive_reasons.extend(missing_package_reasons(environment))

    samples = execution["gpu_process_samples"]
    device_evidence = {
        "requested_device": args.device,
        "cuda_visible_devices": child_env["CUDA_VISIBLE_DEVICES"],
        "observed_benchmark_gpu_process": bool(samples),
        "deepmd_backend_reported_gpu_load": gpu_backend_loaded,
        "gpu_process_samples": samples,
        "criterion": (
            "GPU requires a benchmark process or descendant in nvidia-smi; "
            "CPU requires no such process while CUDA_VISIBLE_DEVICES is empty"
        ),
    }
    if args.device == "gpu":
        if not environment["gpu_inventory"]:
            inconclusive_reasons.append("GPU_INVENTORY_NOT_OBSERVED")
        if not environment.get("driver_version"):
            inconclusive_reasons.append("NVIDIA_DRIVER_VERSION_NOT_OBSERVED")
        if not environment.get("cuda_version"):
            inconclusive_reasons.append("CUDA_VERSION_NOT_OBSERVED")
        if not samples and not gpu_backend_loaded:
            inconclusive_reasons.append("GPU_PROCESS_USAGE_NOT_OBSERVED")
    elif samples:
        failure_reasons.append("CPU_RUN_OBSERVED_ON_GPU")

    observables = None
    try:
        observables = _parse_result_line(execution["stdout"])
        observables["forces"] = _parse_force_dump(
            args.output / "forces.dump", observables["natoms"]
        )
    except (FileNotFoundError, ValueError) as exc:
        failure_reasons.append(f"OBSERVABLE_PARSE_FAILED:{type(exc).__name__}:{exc}")

    if failure_reasons:
        status = "failed"
        reason_codes = sorted(set(failure_reasons + inconclusive_reasons))
    elif inconclusive_reasons:
        status = "inconclusive"
        reason_codes = sorted(set(inconclusive_reasons))
    else:
        status = "passed"
        reason_codes = []

    artifacts = [
        input_path,
        data_path,
        runtime_target,
        checkpoint_probe_stdout,
        checkpoint_probe_stderr,
        stdout_path,
        stderr_path,
    ]
    force_dump = args.output / "forces.dump"
    if force_dump.is_file():
        artifacts.append(force_dump)
    record = {
        "schema_version": SCHEMA_VERSION,
        "benchmark_id": BENCHMARK_ID,
        "kind": "lammps_case",
        "status": status,
        "reason_codes": reason_codes,
        "case": case_dir.name,
        "mode": args.mode,
        "device": args.device,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_seconds": duration,
        "command": command,
        "working_directory": str(args.output.resolve()),
        "exit_code": execution["exit_code"],
        "timed_out": execution["timed_out"],
        "stdout": describe_file(stdout_path, args.output),
        "stderr": describe_file(stderr_path, args.output),
        "checkpoint_probe": {
            "command": checkpoint_probe["command"],
            "exit_code": checkpoint_probe["exit_code"],
            "timed_out": checkpoint_probe["timed_out"],
            "stdout": describe_file(checkpoint_probe_stdout, args.output),
            "stderr": describe_file(checkpoint_probe_stderr, args.output),
        },
        "identity": identity,
        "environment": environment,
        "device_evidence": device_evidence,
        "observables": observables,
        "artifacts": [describe_file(path, args.output) for path in artifacts],
    }
    write_json_new(args.output / "result.json", record)
    return record


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-dir", required=True, type=Path)
    parser.add_argument("--checkpoint", required=True, type=Path)
    parser.add_argument(
        "--runtime-model",
        required=True,
        type=Path,
        help="device-specific DeepMD AOTI artifact; must end in .pt2",
    )
    parser.add_argument("--mode", required=True, choices=("run0", "md"))
    parser.add_argument("--device", required=True, choices=("cpu", "gpu"))
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--image-ref", required=True)
    parser.add_argument("--image-digest")
    parser.add_argument(
        "--command-json",
        default='["lmp"]',
        help='shell-free command prefix, e.g. ["mpirun","-n","1","lmp"]',
    )
    parser.add_argument("--gpu-index", default="0")
    parser.add_argument(
        "--lammps-plugin-path",
        help=(
            "colon-separated plugin directories; defaults to LAMMPS_PLUGIN_PATH "
            "and must contain libdeepmd_lmpplugin.so"
        ),
    )
    parser.add_argument("--timeout", type=float, default=300.0)
    args = parser.parse_args(argv)
    record = run(args)
    print(f"{record['case']} {record['device']} {record['mode']}: {record['status']}")
    for reason in record["reason_codes"]:
        print(f"  - {reason}")
    return 0 if record["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
