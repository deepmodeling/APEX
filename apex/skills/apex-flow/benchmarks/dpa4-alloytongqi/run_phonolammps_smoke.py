#!/usr/bin/env python3
"""Run an APEX-shaped phonoLAMMPS smoke test and capture evidence."""

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
    t4_environment_reasons,
    utc_now,
    write_json_new,
)


def _command(raw: str) -> list[str]:
    value = json.loads(raw)
    if not isinstance(value, list) or not value or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ValueError("--command-json must be a non-empty JSON string array")
    return value


def _poscar_atom_count(path: Path) -> int:
    """Return the atom count from a VASP 4/5 POSCAR without external packages."""
    lines = path.read_text(encoding="utf-8").splitlines()
    for index in (6, 5):
        if index >= len(lines):
            continue
        tokens = lines[index].split()
        try:
            counts = [int(token) for token in tokens]
        except ValueError:
            continue
        if counts and all(count > 0 for count in counts):
            return sum(counts)
    raise ValueError(f"could not read positive atom counts from POSCAR: {path}")


def validate_force_constants(path: Path, expected_atoms: int) -> dict[str, Any]:
    """Parse a phonopy FORCE_CONSTANTS file and verify every finite 3x3 block."""
    if expected_atoms <= 0:
        raise ValueError("expected atom count must be positive")
    lines = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not lines:
        raise ValueError("FORCE_CONSTANTS is empty")
    header = lines[0].split()
    if len(header) != 1:
        raise ValueError("FORCE_CONSTANTS header must contain exactly one atom count")
    try:
        atom_count = int(header[0])
    except ValueError as exc:
        raise ValueError("FORCE_CONSTANTS atom count is not an integer") from exc
    if atom_count <= 0:
        raise ValueError("FORCE_CONSTANTS atom count must be positive")
    if atom_count != expected_atoms:
        raise ValueError(
            f"FORCE_CONSTANTS atom count {atom_count} != expected {expected_atoms}"
        )

    cursor = 1
    finite_values = 0
    for first in range(1, atom_count + 1):
        for second in range(1, atom_count + 1):
            if cursor >= len(lines):
                raise ValueError(
                    f"FORCE_CONSTANTS is truncated before block {first} {second}"
                )
            pair_tokens = lines[cursor].split()
            cursor += 1
            if len(pair_tokens) != 2:
                raise ValueError(
                    f"FORCE_CONSTANTS block header {first} {second} is malformed"
                )
            try:
                pair = tuple(int(token) for token in pair_tokens)
            except ValueError as exc:
                raise ValueError(
                    f"FORCE_CONSTANTS block header {first} {second} is not integral"
                ) from exc
            if pair != (first, second):
                raise ValueError(
                    "FORCE_CONSTANTS block order mismatch: "
                    f"expected {first} {second}, found {pair[0]} {pair[1]}"
                )
            for row in range(3):
                if cursor >= len(lines):
                    raise ValueError(
                        "FORCE_CONSTANTS is truncated in matrix block "
                        f"{first} {second}, row {row + 1}"
                    )
                values_raw = lines[cursor].split()
                cursor += 1
                if len(values_raw) != 3:
                    raise ValueError(
                        "FORCE_CONSTANTS matrix row must contain three values: "
                        f"block {first} {second}, row {row + 1}"
                    )
                try:
                    values = [float(value) for value in values_raw]
                except ValueError as exc:
                    raise ValueError(
                        "FORCE_CONSTANTS matrix row contains a non-number: "
                        f"block {first} {second}, row {row + 1}"
                    ) from exc
                if not all(math.isfinite(value) for value in values):
                    raise ValueError(
                        "FORCE_CONSTANTS matrix row contains a non-finite value: "
                        f"block {first} {second}, row {row + 1}"
                    )
                finite_values += len(values)
    if cursor != len(lines):
        raise ValueError("FORCE_CONSTANTS contains trailing non-empty data")
    return {
        "status": "passed",
        "expected_atom_count": expected_atoms,
        "atom_count": atom_count,
        "matrix_blocks": atom_count * atom_count,
        "finite_values": finite_values,
        "error": None,
    }


