#!/usr/bin/env python3
"""Compare one CPU/GPU LAMMPS result pair with explicit numerical gates."""

from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Any, Sequence

from benchmark_lib import (
    BENCHMARK_ID,
    SCHEMA_VERSION,
    describe_file,
    load_json,
    utc_now,
    write_json_new,
)


DEFAULT_THRESHOLDS = {
    "energy_per_atom_abs_eV": 1.0e-4,
    "force_rms_eV_per_A": 1.0e-3,
    "force_max_abs_eV_per_A": 5.0e-3,
    "stress_max_abs_GPa": 1.0e-2,
}


def _force_map(record: dict[str, Any]) -> dict[int, tuple[float, float, float]]:
    forces = record["observables"]["forces"]
    mapping = {}
    for atom in forces:
        atom_id = int(atom["id"])
        if atom_id in mapping:
            raise ValueError(f"duplicate atom id: {atom_id}")
        vector = tuple(float(atom[key]) for key in ("fx", "fy", "fz"))
        if not all(math.isfinite(value) for value in vector):
            raise ValueError(f"non-finite force for atom {atom_id}")
        mapping[atom_id] = vector
    return mapping


def compare(
    cpu: dict[str, Any], gpu: dict[str, Any], thresholds: dict[str, float]
) -> tuple[dict[str, float] | None, list[str], list[str]]:
    failures: list[str] = []
    inconclusive: list[str] = []
    if cpu.get("status") != "passed":
        inconclusive.append("CPU_RESULT_NOT_PASSED")
    if gpu.get("status") != "passed":
        inconclusive.append("GPU_RESULT_NOT_PASSED")
    for key in ("case", "mode"):
        if cpu.get(key) != gpu.get(key):
            inconclusive.append(f"{key.upper()}_MISMATCH")
    if cpu.get("device") != "cpu" or gpu.get("device") != "gpu":
        inconclusive.append("DEVICE_LABEL_MISMATCH")
    cpu_identity = cpu.get("identity", {})
    gpu_identity = gpu.get("identity", {})
    if cpu_identity.get("checkpoint_pt", {}).get("sha256") != gpu_identity.get(
        "checkpoint_pt", {}
    ).get("sha256"):
        inconclusive.append("CHECKPOINT_SHA256_MISMATCH")
    if cpu_identity.get("image") != gpu_identity.get("image"):
        inconclusive.append("IMAGE_IDENTITY_MISMATCH")
    cpu_runtime = cpu_identity.get("runtime_model_pt2", {})
    gpu_runtime = gpu_identity.get("runtime_model_pt2", {})
    if cpu_runtime.get("device") != "cpu" or gpu_runtime.get("device") != "gpu":
        failures.append("RUNTIME_MODEL_DEVICE_LABEL_MISMATCH")
    if not str(cpu_runtime.get("path", "")).endswith(".pt2") or not str(
        gpu_runtime.get("path", "")
    ).endswith(".pt2"):
        failures.append("RUNTIME_MODEL_NOT_PT2")
    cpu_runtime_hash = cpu_runtime.get("sha256")
    gpu_runtime_hash = gpu_runtime.get("sha256")
    if not cpu_runtime_hash or not gpu_runtime_hash:
        inconclusive.append("RUNTIME_MODEL_SHA256_MISSING")
    elif cpu_runtime_hash == gpu_runtime_hash:
        failures.append("RUNTIME_MODEL_HASH_NOT_DEVICE_DISTINCT")
    if inconclusive:
        return None, failures, sorted(set(inconclusive))
    if failures:
        return None, sorted(set(failures)), []

    cpu_obs = cpu.get("observables")
    gpu_obs = gpu.get("observables")
    if not isinstance(cpu_obs, dict) or not isinstance(gpu_obs, dict):
        return None, failures, ["OBSERVABLES_MISSING"]
    if cpu_obs.get("natoms") != gpu_obs.get("natoms"):
        return None, failures, ["ATOM_COUNT_MISMATCH"]
    natoms = int(cpu_obs["natoms"])
    if natoms <= 0:
        return None, failures, ["INVALID_ATOM_COUNT"]
    try:
        energy_delta = abs(float(cpu_obs["energy_eV"]) - float(gpu_obs["energy_eV"])) / natoms
        cpu_forces = _force_map(cpu)
        gpu_forces = _force_map(gpu)
        if set(cpu_forces) != set(gpu_forces):
            return None, failures, ["FORCE_ATOM_IDS_MISMATCH"]
        components = [
            cpu_forces[atom_id][axis] - gpu_forces[atom_id][axis]
            for atom_id in sorted(cpu_forces)
            for axis in range(3)
        ]
        force_rms = math.sqrt(sum(value * value for value in components) / len(components))
        force_max = max(abs(value) for value in components)
        stress_keys = ("pxx", "pyy", "pzz", "pxy", "pxz", "pyz")
        # LAMMPS metal pressure is bar; 1 bar = 1e-4 GPa.
        stress_max = max(
            abs(
                float(cpu_obs["stress_bar"][key])
                - float(gpu_obs["stress_bar"][key])
            )
            * 1.0e-4
            for key in stress_keys
        )
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        return None, failures, [f"COMPARISON_PARSE_FAILED:{type(exc).__name__}:{exc}"]
    metrics = {
        "energy_per_atom_abs_eV": energy_delta,
        "force_rms_eV_per_A": force_rms,
        "force_max_abs_eV_per_A": force_max,
        "stress_max_abs_GPa": stress_max,
    }
    for name, value in metrics.items():
        if not math.isfinite(value):
            failures.append(f"NONFINITE_METRIC:{name}")
        elif value > thresholds[name]:
            failures.append(f"THRESHOLD_EXCEEDED:{name}")
    return metrics, sorted(set(failures)), []


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cpu", required=True, type=Path)
    parser.add_argument("--gpu", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--energy-tol", type=float, default=DEFAULT_THRESHOLDS["energy_per_atom_abs_eV"])
    parser.add_argument("--force-rms-tol", type=float, default=DEFAULT_THRESHOLDS["force_rms_eV_per_A"])
    parser.add_argument("--force-max-tol", type=float, default=DEFAULT_THRESHOLDS["force_max_abs_eV_per_A"])
    parser.add_argument("--stress-tol", type=float, default=DEFAULT_THRESHOLDS["stress_max_abs_GPa"])
    args = parser.parse_args(argv)
    thresholds = {
        "energy_per_atom_abs_eV": args.energy_tol,
        "force_rms_eV_per_A": args.force_rms_tol,
        "force_max_abs_eV_per_A": args.force_max_tol,
        "stress_max_abs_GPa": args.stress_tol,
    }
    if any((not math.isfinite(value) or value < 0) for value in thresholds.values()):
        parser.error("all tolerances must be finite and non-negative")
    cpu = load_json(args.cpu)
    gpu = load_json(args.gpu)
    metrics, failures, inconclusive = compare(cpu, gpu, thresholds)
    if failures:
        status = "failed"
        reasons = failures
    elif inconclusive:
        status = "inconclusive"
        reasons = inconclusive
    else:
        status = "passed"
        reasons = []
    record = {
        "schema_version": SCHEMA_VERSION,
        "benchmark_id": BENCHMARK_ID,
        "kind": "cpu_gpu_parity",
        "created_at": utc_now(),
        "status": status,
        "reason_codes": reasons,
        "case": cpu.get("case"),
        "mode": cpu.get("mode"),
        "thresholds": thresholds,
        "metrics": metrics,
        "inputs": {
            "cpu": describe_file(args.cpu),
            "gpu": describe_file(args.gpu),
        },
    }
    write_json_new(args.output, record)
    print(f"{record['case']} {record['mode']} parity: {status}")
    for reason in reasons:
        print(f"  - {reason}")
    return 0 if status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
