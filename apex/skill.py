"""CLI helpers for the bundled APEX agent skill."""

from __future__ import annotations

import zipfile
from pathlib import Path

from apex.skills import SKILL_NAME, get_skill_root

# Skip noise when packing for MatMaster / agent skill upload.
_ZIP_SKIP_DIR_NAMES = {"__pycache__", ".git"}
_ZIP_SKIP_FILE_NAMES = {".DS_Store"}
_ZIP_SKIP_FILE_SUFFIXES = {".pyc", ".pyo", ".pt", ".partial"}


def _agent_install_prompt(skill_root: Path) -> str:
    skill_md = skill_root / "SKILL.md"
    return f"""\
# Agent prompt: install the APEX skill ({SKILL_NAME})

You are an AI coding agent. Install the bundled APEX agent skill so future
sessions can orchestrate APEX (Alloy Property EXplorer) workflows.

## Preferred: MatMaster zip upload

Ask the user (or run yourself) to build an uploadable archive:

```bash
apex skill --zip
```

This writes `./{SKILL_NAME}.zip` with top-level folder `{SKILL_NAME}/`
(containing `SKILL.md`, `reference/`, `scripts/`, small frozen models under
`models/`, etc.). Upload that zip in MatMaster's skill upload UI.

Optional output path:

```bash
apex skill --zip -o /tmp/{SKILL_NAME}.zip
```

## Alternative: copy from the local package

- Skill directory: `{skill_root}`
- Entry file: `{skill_md}`

Confirm `{skill_md}` exists before copying. Do not invent or regenerate the
skill content; copy the directory as-is (including `reference/`, `scripts/`,
`data/`, `models/DPA2|DPA_alloy`, and `plugin.yaml`).

For DeePMD/DPA jobs, prefer bundled frozen models under `models/` first:
- `models/DPA2/DPA2.pb`
- `models/DPA_alloy/DPA_alloy.pb`

Large multi-head checkpoints (`.pt`, DPA3, etc.) are **not** bundled. Only
download them when the user explicitly needs that model/head:

```bash
python scripts/fetch_models.py --dpa2-pt   # optional ~76MB
python scripts/fetch_models.py --dpa3      # optional ~62MB
```

Install destinations (create parents if missing):

1. Cursor (user-global): `~/.cursor/skills/{SKILL_NAME}/`
2. Codex / OpenAI agents (user-global): `~/.codex/skills/{SKILL_NAME}/`
3. Cursor (project-local, optional): `<repo>/.cursor/skills/{SKILL_NAME}/`

```bash
mkdir -p ~/.cursor/skills ~/.codex/skills
cp -R "{skill_root}" ~/.cursor/skills/{SKILL_NAME}
cp -R "{skill_root}" ~/.codex/skills/{SKILL_NAME}
```

## Verification

- MatMaster: skill appears after zip upload, name `{SKILL_NAME}`
- Local: `~/.cursor/skills/{SKILL_NAME}/SKILL.md` exists
- YAML frontmatter `name:` is `{SKILL_NAME}`

## After install

- Tell the user the skill is available as `{SKILL_NAME}` / `/apex-flow`
- For APEX calculation requests, read `SKILL.md` first, then load referenced
  docs under `reference/` only as needed
- Do not keep duplicate installations under legacy skill names

## Notes

- The PyPI / pip package and agent skill both use the name `{SKILL_NAME}`.
- Re-run `apex skill` to reprint this prompt, or `apex skill --zip` to rebuild
  the MatMaster upload archive.
"""


def build_skill_zip(output: Path | None = None) -> Path:
    """
    Pack the bundled apex-flow directory into a zip for MatMaster upload.

    Large DeePMD checkpoints (``*.pt``) are excluded on purpose so the archive
    stays small. Frozen ``*.pb`` models under ``models/`` are included.
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
            if any(part in _ZIP_SKIP_DIR_NAMES for part in path.parts):
                continue
            if path.name in _ZIP_SKIP_FILE_NAMES:
                continue
            if path.suffix in _ZIP_SKIP_FILE_SUFFIXES:
                continue
            if path.name.endswith(".partial"):
                continue
            arcname = Path(SKILL_NAME) / path.relative_to(skill_root)
            zf.write(path, arcname.as_posix())

    return out


def skill_from_args(args) -> None:
    """Print the Agent install prompt, or write a MatMaster-ready skill zip."""
    skill_root = get_skill_root()
    if not (skill_root / "SKILL.md").is_file():
        raise FileNotFoundError(
            f"Bundled skill not found at {skill_root}. "
            "Reinstall apex-flow or check package data."
        )
    if getattr(args, "zip", False):
        output = getattr(args, "output", None)
        zip_path = build_skill_zip(Path(output) if output else None)
        print(f"Wrote MatMaster skill archive: {zip_path}")
        print(f"Upload this zip in MatMaster (top-level folder: {SKILL_NAME}/).")
        return
    print(_agent_install_prompt(skill_root).rstrip())
