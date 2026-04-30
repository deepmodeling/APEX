import math

import numpy as np
from apex.core.constants import (
    DEFAULT_HCP_C_OVER_A,
    DEFAULT_L10_C_OVER_A,
    get_metallic_radius,
)
from pymatgen.core.lattice import Lattice
from pymatgen.core.structure import Structure
from dflow.python import upload_packages
upload_packages.append(__file__)


PROTOTYPES = {
    "bcc": {
        "family": "cubic",
        "coords": [[0, 0, 0], [0.5, 0.5, 0.5]],
        "sublattice": ["all", "all"],
        "default_species": ["ele", "ele"],
    },
    "B2": {
        "family": "cubic",
        "coords": [[0, 0, 0], [0.5, 0.5, 0.5]],
        "sublattice": ["corner", "body"],
        "default_species": ["A", "B"],
    },
    "fcc": {
        "family": "cubic",
        "coords": [[0, 0, 0], [0, 0.5, 0.5], [0.5, 0, 0.5], [0.5, 0.5, 0]],
        "sublattice": ["all", "all", "all", "all"],
        "default_species": ["ele", "ele", "ele", "ele"],
    },
    "L12": {
        "family": "cubic",
        "coords": [[0, 0, 0], [0, 0.5, 0.5], [0.5, 0, 0.5], [0.5, 0.5, 0]],
        "sublattice": ["corner", "face", "face", "face"],
        "default_species": ["A", "B", "B", "B"],
    },
    "hcp": {
        "family": "hcp",
        "coords": [[0, 0, 0], [1.0 / 3.0, 1.0 / 3.0, 0.5]],
        "sublattice": ["all", "all"],
        "default_species": ["ele", "ele"],
    },
    "tetragonal": {
        "family": "tetragonal",
        "coords": [[0, 0, 0]],
        "sublattice": ["all"],
        "default_species": ["ele"],
    },
    "L10": {
        "family": "tetragonal",
        "coords": [[0, 0, 0], [0.5, 0.5, 0.5]],
        "sublattice": ["layer_A", "layer_B"],
        "default_species": ["A", "B"],
    },
}


def _make_lattice(family, a, c=None):
    if family == "cubic":
        return Lattice.cubic(a)
    if family == "tetragonal":
        if c is None:
            raise ValueError("c must be provided for tetragonal lattices")
        return Lattice.tetragonal(a, c)
    if family == "hcp":
        if c is None:
            raise ValueError("c must be provided for hcp lattices")
        return Lattice.hexagonal(a, c)
    raise ValueError(f"Unknown lattice family: {family}")


def _make_structure_from_prototype(
    prototype,
    a,
    c=None,
    species=None,
    supercell=None,
):
    if prototype not in PROTOTYPES:
        raise ValueError(f"Unknown crystal prototype: {prototype}")

    proto = PROTOTYPES[prototype]
    coords = proto["coords"]
    sublattice = proto["sublattice"]
    if species is None:
        species = proto["default_species"]
    species = list(species)
    if len(species) != len(coords):
        raise ValueError(
            f"Prototype '{prototype}' requires {len(coords)} species, "
            f"got {len(species)}"
        )

    lattice = _make_lattice(proto["family"], a, c=c)
    site_properties = {
        "sublattice": list(sublattice),
        "prototype": [prototype] * len(coords),
        "basis_index": list(range(len(coords))),
    }
    structure = Structure(
        lattice,
        species,
        coords,
        site_properties=site_properties,
    )
    if supercell is not None:
        structure.make_supercell(supercell)
    return structure


def _resolve_ele_a(ele_name, a):
    if isinstance(ele_name, (int, float, np.integer, np.floating)):
        return "ele", float(ele_name)
    return ele_name, a


def _resolve_ele_a_c(ele_name, a, c, default_a, default_c):
    if isinstance(ele_name, (int, float, np.integer, np.floating)):
        if a != default_a and c == default_c:
            return "ele", float(ele_name), a
        return "ele", float(ele_name), c
    return ele_name, a, c


