import numpy as np
import pytest
from monty.serialization import dumpfn, loadfn
from pymatgen.core import Structure

from apex.core.lib.parent_lattice_mapping import (
    resolve_parent_slip_geometry,
    resolve_parent_supercell,
)
from apex.core.calculator.lib.lammps_utils import cvt_lammps_conf
from apex.core.property.gamma_geometry import build_parent_gamma_slab
from apex.core.property.gamma_slab import minimum_pair_distance
from apex.core.property.Gamma import Gamma
from apex.core.property.GammaSurface import GammaSurface


def _make_sheared_bcc_5x5x2():
    parent_lattice = np.array(
        [
            [3.02, 0.00, 0.00],
            [-0.25, 3.01, 0.00],
            [-0.26, -0.28, 2.99],
        ]
    )
    structure = Structure(
        parent_lattice,
        ["V", "V"],
        [[0.0, 0.0, 0.0], [0.5, 0.5, 0.5]],
    )
    structure.make_supercell(np.diag([5, 5, 2]))
    for index in range(5):
        structure.replace(index, "Ti")
    # Deterministic local alloy-scale offsets exercise tolerant anonymous site
    # matching without changing the parent-supercell topology.
    for index in range(len(structure)):
        offset = 0.01 * np.array(
            [
                np.sin(index),
                np.cos(2 * index),
                np.sin(3 * index),
            ]
        )
        structure.translate_sites(
            [index], offset, frac_coords=False, to_unit_cell=True
        )
    return structure


def test_resolve_bcc_parent_mapping_and_indices():
    structure = _make_sheared_bcc_5x5x2()
    mapping = resolve_parent_supercell(structure, "bcc")
    assert np.array_equal(mapping.supercell_matrix, np.diag([5, 5, 2]))
    assert mapping.determinant == 50
    assert mapping.site_max < 0.1

    geometry = resolve_parent_slip_geometry(
        structure,
        mapping,
        plane_miller=[1, 1, 0],
        slip_direction=[-1, 1, 1],
    )
    assert np.allclose(geometry.mapped_plane_full, [5, 5, 0])
    assert np.array_equal(geometry.slab_miller, [1, 1, 0])
    assert np.allclose(geometry.mapped_direction, [-0.2, 0.2, 0.5])
    assert geometry.burgers_fraction == 0.5
    assert abs(np.dot(geometry.plane_normal_cart, geometry.direction_cart)) < 1e-8


def test_resolve_non_diagonal_hnf_supercell():
    parent = Structure(
        [[3.0, 0.0, 0.0], [0.05, 3.02, 0.0], [0.02, -0.03, 2.98]],
        ["V", "V"],
        [[0.0, 0.0, 0.0], [0.5, 0.5, 0.5]],
    )
    expected = np.array([[2, 1, 0], [0, 5, 1], [0, 0, 5]])
    parent.make_supercell(expected)
    mapping = resolve_parent_supercell(parent, "bcc")
    assert np.array_equal(mapping.supercell_matrix, expected)
    assert mapping.source == "automatic_hnf"
    assert mapping.site_max < 1e-8


def test_parent_gamma_slab_is_200_atoms_and_layer_gap_split():
    structure = _make_sheared_bcc_5x5x2()
    mapping = resolve_parent_supercell(structure, "bcc")
    geometry = resolve_parent_slip_geometry(
        structure, mapping, [1, 1, 0], [-1, 1, 1]
    )
    built = build_parent_gamma_slab(
        structure,
        geometry,
        supercell_size=[2, 1, 1],
        plane_target=1,
        min_slab_height=10.0,
        vacuum_size=20.0,
    )
    assert len(built.slab) == 200
    assert len(built.lower_indices) + len(built.upper_indices) == 200
    assert len(built.lower_indices) > 0
    assert len(built.upper_indices) > 0
    assert built.metadata["moved_count"] == len(built.upper_indices)
    assert 9.0 < built.metadata["material_height_angstrom"] < 12.0
    assert built.metadata["added_vacuum_angstrom"] == 20.0
    assert built.metadata["interface_count"] == 1
    assert built.metadata["parent_translation_topology_closed"] is True
    assert np.allclose(built.slab.lattice.matrix[2, :2], 0.0, atol=1e-10)
    assert np.allclose(built.slab.lattice.matrix[:2, 2], 0.0, atol=1e-10)
    # No atomic layer may be split between the two vacuum-facing boundaries.
    z = np.sort(built.slab.cart_coords[:, 2])
    layer_cuts = np.where(np.diff(z) > 0.5)[0] + 1
    layer_sizes = [len(group) for group in np.split(z, layer_cuts)]
    assert layer_sizes == [40, 40, 40, 40, 40]

    midpoint = built.slab.copy()
    local_burgers = geometry.local_frame @ geometry.burgers_vector_cart
    midpoint.translate_sites(
        built.upper_indices,
        0.5 * local_burgers,
        frac_coords=False,
        to_unit_cell=True,
    )
    assert minimum_pair_distance(built.slab) > 2.0
    assert minimum_pair_distance(midpoint) > 2.0


