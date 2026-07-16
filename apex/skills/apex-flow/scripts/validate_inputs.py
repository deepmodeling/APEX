#!/usr/bin/env python3
"""
APEX Input Validator

Validates param.json and global.json before submission.

Usage:
    python validate_inputs.py --param param.json --global global.json
"""

import argparse
import json
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


def validate_global(global_config: dict) -> list:
    """Validate global.json configuration."""
    errors = []
    warnings = []

    # Check machine config
    if "machine" not in global_config:
        errors.append("Missing 'machine' section in global.json")
    else:
        machine = global_config["machine"]
        if "batch_type" not in machine:
            errors.append("Missing 'machine.batch_type'")

    # Check resources
    if "resources" not in global_config:
        warnings.append("No 'resources' section - will use defaults")

    # Check run_command
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

    # VASP-specific checks
    elif int_type == "vasp":
        if "potcars" not in interaction:
            errors.append("VASP requires 'potcars' mapping")
        if "potcar_prefix" not in interaction:
            warnings.append("VASP: 'potcar_prefix' not specified")

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
    errors, warnings = validate_structures(param_config, param_path.parent)
    all_errors.extend(errors)
    all_warnings.extend(warnings)

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


if __name__ == "__main__":
    main()
