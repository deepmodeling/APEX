"""Bundled Agent Skills shipped with the APEX package."""

from __future__ import annotations

from pathlib import Path

SKILL_NAME = "apex-flow"


def get_skill_root() -> Path:
    """Return the filesystem path to the bundled ``apex-flow`` directory."""
    return Path(__file__).resolve().parent / SKILL_NAME
