#!/usr/bin/env python3
"""
Validate / recommend APEX Bohrium image × machine (scass_type) combinations.

Prevents known-failing DeepMD-kit image + scass_type pairs before submission.
Use from the agent skill or as a pre-check in generate_config.py.

Usage:
    python validate_apex_combo.py list-combos --backend lammps --prefer gpu
    python validate_apex_combo.py check \\
        --image registry.dp.tech/dptech/dp/native/prod-16664/dpa4-phonolammps:0.0.2 \\
        --scass "c16_m120_1 * NVIDIA L20"
    python validate_apex_combo.py recommend --backend lammps --prefer cpu
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional


REGISTRY_PREFIX = "registry.dp.tech/dptech/"
DPA4_LAMMPS_IMAGE = (
    "registry.dp.tech/dptech/dp/native/prod-16664/"
    "dpa4-phonolammps:0.0.2"
)
CPU_LAMMPS_IMAGE = (
    "registry.dp.tech/dptech/dp/native/prod-397637/"
    "apex-flow:1.3.0.post"
)

# Normalized tag → reason (always reject)
BLOCKED_IMAGES = {
    "deepmd-kit:3.1.0": "imageName not found on Bohrium for this project",
    "deepmd-kit:3.1.1-cuda12.1": "imageName not found on Bohrium for this project",
    "deepmd-kit:3.1.2": "LAMMPS lacks pair style 'deepmd' in this image",
}

# Exact scass_type strings that fail scheduling / do not exist
BLOCKED_SCASS = {
    "c4_m16_cpu": "No resources available for this machine type",
    "c12_m46_1 * NVIDIA T4": "machine type does not exist on Bohrium",
}

# Image × accelerator combinations that are known to be incompatible.
# Match the accelerator name so every T4 machine size is covered.
BLOCKED_IMAGE_ACCELERATORS = {
    ("deepmd-kit:3.1.1", "NVIDIA T4"):
        "DeepMD-kit 3.1.1 is incompatible with NVIDIA T4",
}

# Recommended allow-lists (short tags or full image refs)
RECOMMENDED_LAMMPS_IMAGES = {
    "cpu": [
        CPU_LAMMPS_IMAGE,
        "registry.dp.tech/dptech/dp/native/prod-397637/deepmd-kit-phonolammps:3.1.3",
        "registry.dp.tech/dptech/deepmd-kit:3.1.1",
        "registry.dp.tech/dptech/deepmd-kit:2024Q1-d23cf3e",
    ],
    "gpu": [
        DPA4_LAMMPS_IMAGE,
        "registry.dp.tech/dptech/deepmd-kit:3.1.1",
        "registry.dp.tech/dptech/deepmd-kit:2024Q1-d23cf3e",
        "registry.dp.tech/dptech/deepmd-kit:3.1.0-cuda12.1",
        "registry.dp.tech/dptech/deepmd-kit:3.0.1-cuda12.1",
    ],
}

RECOMMENDED_SCASS = {
    "lammps_cpu": [
        "c32_m64_cpu",
        "c32_m128_cpu",
        "c16_m64_cpu",
        "c16_m32_cpu",
        "c8_m32_cpu",
    ],
    "lammps_gpu": [
        "c16_m120_1 * NVIDIA L20",
        "c8_m32_1 * NVIDIA 4090",
        "c8_m31_1 * NVIDIA T4",
        "c4_m15_1 * NVIDIA T4",
        "c16_m62_1 * NVIDIA T4",
    ],
    "abacus": ["c16_m32_cpu", "c32_m64_cpu", "c32_m128_cpu"],
    "vasp": ["c32_m128_cpu", "c16_m64_cpu", "c32_m64_cpu"],
    "outer": ["c1_m2_cpu", "c2_m4_cpu", "c2_m8_cpu"],
}

# Triclinic / non-orthogonal cells: avoid deepmd-kit:3.1.1 (segfault)
TRICLINIC_UNSAFE_TAGS = {"deepmd-kit:3.1.1"}


def normalize_image(image: str) -> str:
    """Return short tag like deepmd-kit:3.1.3 from a full or short ref."""
    image = (image or "").strip()
    if image.startswith(REGISTRY_PREFIX):
        image = image[len(REGISTRY_PREFIX) :]
    return image


def full_image(image: str) -> str:
    image = (image or "").strip()
    if not image:
        return image
    if image.startswith("registry.") or "/" in image.split(":")[0]:
        return image
    return REGISTRY_PREFIX + image


def check_combo(
    image: str,
    scass: str,
    *,
    triclinic: bool = False,
) -> tuple[bool, list[str]]:
    """
    Return (ok, messages). ok=False means do not submit.
    """
    errors: list[str] = []
    tag = normalize_image(image)
    scass = (scass or "").strip()

    if tag in BLOCKED_IMAGES:
        errors.append(f"blocked image '{tag}': {BLOCKED_IMAGES[tag]}")
    if scass in BLOCKED_SCASS:
        errors.append(f"blocked scass_type '{scass}': {BLOCKED_SCASS[scass]}")
    for (blocked_tag, accelerator), reason in BLOCKED_IMAGE_ACCELERATORS.items():
        if tag == blocked_tag and accelerator in scass:
            errors.append(
                f"blocked image × accelerator '{tag}' × '{accelerator}': {reason}"
            )
    if tag.endswith("dpa4-phonolammps:0.0.2") and "_cpu" in scass.lower():
        errors.append(
            "blocked DPA4 image × CPU machine: image 0.0.2 did not finish "
            "container preparation in sequential c8_m32_cpu validation; use "
            "the apex-flow CPU image or a dedicated CPU DeepMD image"
        )
    if triclinic and tag in TRICLINIC_UNSAFE_TAGS:
        errors.append(
            f"image '{tag}' is unsafe for triclinic/non-orthogonal cells "
            "(known segfault); use deepmd-kit:3.1.3 or later"
        )
    return (len(errors) == 0, errors)


def recommend(
    backend: str = "lammps",
    prefer: str = "cpu",
) -> dict:
    """Return a recommended image + scass_type for the backend."""
    prefer = prefer.lower()
    backend = backend.lower()

    if backend == "lammps":
        key = "lammps_gpu" if prefer == "gpu" else "lammps_cpu"
        images = RECOMMENDED_LAMMPS_IMAGES["gpu" if prefer == "gpu" else "cpu"]
        return {
            "backend": backend,
            "prefer": prefer,
            "image": images[0],
            "scass_type": RECOMMENDED_SCASS[key][0],
            "alternatives": {
                "images": images,
                "scass_types": RECOMMENDED_SCASS[key],
            },
            "outer_machine": RECOMMENDED_SCASS["outer"][0],
        }
    if backend == "abacus":
        return {
            "backend": backend,
            "prefer": "cpu",
            "image": "registry.dp.tech/dptech/dp/native/prod-397637/apex-flow:1.3.0.post",
            "scass_type": RECOMMENDED_SCASS["abacus"][0],
            "alternatives": {"scass_types": RECOMMENDED_SCASS["abacus"]},
            "outer_machine": RECOMMENDED_SCASS["outer"][0],
        }
    if backend == "vasp":
        return {
            "backend": backend,
            "prefer": "cpu",
            "image": "<user-provided VASP image>",
            "scass_type": RECOMMENDED_SCASS["vasp"][0],
            "alternatives": {"scass_types": RECOMMENDED_SCASS["vasp"]},
            "outer_machine": RECOMMENDED_SCASS["outer"][0],
        }
    raise ValueError(f"Unknown backend: {backend}")


def list_combos(backend: str = "lammps", prefer: str = "cpu") -> dict:
    rec = recommend(backend, prefer)
    return {
        "recommended": rec,
        "blocked_images": BLOCKED_IMAGES,
        "blocked_scass": BLOCKED_SCASS,
        "blocked_image_accelerators": {
            f"{image} × {accelerator}": reason
            for (image, accelerator), reason in BLOCKED_IMAGE_ACCELERATORS.items()
        },
        "notes": [
            "Always validate image×scass before writing global.json / submitting.",
            "Outer Bohrium job should use c1_m2_cpu, never GPU.",
            "DPA4 image 0.0.2 is validated on NVIDIA L20 and RTX 4090; L20 is the default GPU resource.",
            "DPA4 image 0.0.2 is blocked on CPU machines after sequential deployment timeouts.",
            "For triclinic cells, avoid deepmd-kit:3.1.1; prefer the DPA4 0.0.2 image.",
        ],
    }


def cmd_list_combos(args: argparse.Namespace) -> int:
    data = list_combos(args.backend, args.prefer)
    if args.format == "json":
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        rec = data["recommended"]
        print(f"Backend: {rec['backend']}  prefer={rec['prefer']}")
        print(f"  image:      {rec['image']}")
        print(f"  scass_type: {rec['scass_type']}")
        print(f"  outer:      {rec['outer_machine']}")
        print("\nBlocked images:")
        for k, v in BLOCKED_IMAGES.items():
            print(f"  - {k}: {v}")
        print("\nBlocked scass_type:")
        for k, v in BLOCKED_SCASS.items():
            print(f"  - {k}: {v}")
        print("\nBlocked image x accelerator:")
        for (image, accelerator), reason in BLOCKED_IMAGE_ACCELERATORS.items():
            print(f"  - {image} x {accelerator}: {reason}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    ok, errors = check_combo(args.image, args.scass, triclinic=args.triclinic)
    payload = {"ok": ok, "image": full_image(args.image), "scass_type": args.scass, "errors": errors}
    if args.format == "json":
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        if ok:
            print(f"OK: {payload['image']} @ {args.scass}")
        else:
            print("FAIL:", file=sys.stderr)
            for e in errors:
                print(f"  - {e}", file=sys.stderr)
    return 0 if ok else 1


def cmd_recommend(args: argparse.Namespace) -> int:
    rec = recommend(args.backend, args.prefer)
    if args.format == "json":
        print(json.dumps(rec, indent=2, ensure_ascii=False))
    else:
        print(f"image={rec['image']}")
        print(f"scass_type={rec['scass_type']}")
        print(f"outer_machine={rec['outer_machine']}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate APEX Bohrium image × scass_type combinations",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list-combos", help="List recommended and blocked combos")
    p_list.add_argument("--backend", default="lammps", choices=["lammps", "abacus", "vasp"])
    p_list.add_argument("--prefer", default="cpu", choices=["cpu", "gpu"])
    p_list.add_argument("--format", choices=["json", "text"], default="text")
    p_list.set_defaults(func=cmd_list_combos)

    p_check = sub.add_parser("check", help="Check one image × scass pair")
    p_check.add_argument("--image", "-m", required=True)
    p_check.add_argument("--scass", "-t", required=True, help="Inner scass_type")
    p_check.add_argument("--triclinic", action="store_true",
                         help="Cell is triclinic / non-orthogonal")
    p_check.add_argument("--format", choices=["json", "text"], default="text")
    p_check.set_defaults(func=cmd_check)

    p_rec = sub.add_parser("recommend", help="Print a safe default combo")
    p_rec.add_argument("--backend", default="lammps", choices=["lammps", "abacus", "vasp"])
    p_rec.add_argument("--prefer", default="cpu", choices=["cpu", "gpu"])
    p_rec.add_argument("--format", choices=["json", "text"], default="text")
    p_rec.set_defaults(func=cmd_recommend)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
