import json
from pathlib import Path

from pymatgen.core import Structure
from pymatgen.io.vasp import Poscar

from apex.core.lib.crys import (
    B2,
    L10,
    L12,
    bcc,
    diamond,
    fcc,
    hcp,
    sc,
    tetragonal,
)
from apex.core.lib.rss import generate_rss, resolve_parent_lattice_auto


def _jsonable(value):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if isinstance(key, tuple) and len(key) == 2:
                key = f"{key[0]}-{key[1]}"
            else:
                key = str(key)
            result[key] = _jsonable(item)
        return result
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _build_parent_lattice(config: dict) -> Structure:
    lattice = config.get("type")
    element = config.get("element", "Ni")
    a = float(config.get("a", 3.6))
    c = config.get("c")
    if c is not None:
        c = float(c)
    builder_map = {
        "fcc": fcc,
        "bcc": bcc,
        "sc": sc,
        "hcp": hcp,
        "tetragonal": tetragonal,
        "diamond": diamond,
        "B2": B2,
        "L12": L12,
        "L10": L10,
    }
    if lattice not in builder_map:
        raise ValueError(f"Unsupported parent_lattice type: {lattice}")

    builder = builder_map[lattice]
    if lattice in {"B2", "L12", "L10"}:
        kwargs = {"a": a}
        if lattice == "L10" and c is not None:
            kwargs["c"] = c
        if "species" in config:
            kwargs["species"] = config["species"]
        parent = builder(**kwargs)
    elif lattice in {"hcp", "tetragonal"} and c is not None:
        parent = builder(element, a=a, c=c)
    else:
        parent = builder(element, a=a)
    supercell = config.get("supercell")
    if supercell is not None:
        parent.make_supercell(supercell)
    return parent


def _load_or_build_parent(root: Path, config: dict) -> Structure:
    parent_structure_path = config.get("parent_structure")
    if parent_structure_path:
        candidate = (root / parent_structure_path).resolve()
        if candidate.exists():
            return Structure.from_file(candidate)

    parent_cfg = config.get("parent_lattice")
    if not parent_cfg:
        raise ValueError(
            "rss.json must provide either parent_structure or parent_lattice"
        )
    return _build_parent_lattice(parent_cfg)


def _auto_assign_sublattices(
    compositions: dict,
    base_parent: Structure,
    expanded_parent: Structure,
):
    if not isinstance(compositions, dict) or not compositions:
        raise ValueError("compositions must be a non-empty dict")
    if set(compositions.keys()) == {"all"}:
        return None

    labels = expanded_parent.site_properties.get("sublattice")
    if labels is not None and set(compositions.keys()) == set(labels):
        sublattices = []
        for name in compositions.keys():
            site_indices = [
                idx for idx, label in enumerate(labels) if str(label) == str(name)
            ]
            sublattices.append({"name": name, "site_indices": site_indices})
        return sublattices

    n_base = len(base_parent)
    n_expanded = len(expanded_parent)
    if n_base <= 0 or n_expanded % n_base != 0:
        raise ValueError(
            "Cannot auto-assign sublattices from supercell: invalid parent size"
        )
    if len(compositions) != n_base:
        raise ValueError(
            "Auto-assignment requires number of composition groups to match "
            f"base parent sites ({n_base}); got {len(compositions)}. "
            "Please provide explicit sublattices.site_indices."
        )

    replica = n_expanded // n_base
    names = list(compositions.keys())
    sublattices = []
    for i, name in enumerate(names):
        start = i * replica
        end = (i + 1) * replica
        sublattices.append(
            {
                "name": name,
                "site_indices": list(range(start, end)),
            }
        )
    return sublattices


def _write_poscar_with_order(structure: Structure, target: Path) -> None:
    # Keep POSCAR element blocks stable and grouped by species label.
    sorted_structure = structure.get_sorted_structure(
        key=lambda site: str(site.specie)
    )
    Poscar(sorted_structure).write_file(str(target))


