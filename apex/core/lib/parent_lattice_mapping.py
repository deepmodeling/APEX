"""Parent-lattice mapping helpers for disordered alloy supercells.

The public Gamma API uses crystallographic indices in the parent lattice.
This module keeps the supercell-specific basis conversion internal so a
chemically disordered (P1) RSS cell is not mistaken for its own parent cell.

All lattice matrices follow pymatgen's row-vector convention::

    r_cart = f @ lattice
    super_lattice = M @ parent_lattice

Consequently a parent direction and plane transform as::

    d_super = d_parent @ inv(M)
    h_super = h_parent @ M.T
"""

from __future__ import annotations

from dataclasses import dataclass
import itertools
import math

import numpy as np
from dflow.python import upload_packages
from pymatgen.core import Structure
from scipy.optimize import linear_sum_assignment

upload_packages.append(__file__)


_PARENT_BASES = {
    "bcc": np.array([[0.0, 0.0, 0.0], [0.5, 0.5, 0.5]]),
    "fcc": np.array(
        [
            [0.0, 0.0, 0.0],
            [0.0, 0.5, 0.5],
            [0.5, 0.0, 0.5],
            [0.5, 0.5, 0.0],
        ]
    ),
    "hcp": np.array([[0.0, 0.0, 0.0], [2.0 / 3.0, 1.0 / 3.0, 0.5]]),
}


@dataclass(frozen=True)
class ParentLatticeMapping:
    """Resolved relationship between a parent conventional cell and a bulk."""

    parent_lattice: str
    supercell_matrix: np.ndarray
    relaxed_parent_lattice: np.ndarray
    determinant: int
    metric_score: float
    site_rms: float
    site_max: float
    candidate_count: int
    source: str

    def as_dict(self) -> dict:
        return {
            "parent_lattice": self.parent_lattice,
            "supercell_matrix": self.supercell_matrix.astype(int).tolist(),
            "relaxed_parent_lattice": self.relaxed_parent_lattice.tolist(),
            "determinant": int(self.determinant),
            "metric_score": float(self.metric_score),
            "site_rms_angstrom": float(self.site_rms),
            "site_max_angstrom": float(self.site_max),
            "candidate_count": int(self.candidate_count),
            "source": self.source,
            "row_vector_convention": True,
        }


@dataclass(frozen=True)
class ParentSlipGeometry:
    """A parent slip system expressed in the actual relaxed bulk geometry."""

    parent_plane: np.ndarray
    parent_direction: np.ndarray
    mapped_plane_full: np.ndarray
    slab_miller: np.ndarray
    mapped_direction: np.ndarray
    plane_normal_cart: np.ndarray
    direction_cart: np.ndarray
    burgers_fraction: float
    burgers_vector_cart: np.ndarray
    local_frame: np.ndarray

    def as_dict(self) -> dict:
        return {
            "parent_plane_miller": self.parent_plane.tolist(),
            "parent_slip_direction": self.parent_direction.tolist(),
            "mapped_plane_full": self.mapped_plane_full.tolist(),
            "slab_miller": self.slab_miller.astype(int).tolist(),
            "mapped_direction": self.mapped_direction.tolist(),
            "plane_normal_cart": self.plane_normal_cart.tolist(),
            "direction_cart": self.direction_cart.tolist(),
            "burgers_fraction": float(self.burgers_fraction),
            "burgers_vector_cart": self.burgers_vector_cart.tolist(),
            "burgers_length_angstrom": float(
                np.linalg.norm(self.burgers_vector_cart)
            ),
            "local_frame_rows": self.local_frame.tolist(),
        }


def _factor_triples(number: int):
    for first in range(1, number + 1):
        if number % first:
            continue
        remaining = number // first
        for second in range(1, remaining + 1):
            if remaining % second:
                continue
            yield first, second, remaining // second


def _diagonal_candidates(determinant: int):
    seen = set()
    for values in _factor_triples(determinant):
        for diagonal in set(itertools.permutations(values)):
            matrix = np.diag(diagonal).astype(int)
            key = tuple(matrix.ravel())
            if key not in seen:
                seen.add(key)
                yield matrix


def _hnf_candidates(determinant: int):
    """Yield row-HNF candidates used as a fail-closed fallback.

    APEX RSS structures normally retain a diagonal/permuted conventional-cell
    basis. HNF coverage handles externally supplied non-diagonal supercells
    without exposing a matrix parameter to the user.
    """

    for a, e, g in _factor_triples(determinant):
        for b, c, f in itertools.product(range(a), range(a), range(e)):
            yield np.array([[a, b, c], [0, e, f], [0, 0, g]], dtype=int)


