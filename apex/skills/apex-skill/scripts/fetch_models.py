#!/usr/bin/env python3
"""
Optional helper to download large DPA checkpoints.

The skill itself only ships small frozen graphs:
  models/DPA2/DPA2.pb
  models/DPA_alloy/DPA_alloy.pb

Large multi-head ``.pt`` files are NOT bundled and are NOT downloaded by
``apex skill --zip``. Fetch them only when the user explicitly needs them.

Usage:
    python fetch_models.py                 # sync frozen .pb aliases only (no download)
    python fetch_models.py --dpa2-pt       # optional ~76MB dpa-2.4-7M.pt
    python fetch_models.py --dpa3          # optional ~62MB DPA-3.2-5M.pt
    python fetch_models.py --dpa2-pt --dpa3 --force
"""

from __future__ import annotations

import argparse
import shutil
import sys
import urllib.request
from pathlib import Path

MODELS_ROOT = Path(__file__).resolve().parents[1] / "models"

DPA2_PT = (
    "DPA2",
    "dpa-2.4-7M.pt",
    "https://huggingface.co/deepmodelingcommunity/DPA-2.4-7M/resolve/main/dpa-2.4-7M.pt",
)
DPA3_PT = (
    "DPA3",
    "DPA-3.2-5M.pt",
    "https://huggingface.co/deepmodelingcommunity/DPA-3.2-5M/resolve/main/DPA-3.2-5M.pt",
)

# Frozen TF graphs shipped with the skill for immediate APEX use.
FROZEN_ALIASES = [
    ("DPA2", "DPA2.pb", "DPA_alloy", "DPA_alloy.pb"),
]


def _download(url: str, dest: Path, force: bool = False) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.stat().st_size > 1_000_000 and not force:
        print(f"SKIP {dest} ({dest.stat().st_size // (1024 * 1024)} MB)")
        return
    partial = dest.with_suffix(dest.suffix + ".partial")
    print(f"GET  {url}")
    print(f"  -> {dest}")
    urllib.request.urlretrieve(url, partial)
    partial.replace(dest)
    print(f"OK   {dest} ({dest.stat().st_size // (1024 * 1024)} MB)")


def _sync_frozen_aliases() -> None:
    """Ensure DPA_alloy.pb exists if a frozen DPA2.pb is present."""
    for src_dir, src_name, dst_dir, dst_name in FROZEN_ALIASES:
        src = MODELS_ROOT / src_dir / src_name
        legacy = MODELS_ROOT / src_name
        if not src.is_file() and legacy.is_file():
            src.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(legacy, src)
            print(f"COPY {legacy} -> {src}")
        if not src.is_file():
            print(f"WARN missing frozen model: {src}")
            continue
        dst = MODELS_ROOT / dst_dir / dst_name
        if dst.is_file() and dst.stat().st_size == src.stat().st_size:
            print(f"SKIP {dst}")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        print(f"COPY {src} -> {dst}")


def main(argv: list[str] | None = None) -> int:
    # Important: default to [] so runpy / accidental sys.argv inheritance
    # (e.g. from `apex skill --zip`) cannot break this script.
    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dpa2-pt",
        action="store_true",
        help="Download multi-head dpa-2.4-7M.pt (~76MB); not needed for APEX with DPA2.pb",
    )
    parser.add_argument(
        "--dpa3",
        action="store_true",
        help="Download DPA-3.2-5M.pt (~62MB); freeze a head before LAMMPS use",
    )
    parser.add_argument("--force", action="store_true", help="Re-download even if present")
    args = parser.parse_args(argv)

    MODELS_ROOT.mkdir(parents=True, exist_ok=True)
    _sync_frozen_aliases()

    if args.dpa2_pt:
        _download(DPA2_PT[2], MODELS_ROOT / DPA2_PT[0] / DPA2_PT[1], force=args.force)
    if args.dpa3:
        _download(DPA3_PT[2], MODELS_ROOT / DPA3_PT[0] / DPA3_PT[1], force=args.force)

    if not args.dpa2_pt and not args.dpa3:
        print(
            "No large checkpoints requested. "
            "Skill keeps only frozen *.pb; use --dpa2-pt / --dpa3 if needed."
        )

    print(f"Models root: {MODELS_ROOT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
