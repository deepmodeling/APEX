"""Shared, parent-aware geometry construction for Gamma properties."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from dflow.python import upload_packages
from pymatgen.core import Structure
from scipy.optimize import linear_sum_assignment

from apex.core.lib.parent_lattice_mapping import ParentSlipGeometry
from apex.core.property.gamma_slab import (
    get_first_gamma_slab,
    make_gamma_slab_generator,
)

upload_packages.append(__file__)


@dataclass(frozen=True)
class GammaSlabGeometry:
    slab: Structure
    upper_indices: np.ndarray
    lower_indices: np.ndarray
    metadata: dict
    generation_metadata: dict


def validate_vacuum_size(value, property_name="Gamma") -> float:
    """Return a finite, non-negative vacuum thickness."""

    if isinstance(value, (bool, np.bool_)):
        raise ValueError(f"{property_name} vacuum_size must be a finite number >= 0")
    try:
        vacuum_size = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{property_name} vacuum_size must be a finite number >= 0"
        ) from exc
    if not np.isfinite(vacuum_size) or vacuum_size < 0.0:
        raise ValueError(f"{property_name} vacuum_size must be a finite number >= 0")
    return vacuum_size


def validate_gamma_cell_geometry(
    structure: Structure,
    *,
    require_orthogonal: bool = False,
    property_name: str = "Gamma",
    tolerance: float = 1.0e-8,
) -> dict:
    """Record the slab metric and optionally require a zero-tilt cell.

    APEX deliberately does not Gram-Schmidt a periodic cell: doing so changes
    its periodic boundaries.  The strict option is therefore a fail-closed
    validation for workflows that require an orthogonal, Cartesian-z slab.
    """

    matrix = np.asarray(structure.lattice.matrix, dtype=float)
    if matrix.shape != (3, 3) or not np.all(np.isfinite(matrix)):
        raise RuntimeError(f"{property_name} slab lattice is not a finite 3x3 matrix")
    lengths = np.linalg.norm(matrix, axis=1)
    if np.any(lengths <= 0.0):
        raise RuntimeError(f"{property_name} slab lattice contains a zero vector")
    cosine_matrix = matrix @ matrix.T / np.outer(lengths, lengths)
    off_diagonal = np.array(
        [cosine_matrix[0, 1], cosine_matrix[0, 2], cosine_matrix[1, 2]],
        dtype=float,
    )
    maximum_cosine = float(np.max(np.abs(off_diagonal)))
    orthogonal = bool(maximum_cosine <= tolerance)
    cartesian_z_normal = bool(
        np.max(np.abs(matrix[:2, 2])) <= tolerance
        and np.max(np.abs(matrix[2, :2])) <= tolerance
        and matrix[2, 2] > 0.0
    )
    metrics = {
        "lattice_matrix_angstrom": matrix.tolist(),
        "lattice_lengths_angstrom": lengths.tolist(),
        "lattice_angles_degree": list(map(float, structure.lattice.angles)),
        "normalized_dot_ab_ac_bc": off_diagonal.tolist(),
        "maximum_abs_normalized_dot": maximum_cosine,
        "orthogonal": orthogonal,
        "cartesian_z_normal": cartesian_z_normal,
        "zero_tilt": orthogonal and cartesian_z_normal,
        "orthogonality_tolerance": float(tolerance),
        "orthogonalization_applied": False,
    }
    if require_orthogonal and not metrics["zero_tilt"]:
        raise RuntimeError(
            f"{property_name} require_orthogonal_cell=true, but the generated "
            "periodic slab is not an orthogonal Cartesian-z zero-tilt cell "
            f"(max normalized dot={maximum_cosine:.3e}). APEX will not "
            "Gram-Schmidt the cell because that changes periodic boundaries; "
            "rebuild or relax an orthogonal parent bulk instead."
        )
    return metrics


def _rotate_structure(structure: Structure, local_frame: np.ndarray) -> Structure:
    lattice = np.array([local_frame @ vector for vector in structure.lattice.matrix])
    rotated = Structure(
        lattice,
        structure.species,
        structure.frac_coords,
        site_properties=structure.site_properties,
    )
    if rotated.lattice.matrix[2, 2] < 0:
        flipped_lattice = rotated.lattice.matrix.copy()
        flipped_lattice[2] *= -1.0
        rotated = Structure(
            flipped_lattice,
            rotated.species,
            rotated.cart_coords,
            coords_are_cartesian=True,
            to_unit_cell=True,
            site_properties=rotated.site_properties,
        )
    return rotated


def _select_fault_gap(
    structure: Structure,
    target_fraction: float,
) -> tuple[np.ndarray, np.ndarray, float, float]:
    """Select a material-internal layer gap and freeze the atom grouping."""

    frac_z = np.mod(structure.frac_coords[:, 2], 1.0)
    order = np.argsort(frac_z)
    values = frac_z[order]
    following = np.r_[values[1:], values[0] + 1.0]
    gaps = following - values
    height = abs(float(structure.lattice.matrix[2, 2]))
    gaps_angstrom = gaps * height
    # Local alloy relaxation splits nominal layers into nearby z values. Only
    # compare the pronounced inter-layer gaps when choosing the fault plane.
    threshold = max(0.5, 0.4 * float(np.max(gaps_angstrom)))
    candidates = np.where(gaps_angstrom >= threshold)[0]
    if not len(candidates):
        raise RuntimeError("Could not find a material-internal gap for Gamma")
    centers = np.mod((values + following) / 2.0, 1.0)
    target_fraction = float(target_fraction) % 1.0
    half_cut = len(values) // 2 - 1
    if (
        len(values) % 2 == 0
        and np.isclose(target_fraction, 0.5, atol=1e-12, rtol=0.0)
        and half_cut in candidates
    ):
        # APEX constructs the normal Gamma slab from two equivalent material
        # blocks whenever the central layer gap permits it. Prefer the exact
        # half split over a nearby gap whose fractional center was shifted by
        # alloy relaxation or cell skew.
        cut = half_cut
    else:
        circular_distance = np.abs(
            np.mod(centers[candidates] - target_fraction + 0.5, 1.0) - 0.5
        )
        cut = int(candidates[np.argmin(circular_distance)])
    lower = order[: cut + 1]
    upper = order[cut + 1 :]
    if not len(lower) or not len(upper):
        # If the selected gap crosses the periodic boundary, rotate the order
        # to place that gap at the end and split at the closest internal gap.
        raise RuntimeError("Gamma fault split selected the periodic boundary")
    return lower, upper, float(centers[cut]), float(gaps_angstrom[cut])


def _center_fault_and_add_vacuum(
    structure: Structure,
    fault_center_fraction: float,
    vacuum_size: float,
) -> Structure:
    material = structure.copy()
    if vacuum_size <= 0:
        # In a fully periodic GSFE cell there is no physical surface. Centering
        # the selected fault only changes the periodic origin and keeps the two
        # interfaces easy to inspect.
        shift = 0.5 - fault_center_fraction
        material.translate_sites(
            list(range(len(material))),
            [0.0, 0.0, shift],
            frac_coords=True,
            to_unit_cell=True,
        )
        return material
    # With vacuum, do not move the fault gap to z=0.5 before opening the cell.
    # For an odd number of atomic layers, the point opposite an inter-layer
    # fault gap lies inside a layer; opening vacuum there would split one layer
    # across the two free surfaces. The slab generator already places a real
    # material gap at its periodic boundary, so retain that boundary and keep
    # the independently frozen fault indices at their actual internal gap.
    # Once a free-surface vacuum is present, the old bulk-periodic in-plane
    # component of c is neither required nor desirable.  Retaining it creates
    # a strongly tilted vacuum box (often c_xy ~= half an in-plane vector),
    # even though the physical surface normal is already local z.  Preserve
    # the complete Cartesian slab and its in-plane periodicity, but replace c
    # by the pure normal repeat before wrapping.  Vacuum-free GSFE cells keep
    # the exact bulk-periodic c vector above.
    lattice = material.lattice.matrix.copy()
    material_height = abs(float(lattice[2, 2]))
    lattice[2] = np.array(
        [0.0, 0.0, material_height + float(vacuum_size)]
    )
    coords = material.cart_coords.copy()
    z_min = float(np.min(coords[:, 2]))
    z_max = float(np.max(coords[:, 2]))
    coords[:, 2] += (
        material_height + float(vacuum_size) - (z_max - z_min)
    ) / 2.0 - z_min
    return Structure(
        lattice,
        material.species,
        coords,
        coords_are_cartesian=True,
        to_unit_cell=True,
        site_properties=material.site_properties,
    )


def _anonymous_closure(
    reference: Structure,
    displaced: Structure,
) -> tuple[float, float]:
    distances = reference.lattice.get_all_distances(
        reference.frac_coords, displaced.frac_coords
    )
    rows, cols = linear_sum_assignment(distances)
    matched = distances[rows, cols]
    return float(np.sqrt(np.mean(matched**2))), float(np.max(matched))


def _chemical_closure(
    reference: Structure,
    displaced: Structure,
) -> tuple[float, float]:
    matched_distances = []
    reference_symbols = np.array([site.specie.symbol for site in reference])
    displaced_symbols = np.array([site.specie.symbol for site in displaced])
    all_distances = reference.lattice.get_all_distances(
        reference.frac_coords, displaced.frac_coords
    )
    for symbol in sorted(set(reference_symbols)):
        left = np.where(reference_symbols == symbol)[0]
        right = np.where(displaced_symbols == symbol)[0]
        if len(left) != len(right):
            return float("inf"), float("inf")
        rows, cols = linear_sum_assignment(all_distances[np.ix_(left, right)])
        matched_distances.extend(
            all_distances[np.ix_(left, right)][rows, cols].tolist()
        )
    matched = np.asarray(matched_distances)
    return float(np.sqrt(np.mean(matched**2))), float(np.max(matched))


def build_parent_gamma_slab(
    structure: Structure,
    slip_geometry: ParentSlipGeometry,
    supercell_size,
    plane_target: float,
    min_slab_height: float | None,
    vacuum_size: float,
    plane_shift: float = 0.0,
    require_orthogonal_cell: bool = False,
) -> GammaSlabGeometry:
    """Build a fault-ready slab while retaining parent crystallography."""

    generator, generation_metadata = make_gamma_slab_generator(
        structure,
        tuple(int(value) for value in slip_geometry.slab_miller),
        plane_target,
        min_slab_height,
    )
    # RSS relaxation splits atoms belonging to one parent plane by up to a
    # few tenths of an Angstrom. Cluster those sites into the same termination
    # plane; an almost-zero tolerance can cut through a relaxed alloy layer.
    material = get_first_gamma_slab(generator, ftol=0.2)
    material = _rotate_structure(material, slip_geometry.local_frame)
    material.make_supercell([int(supercell_size[0]), int(supercell_size[1]), 1])

    a, b, c = material.lattice.matrix
    material_height = abs(float(c[2]))
    if material_height <= 0:
        raise RuntimeError("Resolved Gamma slab has zero normal thickness")
    if abs(a[2]) > 1e-7 or abs(b[2]) > 1e-7:
        raise RuntimeError(
            "Resolved Gamma in-plane lattice vectors are not perpendicular "
            "to the fault normal"
        )
    # plane_shift remains an existing API. In parent mode it selects the
    # nearest material gap to a shifted fractional target; it never acts on
    # the vacuum-extended cell.
    target = (0.5 + float(plane_shift)) % 1.0
    lower, upper, fault_center, fault_gap = _select_fault_gap(material, target)
    slab = _center_fault_and_add_vacuum(material, fault_center, vacuum_size)

    local_burgers = slip_geometry.local_frame @ slip_geometry.burgers_vector_cart
    if np.linalg.norm(local_burgers[1:]) > 1e-7:
        raise RuntimeError(
            "Resolved Burgers vector is not aligned with the local Gamma x axis"
        )
    endpoint = slab.copy()
    endpoint.translate_sites(
        upper,
        local_burgers,
        frac_coords=False,
        to_unit_cell=True,
    )
    geometry_rms, geometry_max = _anonymous_closure(slab, endpoint)
    chemical_rms, chemical_max = _chemical_closure(slab, endpoint)
    # The parent translation is topologically closed by construction. A
    # chemically disordered, locally relaxed RSS is not invariant under one
    # elementary parent translation, so its endpoint coordinate mismatch is a
    # diagnostic rather than a valid periodicity gate.

    cell_geometry = validate_gamma_cell_geometry(
        slab,
        require_orthogonal=require_orthogonal_cell,
        property_name="parent-aware Gamma",
    )
    surface_gap = float(vacuum_size)
    if vacuum_size > 0:
        frac_z = np.sort(np.mod(slab.frac_coords[:, 2], 1.0))
        boundary_gap = (frac_z[0] + 1.0 - frac_z[-1]) * abs(
            float(slab.lattice.matrix[2, 2])
        )
        surface_gap = float(boundary_gap)
    metadata = {
        "parent_aware_geometry": True,
        "atom_count": len(slab),
        "lower_count": int(len(lower)),
        "upper_count": int(len(upper)),
        "lower_indices": lower.astype(int).tolist(),
        "upper_indices": upper.astype(int).tolist(),
        "moved_count": int(len(upper)),
        "material_height_angstrom": material_height,
        "fault_gap_angstrom": fault_gap,
        "fault_center_material_fraction": fault_center,
        "added_vacuum_angstrom": float(vacuum_size),
        "surface_gap_angstrom": surface_gap,
        "interface_count": 1 if vacuum_size > 0 else 2,
        "anonymous_u1_rms_angstrom": geometry_rms,
        "anonymous_u1_max_angstrom": geometry_max,
        "chemical_u1_rms_angstrom": chemical_rms,
        "chemical_u1_max_angstrom": chemical_max,
        "anonymous_u1_closed": geometry_max <= 0.65,
        "chemical_u1_closed": chemical_max <= 0.2,
        "parent_translation_topology_closed": True,
        "local_burgers_vector": local_burgers.tolist(),
        "slab_lattice": slab.lattice.matrix.tolist(),
        "cell_geometry": cell_geometry,
        "require_orthogonal_cell": bool(require_orthogonal_cell),
    }
    return GammaSlabGeometry(
        slab=slab,
        upper_indices=upper,
        lower_indices=lower,
        metadata=metadata,
        generation_metadata=generation_metadata,
    )