def _prepare(
    case_dir: Path, runtime_model: Path, output: Path
) -> tuple[Path, Path]:
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"refusing to overwrite non-empty evidence directory: {output}")
    output.mkdir(parents=True, exist_ok=True)
    for name in ("POSCAR", "structure.data", "in.phonon.lammps"):
        source = case_dir / name
        if not source.is_file():
            raise FileNotFoundError(source)
        shutil.copy2(source, output / name)
    copied_model = output / "runtime.gpu.pt2"
    shutil.copy2(runtime_model, copied_model)
    input_path = output / "in.phonon.lammps"
    text = input_path.read_text(encoding="utf-8")
    if text.count("${runtime_model}") != 1:
        raise ValueError(
            "in.phonon.lammps must contain exactly one ${runtime_model} placeholder"
        )
    input_path.write_text(
        text.replace("${runtime_model}", copied_model.name), encoding="utf-8"
    )
    return input_path, copied_model


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
        raise ValueError("--runtime-model must be a GPU-specific .pt2 file")
    if args.device != "gpu":
        raise ValueError("this qualification smoke is T4 GPU-only")
    input_path, copied_model = _prepare(case_dir, runtime_model, args.output)
    base_command = _command(args.command_json)
    command = [
        *base_command,
        input_path.name,
        "-c",
        "POSCAR",
        "--dim",
        *(str(value) for value in args.dim),
        "-pa",
        *(str(value) for value in args.primitive_axes),
    ]
    child_env = os.environ.copy()
    child_env["OMP_NUM_THREADS"] = "1"
    child_env["DP_INTRA_OP_PARALLELISM_THREADS"] = "1"
    child_env["DP_INTER_OP_PARALLELISM_THREADS"] = "1"
    child_env["CUDA_VISIBLE_DEVICES"] = (
        "" if args.device == "cpu" else args.gpu_index
    )
    plugin_path, plugins, plugin_issues = resolve_lammps_plugin_path(
        args.lammps_plugin_path, os.environ.get("LAMMPS_PLUGIN_PATH")
    )
    if plugin_path:
        child_env["LAMMPS_PLUGIN_PATH"] = plugin_path
    identity, identity_issues = identity_reasons(
        checkpoint, args.image_ref, args.image_digest
    )
    runtime_identity, runtime_issues = runtime_model_identity(
        copied_model, "gpu", args.output
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
        base_command, {"phonolammps", "dpa4-phonolammps"}
    )
    setup_failures.extend(plugin_issues)
    setup_failures.extend(t4_environment_reasons(environment, args.gpu_index))

    started_at = utc_now()
    start = time.monotonic()
    checkpoint_probe = _checkpoint_probe(
        checkpoint, child_env, min(args.timeout, 120.0), args.output
    )
    checkpoint_stdout = args.output / "dp_show.stdout.log"
    checkpoint_stderr = args.output / "dp_show.stderr.log"
    checkpoint_stdout.write_text(checkpoint_probe["stdout"], encoding="utf-8")
    checkpoint_stderr.write_text(checkpoint_probe["stderr"], encoding="utf-8")
    execution_error = None
    if (
        setup_failures
        or checkpoint_probe["exit_code"] != 0
        or checkpoint_probe["timed_out"]
    ):
        execution_error = (
            "phonoLAMMPS skipped because setup or checkpoint probe did not pass"
        )
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
    gpu_backend_loaded = bool(
        re.search(
            r"load model .*\.pt2 to gpu\s+\d+",
            execution.get("stdout", "") + "\n" + execution.get("stderr", ""),
            re.IGNORECASE,
        )
    )
    duration = time.monotonic() - start
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

    force_constants = args.output / "FORCE_CONSTANTS"
    force_constants_validation: dict[str, Any] = {
        "status": "failed",
        "expected_atom_count": None,
        "atom_count": None,
        "matrix_blocks": 0,
        "finite_values": 0,
        "error": "FORCE_CONSTANTS was not validated",
    }
    if not force_constants.is_file() or force_constants.stat().st_size == 0:
        failure_reasons.append("FORCE_CONSTANTS_MISSING_OR_EMPTY")
        force_constants_validation["error"] = "FORCE_CONSTANTS is missing or empty"
    else:
        try:
            expected_atoms = _poscar_atom_count(args.output / "POSCAR") * math.prod(
                args.dim
            )
            force_constants_validation = validate_force_constants(
                force_constants, expected_atoms
            )
        except (OSError, ValueError) as exc:
            failure_reasons.append("FORCE_CONSTANTS_INVALID")
            force_constants_validation["error"] = f"{type(exc).__name__}: {exc}"

    if failure_reasons:
        status = "failed"
        reasons = sorted(set(failure_reasons + inconclusive_reasons))
    elif inconclusive_reasons:
        status = "inconclusive"
        reasons = sorted(set(inconclusive_reasons))
    else:
        status = "passed"
        reasons = []

    artifact_names = [
        "POSCAR",
        "structure.data",
        "in.phonon.lammps",
        "runtime.gpu.pt2",
        "dp_show.stdout.log",
        "dp_show.stderr.log",
        "stdout.log",
        "stderr.log",
        "FORCE_CONSTANTS",
        "phonopy_disp.yaml",
        "phonopy.yaml",
    ]
    artifacts = [
        describe_file(args.output / name, args.output)
        for name in artifact_names
        if (args.output / name).is_file()
    ]
    record = {
        "schema_version": SCHEMA_VERSION,
        "benchmark_id": BENCHMARK_ID,
        "kind": "phonolammps_smoke",
        "status": status,
        "reason_codes": reasons,
        "case": case_dir.name,
        "device": args.device,
        "supercell_dimension": args.dim,
        "primitive_axes": args.primitive_axes,
        "started_at": started_at,
        "finished_at": utc_now(),
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
            "stdout": describe_file(checkpoint_stdout, args.output),
            "stderr": describe_file(checkpoint_stderr, args.output),
        },
        "identity": identity,
        "environment": environment,
        "device_evidence": {
            "requested_device": args.device,
            "cuda_visible_devices": child_env["CUDA_VISIBLE_DEVICES"],
            "observed_benchmark_gpu_process": bool(samples),
            "deepmd_backend_reported_gpu_load": gpu_backend_loaded,
            "gpu_process_samples": samples,
        },
        "force_constants_validation": force_constants_validation,
        "artifacts": artifacts,
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
        help="T4 GPU-specific DeepMD AOTI artifact; must end in .pt2",
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--image-ref", required=True)
    parser.add_argument("--image-digest")
    parser.add_argument("--device", choices=("gpu",), default="gpu")
    parser.add_argument("--gpu-index", default="0")
    parser.add_argument(
        "--lammps-plugin-path",
        help=(
            "colon-separated plugin directories; defaults to LAMMPS_PLUGIN_PATH "
            "and must contain libdeepmd_lmpplugin.so"
        ),
    )
    parser.add_argument("--dim", nargs=3, type=int, default=[2, 2, 2])
    parser.add_argument(
        "--primitive-axes",
        nargs=9,
        type=float,
        default=[1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0],
    )
    parser.add_argument("--command-json", default='["phonolammps"]')
    parser.add_argument("--timeout", type=float, default=600.0)
    args = parser.parse_args(argv)
    if any(value <= 0 for value in args.dim):
        parser.error("all --dim values must be positive")
    record = run(args)
    print(f"{record['case']} phonoLAMMPS {record['device']}: {record['status']}")
    for reason in record["reason_codes"]:
        print(f"  - {reason}")
    return 0 if record["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
