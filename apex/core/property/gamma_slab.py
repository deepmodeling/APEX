"""Shared slab-generation safeguards for gamma-line and gamma-surface jobs."""

from __future__ import annotations

import itertools
import math
from numbers import Integral, Real

import numpy as np
from dflow.python import upload_packages
from pymatgen.core.structure import Structure
from pymatgen.core.surface import SlabGenerator
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform

upload_packages.append(__file__)


_LAYER_TOL = 1.0e-10


def _ceil_with_tolerance(value: float) -> int:
    """Ceil a layer ratio without promoting round-off above an integer."""
    return int(math.ceil(float(value) - _LAYER_TOL))


def validate_gamma_slab_settings(
    supercell_size,
    min_slab_height,
    max_atoms,
    min_distance,
):
    """Validate and normalize user-facing gamma slab protection settings."""
    if (
        not isinstance(supercell_size, (list, tuple, np.ndarray))
        or len(supercell_size) != 3
    ):
        raise ValueError("gamma supercell_size must contain three values")

    inplane = []
    for axis, value in zip(("x", "y"), supercell_size[:2]):
        if (
            isinstance(value, (bool, np.bool_))
            or not isinstance(value, Integral)
            or value <= 0
        ):
            raise ValueError(
                f"gamma supercell_size[{axis}] must be a positive integer"
            )
        inplane.append(int(value))

    plane_target = supercell_size[2]
    if (
        isinstance(plane_target, (bool, np.bool_))
        or not isinstance(plane_target, Real)
        or not np.isfinite(plane_target)
        or plane_target <= 0
    ):
        raise ValueError(
            "gamma supercell_size[z] must be a positive finite number of "
            "Miller-plane spacings"
        )

    if min_slab_height is not None:
        if (
            isinstance(min_slab_height, (bool, np.bool_))
            or not isinstance(min_slab_height, Real)
            or not np.isfinite(min_slab_height)
            or min_slab_height <= 0
        ):
            raise ValueError("gamma min_slab_height must be a positive number")
        min_slab_height = float(min_slab_height)

    if max_atoms is not None:
        if (
            isinstance(max_atoms, (bool, np.bool_))
            or not isinstance(max_atoms, Integral)
            or max_atoms <= 0
        ):
            raise ValueError("gamma max_atoms must be a positive integer")
        max_atoms = int(max_atoms)

    if (
        isinstance(min_distance, (bool, np.bool_))
        or not isinstance(min_distance, Real)
        or not np.isfinite(min_distance)
        or min_distance < 0
    ):
        raise ValueError("gamma min_distance must be a non-negative number")

    return (
        (inplane[0], inplane[1], float(plane_target)),
        min_slab_height,
        max_atoms,
        float(min_distance),
    )


def make_gamma_slab_generator(
    structure: Structure,
    plane_miller,
    plane_target: float,
    min_slab_height: float | None,
):
    """Build a Pymatgen slab generator using plane counts, not Angstrom ratios."""

    def new_generator(target):
        return SlabGenerator(
            structure,
            miller_index=plane_miller,
            min_slab_size=target,
            min_vacuum_size=0,
            center_slab=True,
            in_unit_planes=True,
            # A three-dimensional LLL reduction can mix the slab-normal
            # lattice vector into the in-plane basis. Gamma geometry is
            # reduced only after the physical fault frame is known.
            lll_reduce=False,
            reorient_lattice=False,
            primitive=False,
        )

    slab_generator = new_generator(plane_target)
    d_hkl = structure.lattice.d_hkl(plane_miller)
    planes_per_oriented_cell = round(
        slab_generator._proj_height / d_hkl, 8
    )
    if planes_per_oriented_cell <= 0:
        raise RuntimeError(
            f"Cannot determine a positive layer count for Miller plane "
            f"{tuple(plane_miller)}"
        )

    repeats_for_planes = _ceil_with_tolerance(
        plane_target / planes_per_oriented_cell
    )
    repeats_for_height = 1
    if min_slab_height is not None:
        repeats_for_height = _ceil_with_tolerance(
            min_slab_height / slab_generator._proj_height
        )
    oriented_cell_repeats = max(
        1, repeats_for_planes, repeats_for_height
    )

    effective_plane_target = (
        oriented_cell_repeats * planes_per_oriented_cell
    )
    if not np.isclose(
        effective_plane_target,
        plane_target,
        atol=_LAYER_TOL,
        rtol=0.0,
    ):
        slab_generator = new_generator(effective_plane_target)

    expected_base_atoms = (
        len(slab_generator.oriented_unit_cell) * oriented_cell_repeats
    )
    slab_height = (
        slab_generator._proj_height * oriented_cell_repeats
    )
    metadata = {
        "requested_plane_spacings": float(plane_target),
        "effective_plane_spacings": float(effective_plane_target),
        "planes_per_oriented_cell": float(planes_per_oriented_cell),
        "oriented_cell_repeats": int(oriented_cell_repeats),
        "slab_height": float(slab_height),
        "expected_base_atoms": int(expected_base_atoms),
    }
    return slab_generator, metadata


