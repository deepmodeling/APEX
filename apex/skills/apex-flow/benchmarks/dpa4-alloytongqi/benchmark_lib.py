#!/usr/bin/env python3
"""Shared, dependency-free helpers for the DPA4 alloytongqi benchmark."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import os
import re
import signal
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


SCHEMA_VERSION = "2.0.0"
BENCHMARK_ID = "dpa4-alloytongqi-small-cell-gpu-compat-v2"
EXPECTED_MODEL_SHA256 = (
    "c84b268cc6191afc72bd2d5c001cbe526a0d2e04ebf6dbd7df021306e9abe9ad"
)
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
HEX_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_PACKAGES = (
    "deepmd-kit",
    "phonolammps",
    "phonopy",
    "lammps",
    "dpdata",
    "torch",
    "numpy",
)
FATAL_OUTPUT_PATTERNS = (
    re.compile(r"Unknown model type", re.IGNORECASE),
    re.compile(r"(?:^|\n)\s*ERROR(?:\s|:)", re.IGNORECASE),
    re.compile(r"Segmentation fault", re.IGNORECASE),
    re.compile(r"CUDA error", re.IGNORECASE),
    re.compile(r"lost atoms", re.IGNORECASE),
    re.compile(r"Traceback \(most recent call last\)", re.IGNORECASE),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def describe_file(path: Path, root: Path | None = None) -> dict[str, Any]:
    resolved = path.resolve()
    display = str(resolved)
    if root is not None:
        try:
            display = str(resolved.relative_to(root.resolve()))
        except ValueError:
            pass
    return {
        "path": display,
        "sha256": sha256_file(resolved),
        "bytes": resolved.stat().st_size,
    }


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def write_json_new(path: Path, value: dict[str, Any]) -> None:
    """Write a JSON file, refusing to replace any existing evidence."""

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite existing evidence: {path}")
    text = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8")


def write_text_if_identical(path: Path, text: str) -> None:
    """Create deterministic input, or accept an already-identical file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_text(encoding="utf-8") != text:
            raise FileExistsError(f"existing generated input differs: {path}")
        return
    path.write_text(text, encoding="utf-8")


def normalize_image_identity(
    image_ref: str | None, image_digest: str | None
) -> tuple[dict[str, Any], list[str]]:
    reasons: list[str] = []
    reference = (image_ref or "").strip() or None
    digest = (image_digest or "").strip().lower() or None
    if reference and "@sha256:" in reference:
        ref_digest = "sha256:" + reference.rsplit("@sha256:", 1)[1].lower()
        if digest and digest != ref_digest:
            reasons.append("IMAGE_DIGEST_CONFLICT")
        digest = digest or ref_digest
    if reference is None:
        reasons.append("IMAGE_REFERENCE_MISSING")
    if digest is None or not SHA256_RE.fullmatch(digest):
        reasons.append("IMAGE_DIGEST_MISSING_OR_INVALID")
    return {"reference": reference, "digest": digest}, reasons