def test_gamma_make_confs_uses_parent_indices_without_extra_parameters(tmp_path):
    equi = tmp_path / "relaxation" / "relax_task"
    work = tmp_path / "gamma_00"
    equi.mkdir(parents=True)
    structure = _make_sheared_bcc_5x5x2()
    structure.to(equi / "CONTCAR", "POSCAR")
    dumpfn(
        {
            "energies": [-100.0],
            "atom_numbs": [5, 95],
            "cells": [structure.lattice.matrix.tolist()],
        },
        equi / "result.json",
    )
    prop = Gamma(
        {
            "type": "gamma",
            "parent_lattice": "bcc",
            "plane_miller": [1, 1, 0],
            "slip_direction": [-1, 1, 1],
            "supercell_size": [2, 1, 1],
            "min_slab_height": 10.0,
            "max_atoms": 220,
            "min_distance": 1.7,
            "vacuum_size": 20.0,
            "displacement_points": [0.0, 0.5],
        },
        {"type": "deepmd"},
    )
    tasks = prop.make_confs(work, equi)
    assert len(tasks) == 2
    manifest = loadfn(work / "gamma_geometry.json")
    assert manifest["parent_mapping"]["supercell_matrix"] == [
        [5, 0, 0],
        [0, 5, 0],
        [0, 0, 2],
    ]
    assert manifest["slip_geometry"]["burgers_fraction"] == 0.5
    assert manifest["slab_geometry"]["upper_count"] > 0
    assert manifest["slab_geometry"]["lower_count"] > 0
    assert (
        manifest["slab_geometry"]["upper_count"]
        + manifest["slab_geometry"]["lower_count"]
        == 200
    )
    assert Structure.from_file(work / "task.000000" / "POSCAR").num_sites == 200
    assert Structure.from_file(work / "task.000001" / "POSCAR").num_sites == 200


def test_lammps_conversion_preserves_local_surface_normal(tmp_path):
    structure = _make_sheared_bcc_5x5x2()
    mapping = resolve_parent_supercell(structure, "bcc")
    geometry = resolve_parent_slip_geometry(
        structure, mapping, [1, 1, 0], [-1, 1, 1]
    )
    built = build_parent_gamma_slab(
        structure,
        geometry,
        supercell_size=[2, 1, 1],
        plane_target=1,
        min_slab_height=10.0,
        vacuum_size=20.0,
    )
    poscar = tmp_path / "POSCAR"
    lammps_data = tmp_path / "conf.lmp"
    built.slab.to(poscar, "POSCAR")
    cvt_lammps_conf(str(poscar), str(lammps_data), ["Ti", "V"])

    # dpdata converts the already surface-aligned POSCAR to LAMMPS's restricted
    # triclinic convention.  It may rotate within the surface plane, but both
    # in-plane lattice vectors must remain perpendicular to local z.  Thus the
    # existing setforce 0 0 NULL constraint still means normal-only relaxation.
    import dpdata

    converted = dpdata.System(
        str(lammps_data), fmt="lammps/lmp", type_map=["Ti", "V"]
    )
    cell = np.asarray(converted.data["cells"][0], dtype=float)
    assert np.allclose(cell[:2, 2], 0.0, atol=1e-10)
    assert cell[2, 2] > 0.0