def get_first_gamma_slab(
    slab_generator: SlabGenerator,
    ftol: float = 0.001,
):
    """Return the same first termination without building every SQS slab.

    APEX consumes only the first Pymatgen termination. Calling ``get_slabs``
    nevertheless materializes and structure-matches all terminations, which
    can exhaust memory for large disordered parent cells.
    """
    frac_coords = slab_generator.oriented_unit_cell.frac_coords
    n_atoms = len(frac_coords)
    if n_atoms == 1:
        termination = frac_coords[0][2] + 0.5
        shift = termination - math.floor(termination)
        return slab_generator.get_slab(shift=shift)

    distances = np.zeros((n_atoms, n_atoms))
    for ii, jj in itertools.combinations(range(n_atoms), 2):
        z_distance = frac_coords[ii][2] - frac_coords[jj][2]
        z_distance = (
            abs(z_distance - round(z_distance))
            * slab_generator._proj_height
        )
        distances[ii, jj] = z_distance
        distances[jj, ii] = z_distance

    clusters = fcluster(
        linkage(squareform(distances)), ftol, criterion="distance"
    )
    cluster_locations = {
        cluster: frac_coords[index][2]
        for index, cluster in enumerate(clusters)
    }
    locations = [
        coordinate - math.floor(coordinate)
        for coordinate in sorted(cluster_locations.values())
    ]
    terminations = []
    for index, location in enumerate(locations):
        if index == len(locations) - 1:
            termination = (locations[0] + 1 + location) * 0.5
        else:
            termination = (location + locations[index + 1]) * 0.5
        terminations.append(termination - math.floor(termination))

    return slab_generator.get_slab(shift=sorted(terminations)[0])


def minimum_pair_distance(structure: Structure) -> float:
    """Return the shortest periodic pair distance in a structure."""
    if len(structure) < 2:
        return float("inf")
    distances = structure.distance_matrix
    upper = np.triu_indices(len(structure), k=1)
    return float(np.min(distances[upper]))


def validate_generated_gamma_slab(
    slab: Structure,
    metadata: dict,
    inplane_size,
    max_atoms: int | None,
    min_distance: float,
    property_name: str,
):
    """Stop on unexpected layer promotion, excessive size, or atom overlap."""
    expected_atoms = (
        metadata["expected_base_atoms"]
        * int(inplane_size[0])
        * int(inplane_size[1])
    )
    actual_atoms = len(slab)
    if actual_atoms != expected_atoms:
        raise RuntimeError(
            f"{property_name} generated {actual_atoms} atoms, but "
            f"{expected_atoms} were expected from "
            f"{metadata['oriented_cell_repeats']} oriented-cell repeat(s). "
            "This may indicate an unintended slab-layer promotion."
        )
    if max_atoms is not None and actual_atoms > max_atoms:
        raise RuntimeError(
            f"{property_name} generated {actual_atoms} atoms, exceeding "
            f"max_atoms={max_atoms}. Increase max_atoms only after checking "
            "the slab thickness and Miller indices."
        )

    pair_distance = minimum_pair_distance(slab)
    if pair_distance < min_distance:
        overlap_message = (
            "Generated Gamma surface contains overlapping atoms."
            if property_name.startswith("GammaSurface")
            else "Generated Gamma line contains overlapping atoms."
        )
        raise RuntimeError(
            f"{overlap_message} {property_name} minimum pair "
            f"distance {pair_distance:.6f} A is below "
            f"min_distance={min_distance:.6f} A."
        )

    result = dict(metadata)
    result.update(
        {
            "atom_count": int(actual_atoms),
            "minimum_pair_distance": float(pair_distance),
        }
    )
    return result
