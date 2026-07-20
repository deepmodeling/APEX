#!/usr/bin/env python3
"""
Optional helper to download the DPA-3.2-5M source checkpoint.

The skill itself ships the ready-to-run frozen model:
  models/DPA-3.2-5M/DPA-3.2-5M-OMat24.pth

The multi-head source ``.pt`` is NOT bundled and is NOT downloaded by
``apex skill --zip``. Fetch it only when the user explicitly needs another
task head.

Usage:
    python fetch_models.py                       # show model location
    python fetch_models.py --source-checkpoint   # optional ~62MB source .pt
    python fetch_models.py --source-checkpoint --force
"""

from __future__ import annotations

import argparse
import sys
import urllib.request
from pathlib import Path

MODELS_ROOT = Path(__file__).resolve().parents[1] / "models"

SOURCE_CHECKPOINT = (
    "DPA-3.2-5M",
    "DPA-3.2-5M.pt",
    "https://huggingface.co/deepmodelingcommunity/DPA-3.2-5M/resolve/main/DPA-3.2-5M.pt",
)
FROZEN_MODEL = MODELS_ROOT / "DPA-3.2-5M" / "DPA-3.2-5M-OMat24.pth"


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


def main(argv: list[str] | None = None) -> int:
    # Important: default to [] so runpy / accidental sys.argv inheritance
    # (e.g. from `apex skill --zip`) cannot break this script.
    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-checkpoint",
        action="store_true",
        help="Download DPA-3.2-5M.pt (~62MB) to freeze a different task head",
    )
    parser.add_argument("--force", action="store_true", help="Re-download even if present")
    args = parser.parse_args(argv)

    MODELS_ROOT.mkdir(parents=True, exist_ok=True)
    if args.source_checkpoint:
        _download(
            SOURCE_CHECKPOINT[2],
            MODELS_ROOT / SOURCE_CHECKPOINT[0] / SOURCE_CHECKPOINT[1],
            force=args.force,
        )
    else:
        print("No source checkpoint requested; use --source-checkpoint if needed.")

    print(f"Bundled frozen model: {FROZEN_MODEL}")
    print(f"Models root: {MODELS_ROOT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
