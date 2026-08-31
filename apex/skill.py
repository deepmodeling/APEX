"""CLI helpers for the bundled APEX agent skill."""

from __future__ import annotations

import zipfile
from pathlib import Path

from apex.skills import SKILL_NAME, get_skill_root

# Skip noise when packing for MatMaster / agent skill upload.
_ZIP_SKIP_DIR_NAMES = {"__pycache__", ".git", "variants"}
_ZIP_SKIP_FILE_NAMES = {
    ".DS_Store",
    "global_bohrium_direct.json",
    "global_local_cluster_slurm.json",
    "global_local_debug.json",
}
_ZIP_SKIP_FILE_SUFFIXES = {".pyc", ".pyo", ".pt", ".partial"}
_ZIP_INCLUDED_PT_FILES = {
    Path("models") / "DPA4-alloytongqi" / "model.pt",
}


def _agent_install_prompt() -> str:
    """Return a portable local-Agent installation prompt."""
    return f"""\
# Agent prompt: install the local APEX skill ({SKILL_NAME})

You are an AI coding agent. Install the bundled APEX agent skill so future
sessions can run APEX from this machine.

## Required: choose the execution profile

Before copying files, ask the user exactly where APEX calculations will run:

1. **Bohrium cloud** — this local Agent runs `apex submit` directly and uses
   credentials saved by `apex account`; do not require a ticket or outer job.
2. **local** — run calculators on this workstation with `apex submit -d`.
3. **local cluster** — run from a cluster login node and dispatch calculator
   tasks through Slurm/PBS with DPDispatcher.

Do not guess. Wait for the user's answer, then map it to one profile file:

- Bohrium cloud: `bohrium-direct.md`
- local: `local-debug.md`
- local cluster: `local-cluster.md`

## Install from this package

Resolve the bundled skill directory at runtime. Do not hardcode or copy a
host-absolute path into a shared prompt:

```bash
SKILL_ROOT="$(python -c 'from apex.skills import get_skill_root; print(get_skill_root())')"
LOCAL_VARIANT="$SKILL_ROOT/variants/local"
```

Confirm `$LOCAL_VARIANT/SKILL.md` exists. Copy the shared directory, then
overlay the local entry/reference and the selected execution profile:

```bash
SKILL_ROOT="$(python -c 'from apex.skills import get_skill_root; print(get_skill_root())')"
LOCAL_VARIANT="$SKILL_ROOT/variants/local"
DEST="$HOME/.cursor/skills/{SKILL_NAME}"  # or ~/.codex/skills/{SKILL_NAME}
PROFILE="<bohrium-direct.md|local-debug.md|local-cluster.md>"
mkdir -p "$(dirname "$DEST")"
rm -rf "$DEST"
cp -R "$SKILL_ROOT" "$DEST"
cp "$LOCAL_VARIANT/SKILL.md" "$DEST/SKILL.md"
cp "$LOCAL_VARIANT/reference/submission.md" \
  "$DEST/reference/submission.md"
cp "$LOCAL_VARIANT/profiles/$PROFILE" \
  "$DEST/reference/execution-profile.md"
rm -rf "$DEST/variants"
```

## Verification

- `$DEST/SKILL.md` exists and contains `Local Agent Edition`
- `$DEST/reference/execution-profile.md` exists and names the selected profile
- YAML frontmatter `name:` is `{SKILL_NAME}`

## After install

- Tell the user the skill is available as `{SKILL_NAME}` / `/apex-flow`
- State which execution profile was installed and how to change it
- For APEX calculation requests, read `SKILL.md` first, then load referenced
  docs under `reference/` only as needed

`apex skill --zip` is a separate Bohrium Cloud/MatMaster distribution. Do not
install that ticket/outer-job variant for a local Agent.
"""


def build_skill_zip(output: Path | None = None) -> Path:
    """
    Pack the Bohrium Cloud apex-flow variant into a zip for MatMaster upload.

    Arbitrary DeePMD source/training checkpoints (``*.pt``) are excluded on
    purpose. The explicitly allow-listed, ready-to-run single-task DPA4
    checkpoint bundled under ``models/`` is included.
    """
    skill_root = get_skill_root()
    if not (skill_root / "SKILL.md").is_file():
        raise FileNotFoundError(
            f"Bundled skill not found at {skill_root}. "
            "Reinstall apex-flow or check package data."
        )

    out = Path(output).expanduser().resolve() if output else (
        Path.cwd() / f"{SKILL_NAME}.zip"
    )
    if out.suffix.lower() != ".zip":
        out = out.with_suffix(".zip")
    out.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(skill_root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(skill_root)
            if any(part in _ZIP_SKIP_DIR_NAMES for part in path.parts):
                continue
            if path.name in _ZIP_SKIP_FILE_NAMES:
                continue
            if (
                path.suffix in _ZIP_SKIP_FILE_SUFFIXES
                and relative not in _ZIP_INCLUDED_PT_FILES
            ):
                continue
            if path.name.endswith(".partial"):
                continue
            arcname = Path(SKILL_NAME) / relative
            zf.write(path, arcname.as_posix())

    return out


def skill_from_args(args) -> None:
    """Print the local Agent prompt, or write the Bohrium Cloud skill zip."""
    skill_root = get_skill_root()
    if not (skill_root / "SKILL.md").is_file():
        raise FileNotFoundError(
            f"Bundled skill not found at {skill_root}. "
            "Reinstall apex-flow or check package data."
        )
    if getattr(args, "zip", False):
        output = getattr(args, "output", None)
        zip_path = build_skill_zip(Path(output) if output else None)
        print(f"Wrote Bohrium Cloud/MatMaster skill archive: {zip_path}")
        print(f"Upload this zip in MatMaster (top-level folder: {SKILL_NAME}/).")
        return
    print(_agent_install_prompt().rstrip())