def _make_legacy_fcc(ele_name, a, supercell=None):
    box = np.array([[0.0, 0.5, 0.5], [0.5, 0.0, 0.5], [0.5, 0.5, 0.0]])
    box *= a
    structure = Structure(
        box,
        [ele_name],
        [[0, 0, 0]],
        site_properties={
            "sublattice": ["all"],
            "prototype": ["fcc"],
            "basis_index": [0],
        },
    )
    if supercell is not None:
        structure.make_supercell(supercell)
    return structure


def fcc(ele_name=None, a=3.6, supercell=None):
    """Return a conventional fcc Structure with single-sublattice labels."""
    if isinstance(ele_name, str):
        return _make_legacy_fcc(ele_name, a, supercell=supercell)
    if ele_name is None:
        ele_name = "ele"
    else:
        ele_name, a = _resolve_ele_a(ele_name, a)
    return _make_structure_from_prototype(
        "fcc",
        a,
        species=[ele_name] * len(PROTOTYPES["fcc"]["coords"]),
        supercell=supercell,
    )


def fcc1(ele_name="ele", a=4.05):
    """Return a conventional fcc Structure."""
    return _make_structure_from_prototype(
        "fcc",
        a,
        species=[ele_name] * len(PROTOTYPES["fcc"]["coords"]),
    )


def sc(ele_name="ele", a=2.551340126037118):
    latt = Lattice.cubic(a)
    return Structure(latt, [ele_name], [[0, 0, 0]])


def bcc(ele_name="ele", a=3.0, supercell=None):
    """Return a bcc Structure with single-sublattice labels."""
    ele_name, a = _resolve_ele_a(ele_name, a)
    return _make_structure_from_prototype(
        "bcc",
        a,
        species=[ele_name] * len(PROTOTYPES["bcc"]["coords"]),
        supercell=supercell,
    )


def hcp(
    ele_name="ele",
    a=3.0,
    c=4.9,
    supercell=None,
):
    """Return an hcp Structure with single-sublattice labels."""
    ele_name, a, c = _resolve_ele_a_c(ele_name, a, c, 3.0, 4.9)
    return _make_structure_from_prototype(
        "hcp",
        a,
        c=c,
        species=[ele_name] * len(PROTOTYPES["hcp"]["coords"]),
        supercell=supercell,
    )


def tetragonal(ele_name="ele", a=3.0, c=3.0, supercell=None):
    """Return a simple tetragonal Structure with single-sublattice labels."""
    ele_name, a, c = _resolve_ele_a_c(ele_name, a, c, 3.0, 3.0)
    return _make_structure_from_prototype(
        "tetragonal",
        a,
        c=c,
        species=[ele_name] * len(PROTOTYPES["tetragonal"]["coords"]),
        supercell=supercell,
    )


def B2(a=3.0, species=("A", "B"), supercell=None):
    """Return a B2 ordered-alloy Structure with corner/body sublattices."""
    return _make_structure_from_prototype(
        "B2",
        a,
        species=species,
        supercell=supercell,
    )


def L12(a=3.6, species=("A", "B", "B", "B"), supercell=None):
    """Return an L12 ordered-alloy Structure with corner/face sublattices."""
    return _make_structure_from_prototype(
        "L12",
        a,
        species=species,
        supercell=supercell,
    )


def L10(a=3.8, c=3.6, species=("A", "B"), supercell=None):
    """Return an L10 ordered-alloy Structure with alternating layer sublattices."""
    return _make_structure_from_prototype(
        "L10",
        a,
        c=c,
        species=species,
        supercell=supercell,
    )


def dhcp(
    ele_name="ele", a=4.05 / np.sqrt(2), c=4.05 / np.sqrt(2) * 4.0 * np.sqrt(2.0 / 3.0)
):
    box = np.array([[1, 0, 0], [0.5, 0.5 * np.sqrt(3), 0], [0, 0, 1]])
    box[0] *= a
    box[1] *= a
    box[2] *= c
    latt = Lattice(box)
    return Structure(
        latt,
        [ele_name] * 4,
        [
            [0, 0, 0],
            [1.0 / 3.0, 1.0 / 3.0, 1.0 / 4.0],
            [0, 0, 1.0 / 2.0],
            [2.0 / 3.0, 2.0 / 3.0, 3.0 / 4.0]
        ]
    )


