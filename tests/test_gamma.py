import glob
import os
import shutil
import sys
import unittest

import pytest
from monty.serialization import dumpfn
from pymatgen.core import Lattice, Structure
from pymatgen.io.vasp import Incar

from apex.core.property.Gamma import Gamma

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
__package__ = "tests"


class TestGamma(unittest.TestCase):
    def setUp(self):
        _jdata = {
            "structures": ["confs/std-fcc"],
            "interaction": {
                "type": "vasp",
                "incar": "vasp_input/INCAR_Mo",
                "potcar_prefix": "vasp_input",
                "potcars": {"Mo": "POTCAR_Mo"},
            },
            "properties": [
                {
                    "type": "gamma",
                    "plane_miller": [0, 0, 1],
                    "slip_direction": [1, 0, 0],
                    "hcp": {
                        "plane_miller": [0, 0, 0, 1],
                        "slip_direction": [2, -1, -1, 0],
                    },
                    "supercell_size": [1, 1, 10],
                    "vacuum_size": 10,
                    "add_fix": ["true", "true", "false"],
                    "n_steps": 10
                }

            ],
        }

        self.equi_path = "confs/hp-Mo/relaxation/relax_task"
        self.source_path = "equi/vasp"
        self.target_path = "confs/hp-Mo/gamma_00"
        self.res_data = "output/gamma_00/result.json"
        self.ptr_data = "output/gamma_00/result.out"

        if not os.path.exists(self.equi_path):
            os.makedirs(self.equi_path)
        if not os.path.exists(self.target_path):
            os.makedirs(self.target_path)

        self.confs = _jdata["structures"]
        self.inter_param = _jdata["interaction"]
        self.prop_param = _jdata["properties"]

        self.gamma = Gamma(_jdata["properties"][0])

    def tearDown(self):
        if os.path.exists(self.equi_path):
            shutil.rmtree(self.equi_path)
        if os.path.exists(self.target_path):
            shutil.rmtree(self.target_path)
        if os.path.exists(self.res_data):
            os.remove(self.res_data)
        if os.path.exists(self.ptr_data):
            os.remove(self.ptr_data)

    def test_task_type(self):
        self.assertEqual("gamma", self.gamma.task_type())

    def test_task_param(self):
        self.assertEqual(self.prop_param[0], self.gamma.task_param())

    def test_parent_lattice_hint_is_normalized(self):
        gamma = Gamma(
            {
                "type": "gamma",
                "parent_lattice": " BCC ",
                "plane_miller": [0, 1, 1],
                "slip_direction": [1, -1, 1],
            }
        )
        self.assertEqual(gamma.parent_lattice, "bcc")
        self.assertEqual(gamma.task_param()["parent_lattice"], "bcc")

    def test_invalid_parent_lattice_hint_fails(self):
        with self.assertRaisesRegex(ValueError, "bcc, fcc, hcp"):
            Gamma({"type": "gamma", "parent_lattice": "b2"})

    def test_displacement_points_require_zero_reference(self):
        with self.assertRaisesRegex(ValueError, "must include 0"):
            Gamma({"type": "gamma", "displacement_points": [0.25, 0.5, 1.0]})

    def test_gamma_rejects_invalid_steps_and_vacuum(self):
        for value in (0, -1, 1.5, True):
            with self.subTest(n_steps=value):
                with self.assertRaisesRegex(ValueError, "positive integer"):
                    Gamma({"type": "gamma", "n_steps": value})
        for value in (-1.0, float("nan"), float("inf"), True):
            with self.subTest(vacuum_size=value):
                with self.assertRaisesRegex(ValueError, "finite number"):
                    Gamma({"type": "gamma", "vacuum_size": value})

    def test_gamma_orthogonalize_alias_is_strict_gate(self):
        gamma = Gamma({"type": "gamma", "orthogonalize_cell": True})
        self.assertTrue(gamma.require_orthogonal_cell)
        self.assertTrue(gamma.task_param()["require_orthogonal_cell"])
        with self.assertRaisesRegex(ValueError, "must be a boolean"):
            Gamma({"type": "gamma", "orthogonalize_cell": "false"})
        with self.assertRaisesRegex(ValueError, "disagree"):
            Gamma(
                {
                    "type": "gamma",
                    "require_orthogonal_cell": True,
                    "orthogonalize_cell": False,
                }
            )

        gamma = Gamma({"type": "gamma", "displacement_points": [0.5, 0.0]})
        self.assertEqual([0.0, 0.5], gamma.displacement_points)

    def test_nonrecommended_system_warns_and_uses_geometric_check(self):
        self.gamma.structure_type = "fcc"
        self.gamma.plane_miller = [0, 0, 1]
        self.gamma.slip_direction = [1, 0, 0]
        self.gamma.slip_length = None
        structure = Structure(
            Lattice.cubic(4.0),
            ["Al"],
            [[0.0, 0.0, 0.0]],
        )

        with self.assertLogs(level="WARNING") as captured:
            plane, direction, slip_length, _ = (
                self.gamma._Gamma__convert_input_miller(structure)
            )

        self.assertEqual(plane, (0, 0, 1))
        self.assertEqual(direction, (1, 0, 0))
        self.assertEqual(slip_length, 1)
        self.assertTrue(
            any(
                "falling back to a geometric construction" in message
                for message in captured.output
            )
        )

        self.gamma.slip_direction = [1, 0, 1]
        with self.assertRaisesRegex(RuntimeError, "is not on plane"):
            self.gamma._Gamma__convert_input_miller(structure)

    def test_refine_style_gamma_defaults_cal_type(self):
        gamma = Gamma({
            "type": "gamma",
            "init_from_suffix": "00",
            "output_suffix": "01",
        })
        self.assertEqual(gamma.cal_type, "relaxation")

    def test_make_confs_bcc(self):
        if not os.path.exists(os.path.join(self.equi_path, "CONTCAR")):
            with self.assertRaises(RuntimeError):
                self.gamma.make_confs(self.target_path, self.equi_path)
        shutil.copy(
            os.path.join(self.source_path, "CONTCAR_Mo_bcc"),
            os.path.join(self.equi_path, "CONTCAR"),
        )
        with self.assertRaisesRegex(RuntimeError, "result.json"):
            self.gamma.make_confs(self.target_path, self.equi_path)
        with open(os.path.join(self.equi_path, "result.json"), "w", encoding="utf-8") as fp:
            fp.write('{"energies": [-1.0], "atom_numbs": [1]}')
        task_list = self.gamma.make_confs(self.target_path, self.equi_path)
        dfm_dirs = glob.glob(os.path.join(self.target_path, "task.*"))
        self.assertEqual(len(dfm_dirs), self.gamma.n_steps + 1)
        self.assertTrue(
            os.path.isfile(
                os.path.join(self.target_path, "slab_generation.json")
            )
        )

        incar0 = Incar.from_file(os.path.join("vasp_input", "INCAR.rlx"))
        incar0["ISIF"] = 4

        self.assertEqual(
            os.path.realpath(os.path.join(self.equi_path, "CONTCAR")),
            os.path.realpath(os.path.join(self.target_path, "POSCAR")),
        )
        ref_st = Structure.from_file(os.path.join(self.target_path, "POSCAR"))
        dfm_dirs.sort()
        for ii in dfm_dirs:
            st_file = os.path.join(ii, "POSCAR")
            self.assertTrue(os.path.isfile(st_file))
            st0 = Structure.from_file(st_file)
            st1_file = os.path.join(ii, "POSCAR.tmp")
            self.assertTrue(os.path.isfile(st1_file))
            st1 = Structure.from_file(st1_file)
            with open(st1_file, mode="r") as f:
                z_coord_str = f.readlines()[-1].split()[-2]
                z_coord = float(z_coord_str)
            self.assertTrue(z_coord <= 1)

    def test_static_gamma_defaults_without_add_fix(self):
        gamma = Gamma({
            "type": "gamma",
            "cal_type": "static",
            "plane_miller": [0, 0, 1],
            "slip_direction": [1, 0, 0],
        })
        self.assertIsNone(gamma.add_fix)

    def test_incompatible_lammps_add_fix_fails_clearly(self):
        gamma = Gamma({
            "type": "gamma",
            "cal_type": "static",
            "plane_miller": [0, 0, 1],
            "slip_direction": [1, 0, 0],
            "add_fix": ["true", "true", "false"],
        })
        task_dir = os.path.join(self.target_path, "task.000000")
        os.makedirs(task_dir, exist_ok=True)
        with open(os.path.join(task_dir, "inter.json"), "w", encoding="utf-8") as fp:
            fp.write('{"type": "deepmd"}')
        with open(os.path.join(task_dir, "in.lammps"), "w", encoding="utf-8") as fp:
            fp.write("run 0\n")

        with self.assertRaisesRegex(RuntimeError, "add_fix was requested"):
            gamma.post_process([task_dir])

    def test_compute_lower(self):
        cwd = os.getcwd()
        output_file = os.path.join(cwd, "output/gamma_00/result.json")
        all_tasks = glob.glob("output/gamma_00/task.*")
        all_tasks.sort()
        all_res = [os.path.join(task, "result_task.json") for task in all_tasks]

        self.gamma._compute_lower(output_file, all_tasks, all_res)

        self.assertTrue(os.path.isfile(self.res_data))


