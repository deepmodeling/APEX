#!/usr/bin/env python3
"""
APEX Configuration Generator

Generates global.json and param.json for APEX workflows.
Handles access_key → ticket conversion and RFC 1123 name compliance.

Environment variables:
    BOHRIUM_ACCESS_KEY  — Bohrium access key (converted to ticket via API)
    BOHRIUM_PROJECT_ID  — Bohrium project ID (required unless --project-id is set)

Usage:
    python generate_config.py \
        --structure POSCAR \
        --backend lammps \
        --potential eam_alloy \
        --model Cu01.eam.alloy \
        --properties elastic \
        --flow-type joint \
        --workflow-name "cu-fcc-elastic" \
        --output-dir ./job
"""

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError


# =============================================================================
# Constants
# =============================================================================

TICKET_API_URL = "https://openapi.dp.tech/openapi/v1/ticket/get"
DFLOW_HOST = "https://workflows.deepmodeling.com"
APEX_IMAGE = "registry.dp.tech/dptech/dp/native/prod-397637/apex-flow:1.3.0.post2"
LAMMPS_IMAGE = (
    "registry.dp.tech/dptech/dp/native/prod-397637/"
    "deepmd-kit-phonolammps:3.1.3"
)

# Default parameters for each property type
# These are TESTED stable defaults that prevent KeyError failures.
# All required parameters are included. Crystallographic constraints are satisfied.
# Defaults are physically reasonable for typical FCC/BCC metals.
PROPERTY_DEFAULTS = {
    "eos": {
        "type": "eos",
        "vol_start": 0.8,
        "vol_end": 1.2,
        "vol_step": 0.05,
    },
    "cohesive": {
        "type": "cohesive",
        "latt_start": 0.8,
        "latt_end": 1.5,
        "latt_step": 0.05,
        "cal_type": "static",
    },
    "elastic": {
        "type": "elastic",
        "norm_deform": 0.01,
        "shear_deform": 0.01,
    },
    "surface": {
        "type": "surface",
        "min_slab_size": 50,
        "min_vacuum_size": 20,
        "max_miller": 2,
    },
    "vacancy": {
        "type": "vacancy",
        "supercell": [3, 3, 3],
    },
    "interstitial": {
        "type": "interstitial",
        "supercell": [3, 3, 3],
    },
    "phonon": {
        "type": "phonon",
        "supercell_size": [3, 3, 3],
        "BAND_POINTS": 51,
    },
    "gamma": {
        "type": "gamma",
        "plane_miller": [1, 1, 1],
        "slip_direction": [-1, 1, 0],
        "supercell_size": [1, 1, 5],
        "n_steps": 10,
    },
    "gamma_surface": {
        "type": "gamma_surface",
        "plane_miller": [1, 1, 1],
        "slip_direction": [-1, 1, 0],
        "supercell_size": [1, 1, 5],
        "closed_loop": False,
        "n_steps_x": 10,
        "n_steps_y": 10,
    },
    "decohesive": {
        "type": "decohesive",
        "miller_index": [1, 1, 1],
        "min_slab_size": 40,
        "max_vacuum_size": 15,
        "vacuum_size_step": 1.0,
    },
    "finite_t_latt": {
        "type": "finite_t_latt",
        "supercell_size": [3, 3, 3],
        "cal_setting": {
            "temperature": [200, 400, 600, 800],
        },
    },
    "finite_t_elastic": {
        "type": "finite_t_elastic",
        "supercell_size": [3, 3, 3],
        "cal_setting": {
            "temperature": [300],
            "strain": 0.001,
        },
    },
    "gruneisen": {
        "type": "gruneisen",
        "supercell_size": [2, 2, 2],
        "volume_strains": [-0.02, -0.01, 0.0, 0.01, 0.02],
        "temperatures": [100, 200, 300, 400, 500],
        "alpha_mode": "full",
    },
    "annealing": {
        "type": "annealing",
        "supercell_size": [3, 3, 3],
        "cal_setting": {
            "start_temp": 4,
            "target_temp": 300,
            "end_temp": 4,
            "temp_ramp_rate": 1000,
        },
    },
}