def diamond(ele_name="ele", a=2.551340126037118):
    box = np.array([[0.0, 1.0, 1.0], [1.0, 0.0, 1.0], [1.0, 1.0, 0.0]])
    box *= a
    latt = Lattice(box)
    return Structure(
        latt,
        [ele_name] * 2,
        [
            [0.12500000000000, 0.12500000000000, 0.12500000000000],
            [0.87500000000000, 0.87500000000000, 0.87500000000000]
        ]
    )


def _weighted_average_radius(composition):
    total = 0.0
    for element, fraction in composition.items():
        total += float(fraction) * get_metallic_radius(element)
    return total


def estimate_lattice_constant(parent_type, compositions, c_over_a=None):
    """Estimate lattice parameters from sublattice-weighted metallic radii.

    Returns:
        dict: For cubic-like lattices: {"a": value}
              For tetragonal / hcp: {"a": value, "c": value}
    """
    ptype = str(parent_type)

    if ptype in {"bcc", "fcc", "hcp"}:
        if "all" not in compositions:
            raise ValueError(f"{ptype} requires an 'all' composition block")
        r_all = _weighted_average_radius(compositions["all"])
        if ptype == "bcc":
            a_value = 4.0 * r_all / math.sqrt(3.0)
            return {"a": a_value}
        if ptype == "fcc":
            a_value = 2.0 * math.sqrt(2.0) * r_all
            return {"a": a_value}
        a_value = 2.0 * r_all
        ratio = DEFAULT_HCP_C_OVER_A if c_over_a is None else float(c_over_a)
        return {"a": a_value, "c": ratio * a_value}

    if ptype == "B2":
        if "corner" not in compositions or "body" not in compositions:
            raise ValueError("B2 requires 'corner' and 'body' composition blocks")
        r_corner = _weighted_average_radius(compositions["corner"])
        r_body = _weighted_average_radius(compositions["body"])
        a_value = 2.0 * (r_corner + r_body) / math.sqrt(3.0)
        return {"a": a_value}

    if ptype == "L12":
        if "corner" not in compositions or "face" not in compositions:
            raise ValueError("L12 requires 'corner' and 'face' composition blocks")
        r_corner = _weighted_average_radius(compositions["corner"])
        r_face = _weighted_average_radius(compositions["face"])
        a_value = math.sqrt(2.0) * (r_corner + r_face)
        return {"a": a_value}

    if ptype == "L10":
        if "layer_A" not in compositions or "layer_B" not in compositions:
            raise ValueError("L10 requires 'layer_A' and 'layer_B' composition blocks")
        r_a = _weighted_average_radius(compositions["layer_A"])
        r_b = _weighted_average_radius(compositions["layer_B"])
        a_value = math.sqrt(2.0) * (r_a + r_b)
        ratio = DEFAULT_L10_C_OVER_A if c_over_a is None else float(c_over_a)
        return {"a": a_value, "c": ratio * a_value}

    if ptype == "tetragonal":
        if "all" not in compositions:
            raise ValueError("tetragonal requires an 'all' composition block")
        r_all = _weighted_average_radius(compositions["all"])
        a_value = 2.0 * r_all
        ratio = DEFAULT_L10_C_OVER_A if c_over_a is None else float(c_over_a)
        return {"a": a_value, "c": ratio * a_value}

    raise ValueError(
        "Unsupported parent lattice type for automatic lattice estimation: "
        f"{ptype}"
    )


def _composition_rounding_error(total_sites, composition, normalizer=None):
    if normalizer is None:
        normalizer = total_sites
    max_error = 0.0
    for fraction in composition.values():
        target = float(fraction) * total_sites
        error = abs(target - round(target)) / normalizer
        if error > max_error:
            max_error = error
    return max_error


def _iter_near_cubic_triplets(max_edge):
    for nx in range(1, max_edge + 1):
        for ny in range(nx, max_edge + 1):
            for nz in range(ny, max_edge + 1):
                yield nx, ny, nz


def _auto_supercell_atoms_per_cell(parent_type):
    return {
        "bcc": 2,
        "fcc": 1,
        "hcp": 2,
        "tetragonal": 1,
        "B2": 2,
        "L12": 4,
        "L10": 2,
    }[parent_type]


