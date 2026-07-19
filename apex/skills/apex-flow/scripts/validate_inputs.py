#!/usr/bin/env python3
"""
APEX Input Validator

Validates param.json and global.json before submission.

Usage:
    python validate_inputs.py --param param.json --global global.json
"""

import argparse
import glob
import json
import os
import sys
from pathlib import Path


# Valid property types
VALID_PROPERTIES = {
    "eos", "cohesive", "elastic", "surface", "vacancy",
    "interstitial", "phonon", "gamma", "gamma_surface",
    "decohesive", "finite_t_latt", "finite_t_elastic",
    "gruneisen", "annealing",
}

# LAMMPS-only properties
LAMMPS_ONLY_PROPERTIES = {"finite_t_elastic"}

# Valid LAMMPS potential types
VALID_LAMMPS_TYPES = {
    "deepmd", "mace", "nep", "gap", "snap", "rann",
    "eam_alloy", "eam_fs", "meam", "meam_spline",
}

# Valid backend types
VALID_BACKENDS = {"vasp", "abacus"} | VALID_LAMMPS_TYPES


def _resolve_under_base(path_str: str, base_dir: Path) -> Path:
    path = Path(path_str).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path


def _elements_from_poscar(poscar_path: Path) -> list:
    """Best-effort VASP5 species line parse (no pymatgen required)."""
    try:
        lines = poscar_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    if len(lines) < 6:
        return []
    # VASP 5+: line index 5 is element symbols when tokens start with a letter
    tokens = lines[5].split()
    if tokens and all(tok and tok[0].isalpha() for tok in tokens):
        return tokens
    return []


def _iter_structure_poscars(param_config: dict, base_dir: Path) -> list:
    """Resolve structures patterns to POSCAR/CONTCAR/STRU files."""
    found = []
    for pattern in param_config.get("structures", []) or []:
        search = pattern if os.path.isabs(pattern) else str(base_dir / pattern)
        matches = sorted(glob.glob(search))
        if not matches and not any(ch in pattern for ch in "*?[]"):
            # literal directory that exists but was not glob-expanded
            candidate = _resolve_under_base(pattern, base_dir)
            if candidate.exists():
                matches = [str(candidate)]
        for match in matches:
            path = Path(match)
            if path.is_file():
                found.append(path)
                continue
            if path.is_dir():
                for name in ("POSCAR", "CONTCAR", "STRU"):
                    nested = path / name
                    if nested.is_file():
                        found.append(nested)
                        break
                else:
                    found.extend(sorted(path.glob("conf_*/POSCAR")))
    return found


def collect_structure_elements(param_config: dict, base_dir: Path) -> set:
    elements = set()
    for poscar in _iter_structure_poscars(param_config, base_dir):
        elements.update(_elements_from_poscar(poscar))
    return elements


def resolve_vasp_potcar_file(prefix: Path, potcar_entry: str) -> tuple:
    """Resolve POTCAR file the same way APEX VASP.py opens it.

    Returns (path_or_None, hint_for_agent).
    APEX uses ``os.path.join(prefix, potcars[element])`` as a file path.
    """
    direct = (prefix / potcar_entry).expanduser()
    if direct.is_file():
        return direct.resolve(), None
    nested = (prefix / potcar_entry / "POTCAR").expanduser()
    if nested.is_file():
        return None, (
            f"found potpaw-style file at '{nested}', but APEX opens "
            f"join(potcar_prefix, potcars[el]) as a file — set potcars "
            f"entry to '{potcar_entry}/POTCAR' (not '{potcar_entry}')"
        )
    return None, None