# LAMMPS-only properties
LAMMPS_ONLY = {"finite_t_elastic"}

# GPU potential types — benefit from GPU scass_type
GPU_POTENTIALS = {"deepmd", "mace", "nep"}

# scass_type defaults for inner dflow containers
SCASS_TYPES = {
    "lammps_gpu": "c8_m31_1 * NVIDIA T4",
    "lammps_cpu": "c16_m32_cpu",
    "abacus": "c16_m32_cpu",
    "vasp": "c32_m128_cpu",
}

# =============================================================================
# DFT Calculation Defaults (fast/loose for APEX validation workflows)
# =============================================================================
# Philosophy: APEX is for property screening, NOT publication-grade DFT.
# Use fast settings to minimize wall time. Users can override for production.
#
# Key principle for ABACUS in APEX:
#   - Do NOT set pseudo_dir/orbital_dir in INPUT (APEX manages paths via STRU)
#   - STRU must have ATOMIC_SPECIES with PP filename and NUMERICAL_ORBITAL section
#   - APEX's modify_stru_path() adds pp_orb/ prefix; INPUT pseudo_dir would double it

ABACUS_INPUT_DEFAULTS = {
    "relax": {
        "calculation": "cell-relax",
        "basis_type": "lcao",
        "ecutwfc": 100,
        "scf_thr": "1.0e-6",
        "scf_nmax": 100,
        "smearing_method": "gauss",
        "smearing_sigma": 0.02,
        "mixing_type": "broyden",
        "mixing_beta": 0.7,
        "cal_force": 1,
        "cal_stress": 1,
        "force_thr_ev": 0.02,
        "stress_thr": 1.0,
        "relax_nmax": 50,
        "kspacing": 0.20,       # FAST: ~6x6x6 for typical metals (vs 0.10 → 12x12x12)
    },
    "phonon_scf": {
        "calculation": "scf",
        "basis_type": "lcao",
        "ecutwfc": 100,
        "scf_thr": "1.0e-7",
        "scf_nmax": 100,
        "smearing_method": "gauss",
        "smearing_sigma": 0.01,
        "mixing_type": "broyden",
        "mixing_beta": 0.7,
        "cal_force": 1,
        "kspacing": 0.15,       # Slightly tighter for force accuracy
    },
}

# DFT-specific property defaults: smaller supercells, fewer points
DFT_PROPERTY_OVERRIDES = {
    "phonon": {
        "supercell_size": [2, 2, 2],    # 8 atoms (vs 27 for 3x3x3)
        "BAND_POINTS": 21,
    },
    "gruneisen": {
        "supercell_size": [2, 2, 2],
        "volume_strains": [-0.02, 0.0, 0.02],  # 3 points (vs 5), faster
        "temperatures": [100, 300, 500],
    },
    "vacancy": {
        "supercell": [2, 2, 2],         # 8 atoms (vs 27 for 3x3x3)
    },
    "interstitial": {
        "supercell": [2, 2, 2],
    },
    "surface": {
        "min_slab_size": 30,            # Thinner slab, fewer atoms
        "min_vacuum_size": 15,
        "max_miller": 1,                # Only low-index surfaces
    },
}


# =============================================================================
# Ticket conversion
# =============================================================================

def get_bohrium_ticket(access_key: str) -> str:
    """
    Convert a Bohrium access_key to a dflow ticket via the OpenAPI.

    API: GET https://openapi.dp.tech/openapi/v1/ticket/get?accessKey=<KEY>
    Header: x-app-key: (empty string)
    Response: {"code": 0, "data": {"ticket": "UUID-36-chars"}}

    Returns the ticket string (UUID).
    Raises RuntimeError on failure.
    """
    url = f"{TICKET_API_URL}?accessKey={access_key}"
    req = Request(url, method="GET")
    req.add_header("x-app-key", "")

    try:
        with urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode())
    except (URLError, HTTPError) as e:
        raise RuntimeError(f"Failed to get ticket from API: {e}") from e

    if body.get("code") != 0:
        raise RuntimeError(
            f"Ticket API returned error: code={body.get('code')}, "
            f"msg={body.get('message', body.get('msg', 'unknown'))}"
        )

    ticket = body.get("data", {}).get("ticket")
    if not ticket or len(ticket) < 30:
        raise RuntimeError(f"Invalid ticket received: {ticket!r}")

    return ticket