def _parent_metric_score(parent_lattice: str, lattice: np.ndarray) -> float:
    lengths = np.linalg.norm(lattice, axis=1)
    if np.any(lengths <= 0):
        return float("inf")
    cosines = np.array(
        [
            np.dot(lattice[1], lattice[2]) / (lengths[1] * lengths[2]),
            np.dot(lattice[0], lattice[2]) / (lengths[0] * lengths[2]),
            np.dot(lattice[0], lattice[1]) / (lengths[0] * lengths[1]),
        ]
    )
    if parent_lattice in {"bcc", "fcc"}:
        scale = float(np.mean(lengths))
        length_error = np.linalg.norm(lengths / scale - 1.0)
        angle_error = np.linalg.norm(cosines)
        return float(np.hypot(length_error, angle_error))

    # Conventional HCP: a == b, alpha == beta == 90 deg, gamma == 120 deg.
    a_scale = float(np.mean(lengths[:2]))
    length_error = abs(lengths[0] - lengths[1]) / a_scale
    angle_error = np.linalg.norm(cosines - np.array([0.0, 0.0, -0.5]))
    return float(np.hypot(length_error, angle_error))


def _site_fit(
    structure: Structure,
    parent_lattice: str,
    matrix: np.ndarray,
) -> tuple[float, float]:
    parent_matrix = np.linalg.inv(matrix) @ structure.lattice.matrix
    basis = _PARENT_BASES[parent_lattice]
    parent = Structure(
        parent_matrix,
        ["H"] * len(basis),
        basis,
    )
    parent.make_supercell(matrix)
    if len(parent) != len(structure):
        return float("inf"), float("inf")
    distances = structure.lattice.get_all_distances(
        structure.frac_coords, parent.frac_coords
    )
    rows, cols = linear_sum_assignment(distances)
    matched = distances[rows, cols]
    return float(np.sqrt(np.mean(matched**2))), float(np.max(matched))


def resolve_parent_supercell(
    structure: Structure,
    parent_lattice: str,
    *,
    max_metric_score: float = 0.45,
    max_site_rms: float = 0.35,
    max_site_distance: float = 0.65,
) -> ParentLatticeMapping:
    """Infer the conventional-parent supercell matrix without user input.

    The resolver first tries the diagonal/permuted matrices emitted by normal
    RSS workflows. It falls back to all HNF matrices with the required
    determinant. Candidates are accepted only after an anonymous parent-site
    bijection succeeds; chemical species are intentionally ignored.
    """

    parent_lattice = str(parent_lattice).strip().lower()
    if parent_lattice not in _PARENT_BASES:
        raise ValueError("parent_lattice must be one of: bcc, fcc, hcp")
    basis_count = len(_PARENT_BASES[parent_lattice])
    if len(structure) % basis_count:
        raise RuntimeError(
            f"Cannot map {len(structure)} atoms to a {parent_lattice} "
            f"conventional cell containing {basis_count} sites"
        )
    determinant = len(structure) // basis_count
    lattice = np.asarray(structure.lattice.matrix, dtype=float)

    def rank(candidates, source):
        ranked = []
        accepted = []
        count = 0
        for matrix in candidates:
            count += 1
            try:
                parent_matrix = np.linalg.solve(matrix, lattice)
            except np.linalg.LinAlgError:
                continue
            score = _parent_metric_score(parent_lattice, parent_matrix)
            if np.isfinite(score):
                ranked.append((score, tuple(matrix.ravel()), matrix, parent_matrix))
        ranked.sort(key=lambda item: (item[0], item[1]))
        for score, _, matrix, parent_matrix in ranked[:32]:
            if score > max_metric_score:
                break
            site_rms, site_max = _site_fit(structure, parent_lattice, matrix)
            if site_rms <= max_site_rms and site_max <= max_site_distance:
                accepted.append(
                    (site_rms, site_max, score, tuple(matrix.ravel()), matrix, parent_matrix)
                )
        if not accepted:
            return None
        accepted.sort(key=lambda item: item[:4])
        best = accepted[0]
        if len(accepted) > 1:
            second = accepted[1]
            if (
                abs(second[0] - best[0]) < 1e-8
                and abs(second[1] - best[1]) < 1e-8
                and abs(second[2] - best[2]) < 1e-8
                and second[3] != best[3]
            ):
                raise RuntimeError(
                    "Parent-supercell mapping is ambiguous between equally "
                    "good integer matrices; APEX will not guess the index basis"
                )
        site_rms, site_max, score, _, matrix, parent_matrix = best
        return ParentLatticeMapping(
            parent_lattice=parent_lattice,
            supercell_matrix=matrix,
            relaxed_parent_lattice=parent_matrix,
            determinant=determinant,
            metric_score=score,
            site_rms=site_rms,
            site_max=site_max,
            candidate_count=count,
            source=source,
        )

    mapping = rank(_diagonal_candidates(determinant), "automatic_diagonal")
    if mapping is None:
        mapping = rank(_hnf_candidates(determinant), "automatic_hnf")
    if mapping is None:
        raise RuntimeError(
            "Could not infer a unique, low-residual parent-supercell mapping "
            f"for the {parent_lattice} structure. APEX will not interpret "
            "parent Miller indices directly in this P1 cell."
        )
    return mapping