def validate_vasp_potcars(
    interaction: dict, base_dir: Path, required_elements: set = None
) -> tuple:
    """Check potcar_prefix accessibility and required POTCAR files."""
    errors = []
    warnings = []

    prefix_raw = interaction.get("potcar_prefix")
    potcars = interaction.get("potcars") or {}
    if not prefix_raw:
        errors.append(
            "VASP: 'potcar_prefix' missing — ask the user for a readable "
            "POTCAR library path"
        )
        return errors, warnings
    if not potcars:
        errors.append("VASP: 'potcars' mapping missing")
        return errors, warnings

    prefix = _resolve_under_base(str(prefix_raw), base_dir)
    # Absolute host libraries are invisible inside Bohrium/dflow containers.
    if Path(str(prefix_raw)).expanduser().is_absolute():
        errors.append(
            f"VASP: potcar_prefix is an absolute host path ({prefix_raw}). "
            "Bohrium/dflow cannot see paths like /share/PAW_PBE after upload. "
            "Copy POTCAR_<Element> files into the job root and set "
            "potcar_prefix to '.' (or re-run generate_config.py create)."
        )
        return errors, warnings
    if not prefix.exists():
        errors.append(
            f"VASP: potcar_prefix not accessible (path does not exist): {prefix}"
        )
        return errors, warnings
    if not os.access(prefix, os.R_OK):
        errors.append(f"VASP: potcar_prefix exists but is not readable: {prefix}")
        return errors, warnings
    if not prefix.is_dir():
        warnings.append(
            f"VASP: potcar_prefix is not a directory ({prefix}); "
            "continuing file checks anyway"
        )

    check_elements = set(required_elements or [])
    check_elements.update(potcars.keys())
    if not check_elements:
        warnings.append(
            "VASP: no structure elements parsed and potcars is empty — "
            "cannot verify POTCAR files"
        )
        return errors, warnings

    missing = []
    for element in sorted(check_elements):
        if element not in potcars:
            missing.append(f"{element} (no potcars mapping)")
            continue
        entry = potcars[element]
        path, hint = resolve_vasp_potcar_file(prefix, entry)
        if path is None:
            expected = prefix / entry
            if hint:
                missing.append(f"{element} → {hint}")
            else:
                missing.append(f"{element} → missing file {expected}")

    if missing:
        errors.append(
            "VASP: POTCAR location unusable or incomplete for required "
            f"elements under '{prefix}'. Missing/invalid:\n  - "
            + "\n  - ".join(missing)
            + "\nAsk the user to confirm the correct POTCAR library path "
            "and potpaw folder names."
        )
    else:
        mapped = ", ".join(
            f"{el}:{potcars[el]}" for el in sorted(check_elements) if el in potcars
        )
        warnings.append(f"VASP: POTCAR files OK ({mapped})")

    return errors, warnings