# =============================================================================
# RFC 1123 workflow name
# =============================================================================

def sanitize_workflow_name(name: str) -> str:
    """
    Sanitize a workflow name to comply with RFC 1123 subdomain rules.

    Rules:
    - All lowercase
    - Only [a-z0-9-] allowed
    - Cannot start or end with '-'
    - Max 63 characters

    Examples:
        "Cu-FCC-elastic" → "cu-fcc-elastic"
        "Mo_BCC Surface" → "mo-bcc-surface"
    """
    # Lowercase
    name = name.lower()
    # Replace underscores, spaces, and other invalid chars with hyphens
    name = re.sub(r"[^a-z0-9-]", "-", name)
    # Collapse multiple hyphens
    name = re.sub(r"-+", "-", name)
    # Strip leading/trailing hyphens
    name = name.strip("-")
    # Truncate to 63 chars
    name = name[:63].rstrip("-")

    if not name:
        name = "apex-workflow"

    return name


# =============================================================================
# Global config generation
# =============================================================================

def resolve_project_id(project_id: int = None) -> int:
    """
    Resolve Bohrium project ID from --project-id or BOHRIUM_PROJECT_ID.

    Never hardcode a personal/project-specific ID. Fail loudly if unset.
    """
    if project_id is not None:
        return int(project_id)
    env = os.environ.get("BOHRIUM_PROJECT_ID", "").strip()
    if not env:
        raise RuntimeError(
            "BOHRIUM_PROJECT_ID environment variable not set and --project-id "
            "not provided. Set BOHRIUM_PROJECT_ID to the active Bohrium project "
            "before generating global.json."
        )
    try:
        return int(env)
    except ValueError as exc:
        raise RuntimeError(
            f"BOHRIUM_PROJECT_ID must be an integer, got: {env!r}"
        ) from exc


def _validate_image_scass(image: str, scass: str) -> None:
    """Reject known-bad image × scass_type combinations before write-out."""
    script_dir = Path(__file__).resolve().parent
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    from validate_apex_combo import check_combo  # noqa: WPS433

    ok, errors = check_combo(image, scass)
    if not ok:
        raise RuntimeError(
            "Blocked image × scass_type combination:\n  - "
            + "\n  - ".join(errors)
            + "\nRun: python scripts/validate_apex_combo.py list-combos"
        )