def _reduce_integer_vector(vector: np.ndarray) -> np.ndarray:
    rounded = np.rint(vector).astype(int)
    if not np.allclose(vector, rounded, atol=1e-8, rtol=0.0):
        raise RuntimeError(f"Mapped Miller indices are not integral: {vector}")
    divisor = 0
    for value in rounded:
        divisor = math.gcd(divisor, abs(int(value)))
    if divisor == 0:
        raise RuntimeError("Miller plane cannot be the zero vector")
    return rounded // divisor


def shortest_parent_translation_fraction(
    parent_lattice: str,
    direction: np.ndarray,
    max_denominator: int = 24,
) -> float:
    """Return the shortest parent Bravais translation along ``direction``."""

    basis = _PARENT_BASES[parent_lattice]
    direction = np.asarray(direction, dtype=float)
    fractions = sorted(
        {
            numerator / denominator
            for denominator in range(1, max_denominator + 1)
            for numerator in range(1, denominator + 1)
            if math.gcd(numerator, denominator) == 1
        }
    )
    for fraction in fractions:
        shifted = (basis + fraction * direction) % 1.0
        delta = shifted[:, None, :] - basis[None, :, :]
        delta -= np.rint(delta)
        distances = np.linalg.norm(delta, axis=2)
        rows, cols = linear_sum_assignment(distances)
        if np.max(distances[rows, cols]) < 1e-8:
            return float(fraction)
    raise RuntimeError(
        f"Could not derive a parent-lattice translation along {direction.tolist()}"
    )


def resolve_parent_slip_geometry(
    structure: Structure,
    mapping: ParentLatticeMapping,
    plane_miller,
    slip_direction,
    slip_length=None,
) -> ParentSlipGeometry:
    """Map a user-facing parent slip system into the relaxed RSS cell."""

    parent_plane = np.asarray(plane_miller, dtype=float)
    parent_direction = np.asarray(slip_direction, dtype=float)
    if parent_plane.shape != (3,) or parent_direction.shape != (3,):
        raise RuntimeError("Parent-mapped Gamma currently requires 3-index vectors")
    incidence = float(parent_plane @ parent_direction)
    if not np.isclose(incidence, 0.0, atol=1e-10, rtol=0.0):
        raise RuntimeError(
            f"slip direction {slip_direction} is not on plane {plane_miller}"
        )

    matrix = np.asarray(mapping.supercell_matrix, dtype=float)
    mapped_plane_full = parent_plane @ matrix.T
    slab_miller = _reduce_integer_vector(mapped_plane_full)
    mapped_direction = parent_direction @ np.linalg.inv(matrix)
    lattice = np.asarray(structure.lattice.matrix, dtype=float)
    direction_cart = mapped_direction @ lattice
    normal_cart = mapped_plane_full @ np.linalg.inv(lattice).T
    direction_norm = np.linalg.norm(direction_cart)
    normal_norm = np.linalg.norm(normal_cart)
    if direction_norm <= 0 or normal_norm <= 0:
        raise RuntimeError("Resolved Gamma direction or plane normal is zero")
    direction_unit = direction_cart / direction_norm
    normal_unit = normal_cart / normal_norm
    orthogonality = abs(float(direction_unit @ normal_unit))
    if orthogonality > 1e-8:
        raise RuntimeError(
            "Resolved parent slip direction is not in the resolved plane: "
            f"normalized dot={orthogonality:.3e}"
        )

    if slip_length is None:
        burgers_fraction = shortest_parent_translation_fraction(
            mapping.parent_lattice, parent_direction
        )
    elif isinstance(slip_length, (int, float, np.integer, np.floating)):
        # Legacy scalar lengths are expressed in units of the conventional
        # parent a. Convert them into a fraction of the supplied direction;
        # the relaxed Cartesian vector is still obtained affinely below.
        burgers_fraction = float(slip_length) / float(
            np.linalg.norm(parent_direction)
        )
    else:
        raise RuntimeError(
            "Parent-mapped Gamma accepts a scalar legacy slip_length or derives "
            "the shortest parent translation when it is omitted"
        )
    burgers_vector = burgers_fraction * direction_cart
    second = np.cross(normal_unit, direction_unit)
    second /= np.linalg.norm(second)
    normal_unit = np.cross(direction_unit, second)
    normal_unit /= np.linalg.norm(normal_unit)
    local_frame = np.array([direction_unit, second, normal_unit])
    return ParentSlipGeometry(
        parent_plane=parent_plane,
        parent_direction=parent_direction,
        mapped_plane_full=mapped_plane_full,
        slab_miller=slab_miller,
        mapped_direction=mapped_direction,
        plane_normal_cart=normal_unit,
        direction_cart=direction_cart,
        burgers_fraction=burgers_fraction,
        burgers_vector_cart=burgers_vector,
        local_frame=local_frame,
    )
