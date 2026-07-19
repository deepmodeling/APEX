#!/usr/bin/env python3
"""
APEX Configuration Generator

Generates global.json and param.json for APEX workflows.
Handles access_key → ticket conversion and RFC 1123 name compliance.

Environment variables:
    BOHRIUM_ACCESS_KEY  — Bohrium access key (converted to ticket via API)
    BOHRIUM_PROJECT_ID  — Bohrium project ID (required unless --project-id is set)

Usage:
    python generate_config.py create \
        --structure POSCAR \
        --backend lammps \
        --potential eam_alloy \
        --model Cu01.eam.alloy \
        --properties elastic \
        --flow-type joint \
        --workflow-name "cu-fcc-elastic" \
        --output-dir ./job

    # Multiple structures (repeat --structure and/or pass --structure-dir)
    python generate_config.py create \
        --structure pristine.vasp Ti_hcp.vasp V_bcc.vasp \
        --structure-dir ./defects/ \
        --backend lammps --potential deepmd --model DPA.pth \
        --properties elastic --flow-type relax \
        --output-dir ./job

    python generate_config.py refresh-global --global ./job/global.json
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
APEX_IMAGE = "registry.dp.tech/dptech/dp/native/prod-397637/apex-flow:1.3.0.post"
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
        "MESH": [20, 20, 20],
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


def validate_project_id_types(config: dict) -> int:
    """Hard-check every supported Bohrium project ID before submission."""
    program_id = config.get("program_id")
    if type(program_id) is not int or program_id <= 0:
        raise ValueError(
            "global.json program_id must be a positive, unquoted JSON integer"
        )

    bohrium_config = config.get("bohrium_config")
    if not isinstance(bohrium_config, dict):
        raise ValueError("global.json must contain a bohrium_config object")
    project_id = bohrium_config.get("project_id")
    if type(project_id) is not int or project_id <= 0:
        raise ValueError(
            "global.json bohrium_config.project_id must be a positive, "
            "unquoted JSON integer"
        )
    if project_id != program_id:
        raise ValueError(
            "global.json program_id and bohrium_config.project_id must match"
        )

    machine = config.get("machine")
    if isinstance(machine, dict):
        remote_profile = machine.get("remote_profile")
        if isinstance(remote_profile, dict) and "program_id" in remote_profile:
            nested_id = remote_profile["program_id"]
            if type(nested_id) is not int or nested_id <= 0:
                raise ValueError(
                    "global.json machine.remote_profile.program_id must be a "
                    "positive, unquoted JSON integer"
                )
            if nested_id != program_id:
                raise ValueError(
                    "global.json machine.remote_profile.program_id must match "
                    "program_id"
                )
    return program_id


def refresh_global_json(global_path, access_key: str = None,
                        project_id: int = None) -> dict:
    """Refresh ticket and integer project IDs without touching param.json."""
    path = Path(global_path)
    if not path.is_file():
        raise FileNotFoundError(f"global.json not found: {path}")

    with open(path, encoding="utf-8") as f:
        config = json.load(f)
    if not isinstance(config, dict):
        raise ValueError("global.json must contain a JSON object")

    pid = resolve_project_id(project_id)
    key = access_key or os.environ.get("BOHRIUM_ACCESS_KEY")
    if not key:
        raise RuntimeError(
            "BOHRIUM_ACCESS_KEY environment variable not set and --access-key "
            "not provided. Cannot refresh dflow authentication."
        )
    ticket = get_bohrium_ticket(key)

    config["program_id"] = pid
    bohrium_config = config.setdefault("bohrium_config", {})
    if not isinstance(bohrium_config, dict):
        raise ValueError("global.json bohrium_config must be a JSON object")
    bohrium_config["project_id"] = pid
    bohrium_config["ticket"] = ticket

    machine = config.get("machine")
    if isinstance(machine, dict):
        remote_profile = machine.get("remote_profile")
        if isinstance(remote_profile, dict) and "program_id" in remote_profile:
            remote_profile["program_id"] = pid

    validate_project_id_types(config)

    temp_path = path.with_name(f".{path.name}.tmp")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4)
            f.write("\n")
        os.chmod(temp_path, path.stat().st_mode)
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()
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


def resolve_source_potcar_file(prefix: Path, entry: str) -> Path:
    """Locate a readable POTCAR under prefix for a potcars entry."""
    direct = (prefix / entry).expanduser()
    if direct.is_file():
        return direct.resolve()
    nested = (prefix / entry / "POTCAR").expanduser()
    if nested.is_file():
        return nested.resolve()
    raise FileNotFoundError(
        f"POTCAR not found for entry '{entry}' under '{prefix}' "
        f"(tried '{direct}' and '{nested}')"
    )


def stage_vasp_potcars(
    output_dir: Path, source_prefix: str, potcars: dict
) -> tuple:
    """Copy required POTCAR files into the job root and return upload-safe paths.

    Bohrium/dflow containers do not see host absolute libraries such as
    ``/share/PAW_PBE``. Stage flat files into the job directory and set
    ``potcar_prefix`` to ``"."`` so ``pack_upload_dir`` uploads them as
    ordinary job-root files (nested dirs under ``"."`` are not included).
    """
    if not source_prefix:
        raise ValueError(
            "VASP requires --potcar-prefix pointing to a readable POTCAR library"
        )
    if not potcars:
        raise ValueError(
            "VASP requires --potcars mapping, e.g. "
            "'Ti:Ti_pv/POTCAR,V:V_sv/POTCAR'"
        )

    src_root = Path(source_prefix).expanduser()
    if not src_root.exists():
        raise FileNotFoundError(f"potcar_prefix not accessible: {src_root}")
    if not os.access(src_root, os.R_OK):
        raise PermissionError(f"potcar_prefix not readable: {src_root}")

    staged = {}
    for element, entry in potcars.items():
        src_file = resolve_source_potcar_file(src_root, entry)
        # Flat job-root name so Bohrium upload packs the file (not a subdir).
        flat_name = f"POTCAR_{element}"
        dest_file = output_dir / flat_name
        shutil.copy2(src_file, dest_file)
        staged[element] = flat_name
        print(f"Staged POTCAR {element}: {src_file} → {dest_file}")

    return ".", staged


STRUCTURE_FILENAMES = {"POSCAR", "CONTCAR", "STRU"}
STRUCTURE_SUFFIXES = {".vasp", ".cif", ".xyz"}


def sanitize_conf_name(name: str) -> str:
    """Make a filesystem-safe confs/ directory name."""
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", name.strip())
    cleaned = cleaned.strip("._-")
    return cleaned or "input"


def unique_conf_name(stem: str, used: set) -> str:
    """Return a unique conf name, appending _2, _3, ... on collision."""
    base = sanitize_conf_name(stem)
    name = base
    idx = 2
    while name in used:
        name = f"{base}_{idx}"
        idx += 1
    used.add(name)
    return name


def collect_structures_from_dir(dir_path: Path) -> list:
    """Collect structure files from a directory.

    Includes:
    - direct files named POSCAR/CONTCAR/STRU or with .vasp/.cif/.xyz
    - immediate child directories that contain POSCAR/CONTCAR/STRU
    """
    root = Path(dir_path)
    if not root.is_dir():
        raise ValueError(f"--structure-dir is not a directory: {dir_path}")

    found = []
    for entry in sorted(root.iterdir(), key=lambda p: p.name):
        if entry.is_file():
            if entry.name in STRUCTURE_FILENAMES or entry.suffix.lower() in STRUCTURE_SUFFIXES:
                found.append(entry.resolve())
        elif entry.is_dir():
            for candidate in ("POSCAR", "CONTCAR", "STRU"):
                nested = entry / candidate
                if nested.is_file():
                    found.append(nested.resolve())
                    break
    return found


def resolve_structure_sources(structure_args, structure_dir_args) -> list:
    """Resolve --structure / --structure-dir into an ordered list of source files."""
    sources = []
    seen = set()

    for path_str in structure_args or []:
        path = Path(path_str)
        if not path.is_file():
            raise ValueError(f"--structure file not found: {path_str}")
        resolved = path.resolve()
        if resolved not in seen:
            sources.append(resolved)
            seen.add(resolved)

    for dir_str in structure_dir_args or []:
        for path in collect_structures_from_dir(Path(dir_str)):
            if path not in seen:
                sources.append(path)
                seen.add(path)

    if not sources:
        raise ValueError(
            "No structure files found. Pass --structure FILE [FILE ...] "
            "and/or --structure-dir DIR [DIR ...]."
        )
    return sources


def plan_structure_layout(sources: list) -> list:
    """Map source files to job-relative conf dirs and param.json patterns.

    Single structure → confs/input (backward compatible).
    Multiple → confs/<stem>/POSCAR with an explicit structures list.
    """
    if len(sources) == 1:
        return [{
            "source": sources[0],
            "conf_name": "input",
            "rel_dir": "confs/input",
            "dest_name": "POSCAR",
        }]

    used = set()
    layout = []
    for source in sources:
        stem = source.stem
        if source.name in STRUCTURE_FILENAMES:
            # Prefer parent directory name when the file itself is POSCAR/etc.
            parent = source.parent.name
            stem = parent if parent not in (".", "") else "input"
        conf_name = unique_conf_name(stem, used)
        layout.append({
            "source": source,
            "conf_name": conf_name,
            "rel_dir": f"confs/{conf_name}",
            "dest_name": "POSCAR",
        })
    return layout


def build_param_json(structure_paths, interaction: dict,
                     properties: list, flow_type: str = "joint",
                     relaxation_settings: dict = None) -> dict:
    """Build param.json configuration.

    structure_paths: one path string or a list of path strings for `structures`.
    """
    if isinstance(structure_paths, str):
        structures = [structure_paths]
    else:
        structures = list(structure_paths)
    param = {
        "structures": structures,
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
        description="Create APEX configs or refresh an existing global.json."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser(
        "create", help="Generate a complete APEX job directory"
    )
    create.add_argument(
        "--structure", "-s", nargs="+", action="extend", default=[],
        help="Structure file(s) (POSCAR/CIF/VASP). Repeatable / space-separated.",
    )
    create.add_argument(
        "--structure-dir", nargs="+", action="extend", default=[],
        help=(
            "Directory(ies) of structures. Collects POSCAR/CONTCAR/STRU and "
            "*.vasp/*.cif/*.xyz, plus immediate child dirs that contain those files."
        ),
    )
    create.add_argument("--backend", "-b", required=True,
                        choices=["lammps", "abacus", "vasp"],
                        help="Calculator backend")
    create.add_argument("--potential", "-p",
                        help="LAMMPS potential type (deepmd/mace/nep/eam_alloy/...)")
    create.add_argument("--model", "-m",
                        help="Model/potential file path")
    create.add_argument("--properties", nargs="+", required=True,
                        help="Property types to calculate")
    create.add_argument("--flow-type", default="joint",
                        choices=["joint", "relax", "props"],
                        help="Workflow type (default: joint)")
    create.add_argument("--workflow-name", "-n",
                        help="Workflow name (auto-lowercased for RFC 1123 compliance)")
    create.add_argument("--output-dir", "-o", default="./apex_job",
                        help="Output directory for configs")
    create.add_argument(
        "--access-key",
        help="Bohrium access key (or set BOHRIUM_ACCESS_KEY env var)",
    )
    create.add_argument(
        "--project-id", type=int,
        help="Bohrium project ID (or set BOHRIUM_PROJECT_ID)",
    )
    create.add_argument("--scass-type",
                        help="Override scass_type for inner dflow containers")
    create.add_argument("--run-command",
                        help="Override calculator run command")
    create.add_argument("--incar",
                        help="INCAR/INPUT file path (for VASP/ABACUS)")
    create.add_argument("--potcar-prefix",
                        help="POTCAR prefix directory (for VASP/ABACUS)")
    create.add_argument(
        "--potcars",
        help="POTCAR mapping as 'Element1:potcar1,Element2:potcar2'",
    )
    create.add_argument(
        "--orb-files",
        help="Orbital files as 'Element1:file1,Element2:file2' (ABACUS)",
    )

    refresh = subparsers.add_parser(
        "refresh-global",
        help="Refresh ticket and project IDs without changing param.json",
    )
    refresh.add_argument(
        "--global", "-g", dest="global_json", required=True,
        help="Existing global.json to update atomically",
    )
    refresh.add_argument(
        "--access-key",
        help="Bohrium access key (or set BOHRIUM_ACCESS_KEY env var)",
    )
    refresh.add_argument(
        "--project-id", type=int,
        help="Bohrium project ID (or set BOHRIUM_PROJECT_ID)",
    )

    args = parser.parse_args()

    if args.command == "refresh-global":
        try:
            config = refresh_global_json(
                args.global_json,
                access_key=args.access_key,
                project_id=args.project_id,
            )
        except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(1)
        pid = validate_project_id_types(config)
        print(f"Refreshed: {Path(args.global_json).resolve()}")
        print("Hard type check passed:")
        print(f"  program_id={pid!r} type={type(pid).__name__}")
        project_id = config["bohrium_config"]["project_id"]
        print(
            "  bohrium_config.project_id="
            f"{project_id!r} type={type(project_id).__name__}"
        )
        return

    # -------------------------------------------------------------------------
    # Parse complex arguments
    # -------------------------------------------------------------------------
    potcars = parse_str_map(args.potcars)
    orb_files = parse_str_map(args.orb_files)

    # -------------------------------------------------------------------------
    # Resolve structures (--structure and/or --structure-dir)
    # -------------------------------------------------------------------------
    try:
        structure_sources = resolve_structure_sources(
            args.structure, args.structure_dir
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    structure_layout = plan_structure_layout(structure_sources)
    structure_paths = [item["rel_dir"] for item in structure_layout]
    print(
        f"Resolved {len(structure_layout)} structure(s): "
        + ", ".join(structure_paths)
    )

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
        # Auto-generate from first structure basename and properties
        first_stem = structure_layout[0]["conf_name"].lower()
        if len(structure_layout) > 1:
            first_stem = f"{first_stem}-n{len(structure_layout)}"
        props_str = "-".join(args.properties[:3])  # First 3 properties
        workflow_name = sanitize_workflow_name(f"{first_stem}-{props_str}")
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
    validate_project_id_types(global_config)
    print(f"Ticket obtained: {global_config['bohrium_config']['ticket'][:8]}...")

    # -------------------------------------------------------------------------
    # Create output directory and copy files
    # -------------------------------------------------------------------------
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    for item in structure_layout:
        dest_dir = output_dir / item["rel_dir"]
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_file = dest_dir / item["dest_name"]
        shutil.copy2(item["source"], dest_file)
        print(f"Copied structure to {dest_file}")

    # Copy model file if specified
    if args.model and os.path.exists(args.model):
        model_dest = output_dir / os.path.basename(args.model)
        shutil.copy2(args.model, model_dest)
        print(f"Copied model to {model_dest}")

    # Stage VASP POTCAR (+ INCAR) into the job. Absolute host libraries such as
    # /share/PAW_PBE are invisible inside Bohrium/dflow containers.
    staged_prefix = args.potcar_prefix
    staged_potcars = potcars
    staged_incar = args.incar
    if args.backend == "vasp":
        try:
            staged_prefix, staged_potcars = stage_vasp_potcars(
                output_dir, args.potcar_prefix, potcars
            )
        except (OSError, ValueError, FileNotFoundError, PermissionError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            print(
                "POTCAR must be readable locally and will be copied into the "
                "job root as POTCAR_<Element> with potcar_prefix='.'. "
                "Confirm --potcar-prefix and --potcars.",
                file=sys.stderr,
            )
            sys.exit(1)
        if args.incar and os.path.isfile(args.incar):
            incar_dest = output_dir / "INCAR"
            shutil.copy2(args.incar, incar_dest)
            staged_incar = "INCAR"
            print(f"Copied INCAR to {incar_dest}")
        else:
            staged_incar = "INCAR"
            print(
                "WARNING: no --incar file copied; place INCAR in the job dir "
                "before submit",
                file=sys.stderr,
            )

    # -------------------------------------------------------------------------
    # Build interaction + param.json (after staging so paths are job-relative)
    # -------------------------------------------------------------------------
    interaction = build_interaction(
        backend=args.backend,
        potential=args.potential,
        model=args.model,
        incar=staged_incar,
        potcar_prefix=staged_prefix,
        potcars=staged_potcars,
        orb_files=orb_files,
    )
    param_config = build_param_json(
        structure_paths, interaction, args.properties, args.flow_type
    )

    # -------------------------------------------------------------------------
    # Write configs
    # -------------------------------------------------------------------------
    global_path = output_dir / "global.json"
    with open(global_path, "w") as f:
        json.dump(global_config, f, indent=4)
    print(f"Written: {global_path}")
    pid = validate_project_id_types(global_config)
    print(
        "Hard type check passed: "
        f"program_id={pid!r} type={type(pid).__name__}; "
        "bohrium_config.project_id="
        f"{global_config['bohrium_config']['project_id']!r} "
        f"type={type(global_config['bohrium_config']['project_id']).__name__}"
    )

    param_path = output_dir / "param.json"
    with open(param_path, "w") as f:
        json.dump(param_config, f, indent=4)
    print(f"Written: {param_path}")
    if args.backend == "vasp":
        print(
            "VASP potcar_prefix rewritten to job-relative "
            f"'{staged_prefix}' (do not keep absolute /share/... paths)."
        )

    # Write outer-job run.sh (force-upgrade APEX; bare pip install keeps the
    # image-bundled older version and uploads that stale code to inner steps).
    run_script = output_dir / "run.sh"
    cmd = f'apex submit param.json -c global.json -f {args.flow_type} -n "{workflow_name}"'
    with open(run_script, "w") as f:
        f.write("#!/bin/bash\n")
        f.write("set -eo pipefail\n\n")
        f.write("# Upgrade to latest apex-flow.\n")
        f.write(
            "python3 -m pip install --upgrade --no-cache-dir apex-flow "
            "2>&1 | tail -5\n"
        )
        f.write(
            'python3 -c "import apex; '
            'print(f\'APEX version: {apex.__version__}\')"\n\n'
        )
        f.write(
            "# Authentication is already stored in global.json by "
            "generate_config.py.\n"
        )
        f.write("set +eo pipefail\n")
        f.write(f"{cmd} 2>&1 | tee apex_submit.log\n")
        f.write("EXIT_CODE=${PIPESTATUS[0]}\n")
        f.write("set -eo pipefail\n\n")
        f.write("if [ $EXIT_CODE -eq 0 ]; then\n")
        f.write(
            '    echo "=== APEX workflow completed and results retrieved ==="\n'
        )
        f.write('    echo "Retain apex_submit.log and the workflow ID."\n')
        f.write("    exit 0\n")
        f.write("else\n")
        f.write('    echo "APEX failed (exit $EXIT_CODE)"\n')
        f.write("    tail -50 apex_submit.log 2>/dev/null || true\n")
        f.write("    exit 1\n")
        f.write("fi\n")
    os.chmod(run_script, 0o755)
    print(f"Written: {run_script}")

    # Keep a minimal submit.sh for local/debug use inside an already-upgraded env.
    submit_script = output_dir / "submit.sh"
    with open(submit_script, "w") as f:
        f.write("#!/bin/bash\n")
        f.write("# APEX submission command (run inside APEX container)\n")
        f.write(f"# Workflow name: {workflow_name}\n")
        f.write(f"# Flow type: {args.flow_type}\n")
        f.write(
            "# Prefer run.sh for Bohrium outer jobs (it upgrades apex-flow).\n\n"
        )
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
    print(f"Structures:     {len(structure_paths)} ({', '.join(structure_paths)})")
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