def _resolve_output_root(output_structure: str, root: Path) -> Path:
    output_path = (root / output_structure).resolve()
    if output_path.suffix or output_path.name.upper().startswith("POSCAR"):
        return output_path.parent
    return output_path


def _write_outputs(output_root: Path, decorated, metadata: dict) -> None:
    output_root.mkdir(parents=True, exist_ok=True)

    output_files = []
    if isinstance(decorated, list):
        for i, structure in enumerate(decorated):
            conf_dir = output_root / f"conf_{i + 1:03d}"
            conf_dir.mkdir(parents=True, exist_ok=True)
            target = conf_dir / "POSCAR"
            _write_poscar_with_order(structure, target)
            output_files.append(str(target))
            print(f"Wrote {target}")
    else:
        conf_dir = output_root / "conf_001"
        conf_dir.mkdir(parents=True, exist_ok=True)
        target = conf_dir / "POSCAR"
        _write_poscar_with_order(decorated, target)
        output_files.append(str(target))
        print(f"Wrote {target}")

    if metadata is not None:
        metadata["output_structures"] = output_files
        metadata_path = output_root / "rss_metadata.json"
        metadata_path.write_text(json.dumps(_jsonable(metadata), indent=4, sort_keys=True))
        print(f"Wrote {metadata_path}")


def run_rss_config(config_file: str) -> None:
    config_path = Path(config_file).expanduser().resolve()
    if not config_path.exists():
        raise FileNotFoundError(f"rss config file not found: {config_path}")

    root = config_path.parent
    config = json.loads(config_path.read_text())
    composition_tol = config.get("composition_tolerance", 0.005)
    shape_mode = config.get("supercell_shape", "near_cubic")
    maximum_num_atoms = config.get(
        "maximum_num_atoms",
        config.get("maximum_nums_atoms", config.get("maxmium_nums_atoms")),
    )
    parent_lattice = config.get("parent_lattice")
    if parent_lattice is not None:
        resolve_parent_lattice_auto(
            parent_lattice,
            config["compositions"],
            composition_tolerance=composition_tol,
            shape_mode=shape_mode,
            maximum_num_atoms=maximum_num_atoms,
        )

    parent = _load_or_build_parent(root, config)
    base_parent = parent.copy()

    supercell = config.get("supercell")
    if supercell is not None:
        parent.make_supercell(supercell)

    sublattices = config.get("sublattices")
    if sublattices is None and (
        supercell is not None or "sublattice" in parent.site_properties
    ):
        sublattices = _auto_assign_sublattices(
            compositions=config["compositions"],
            base_parent=base_parent,
            expanded_parent=parent,
        )
        if sublattices is not None:
            print("Auto-assigned sublattices from supercell and composition order")
    output_root = _resolve_output_root(config.get("output_structure", "RSS"), root)

    write_metadata = bool(config.get("metadata", True))

    rss_kwargs = {
        "compositions": config["compositions"],
        "sublattices": sublattices,
        "sro_targets": config.get("sro_targets"),
        "shell_cutoffs": config.get("shell_cutoffs"),
        "shell_weights": config.get("shell_weights"),
        "max_steps": config.get("max_steps", 20000),
        "temperature": config.get("temperature", 0.05),
        "seed": config.get("seed"),
        "tol": config.get("tol", 1e-3),
        "allow_vacancies": config.get("allow_vacancies", False),
        "num_configs": config.get("num_configs", 1),
        "interval": config.get("interval", 100),
        "show_progress": config.get("show_progress", True),
        "patience": config.get("patience"),
        "return_metadata": write_metadata,
    }
    result = generate_rss(parent, **rss_kwargs)
    if write_metadata:
        decorated, metadata = result
    else:
        decorated = result
        metadata = None
    _write_outputs(output_root, decorated, metadata)


def rss_from_args(config_file: str) -> None:
    run_rss_config(config_file)