def validate_global(global_config: dict) -> list:
    """Validate global.json configuration."""
    errors = []
    warnings = []

    # generate_config.py writes the current, top-level APEX schema. Keep
    # accepting the older nested-machine schema for existing user configs.
    is_current_schema = any(
        key in global_config
        for key in ("dflow_host", "context_type", "bohrium_config", "program_id")
    )

    if is_current_schema:
        for key in ("batch_type", "context_type"):
            if not global_config.get(key):
                errors.append(f"Missing '{key}' in global.json")

        program_id = global_config.get("program_id")
        if not isinstance(program_id, int) or isinstance(program_id, bool):
            errors.append(
                "'program_id' must be an unquoted JSON integer, not a string; "
                "generate global.json with "
                "generate_config.py"
            )
        elif program_id <= 0:
            errors.append("'program_id' must be a positive integer")

        bohrium_config = global_config.get("bohrium_config")
        if not isinstance(bohrium_config, dict):
            errors.append("Missing 'bohrium_config' section in global.json")
        else:
            project_id = bohrium_config.get("project_id")
            if not isinstance(project_id, int) or isinstance(project_id, bool):
                errors.append(
                    "'bohrium_config.project_id' must be a JSON integer, "
                    "not a quoted string"
                )
            elif project_id <= 0:
                errors.append(
                    "'bohrium_config.project_id' must be a positive integer"
                )
            elif isinstance(program_id, int) and not isinstance(program_id, bool):
                if project_id != program_id:
                    errors.append(
                        "'program_id' and 'bohrium_config.project_id' must match"
                    )

            ticket = bohrium_config.get("ticket")
            if not isinstance(ticket, str) or not ticket.strip():
                errors.append(
                    "Missing non-empty 'bohrium_config.ticket'; regenerate "
                    "global.json with generate_config.py"
                )

        machine = global_config.get("machine")
        if isinstance(machine, dict):
            remote_profile = machine.get("remote_profile")
            if isinstance(remote_profile, dict) and "program_id" in remote_profile:
                nested_id = remote_profile["program_id"]
                if not isinstance(nested_id, int) or isinstance(nested_id, bool):
                    errors.append(
                        "'machine.remote_profile.program_id' must be a JSON "
                        "integer, not a quoted string"
                    )
                elif nested_id != program_id:
                    errors.append(
                        "'machine.remote_profile.program_id' must match "
                        "'program_id'"
                    )

        if not global_config.get("scass_type"):
            errors.append("Missing 'scass_type' in global.json")

        run_commands = (
            "lammps_run_command", "abacus_run_command", "vasp_run_command"
        )
        if not any(global_config.get(key) for key in run_commands):
            errors.append(
                "Missing calculator run command in global.json "
                "(expected one of lammps_run_command, abacus_run_command, "
                "vasp_run_command)"
            )
    else:
        # Legacy nested-machine schema.
        if "machine" not in global_config:
            errors.append("Missing 'machine' section in global.json")
        else:
            machine = global_config["machine"]
            if "batch_type" not in machine:
                errors.append("Missing 'machine.batch_type'")

        if "resources" not in global_config:
            warnings.append("No 'resources' section - will use defaults")

        if "run_command" not in global_config:
            errors.append("Missing 'run_command' in global.json")

    return errors, warnings


def validate_interaction(interaction: dict) -> list:
    """Validate interaction configuration."""
    errors = []
    warnings = []

    if "type" not in interaction:
        errors.append("Missing 'interaction.type'")
        return errors, warnings

    int_type = interaction["type"]

    if int_type not in VALID_BACKENDS:
        errors.append(f"Unknown interaction type: '{int_type}'")
        return errors, warnings

    # LAMMPS-specific checks
    if int_type in VALID_LAMMPS_TYPES:
        if "model" not in interaction:
            errors.append(f"LAMMPS potential '{int_type}' requires 'model' field")
        if "type_map" not in interaction:
            errors.append(f"LAMMPS potential '{int_type}' requires 'type_map' field")

    # ABACUS-specific checks
    elif int_type == "abacus":
        if "potcars" not in interaction:
            errors.append("ABACUS requires 'potcars' (pseudopotential mapping)")
        if "orb_files" not in interaction:
            warnings.append("ABACUS: 'orb_files' not specified (required for LCAO)")

    # VASP-specific checks (filesystem checks run in validate_vasp_potcars)
    elif int_type == "vasp":
        if "potcars" not in interaction:
            errors.append("VASP requires 'potcars' mapping")
        if "potcar_prefix" not in interaction:
            errors.append("VASP requires 'potcar_prefix' (POTCAR library path)")

    return errors, warnings