def suggest_supercell(
    parent_type,
    compositions,
    composition_tolerance=0.005,
    shape_mode="near_cubic",
    maximum_num_atoms=None,
    maxmium_nums_atoms=None,
    max_edge=24,
):
    """Suggest a supercell from composition tolerance and shape preference."""
    ptype = str(parent_type)
    if maximum_num_atoms is None and maxmium_nums_atoms is not None:
        maximum_num_atoms = maxmium_nums_atoms
    composition_tolerance = float(composition_tolerance)
    max_edge = int(max_edge)
    if maximum_num_atoms is not None:
        maximum_num_atoms = int(maximum_num_atoms)
        if maximum_num_atoms <= 0:
            raise ValueError("maximum_num_atoms must be positive")
    if composition_tolerance <= 0:
        raise ValueError("composition_tolerance must be positive")
    if max_edge <= 0:
        raise ValueError("max_edge must be positive")
    if shape_mode not in {"near_cubic", "xy_equal_z_free"}:
        raise ValueError(
            "shape_mode must be one of: 'near_cubic', 'xy_equal_z_free'"
        )

    if ptype in {"bcc", "fcc", "hcp", "tetragonal"}:
        if "all" not in compositions:
            raise ValueError(f"{ptype} requires an 'all' composition block")
        blocks = {"all": compositions["all"]}
    elif ptype == "B2":
        if "corner" not in compositions or "body" not in compositions:
            raise ValueError("B2 requires 'corner' and 'body' composition blocks")
        blocks = {
            "corner": compositions["corner"],
            "body": compositions["body"],
        }
    elif ptype == "L12":
        if "corner" not in compositions or "face" not in compositions:
            raise ValueError("L12 requires 'corner' and 'face' composition blocks")
        blocks = {
            "corner": compositions["corner"],
            "face": compositions["face"],
        }
    elif ptype == "L10":
        if "layer_A" not in compositions or "layer_B" not in compositions:
            raise ValueError("L10 requires 'layer_A' and 'layer_B' composition blocks")
        blocks = {
            "layer_A": compositions["layer_A"],
            "layer_B": compositions["layer_B"],
        }
    else:
        raise ValueError(
            "Unsupported parent lattice type for automatic supercell suggestion: "
            f"{ptype}"
        )

    c_min_values = []
    for block_composition in blocks.values():
        positive_fractions = [
            float(fraction)
            for fraction in block_composition.values()
            if float(fraction) > 0
        ]
        if not positive_fractions:
            raise ValueError("composition blocks must contain a positive fraction")
        c_min_values.append(min(positive_fractions))
    n_min = max(math.ceil(1.0 / c_min) for c_min in c_min_values)
    n_target = int(n_min * max(1.0, 0.5 / composition_tolerance))
    atoms_per_cell = _auto_supercell_atoms_per_cell(ptype)

    best_triplet = None
    best_rank = None
    fallback_triplet = None
    fallback_rank = None

    for nx, ny, nz in _iter_near_cubic_triplets(max_edge):
        n_cells = nx * ny * nz
        if (
            maximum_num_atoms is not None
            and n_cells * atoms_per_cell > maximum_num_atoms
        ):
            continue

        per_block_errors = []
        for block_name, block_composition in blocks.items():
            if ptype == "L12" and block_name == "face":
                total_sites = 3 * n_cells
            elif ptype in {"bcc", "hcp"} and block_name == "all":
                total_sites = 2 * n_cells
            else:
                total_sites = n_cells
            per_block_errors.append(
                _composition_rounding_error(
                    total_sites,
                    block_composition,
                    normalizer=n_cells,
                )
            )

        composition_error = max(per_block_errors)
        if shape_mode == "near_cubic":
            shape_penalty = nz - nx
        else:
            shape_penalty = abs(nx - ny)
        rank = (composition_error, shape_penalty, n_cells)

        fallback = rank + (nz, ny, nx)
        if fallback_rank is None or fallback < fallback_rank:
            fallback_rank = fallback
            fallback_triplet = [nx, ny, nz]

        if n_cells < n_target:
            continue

        if best_rank is None or rank < best_rank:
            best_rank = rank
            best_triplet = [nx, ny, nz]

        if composition_error < composition_tolerance and shape_penalty == 0:
            return best_triplet

    if fallback_triplet is None:
        raise ValueError(
            "No automatic supercell candidate fits maximum_num_atoms="
            f"{maximum_num_atoms}"
        )
    return best_triplet if best_triplet is not None else fallback_triplet
