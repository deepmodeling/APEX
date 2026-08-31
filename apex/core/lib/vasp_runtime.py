"""Build per-task VASP commands from the generated KPOINTS grid."""

import re
import shlex


_VASP_EXECUTABLE_RE = re.compile(r"\bvasp_(?:std|gam)\b", re.IGNORECASE)
_GAMMA_ONLY_PROBE = (
    "awk 'NF { n++; "
    "if (n == 3) style = tolower($1); "
    "if (n == 4) ok = "
    "(style == \"gamma\" && $1 == 1 && $2 == 1 && $3 == 1) "
    "} END { exit !(n >= 4 && ok) }' KPOINTS"
)


def is_switchable_vasp_command(run_command: str) -> bool:
    return bool(
        isinstance(run_command, str)
        and _VASP_EXECUTABLE_RE.search(run_command)
    )


def _replace_vasp_executable(run_command: str, executable: str) -> str:
    if not isinstance(run_command, str) or not run_command.strip():
        raise ValueError("VASP run command must be a non-empty string")
    if not _VASP_EXECUTABLE_RE.search(run_command):
        raise ValueError(
            "VASP run command must contain vasp_std or vasp_gam so APEX can "
            "select the executable from the generated KPOINTS grid"
        )
    return _VASP_EXECUTABLE_RE.sub(executable, run_command)


def build_kpoint_aware_vasp_command(
    run_command: str, *, staged_run_command: bool = False
) -> str:
    """Select vasp_gam for Gamma-centered 1x1x1, otherwise vasp_std.

    ``staged_run_command`` is used by properties whose task-local
    ``run_command`` performs multiple VASP stages.  The selected command is
    passed through ``APEX_RUN_COMMAND`` so every stage uses the same binary.
    """
    gamma_command = _replace_vasp_executable(run_command, "vasp_gam")
    standard_command = _replace_vasp_executable(run_command, "vasp_std")
    selector = (
        f"if {_GAMMA_ONLY_PROBE}; then "
        f"APEX_RUN_COMMAND={shlex.quote(gamma_command)}; "
        "echo 'APEX VASP executable: vasp_gam (Gamma 1x1x1)'; "
        "else "
        f"APEX_RUN_COMMAND={shlex.quote(standard_command)}; "
        "echo 'APEX VASP executable: vasp_std (non-Gamma-only grid)'; "
        "fi"
    )
    if staged_run_command:
        return (
            f"{selector}; "
            "if [ -f run_command ]; then "
            'APEX_RUN_COMMAND="$APEX_RUN_COMMAND" bash run_command; '
            'else eval "$APEX_RUN_COMMAND"; fi'
        )
    return f'{selector}; eval "$APEX_RUN_COMMAND"'