def validate_properties(properties: list, interaction_type: str) -> list:
    """Validate property configurations."""
    errors = []
    warnings = []

    if not properties:
        errors.append("No properties defined")
        return errors, warnings

    for i, prop in enumerate(properties):
        prefix = f"properties[{i}]"

        if "type" not in prop:
            errors.append(f"{prefix}: missing 'type' field")
            continue

        prop_type = prop["type"]
        if prop_type not in VALID_PROPERTIES:
            errors.append(f"{prefix}: unknown property type '{prop_type}'")
            continue

        # Check LAMMPS-only constraint
        if prop_type in LAMMPS_ONLY_PROPERTIES:
            if interaction_type in ("vasp", "abacus"):
                errors.append(
                    f"{prefix}: '{prop_type}' is LAMMPS-only, "
                    f"but backend is '{interaction_type}'"
                )

        # Property-specific validation
        if prop_type == "eos":
            for key in ("vol_start", "vol_end", "vol_step"):
                if key not in prop:
                    errors.append(f"{prefix}: EOS requires '{key}'")

        elif prop_type == "cohesive":
            for key in ("latt_start", "latt_end", "latt_step"):
                if key not in prop:
                    errors.append(f"{prefix}: cohesive requires '{key}'")

        elif prop_type == "surface":
            for key in ("min_slab_size", "min_vacuum_size"):
                if key not in prop:
                    errors.append(f"{prefix}: surface requires '{key}'")

        elif prop_type == "decohesive":
            if "miller_index" not in prop:
                errors.append(f"{prefix}: decohesive requires 'miller_index'")
            if "min_slab_size" not in prop:
                errors.append(f"{prefix}: decohesive requires 'min_slab_size'")

        elif prop_type == "gruneisen":
            mesh = prop.get("MESH")
            if mesh is None:
                mesh = [20, 20, 20]
            if (
                not isinstance(mesh, (list, tuple))
                or len(mesh) != 3
                or any(
                    not isinstance(value, int)
                    or isinstance(value, bool)
                    or value <= 0
                    for value in mesh
                )
            ):
                errors.append(
                    f"{prefix}: gruneisen 'MESH' must contain 3 positive integers"
                )
            if "volume_strains" not in prop:
                errors.append(f"{prefix}: gruneisen requires 'volume_strains'")
            else:
                vs = prop["volume_strains"]
                if 0.0 not in vs:
                    errors.append(f"{prefix}: gruneisen 'volume_strains' must include 0.0")
                if len(vs) < 3:
                    errors.append(f"{prefix}: gruneisen needs ≥3 volume_strains points")
            if "temperatures" not in prop:
                errors.append(f"{prefix}: gruneisen requires 'temperatures'")

        elif prop_type == "finite_t_elastic":
            cal_setting = prop.get("cal_setting", {})
            method = cal_setting.get("method", "paired_langevin")
            if method != "paired_langevin":
                errors.append(
                    f"{prefix}: finite_t_elastic only supports method='paired_langevin'"
                )

        if prop_type in {"gamma", "gamma_surface"}:
            plane = prop.get("plane_miller")
            direction = prop.get("slip_direction")
            if not plane or not direction:
                errors.append(
                    f"{prefix}: {prop_type} requires plane_miller and slip_direction"
                )
            elif len(plane) != len(direction):
                errors.append(
                    f"{prefix}: plane_miller and slip_direction dimensions differ"
                )
            elif sum(p * d for p, d in zip(plane, direction)) != 0:
                errors.append(
                    f"{prefix}: slip_direction must lie on plane_miller"
                )

        if prop_type == "gamma_surface":
            for key in ("n_steps_x", "n_steps_y"):
                value = prop.get(key, prop.get("n_steps", 10))
                if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                    errors.append(f"{prefix}: {key} must be a positive integer")
            closed_loop = prop.get("closed_loop", False)
            if not isinstance(closed_loop, bool):
                errors.append(f"{prefix}: closed_loop must be a boolean")
            if closed_loop is True and (
                prop.get("slip_length") is not None
                or prop.get("slip_length_y") is not None
            ):
                errors.append(
                    f"{prefix}: closed_loop cannot be combined with "
                    "slip_length/slip_length_y"
                )

    return errors, warnings


def validate_structures(param_config: dict, base_dir: Path) -> list:
    """Validate structure paths."""
    errors = []
    warnings = []

    structures = param_config.get("structures", [])
    if not structures:
        errors.append("No 'structures' defined in param.json")
        return errors, warnings

    for s in structures:
        struct_path = base_dir / s
        if not struct_path.exists():
            warnings.append(f"Structure path not found: {s} (may exist in container)")

    return errors, warnings


