#!/usr/bin/env python3
"""Aggregate benchmark evidence into a fail-closed manifest."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from typing import Any, Iterable, Sequence

from benchmark_lib import (
    BENCHMARK_ID,
    EXPECTED_MODEL_SHA256,
    SCHEMA_VERSION,
    canonical_hash,
    describe_file,
    identity_reasons,
    load_json,
    utc_now,
    write_json_new,
)
from compare_parity import DEFAULT_THRESHOLDS


EXPECTED_CASES = ("Ti_hcp", "V_bcc", "TiV_B2")
EXPECTED_MODES = ("run0", "md")
EXPECTED_DEVICES = ("cpu", "gpu")


def _git_provenance(script_dir: Path) -> dict[str, Any]:
    def call(*args: str) -> tuple[int, str]:
        completed = subprocess.run(
            ["git", "-C", str(script_dir), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        return completed.returncode, completed.stdout.strip()

    commit_rc, commit = call("rev-parse", "HEAD")
    status_rc, status = call("status", "--porcelain")
    return {
        "apex_git_commit": commit if commit_rc == 0 else None,
        "apex_git_dirty": bool(status) if status_rc == 0 else None,
    }


def _load_records(paths: Iterable[Path]) -> list[tuple[Path, dict[str, Any]]]:
    return [(path, load_json(path)) for path in sorted(paths)]


def _evidence_file(value: dict[str, Any], key: str, record_path: Path, workspace: Path) -> dict[str, Any] | None:
    descriptor = value.get(key)
    if not isinstance(descriptor, dict) or not descriptor.get("path"):
        return None
    path = Path(descriptor["path"])
    if not path.is_absolute():
        path = record_path.parent / path
    if not path.is_file():
        return None
    return describe_file(path, workspace)


def _artifact_hashes(workspace: Path, excluded: set[Path]) -> list[dict[str, Any]]:
    records = []
    excluded_resolved = {path.resolve() for path in excluded}
    for path in sorted(workspace.rglob("*")):
        if not path.is_file() or path.resolve() in excluded_resolved:
            continue
        records.append(describe_file(path, workspace))
    return records


def build(args: argparse.Namespace) -> dict[str, Any]:
    workspace = args.workspace.resolve()
    output = args.output.resolve()
    checkpoint = args.checkpoint.resolve()
    if not workspace.is_dir():
        raise FileNotFoundError(workspace)
    if not checkpoint.is_file():
        raise FileNotFoundError(checkpoint)
    identity, identity_issues = identity_reasons(
        checkpoint, args.image_ref, args.image_digest
    )

    cases_path = workspace / "cases" / "cases.json"
    cases_record = load_json(cases_path) if cases_path.is_file() else None
    cases = cases_record.get("cases", []) if cases_record else []

    run_records = _load_records(workspace.glob("runs/*/*/*/result.json"))
    parity_records = _load_records(workspace.glob("parity/*/*.json"))
    phonon_records = _load_records(workspace.glob("phonolammps/*/*/result.json"))
    all_records = run_records + parity_records + phonon_records

    reasons: list[str] = []
    hard_failures: list[str] = []
    if identity["checkpoint_pt"]["sha256"] != EXPECTED_MODEL_SHA256:
        hard_failures.append("CHECKPOINT_SHA256_MISMATCH")
    reasons.extend(
        issue for issue in identity_issues if issue != "CHECKPOINT_SHA256_MISMATCH"
    )
    if cases_record is None:
        reasons.append("CASES_MANIFEST_MISSING")
    else:
        observed_cases = {case.get("name") for case in cases}
        for name in EXPECTED_CASES:
            if name not in observed_cases:
                reasons.append(f"CASE_MISSING:{name}")

    run_by_key: dict[tuple[str, str, str], tuple[Path, dict[str, Any]]] = {}
    for path, record in run_records:
        key = (record.get("case"), record.get("device"), record.get("mode"))
        if key in run_by_key:
            hard_failures.append(f"DUPLICATE_RUN:{'/'.join(str(x) for x in key)}")
        run_by_key[key] = (path, record)
        if record.get("status") == "failed":
            hard_failures.append(f"RUN_FAILED:{'/'.join(str(x) for x in key)}")
        elif record.get("status") != "passed":
            reasons.append(f"RUN_NOT_PASSED:{'/'.join(str(x) for x in key)}")
    for case in EXPECTED_CASES:
        for device in EXPECTED_DEVICES:
            for mode in EXPECTED_MODES:
                if (case, device, mode) not in run_by_key:
                    reasons.append(f"RUN_MISSING:{case}/{device}/{mode}")

    parity_by_key: dict[tuple[str, str], tuple[Path, dict[str, Any]]] = {}
    for path, record in parity_records:
        key = (record.get("case"), record.get("mode"))
        if key in parity_by_key:
            hard_failures.append(f"DUPLICATE_PARITY:{'/'.join(str(x) for x in key)}")
        parity_by_key[key] = (path, record)
        if record.get("status") == "failed":
            hard_failures.append(f"PARITY_FAILED:{'/'.join(str(x) for x in key)}")
        elif record.get("status") != "passed":
            reasons.append(f"PARITY_NOT_PASSED:{'/'.join(str(x) for x in key)}")
    for case in EXPECTED_CASES:
        for mode in EXPECTED_MODES:
            if (case, mode) not in parity_by_key:
                reasons.append(f"PARITY_MISSING:{case}/{mode}")

    expected_phonon = ("Ti_hcp", "gpu")
    phonon_by_key: dict[tuple[str, str], tuple[Path, dict[str, Any]]] = {}
    for path, record in phonon_records:
        key = (record.get("case"), record.get("device"))
        if key in phonon_by_key:
            hard_failures.append(f"DUPLICATE_PHONOLAMMPS:{'/'.join(str(x) for x in key)}")
        phonon_by_key[key] = (path, record)
        if record.get("status") == "failed":
            hard_failures.append(f"PHONOLAMMPS_FAILED:{'/'.join(str(x) for x in key)}")
        elif record.get("status") != "passed":
            reasons.append(f"PHONOLAMMPS_NOT_PASSED:{'/'.join(str(x) for x in key)}")
        validation = record.get("force_constants_validation")
        if not isinstance(validation, dict) or validation.get("status") != "passed":
            hard_failures.append(
                f"PHONOLAMMPS_FORCE_CONSTANTS_INVALID:{'/'.join(str(x) for x in key)}"
            )
    if expected_phonon not in phonon_by_key:
        reasons.append("PHONOLAMMPS_MISSING:Ti_hcp/gpu")

    case_summaries = []
    for generated_case in cases:
        case = dict(generated_case)
        name = case.get("name")
        related = [
            record
            for key, (_, record) in run_by_key.items()
            if key[0] == name
        ]
        related.extend(
            record
            for key, (_, record) in parity_by_key.items()
            if key[0] == name
        )
        if name == "Ti_hcp" and expected_phonon in phonon_by_key:
            related.append(phonon_by_key[expected_phonon][1])
        expected_count = 7 if name == "Ti_hcp" else 6
        if not related:
            case["status"] = "untested"
        elif any(record.get("status") == "failed" for record in related):
            case["status"] = "failed"
        elif len(related) == expected_count and all(
            record.get("status") == "passed" for record in related
        ):
            case["status"] = "passed"
        else:
            case["status"] = "inconclusive"
        case_summaries.append(case)

    pt2_by_device: dict[str, dict[str, dict[str, Any]]] = {
        "cpu": {},
        "gpu": {},
    }
    environment_by_hash: dict[str, dict[str, Any]] = {}
    expected_image = identity["image"]
    expected_checkpoint_hash = identity["checkpoint_pt"]["sha256"]
    for path, record in run_records + phonon_records:
        record_identity = record.get("identity", {})
        if (
            record_identity.get("checkpoint_pt", {}).get("sha256")
            != expected_checkpoint_hash
        ):
            hard_failures.append(
                f"RECORD_CHECKPOINT_MISMATCH:{path.relative_to(workspace)}"
            )
        if record_identity.get("image") != expected_image:
            reasons.append(f"RECORD_IMAGE_MISMATCH:{path.relative_to(workspace)}")
        runtime = record_identity.get("runtime_model_pt2", {})
        record_device = record.get("device")
        runtime_device = runtime.get("device")
        runtime_hash = runtime.get("sha256")
        if runtime_device != record_device or runtime_device not in pt2_by_device:
            hard_failures.append(
                f"RECORD_RUNTIME_DEVICE_MISMATCH:{path.relative_to(workspace)}"
            )
        elif not runtime_hash:
            hard_failures.append(
                f"RECORD_RUNTIME_SHA256_MISSING:{path.relative_to(workspace)}"
            )
        else:
            aggregate = pt2_by_device[runtime_device].setdefault(
                runtime_hash,
                {
                    "device": runtime_device,
                    "sha256": runtime_hash,
                    "bytes": runtime.get("bytes"),
                    "sources": [],
                },
            )
            aggregate["sources"].append(str(path.relative_to(workspace)))
        environment = record.get("environment")
        if isinstance(environment, dict):
            fingerprint = canonical_hash(environment)
            environment_by_hash.setdefault(
                fingerprint,
                {
                    "fingerprint_sha256": fingerprint,
                    "source": str(path.relative_to(workspace)),
                    "gpu_inventory": environment.get("gpu_inventory", []),
                    "driver_version": environment.get("driver_version"),
                    "cuda_version": environment.get("cuda_version"),
                    "cuda_versions": environment.get("cuda_versions", {}),
                    "packages": environment.get("packages", {}),
                    "executables": environment.get("executables", {}),
                    "selected_environment": environment.get(
                        "selected_environment", {}
                    ),
                    "lammps_plugins": environment.get("lammps_plugins", []),
                },
            )
    runtime_models = []
    for device in ("cpu", "gpu"):
        hashes = pt2_by_device[device]
        if len(hashes) > 1:
            hard_failures.append(f"MULTIPLE_{device.upper()}_RUNTIME_MODEL_HASHES")
        runtime_models.extend(hashes[key] for key in sorted(hashes))
    if runtime_models:
        cpu_hashes = set(pt2_by_device["cpu"])
        gpu_hashes = set(pt2_by_device["gpu"])
        if not cpu_hashes:
            reasons.append("CPU_RUNTIME_MODEL_PT2_MISSING")
        if not gpu_hashes:
            reasons.append("GPU_RUNTIME_MODEL_PT2_MISSING")
        if cpu_hashes & gpu_hashes:
            hard_failures.append("RUNTIME_MODEL_HASH_NOT_DEVICE_DISTINCT")
    identity["runtime_models_pt2"] = runtime_models

    run_summaries = []
    for path, record in run_records:
        run_summaries.append(
            {
                "case": record.get("case"),
                "device": record.get("device"),
                "mode": record.get("mode"),
                "status": record.get("status"),
                "exit_code": record.get("exit_code"),
                "timed_out": record.get("timed_out"),
                "result": describe_file(path, workspace),
                "stdout": _evidence_file(record, "stdout", path, workspace),
                "stderr": _evidence_file(record, "stderr", path, workspace),
                "reason_codes": record.get("reason_codes", []),
            }
        )
    parity_summaries = [
        {
            "case": record.get("case"),
            "mode": record.get("mode"),
            "status": record.get("status"),
            "metrics": record.get("metrics"),
            "thresholds": record.get("thresholds"),
            "result": describe_file(path, workspace),
            "reason_codes": record.get("reason_codes", []),
        }
        for path, record in parity_records
    ]
    phonon_summaries = [
        {
            "case": record.get("case"),
            "device": record.get("device"),
            "status": record.get("status"),
            "exit_code": record.get("exit_code"),
            "timed_out": record.get("timed_out"),
            "result": describe_file(path, workspace),
            "stdout": _evidence_file(record, "stdout", path, workspace),
            "stderr": _evidence_file(record, "stderr", path, workspace),
            "force_constants_validation": record.get(
                "force_constants_validation"
            ),
            "reason_codes": record.get("reason_codes", []),
        }
        for path, record in phonon_records
    ]

    if not all_records:
        status = "untested"
        reasons.append("NO_RUNTIME_EXECUTION_EVIDENCE")
    elif hard_failures:
        status = "failed"
    elif reasons:
        status = "inconclusive"
    else:
        status = "passed"
    reason_codes = sorted(set(hard_failures + reasons))
    excluded = {output}
    artifacts = _artifact_hashes(workspace, excluded)
    provenance = _git_provenance(Path(__file__).resolve().parent)
    manifest = {
        "$schema": str((Path(__file__).resolve().parent / "manifest.schema.json")),
        "schema_version": SCHEMA_VERSION,
        "benchmark_id": BENCHMARK_ID,
        "created_at": utc_now(),
        "status": status,
        "reason_codes": reason_codes,
        "provenance": provenance,
        "identity": identity,
        "environment": {
            "observations": [
                environment_by_hash[key] for key in sorted(environment_by_hash)
            ]
        },
        "thresholds": DEFAULT_THRESHOLDS,
        "cases": case_summaries,
        "runs": run_summaries,
        "parity": parity_summaries,
        "phonolammps": phonon_summaries,
        "artifact_hashes": artifacts,
        "notes": [
            "The checkpoint .pt is identity/probe input only; LAMMPS receives a device-specific .pt2 runtime artifact.",
            "CPU and T4 GPU runtime .pt2 hashes must be distinct and internally consistent across all cases.",
            "phonoLAMMPS evidence must include all finite N^2 FORCE_CONSTANTS blocks for the requested supercell.",
            "Only status=passed is positive image/model/GPU compatibility evidence.",
        ],
    }
    write_json_new(output, manifest)
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path("workspace"))
    parser.add_argument("--checkpoint", required=True, type=Path)
    parser.add_argument("--image-ref", required=True)
    parser.add_argument("--image-digest")
    parser.add_argument("--output", type=Path, default=Path("workspace/manifest.json"))
    args = parser.parse_args(argv)
    manifest = build(args)
    print(f"manifest: {args.output}")
    print(f"status={manifest['status']}")
    for reason in manifest["reason_codes"]:
        print(f"  - {reason}")
    return 0 if manifest["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