def build_global_json(backend: str, potential: str = None,
                      access_key: str = None, project_id: int = None,
                      scass_type: str = None,
                      run_command: str = None) -> dict:
    """
    Build global.json for APEX dflow submission.

    This config tells APEX how to orchestrate calculations via
    workflows.deepmodeling.com (dflow/Argo).

    ``program_id`` / ``bohrium_config.project_id`` always come from
    ``--project-id`` or ``BOHRIUM_PROJECT_ID`` (required).
    """
    pid = resolve_project_id(project_id)

    # Resolve access key and convert to ticket
    key = access_key or os.environ.get("BOHRIUM_ACCESS_KEY")
    if not key:
        raise RuntimeError(
            "BOHRIUM_ACCESS_KEY environment variable not set and --access-key not provided. "
            "Cannot generate ticket for dflow authentication."
        )
    ticket = get_bohrium_ticket(key)

    # Determine scass_type for inner containers
    if scass_type:
        inner_scass = scass_type
    elif backend == "lammps" and potential in GPU_POTENTIALS:
        inner_scass = SCASS_TYPES["lammps_gpu"]
    elif backend == "lammps":
        inner_scass = SCASS_TYPES["lammps_cpu"]
    elif backend == "abacus":
        inner_scass = SCASS_TYPES["abacus"]
    elif backend == "vasp":
        inner_scass = SCASS_TYPES["vasp"]
    else:
        inner_scass = SCASS_TYPES["lammps_cpu"]

    # Determine run command for calculator
    if run_command:
        calc_run_command = run_command
    elif backend == "lammps":
        calc_run_command = "lmp -in in.lammps"
    elif backend == "abacus":
        calc_run_command = "mpirun -n 16 abacus"
    elif backend == "vasp":
        calc_run_command = "mpirun -n 16 vasp_std"
    else:
        calc_run_command = "lmp -in in.lammps"

    # Determine calculator image
    if backend == "lammps":
        lammps_image = LAMMPS_IMAGE
    else:
        lammps_image = LAMMPS_IMAGE  # Still needed as fallback in global.json

    _validate_image_scass(lammps_image, inner_scass)

    config = {
        "dflow_host": DFLOW_HOST,
        "k8s_api_server": DFLOW_HOST,
        "batch_type": "Bohrium",
        "context_type": "Bohrium",
        "program_id": pid,
        "bohrium_config": {
            "ticket": ticket,
            "project_id": pid,
        },
        "apex_image_name": APEX_IMAGE,
        "lammps_image_name": lammps_image,
        "lammps_run_command": calc_run_command,
        "scass_type": inner_scass,
        "group_size": 1,
        "pool_size": 1,
    }

    # Add backend-specific image fields
    if backend == "abacus":
        config["abacus_image_name"] = APEX_IMAGE
        config["abacus_run_command"] = calc_run_command
    elif backend == "vasp":
        # VASP image must be provided by user
        config["vasp_run_command"] = calc_run_command

    return config


# =============================================================================
# Interaction and param.json
# =============================================================================

def build_interaction(backend: str, potential: str = None,
                      model: str = None,
                      incar: str = None, potcar_prefix: str = None,
                      potcars: dict = None, orb_files: dict = None) -> dict:
    """Build interaction configuration."""
    if backend == "lammps":
        if not potential:
            raise ValueError("--potential required for LAMMPS backend")
        if not model:
            raise ValueError("--model required for LAMMPS backend")
        return {
            "type": potential,
            "model": model,
            "type_map": "auto",
        }
    elif backend == "abacus":
        interaction = {
            "type": "abacus",
            "incar": incar or "INPUT",
            "potcar_prefix": potcar_prefix or ".",
        }
        if potcars:
            interaction["potcars"] = potcars
        if orb_files:
            interaction["orb_files"] = orb_files
        return interaction
    elif backend == "vasp":
        interaction = {
            "type": "vasp",
            "incar": incar or "INCAR",
            "potcar_prefix": potcar_prefix or ".",
        }
        if potcars:
            interaction["potcars"] = potcars
        return interaction
    else:
        raise ValueError(f"Unknown backend: {backend}")


def build_param_json(structure_path: str, interaction: dict,
                     properties: list, flow_type: str = "joint",
                     relaxation_settings: dict = None) -> dict:
    """Build param.json configuration."""
    param = {
        "structures": [structure_path],
        "interaction": interaction,
    }

    # Relaxation settings
    if flow_type in ("joint", "relax"):
        if relaxation_settings:
            param["relaxation"] = {"cal_setting": relaxation_settings}
        else:
            # Default relaxation settings based on backend
            if interaction["type"] in ("vasp", "abacus"):
                param["relaxation"] = {
                    "cal_setting": {
                        "relax_pos": True,
                        "relax_shape": True,
                        "relax_vol": True,
                    }
                }
            else:
                param["relaxation"] = {
                    "cal_setting": {
                        "etol": 0,
                        "ftol": 1e-10,
                        "maxiter": 5000,
                        "maximal": 500000,
                    }
                }

    # Properties
    if flow_type in ("joint", "props"):
        prop_configs = []
        is_dft = interaction.get("type") in ("abacus", "vasp")
        for prop_name in properties:
            if prop_name in PROPERTY_DEFAULTS:
                prop_config = PROPERTY_DEFAULTS[prop_name].copy()
                # Apply DFT-specific overrides (smaller supercells, fewer points)
                if is_dft and prop_name in DFT_PROPERTY_OVERRIDES:
                    prop_config.update(DFT_PROPERTY_OVERRIDES[prop_name])
                prop_configs.append(prop_config)
            else:
                print(f"Warning: Unknown property '{prop_name}', skipping",
                      file=sys.stderr)
        param["properties"] = prop_configs

    return param