def package_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for name in REQUIRED_PACKAGES:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def _run_probe(command: Sequence[str], timeout: float = 8.0) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            list(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
        return {
            "command": list(command),
            "exit_code": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return {
            "command": list(command),
            "exit_code": None,
            "stdout": "",
            "stderr": f"{type(exc).__name__}: {exc}",
        }


def capture_gpu_inventory() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    fields = "index,name,uuid,driver_version,memory.total,compute_cap"
    probe = _run_probe(
        [
            "nvidia-smi",
            f"--query-gpu={fields}",
            "--format=csv,noheader,nounits",
        ]
    )
    inventory: list[dict[str, Any]] = []
    if probe["exit_code"] == 0:
        for line in probe["stdout"].splitlines():
            parts = [part.strip() for part in line.split(",")]
            if len(parts) != 6:
                continue
            index, name, uuid, driver, memory_mib, compute_capability = parts
            try:
                memory_value: int | None = int(memory_mib)
            except ValueError:
                memory_value = None
            inventory.append(
                {
                    "index": index,
                    "name": name,
                    "uuid": uuid,
                    "driver_version": driver,
                    "memory_mib": memory_value,
                    "compute_capability": compute_capability,
                }
            )
    header_probe = _run_probe(["nvidia-smi"])
    header = header_probe.get("stdout", "") + header_probe.get("stderr", "")
    match = re.search(r"CUDA Version:\s*([0-9.]+)", header)
    probe["cuda_version_from_nvidia_smi"] = match.group(1) if match else None
    probe["header_exit_code"] = header_probe.get("exit_code")
    probe["header_stderr"] = header_probe.get("stderr", "")
    return inventory, probe


def capture_runtime_environment() -> dict[str, Any]:
    inventory, nvidia_probe = capture_gpu_inventory()
    drivers = sorted(
        {
            item["driver_version"]
            for item in inventory
            if item.get("driver_version")
        }
    )
    nvcc_probe = _run_probe(["nvcc", "--version"])
    nvcc_text = nvcc_probe.get("stdout", "") + nvcc_probe.get("stderr", "")
    nvcc_match = re.search(r"release\s+([0-9.]+)", nvcc_text)
    torch_cuda_probe = _run_probe(
        [
            sys.executable,
            "-c",
            "import torch; print(torch.version.cuda or '')",
        ]
    )
    torch_cuda = (
        torch_cuda_probe.get("stdout", "").strip()
        if torch_cuda_probe.get("exit_code") == 0
        else None
    )
    return {
        "gpu_inventory": inventory,
        "driver_version": drivers[0] if len(drivers) == 1 else None,
        "cuda_version": nvidia_probe.get("cuda_version_from_nvidia_smi"),
        "cuda_versions": {
            "driver_supported": nvidia_probe.get("cuda_version_from_nvidia_smi"),
            "toolkit_nvcc": nvcc_match.group(1) if nvcc_match else None,
            "torch_built_against": torch_cuda or None,
        },
        "cuda_probes": {
            "nvcc": nvcc_probe,
            "torch": torch_cuda_probe,
        },
        "packages": package_versions(),
        "executables": {
            name: shutil.which(name)
            for name in ("python", "dp", "lmp", "phonolammps", "nvidia-smi")
        },
        "nvidia_smi_probe": nvidia_probe,
        "selected_environment": {
            "CONDA_DEFAULT_ENV": os.environ.get("CONDA_DEFAULT_ENV"),
            "CUDA_VISIBLE_DEVICES": os.environ.get("CUDA_VISIBLE_DEVICES"),
            "LAMMPS_PLUGIN_PATH": os.environ.get("LAMMPS_PLUGIN_PATH"),
            "DP_INTRA_OP_PARALLELISM_THREADS": os.environ.get(
                "DP_INTRA_OP_PARALLELISM_THREADS"
            ),
            "DP_INTER_OP_PARALLELISM_THREADS": os.environ.get(
                "DP_INTER_OP_PARALLELISM_THREADS"
            ),
            "OMP_NUM_THREADS": os.environ.get("OMP_NUM_THREADS"),
        },
    }


def _descendant_pids(root_pid: int) -> set[int]:
    """Return root plus Linux /proc descendants; root-only elsewhere."""

    descendants = {root_pid}
    proc = Path("/proc")
    if not proc.is_dir():
        return descendants
    parent_map: dict[int, int] = {}
    for status_path in proc.glob("[0-9]*/status"):
        try:
            pid = int(status_path.parent.name)
            ppid = None
            for line in status_path.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("PPid:"):
                    ppid = int(line.split()[1])
                    break
            if ppid is not None:
                parent_map[pid] = ppid
        except (OSError, ValueError, IndexError):
            continue
    changed = True
    while changed:
        changed = False
        for pid, ppid in parent_map.items():
            if ppid in descendants and pid not in descendants:
                descendants.add(pid)
                changed = True
    return descendants


def _gpu_compute_processes() -> list[dict[str, Any]]:
    probe = _run_probe(
        [
            "nvidia-smi",
            "--query-compute-apps=pid,process_name,gpu_uuid,used_gpu_memory",
            "--format=csv,noheader,nounits",
        ],
        timeout=2.0,
    )
    if probe["exit_code"] != 0:
        return []
    rows: list[dict[str, Any]] = []
    for line in probe["stdout"].splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 4:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        try:
            memory_mib: int | None = int(parts[3])
        except ValueError:
            memory_mib = None
        rows.append(
            {
                "pid": pid,
                "process_name": parts[1],
                "gpu_uuid": parts[2],
                "used_gpu_memory_mib": memory_mib,
            }
        )
    return rows


def run_monitored(
    command: Sequence[str],
    cwd: Path,
    env: dict[str, str],
    timeout: float,
) -> dict[str, Any]:
    process = subprocess.Popen(
        list(command),
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    observed: list[dict[str, Any]] = []
    stop = threading.Event()

    def monitor() -> None:
        while not stop.is_set():
            family = _descendant_pids(process.pid)
            for row in _gpu_compute_processes():
                if row["pid"] in family:
                    sample = dict(row)
                    sample["observed_at"] = utc_now()
                    observed.append(sample)
            stop.wait(0.05)

    thread = threading.Thread(target=monitor, name="gpu-process-monitor", daemon=True)
    thread.start()
    # AOTI model loading is brief enough that a process can disappear between
    # two 50 ms monitor ticks.  Capture the root process immediately as well.
    family = _descendant_pids(process.pid)
    for row in _gpu_compute_processes():
        if row["pid"] in family:
            sample = dict(row)
            sample["observed_at"] = utc_now()
            observed.append(sample)
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            process.kill()
        stdout, stderr = process.communicate()
    finally:
        stop.set()
        thread.join(timeout=3.0)
    unique_samples: dict[tuple[Any, ...], dict[str, Any]] = {}
    for sample in observed:
        key = (
            sample.get("pid"),
            sample.get("gpu_uuid"),
            sample.get("used_gpu_memory_mib"),
        )
        unique_samples[key] = sample
    return {
        "exit_code": process.returncode,
        "timed_out": timed_out,
        "stdout": stdout,
        "stderr": stderr,
        "gpu_process_samples": list(unique_samples.values()),
        "root_pid": process.pid,
    }


def detect_fatal_output(stdout: str, stderr: str) -> list[str]:
    combined = stdout + "\n" + stderr
    reasons = []
    for pattern in FATAL_OUTPUT_PATTERNS:
        if pattern.search(combined):
            reasons.append(f"FATAL_OUTPUT:{pattern.pattern}")
    return reasons


def finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def identity_reasons(
    checkpoint_path: Path, image_ref: str | None, image_digest: str | None
) -> tuple[dict[str, Any], list[str]]:
    checkpoint = describe_file(checkpoint_path)
    reasons: list[str] = []
    if checkpoint["sha256"] != EXPECTED_MODEL_SHA256:
        reasons.append("CHECKPOINT_SHA256_MISMATCH")
    image, image_reasons = normalize_image_identity(image_ref, image_digest)
    reasons.extend(image_reasons)
    return {"checkpoint_pt": checkpoint, "image": image}, reasons


def runtime_model_identity(
    runtime_model: Path, device: str, root: Path | None = None
) -> tuple[dict[str, Any], list[str]]:
    descriptor = describe_file(runtime_model, root)
    descriptor["device"] = device
    reasons: list[str] = []
    if runtime_model.suffix.lower() != ".pt2":
        reasons.append("RUNTIME_MODEL_MUST_BE_PT2")
    if descriptor["bytes"] <= 0:
        reasons.append("RUNTIME_MODEL_EMPTY")
    return descriptor, reasons


def resolve_lammps_plugin_path(
    explicit: str | None, inherited: str | None
) -> tuple[str | None, list[dict[str, Any]], list[str]]:
    """Resolve the b95 plugin directory contract without modifying the image."""

    raw = (explicit if explicit is not None else inherited) or ""
    raw = raw.strip()
    if not raw:
        return None, [], ["LAMMPS_PLUGIN_PATH_MISSING"]
    directories: list[Path] = []
    reasons: list[str] = []
    for entry in raw.split(os.pathsep):
        entry = entry.strip()
        if not entry:
            reasons.append("LAMMPS_PLUGIN_PATH_EMPTY_ENTRY")
            continue
        directory = Path(entry).expanduser().resolve()
        if not directory.is_dir():
            reasons.append(f"LAMMPS_PLUGIN_DIRECTORY_MISSING:{directory}")
            continue
        directories.append(directory)
    plugins = []
    for directory in directories:
        candidate = directory / "libdeepmd_lmpplugin.so"
        if candidate.is_file():
            plugins.append(describe_file(candidate))
    if not plugins:
        reasons.append("LIBDEEPMD_LMPPLUGIN_SO_NOT_FOUND")
    normalized = os.pathsep.join(str(directory) for directory in directories)
    return normalized or None, plugins, reasons


def single_rank_command_reasons(
    command: Sequence[str], allowed_programs: set[str]
) -> list[str]:
    """Reject launchers unless they explicitly request exactly one rank."""

    if not command:
        return ["COMMAND_EMPTY"]
    launcher = Path(command[0]).name.lower()
    if launcher not in {"mpirun", "mpiexec", "srun"}:
        program = launcher
        return [] if program in allowed_programs else [f"PROGRAM_NOT_ALLOWED:{program}"]
    rank_values: list[str] = []
    for index, token in enumerate(command):
        if token in {"-n", "-np", "--np", "--ntasks"}:
            if index + 1 >= len(command):
                return ["MPI_RANK_ARGUMENT_MISSING"]
            rank_values.append(command[index + 1])
        elif token.startswith("--ntasks=") or token.startswith("--np="):
            rank_values.append(token.split("=", 1)[1])
    if not rank_values:
        return ["SINGLE_RANK_NOT_EXPLICIT"]
    if any(value != "1" for value in rank_values):
        return ["MULTI_RANK_NOT_ALLOWED"]
    program = Path(command[-1]).name.lower()
    if program not in allowed_programs:
        return [f"PROGRAM_NOT_ALLOWED:{program}"]
    return []


def t4_environment_reasons(
    environment: dict[str, Any], gpu_index: str
) -> list[str]:
    inventory = environment.get("gpu_inventory", [])
    selected = [item for item in inventory if str(item.get("index")) == str(gpu_index)]
    if len(selected) != 1:
        return ["T4_GPU_NOT_UNIQUELY_IDENTIFIED"]
    name = str(selected[0].get("name", "")).strip()
    if not re.search(r"(?:^|\s)T4$", name, re.IGNORECASE):
        return [f"GPU_NOT_T4:{name or 'unknown'}"]
    reasons = []
    for field in ("uuid", "driver_version", "compute_capability"):
        if not str(selected[0].get(field, "")).strip():
            reasons.append(f"T4_{field.upper()}_MISSING")
    if selected[0].get("memory_mib") is None:
        reasons.append("T4_MEMORY_MIB_MISSING")
    if str(selected[0].get("compute_capability", "")).strip() != "7.5":
        reasons.append("T4_COMPUTE_CAPABILITY_NOT_7_5")
    return reasons


def t4_environment_reasons_for_device(
    environment: dict[str, Any], gpu_index: str, device: str
) -> list[str]:
    """Require an exact T4 only for GPU legs.

    CPU parity intentionally runs in the same candidate image on the same T4
    node with ``CUDA_VISIBLE_DEVICES`` empty.  Its hardware inventory is useful
    provenance, but the CPU command must not be rejected merely because the
    host still exposes a T4 to ``nvidia-smi``.
    """

    if device == "cpu":
        return []
    return t4_environment_reasons(environment, gpu_index)


def missing_package_reasons(environment: dict[str, Any]) -> list[str]:
    packages = environment.get("packages", {})
    return [
        f"PACKAGE_VERSION_MISSING:{name}"
        for name in REQUIRED_PACKAGES
        if not packages.get(name)
    ]


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()