def main():
    parser = argparse.ArgumentParser(
        description="Validate APEX configuration files"
    )
    parser.add_argument("--param", "-p", required=True,
                        help="Path to param.json")
    parser.add_argument("--global", "-g", dest="global_json",
                        help="Path to global.json")
    parser.add_argument("--strict", action="store_true",
                        help="Treat warnings as errors")

    args = parser.parse_args()

    all_errors = []
    all_warnings = []
    global_config = None

    # Load param.json
    param_path = Path(args.param)
    if not param_path.exists():
        print(f"ERROR: param.json not found: {param_path}", file=sys.stderr)
        sys.exit(1)

    with open(param_path) as f:
        param_config = json.load(f)

    # Validate global.json if provided
    if args.global_json:
        global_path = Path(args.global_json)
        if not global_path.exists():
            all_errors.append(f"global.json not found: {global_path}")
        else:
            with open(global_path) as f:
                global_config = json.load(f)
            errors, warnings = validate_global(global_config)
            all_errors.extend(errors)
            all_warnings.extend(warnings)

    # Validate interaction
    interaction = param_config.get("interaction", {})
    base_dir = param_path.parent
    if not interaction:
        all_errors.append("Missing 'interaction' in param.json")
    else:
        errors, warnings = validate_interaction(interaction)
        all_errors.extend(errors)
        all_warnings.extend(warnings)

    # Validate properties
    properties = param_config.get("properties", [])
    interaction_type = interaction.get("type", "unknown")
    errors, warnings = validate_properties(properties, interaction_type)
    all_errors.extend(errors)
    all_warnings.extend(warnings)

    # Validate structures
    errors, warnings = validate_structures(param_config, base_dir)
    all_errors.extend(errors)
    all_warnings.extend(warnings)

    # VASP POTCAR filesystem check (prefix + per-element files)
    if interaction.get("type") == "vasp":
        required_elements = collect_structure_elements(param_config, base_dir)
        errors, warnings = validate_vasp_potcars(
            interaction, base_dir, required_elements=required_elements
        )
        all_errors.extend(errors)
        # "POTCAR files OK" is informational; keep as warning only when useful
        for w in warnings:
            if w.startswith("VASP: POTCAR files OK"):
                print(f"  {w}")
            else:
                all_warnings.append(w)

    # Report
    if all_warnings:
        print("WARNINGS:", file=sys.stderr)
        for w in all_warnings:
            print(f"  ⚠ {w}", file=sys.stderr)

    if all_errors:
        print("\nERRORS:", file=sys.stderr)
        for e in all_errors:
            print(f"  ✗ {e}", file=sys.stderr)
        print(f"\nValidation FAILED: {len(all_errors)} error(s)", file=sys.stderr)
        sys.exit(1)
    elif args.strict and all_warnings:
        print(f"\nValidation FAILED (strict mode): {len(all_warnings)} warning(s)",
              file=sys.stderr)
        sys.exit(1)
    else:
        print(f"✓ Validation PASSED ({len(all_warnings)} warnings)")
        # Print summary
        print(f"  Backend: {interaction_type}")
        print(f"  Properties: {[p.get('type') for p in properties]}")
        print(f"  Structures: {param_config.get('structures', [])}")
        if global_config and "program_id" in global_config:
            program_id = global_config["program_id"]
            project_id = global_config.get(
                "bohrium_config", {}
            ).get("project_id")
            print("  Hard project ID type check:")
            print(
                f"    program_id={program_id!r} "
                f"type={type(program_id).__name__}"
            )
            print(
                f"    bohrium_config.project_id={project_id!r} "
                f"type={type(project_id).__name__}"
            )


if __name__ == "__main__":
    main()