def test_gamma_compute_lower_uses_interface_count_and_fixed_area(tmp_path):
    prop_dir = tmp_path / "conf" / "gamma_00"
    task0 = prop_dir / "task.000000"
    task1 = prop_dir / "task.000001"
    equi_dir = tmp_path / "conf" / "relaxation" / "relax_task"
    task0.mkdir(parents=True)
    task1.mkdir(parents=True)
    equi_dir.mkdir(parents=True)
    dumpfn({"energies": [-2.0], "atom_numbs": [2]}, equi_dir / "result.json")
    cell = [[2.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 10.0]]
    for task, energy, displacement in (
        (task0, -10.0, 0.0),
        (task1, -9.0, 0.5),
    ):
        dumpfn(
            {"energies": [energy], "atom_numbs": [2], "cells": [cell]},
            task / "result_task.json",
        )
        dumpfn([1, 1, 0], task / "miller.json")
        dumpfn(1.0, task / "slip_length.json")
        dumpfn(displacement, task / "normalized_displacement.json")
        dumpfn(
            {"slab_geometry": {"interface_count": 2}},
            task / "gamma_geometry.json",
        )
    prop = Gamma(
        {
            "type": "gamma",
            "plane_miller": [1, 1, 0],
            "slip_direction": [-1, 1, 1],
            "vacuum_size": 0.0,
        }
    )
    result, _ = prop._compute_lower(
        str(prop_dir / "result.json"), [str(task0), str(task1)], {}
    )
    expected = 1.0 / (2.0 * 2.0) * 16.0217657
    assert result[0.5][1] == pytest.approx(expected)

    changed_cell = [[2.1, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 10.0]]
    dumpfn(
        {"energies": [-9.0], "atom_numbs": [2], "cells": [changed_cell]},
        task1 / "result_task.json",
    )
    with pytest.raises(RuntimeError, match="in-plane area changed"):
        prop._compute_lower(
            str(prop_dir / "result.json"), [str(task0), str(task1)], {}
        )