def test_parent_gamma_surface_matches_gamma_line_x_section(tmp_path):
    equi = tmp_path / "relaxation" / "relax_task"
    equi.mkdir(parents=True)
    structure = _make_sheared_bcc_5x5x2()
    structure.to(equi / "CONTCAR", "POSCAR")
    dumpfn(
        {
            "energies": [-100.0],
            "atom_numbs": [5, 95],
            "cells": [structure.lattice.matrix.tolist()],
        },
        equi / "result.json",
    )
    common = {
        "parent_lattice": "bcc",
        "plane_miller": [1, 1, 0],
        "slip_direction": [-1, 1, 1],
        "supercell_size": [2, 1, 1],
        "min_slab_height": 10.0,
        "max_atoms": 220,
        "min_distance": 1.7,
        "vacuum_size": 20.0,
    }
    line = Gamma(
        {
            "type": "gamma",
            **common,
            "displacement_points": [0.0, 0.5],
        },
        {"type": "deepmd"},
    )
    surface = GammaSurface(
        {
            "type": "gamma_surface",
            **common,
            "closed_loop": False,
            "n_steps_x": 2,
            "n_steps_y": 1,
        },
        {"type": "deepmd"},
    )
    line_tasks = line.make_confs(tmp_path / "gamma_00", equi)
    surface_tasks = surface.make_confs(tmp_path / "gamma_surface_00", equi)
    assert len(line_tasks) == 2
    assert len(surface_tasks) == 6

    manifest = loadfn(tmp_path / "gamma_surface_00" / "gamma_geometry.json")
    assert manifest["parent_mapping"]["supercell_matrix"] == [
        [5, 0, 0],
        [0, 5, 0],
        [0, 0, 2],
    ]
    assert manifest["slab_geometry"]["interface_count"] == 1
    assert manifest["slab_geometry"]["upper_count"] > 0
    assert manifest["slab_geometry"]["lower_count"] > 0

    for line_task, surface_task in zip(
        line_tasks,
        [surface_tasks[0], surface_tasks[2]],
    ):
        line_structure = Structure.from_file(line_task + "/POSCAR")
        surface_structure = Structure.from_file(surface_task + "/POSCAR")
        np.testing.assert_allclose(
            line_structure.lattice.matrix,
            surface_structure.lattice.matrix,
            atol=1.0e-10,
        )
        assert [site.specie.symbol for site in line_structure] == [
            site.specie.symbol for site in surface_structure
        ]
        np.testing.assert_allclose(
            line_structure.frac_coords,
            surface_structure.frac_coords,
            atol=1.0e-10,
        )


def test_parent_gamma_strict_orthogonal_gate_is_fail_closed(tmp_path):
    equi = tmp_path / "relaxation" / "relax_task"
    equi.mkdir(parents=True)
    structure = _make_sheared_bcc_5x5x2()
    structure.to(equi / "CONTCAR", "POSCAR")
    dumpfn(
        {"energies": [-100.0], "atom_numbs": [5, 95]},
        equi / "result.json",
    )
    prop = GammaSurface(
        {
            "type": "gamma_surface",
            "parent_lattice": "bcc",
            "plane_miller": [1, 1, 0],
            "slip_direction": [-1, 1, 1],
            "supercell_size": [2, 1, 1],
            "min_slab_height": 10.0,
            "max_atoms": 220,
            "min_distance": 1.7,
            "vacuum_size": 20.0,
            "require_orthogonal_cell": True,
            "n_steps_x": 1,
            "n_steps_y": 1,
        },
        {"type": "deepmd"},
    )
    with pytest.raises(RuntimeError, match="will not Gram-Schmidt"):
        prop.make_confs(tmp_path / "gamma_surface_00", equi)


def test_parent_gamma_strict_orthogonal_gate_accepts_pure_bcc():
    structure = Structure(
        np.eye(3) * 3.0945974428563945,
        ["V", "V"],
        [[0.0, 0.0, 0.0], [0.5, 0.5, 0.5]],
    )
    structure.make_supercell(np.diag([5, 5, 2]))
    mapping = resolve_parent_supercell(structure, "bcc")
    geometry = resolve_parent_slip_geometry(
        structure, mapping, [1, 1, 0], [-1, 1, 1]
    )
    built = build_parent_gamma_slab(
        structure,
        geometry,
        supercell_size=[2, 1, 1],
        plane_target=1,
        min_slab_height=10.0,
        vacuum_size=20.0,
        require_orthogonal_cell=True,
    )
    assert built.metadata["cell_geometry"]["zero_tilt"] is True
    assert built.metadata["cell_geometry"]["orthogonalization_applied"] is False
