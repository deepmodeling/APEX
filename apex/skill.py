"""CLI helpers for the bundled APEX agent skill."""

from __future__ import annotations

from pathlib import Path

from apex.skills import SKILL_NAME, get_skill_root


def _agent_install_prompt(skill_root: Path) -> str:
    skill_md = skill_root / "SKILL.md"
    return f"""\
# Agent prompt: install the APEX skill ({SKILL_NAME})

You are an AI coding agent. Install the bundled APEX agent skill so future
sessions can orchestrate APEX (Alloy Property EXplorer) workflows.

## Source (already on this machine)

- Skill directory: `{skill_root}`
- Entry file: `{skill_md}`

Confirm `{skill_md}` exists before copying. Do not invent or regenerate the
skill content; copy the directory as-is (including `reference/`, `scripts/`,
`data/`, `models/`, and `plugin.yaml`).

## Install destinations

Copy the whole `{SKILL_NAME}/` directory to one or more of these locations
(create parents if missing). Prefer user-global installs unless the user asked
for a project-only skill:

1. Cursor (user-global): `~/.cursor/skills/{SKILL_NAME}/`
2. Codex / OpenAI agents (user-global): `~/.codex/skills/{SKILL_NAME}/`
3. Cursor (project-local, optional): `<repo>/.cursor/skills/{SKILL_NAME}/`

Example commands:

```bash
mkdir -p ~/.cursor/skills ~/.codex/skills
cp -R "{skill_root}" ~/.cursor/skills/{SKILL_NAME}
cp -R "{skill_root}" ~/.codex/skills/{SKILL_NAME}
```

On Windows (PowerShell):

```powershell
New-Item -ItemType Directory -Force -Path "$HOME/.cursor/skills","$HOME/.codex/skills" | Out-Null
Copy-Item -Recurse -Force "{skill_root}" "$HOME/.cursor/skills/{SKILL_NAME}"
Copy-Item -Recurse -Force "{skill_root}" "$HOME/.codex/skills/{SKILL_NAME}"
```

## Verification

After copying, verify:

- `~/.cursor/skills/{SKILL_NAME}/SKILL.md` exists (and/or the Codex path)
- YAML frontmatter `name:` is `{SKILL_NAME}`
- Relative paths in `SKILL.md` still resolve (`reference/`, `scripts/`)

## After install

- Tell the user the skill is available as `{SKILL_NAME}` / `/apex-skill`
- For APEX calculation requests, read `SKILL.md` first, then load referenced
  docs under `reference/` only as needed
- Do not keep a duplicate skill named `apex-flow`; remove any old
  `apex-flow` skill directories if present

## Notes

- The PyPI / pip package name remains `apex-flow`; the agent skill name is
  `{SKILL_NAME}`.
- Re-run `apex skill` anytime to reprint this prompt with the current
  bundled skill path.
"""


def skill_from_args(args) -> None:
    """Print the Agent install prompt for the bundled apex-skill."""
    skill_root = get_skill_root()
    if not (skill_root / "SKILL.md").is_file():
        raise FileNotFoundError(
            f"Bundled skill not found at {skill_root}. "
            "Reinstall apex-flow or check package data."
        )
    if getattr(args, "path", False):
        print(skill_root)
        return
    print(_agent_install_prompt(skill_root).rstrip())
