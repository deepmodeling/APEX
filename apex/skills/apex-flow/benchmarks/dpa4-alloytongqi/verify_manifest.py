#!/usr/bin/env python3
"""Verify manifest invariants and every declared artifact hash without extras."""

from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Sequence

from benchmark_lib import (
    BENCHMARK_ID,
    EXPECTED_MODEL_SHA256,
    HEX_SHA256_RE,
    REQUIRED_PACKAGES,
    SHA256_RE,
    load_json,
    sha256_file,
)


def _safe_artifact_path(root: Path, raw: str) -> Path:
    path = Path(raw)
    resolved = path.resolve() if path.is_absolute() else (root / path).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"artifact path escapes manifest root: {raw}") from exc
    return resolved


def verify(manifest_path: Path, root: Path) -> list[str]:
    manifest = load_json(manifest_path)
    errors: list[str] = []
    required_top = {
        "$schema",
        "schema_version",
        "benchmark_id",
        "created_at",
        "status",
        "reason_codes",
        "provenance",
        "identity",
        "environment",
        "thresholds",
        "cases",
        "runs",
        "parity",
        "phonolammps",
        "artifact_hashes",
        "notes",
    }
    missing_top = sorted(required_top - set(manifest))
    if missing_top:
        errors.append("missing top-level fields: " + ", ".join(missing_top))
    if manifest.get("schema_version") != "2.0.0":
        errors.append("schema_version mismatch")
    if manifest.get("benchmark_id") != BENCHMARK_ID:
        errors.append("benchmark_id mismatch")
    status = manifest.get("status")
    if status not in {"untested", "inconclusive", "failed", "passed"}:
        errors.append(f"invalid status: {status!r}")
    checkpoint = manifest.get("identity", {}).get("checkpoint_pt", {})
    if checkpoint.get("sha256") != EXPECTED_MODEL_SHA256:
        errors.append("checkpoint model.pt sha256 mismatch")
    if checkpoint.get("bytes") != 30403297:
        errors.append("checkpoint model.pt size mismatch")
    pt2 = manifest.get("identity", {}).get("runtime_models_pt2")
    if not isinstance(pt2, list):
        errors.append("identity.runtime_models_pt2 must be an array")
    else:
        for index, descriptor in enumerate(pt2):
            if not isinstance(descriptor, dict):
                errors.append(f"runtime_models_pt2[{index}] is not an object")
                continue
            if not isinstance(descriptor.get("sha256"), str) or not HEX_SHA256_RE.fullmatch(
                descriptor["sha256"]
            ):
                errors.append(f"runtime_models_pt2[{index}] has invalid sha256")
            if not isinstance(descriptor.get("bytes"), int) or descriptor["bytes"] <= 0:
                errors.append(f"runtime_models_pt2[{index}] has invalid size")
            if descriptor.get("device") not in {"cpu", "gpu"}:
                errors.append(f"runtime_models_pt2[{index}] has invalid device")
            sources = descriptor.get("sources")
            if not isinstance(sources, list) or not sources or not all(
                isinstance(source, str) and source for source in sources
            ):
                errors.append(f"runtime_models_pt2[{index}] has invalid sources")
    digest = manifest.get("identity", {}).get("image", {}).get("digest")
    if digest is not None and not (
        isinstance(digest, str) and SHA256_RE.fullmatch(digest)
    ):
        errors.append("image digest is present but invalid")
    if status == "passed" and not (isinstance(digest, str) and SHA256_RE.fullmatch(digest)):
        errors.append("passed manifest lacks full image digest")
    reasons = manifest.get("reason_codes")
    if not isinstance(reasons, list) or not all(
        isinstance(reason, str) and reason for reason in reasons
    ):
        errors.append("reason_codes must be non-empty strings")
    elif len(reasons) != len(set(reasons)):
        errors.append("reason_codes contains duplicates")

    expected_thresholds = {
        "energy_per_atom_abs_eV",
        "force_rms_eV_per_A",
        "force_max_abs_eV_per_A",
        "stress_max_abs_GPa",
    }
    thresholds = manifest.get("thresholds")
    if not isinstance(thresholds, dict) or set(thresholds) != expected_thresholds:
        errors.append("threshold fields mismatch")
    elif any(
        not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or value < 0
        for value in thresholds.values()
    ):
        errors.append("threshold values must be finite and non-negative")

    cases = manifest.get("cases")
    if not isinstance(cases, list):
        errors.append("cases must be an array")
    else:
        case_names = [case.get("name") for case in cases if isinstance(case, dict)]
        if sorted(case_names) != sorted(("Ti_hcp", "V_bcc", "TiV_B2")):
            errors.append("cases must contain Ti_hcp, V_bcc, and TiV_B2 exactly once")
    for key in ("runs", "parity", "phonolammps", "artifact_hashes", "notes"):
        if not isinstance(manifest.get(key), list):
            errors.append(f"{key} must be an array")
    if not isinstance(manifest.get("environment", {}).get("observations"), list):
        errors.append("environment.observations must be an array")

    seen_paths: set[str] = set()
    for descriptor in manifest.get("artifact_hashes", []):
        raw = descriptor.get("path")
        expected = descriptor.get("sha256")
        size = descriptor.get("bytes")
        if not isinstance(raw, str) or not raw:
            errors.append("artifact has invalid path")
            continue
        if raw in seen_paths:
            errors.append(f"duplicate artifact path: {raw}")
        seen_paths.add(raw)
        if not isinstance(expected, str) or not HEX_SHA256_RE.fullmatch(expected):
            errors.append(f"artifact has invalid sha256: {raw}")
            continue
        try:
            path = _safe_artifact_path(root, raw)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if not path.is_file():
            errors.append(f"artifact missing: {raw}")
            continue
        if path.stat().st_size != size:
            errors.append(f"artifact size mismatch: {raw}")
        if sha256_file(path) != expected:
            errors.append(f"artifact sha256 mismatch: {raw}")

    for collection in ("runs", "parity", "phonolammps"):
        for index, summary in enumerate(manifest.get(collection, [])):
            if not isinstance(summary, dict):
                errors.append(f"{collection}[{index}] is not an object")
                continue
            for field in ("result", "stdout", "stderr"):
                if field not in summary or summary[field] is None:
                    if field in ("stdout", "stderr") and collection == "parity":
                        continue
                    errors.append(f"{collection}[{index}] lacks {field} evidence")
                    continue
                raw = summary[field].get("path")
                if raw not in seen_paths:
                    errors.append(
                        f"{collection}[{index}] {field} is absent from artifact_hashes"
                    )

    if status == "passed":
        runs = manifest.get("runs", [])
        parity = manifest.get("parity", [])
        phonon = manifest.get("phonolammps", [])
        if len(runs) != 12 or any(item.get("status") != "passed" for item in runs):
            errors.append("passed manifest must contain exactly 12 passed LAMMPS runs")
        if len(parity) != 6 or any(item.get("status") != "passed" for item in parity):
            errors.append("passed manifest must contain exactly 6 passed parity records")
        passed_phonon = False
        for item in phonon:
            if not isinstance(item, dict):
                continue
            validation = item.get("force_constants_validation")
            if not isinstance(validation, dict):
                continue
            atom_count = validation.get("atom_count")
            expected_count = validation.get("expected_atom_count")
            complete_validation = (
                validation.get("status") == "passed"
                and isinstance(atom_count, int)
                and atom_count > 0
                and atom_count == expected_count
                and validation.get("matrix_blocks") == atom_count * atom_count
                and validation.get("finite_values") == 9 * atom_count * atom_count
                and validation.get("error") is None
            )
            if (
                item.get("case") == "Ti_hcp"
                and item.get("device") == "gpu"
                and item.get("status") == "passed"
                and complete_validation
            ):
                passed_phonon = True
                break
        if not passed_phonon:
            errors.append(
                "passed manifest lacks Ti_hcp GPU phonoLAMMPS smoke with "
                "complete finite FORCE_CONSTANTS validation"
            )
        if any(case.get("status") != "passed" for case in manifest.get("cases", [])):
            errors.append("passed manifest contains a non-passed generated case")
        observations = manifest.get("environment", {}).get("observations", [])
        if not observations:
            errors.append("passed manifest lacks runtime environment observations")
        if observations and not any(
            observation.get("gpu_inventory")
            and observation.get("driver_version")
            and observation.get("cuda_version")
            for observation in observations
        ):
            errors.append("passed manifest lacks a complete GPU/driver/CUDA observation")
        for observation in observations:
            for gpu in observation.get("gpu_inventory", []):
                if not str(gpu.get("name", "")).strip().lower().endswith("t4"):
                    errors.append("passed manifest contains a non-T4 GPU observation")
                if str(gpu.get("compute_capability", "")).strip() != "7.5":
                    errors.append("passed manifest T4 compute capability is not 7.5")
        for observation in observations:
            packages = observation.get("packages", {})
            missing = [name for name in REQUIRED_PACKAGES if not packages.get(name)]
            if missing:
                errors.append(
                    "environment observation lacks package versions: " + ", ".join(missing)
                )
            plugin_path = observation.get("selected_environment", {}).get(
                "LAMMPS_PLUGIN_PATH"
            )
            if not plugin_path:
                errors.append("passed manifest lacks LAMMPS_PLUGIN_PATH")
            plugins = observation.get("lammps_plugins", [])
            if not plugins:
                errors.append("passed manifest lacks hashed b95 LAMMPS plugin evidence")
            elif not any(
                Path(plugin.get("path", "")).name == "libdeepmd_lmpplugin.so"
                and isinstance(plugin.get("sha256"), str)
                and HEX_SHA256_RE.fullmatch(plugin["sha256"])
                for plugin in plugins
            ):
                errors.append(
                    "passed manifest lacks libdeepmd_lmpplugin.so hash evidence"
                )
        runtime_models = manifest.get("identity", {}).get("runtime_models_pt2", [])
        if len(runtime_models) != 2:
            errors.append("passed manifest must contain exactly CPU and GPU .pt2 identities")
        else:
            devices = {item.get("device") for item in runtime_models}
            hashes = {item.get("sha256") for item in runtime_models}
            if devices != {"cpu", "gpu"}:
                errors.append("passed manifest .pt2 identities must cover CPU and GPU")
            if len(hashes) != 2:
                errors.append("passed manifest CPU/GPU .pt2 hashes must be distinct")
        if manifest.get("reason_codes"):
            errors.append("passed manifest must have no reason_codes")
    elif status == "untested":
        if not manifest.get("reason_codes"):
            errors.append("untested manifest must explain missing execution evidence")
        if manifest.get("runs") or manifest.get("parity") or manifest.get("phonolammps"):
            errors.append("untested manifest must not contain runtime result summaries")
    elif status in {"failed", "inconclusive"}:
        if not (manifest.get("runs") or manifest.get("parity") or manifest.get("phonolammps")):
            errors.append(f"{status} manifest lacks runtime result summaries")
    return errors


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument(
        "--root",
        type=Path,
        help="artifact root (default: manifest parent)",
    )
    args = parser.parse_args(argv)
    root = (args.root or args.manifest.parent).resolve()
    errors = verify(args.manifest, root)
    if errors:
        print("manifest verification FAILED")
        for error in errors:
            print(f"  - {error}")
        return 1
    print("manifest verification PASSED")
    print("This verifies structure/hashes only; compatibility requires status=passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