# =============================================================================
# Validation
# =============================================================================

def validate_config(backend: str, potential: str, properties: list):
    """Validate configuration consistency."""
    errors = []

    # Check LAMMPS-only properties
    if backend != "lammps":
        lammps_props = set(properties) & LAMMPS_ONLY
        if lammps_props:
            errors.append(
                f"Properties {lammps_props} are LAMMPS-only but backend is '{backend}'"
            )

    # Check potential is valid for LAMMPS
    valid_potentials = {
        "deepmd", "mace", "nep", "gap", "snap", "rann",
        "eam_alloy", "eam_fs", "meam", "meam_spline"
    }
    if backend == "lammps" and potential not in valid_potentials:
        errors.append(f"Unknown LAMMPS potential type: '{potential}'")

    return errors


# =============================================================================
# Helpers
# =============================================================================

def parse_str_map(map_str: str) -> dict:
    """Parse a string map like 'Element1:value1,Element2:value2'."""
    if not map_str:
        return None
    result = {}
    for pair in map_str.split(","):
        parts = pair.strip().split(":")
        if len(parts) == 2:
            result[parts[0].strip()] = parts[1].strip()
    return result


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Generate APEX configuration files (global.json + param.json) "
                    "with dflow ticket authentication and RFC 1123 compliant names."
    )
    parser.add_argument("--structure", "-s", required=True,
                        help="Path to structure file (POSCAR/CIF)")
    parser.add_argument("--backend", "-b", required=True,
                        choices=["lammps", "abacus", "vasp"],
                        help="Calculator backend")
    parser.add_argument("--potential", "-p",
                        help="LAMMPS potential type (deepmd/mace/nep/eam_alloy/...)")
    parser.add_argument("--model", "-m",
                        help="Model/potential file path")
    parser.add_argument("--properties", nargs="+", required=True,
                        help="Property types to calculate")
    parser.add_argument("--flow-type", default="joint",
                        choices=["joint", "relax", "props"],
                        help="Workflow type (default: joint)")
    parser.add_argument("--workflow-name", "-n",
                        help="Workflow name (auto-lowercased for RFC 1123 compliance)")
    parser.add_argument("--output-dir", "-o", default="./apex_job",
                        help="Output directory for configs")
    parser.add_argument("--access-key",
                        help="Bohrium access key (or set BOHRIUM_ACCESS_KEY env var)")
    parser.add_argument("--project-id", type=int,
                        help="Bohrium project ID (required unless BOHRIUM_PROJECT_ID is set)")
    parser.add_argument("--scass-type",
                        help="Override scass_type for inner dflow containers")
    parser.add_argument("--run-command",
                        help="Override calculator run command")
    parser.add_argument("--incar",
                        help="INCAR/INPUT file path (for VASP/ABACUS)")
    parser.add_argument("--potcar-prefix",
                        help="POTCAR prefix directory (for VASP/ABACUS)")
    parser.add_argument("--potcars",
                        help="POTCAR mapping as 'Element1:potcar1,Element2:potcar2'")
    parser.add_argument("--orb-files",
                        help="Orbital files as 'Element1:file1,Element2:file2' (ABACUS)")

    args = parser.parse_args()

    # -------------------------------------------------------------------------
    # Parse complex arguments
    # -------------------------------------------------------------------------
    potcars = parse_str_map(args.potcars)
    orb_files = parse_str_map(args.orb_files)

    # -------------------------------------------------------------------------
    # Validate
    # -------------------------------------------------------------------------
    errors = validate_config(args.backend, args.potential, args.properties)
    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        sys.exit(1)

    # -------------------------------------------------------------------------
    # Sanitize workflow name (RFC 1123)
    # -------------------------------------------------------------------------
    if args.workflow_name:
        workflow_name = sanitize_workflow_name(args.workflow_name)
        if workflow_name != args.workflow_name:
            print(f"Workflow name sanitized: '{args.workflow_name}' → '{workflow_name}'")
    else:
        # Auto-generate from structure basename and properties
        struct_stem = Path(args.structure).stem.lower()
        props_str = "-".join(args.properties[:3])  # First 3 properties
        workflow_name = sanitize_workflow_name(f"{struct_stem}-{props_str}")
        print(f"Auto-generated workflow name: '{workflow_name}'")

    # -------------------------------------------------------------------------
    # Build global.json (includes ticket conversion)
    # -------------------------------------------------------------------------
    print("Converting access_key to dflow ticket...")
    global_config = build_global_json(
        backend=args.backend,
        potential=args.potential,
        access_key=args.access_key,
        project_id=args.project_id,
        scass_type=args.scass_type,
        run_command=args.run_command,
    )
    print(f"Ticket obtained: {global_config['bohrium_config']['ticket'][:8]}...")

    # -------------------------------------------------------------------------
    # Build interaction
    # -------------------------------------------------------------------------
    interaction = build_interaction(
        backend=args.backend,
        potential=args.potential,
        model=args.model,
        incar=args.incar,
        potcar_prefix=args.potcar_prefix,
        potcars=potcars,
        orb_files=orb_files,
    )

    # -------------------------------------------------------------------------
    # Build param.json
    # -------------------------------------------------------------------------
    struct_dir = "confs/input"
    param_config = build_param_json(
        struct_dir, interaction, args.properties, args.flow_type
    )

    # -------------------------------------------------------------------------
    # Create output directory and copy files
    # -------------------------------------------------------------------------
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "confs" / "input").mkdir(parents=True, exist_ok=True)

    # Copy structure file
    struct_dest = output_dir / "confs" / "input" / "POSCAR"
    if os.path.exists(args.structure):
        shutil.copy2(args.structure, struct_dest)
        print(f"Copied structure to {struct_dest}")

    # Copy model file if specified
    if args.model and os.path.exists(args.model):
        model_dest = output_dir / os.path.basename(args.model)
        shutil.copy2(args.model, model_dest)
        print(f"Copied model to {model_dest}")

    # -------------------------------------------------------------------------
    # Write configs
    # -------------------------------------------------------------------------
    global_path = output_dir / "global.json"
    with open(global_path, "w") as f:
        json.dump(global_config, f, indent=4)
    print(f"Written: {global_path}")

    param_path = output_dir / "param.json"
    with open(param_path, "w") as f:
        json.dump(param_config, f, indent=4)
    print(f"Written: {param_path}")

    # Write a submission helper script
    submit_script = output_dir / "submit.sh"
    cmd = f'apex submit param.json -c global.json -f {args.flow_type} -n "{workflow_name}"'
    with open(submit_script, "w") as f:
        f.write("#!/bin/bash\n")
        f.write(f"# APEX submission command (run inside APEX container)\n")
        f.write(f"# Workflow name: {workflow_name}\n")
        f.write(f"# Flow type: {args.flow_type}\n\n")
        f.write(f"{cmd}\n")
    os.chmod(submit_script, 0o755)
    print(f"Written: {submit_script}")

    # -------------------------------------------------------------------------
    # Print summary
    # -------------------------------------------------------------------------
    print(f"\n{'='*60}")
    print(f"APEX Job Configuration Generated")
    print(f"{'='*60}")
    print(f"Backend:        {args.backend}")
    if args.potential:
        print(f"Potential:      {args.potential}")
    print(f"Properties:     {', '.join(args.properties)}")
    print(f"Flow type:      {args.flow_type}")
    print(f"Workflow name:  {workflow_name}")
    print(f"scass_type:     {global_config['scass_type']}")
    print(f"Output dir:     {output_dir}")
    print(f"\nBohrium submit command (for outer job):")
    print(f"  cmd: {cmd}")
    print(f"\nOuter job image: {APEX_IMAGE}")
    print(f"Outer job machine: c1_m2_cpu (recommended lightweight client)")


if __name__ == "__main__":
    main()
