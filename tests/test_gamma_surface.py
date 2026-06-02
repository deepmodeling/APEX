import glob
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pytest
from monty.serialization import dumpfn, loadfn

from apex.core.calculator.Lammps import Lammps
from apex.core.property.GammaSurface import GammaSurface

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
__package__ = "tests"


class TestGammaSurface(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        os.chdir(os.path.abspath(os.path.dirname(__file__)))

        self.equi_path = "confs/hp-Mo/relaxation/relax_task"
        self.source_path = "equi/vasp"
        self.target_path = "confs/hp-Mo/gamma_surface_00"

        if not os.path.exists(self.equi_path):
            os.makedirs(self.equi_path)
        if not os.path.exists(self.target_path):
            os.makedirs(self.target_path)

        self.prop_param = {
            "type": "gamma_surface",
            "plane_miller": [0, 0, 1],
            "slip_direction": [1, 0, 0],
            "supercell_size": [1, 1, 8],
            "vacuum_size": 10,
            "add_fix": ["true", "true", "false"],
            "n_steps_x": 2,
            "n_steps_y": 1,
        }
        self.gamma_surface = GammaSurface(self.prop_param)

    def tearDown(self):
        if os.path.exists(self.equi_path):
            shutil.rmtree(self.equi_path)
        if os.path.exists(self.target_path):
            shutil.rmtree(self.target_path)
        os.chdir(self._cwd)

    def test_task_type(self):
        self.assertEqual("gamma_surface", self.gamma_surface.task_type())

    def test_task_param(self):
        self.assertEqual(self.prop_param, self.gamma_surface.task_param())

    def test_make_confs_bcc(self):
        if not os.path.exists(os.path.join(self.equi_path, "CONTCAR")):
            with self.assertRaises(RuntimeError):
                self.gamma_surface.make_confs(self.target_path, self.equi_path)

        shutil.copy(
            os.path.join(self.source_path, "CONTCAR_Mo_bcc"),
            os.path.join(self.equi_path, "CONTCAR"),
        )
        task_list = self.gamma_surface.make_confs(self.target_path, self.equi_path)
        dfm_dirs = glob.glob(os.path.join(self.target_path, "task.*"))
        self.assertEqual(len(dfm_dirs), (self.gamma_surface.n_steps_x + 1) * (self.gamma_surface.n_steps_y + 1))
        self.assertEqual(len(task_list), len(dfm_dirs))

        pairs = set()
        for ii in sorted(dfm_dirs):
            self.assertTrue(os.path.isfile(os.path.join(ii, "POSCAR")))
            self.assertTrue(os.path.isfile(os.path.join(ii, "miller.json")))
            self.assertTrue(os.path.isfile(os.path.join(ii, "displacement.json")))
            disp = loadfn(os.path.join(ii, "displacement.json"))
            pairs.add((disp["frac_x"], disp["frac_y"]))

        self.assertIn((0.0, 0.0), pairs)
        self.assertIn((1.0, 1.0), pairs)

    def test_legacy_n_steps_aliases_to_n_steps_x(self):
        prop = GammaSurface(
            {
                "type": "gamma_surface",
                "plane_miller": [0, 0, 1],
                "slip_direction": [1, 0, 0],
                "n_steps": 3,
            }
        )

        self.assertEqual(prop.n_steps_x, 3)
        self.assertEqual(prop.n_steps, 3)
        self.assertEqual(prop.task_param()["n_steps_x"], 3)


class TestGammaSurfaceLammps(unittest.TestCase):
    def setUp(self):
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        self.source_poscar = os.path.join(root, "tests/equi/lammps/Al-fcc.vasp")
        self.model = os.path.join(root, "tests/lammps_input/frozen_model.pb")
        self.tempdir = tempfile.TemporaryDirectory(prefix="gamma_surface_lammps_", dir="/tmp")
        self.task_dir = os.path.join(self.tempdir.name, "gamma_surface_00", "task.000000")
        os.makedirs(self.task_dir, exist_ok=True)
        shutil.copy(self.source_poscar, os.path.join(self.task_dir, "POSCAR"))

        self.inter_param = {
            "type": "deepmd",
            "model": self.model,
            "type_map": {"Al": 0},
        }
        self.calc = Lammps(self.inter_param, self.source_poscar)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_make_input_file(self):
        prop = GammaSurface(
            {
                "type": "gamma_surface",
                "plane_miller": [0, 0, 1],
                "slip_direction": [1, 0, 0],
                "supercell_size": [1, 1, 5],
                "vacuum_size": 10,
                "add_fix": ["true", "true", "false"],
                "n_steps_x": 2,
                "n_steps_y": 2,
            },
            self.inter_param,
        )

        self.calc.make_potential_files(self.task_dir)
        cwd = os.getcwd()
        os.chdir(self.task_dir)
        try:
            self.calc.make_input_file(
                self.task_dir, prop.task_type(), prop.task_param()
            )
        finally:
            os.chdir(cwd)

        common_input = os.path.join(self.tempdir.name, "gamma_surface_00", "in.lammps")
        task_input = os.path.join(self.task_dir, "in.lammps")

        self.assertTrue(os.path.isfile(common_input))
        self.assertTrue(os.path.islink(task_input))
        with open(common_input, "r") as fp:
            contents = fp.read()

        self.assertIn("read_data   conf.lmp", contents)
        self.assertIn("pair_style deepmd", contents)
        self.assertIn("pair_coeff * * Al", contents)
        self.assertIn("fix             1 all setforce 0 0 NULL", contents)
        self.assertLess(
            contents.index("min_style       cg"),
            contents.index("fix             1 all setforce 0 0 NULL"),
        )
        self.assertLess(
            contents.index("fix             1 all setforce 0 0 NULL"),
            contents.index("minimize"),
        )

    def test_missing_add_fix_falls_back_to_generic_relaxation(self):
        prop = GammaSurface(
            {
                "type": "gamma_surface",
                "init_from_suffix": "00",
                "output_suffix": "01",
            },
            self.inter_param,
        )

        self.calc.make_potential_files(self.task_dir)
        cwd = os.getcwd()
        os.chdir(self.task_dir)
        try:
            self.calc.make_input_file(self.task_dir, prop.task_type(), prop.task_param())
        finally:
            os.chdir(cwd)

        common_input = os.path.join(self.tempdir.name, "gamma_surface_00", "in.lammps")
        with open(common_input, "r") as fp:
            contents = fp.read()

        self.assertIn("min_style       cg", contents)
        self.assertIn("minimize", contents)
        self.assertNotIn("fix             1 all setforce", contents)


def test_gamma_surface_reproduce_defaults_to_static_calculation():
    prop = GammaSurface({"type": "gamma_surface", "reproduce": True})

    assert prop.reprod is True
    assert prop.cal_type == "static"
    assert prop.init_from_suffix == "00"
    assert prop.cal_setting == {
        "relax_pos": False,
        "relax_shape": False,
        "relax_vol": False,
    }


def test_gamma_surface_default_cal_setting_fills_missing_values():
    prop = GammaSurface(
        {
            "type": "gamma_surface",
            "plane_miller": [0, 0, 1],
            "slip_direction": [1, 0, 0],
            "cal_setting": {"relax_pos": False},
        },
        {"type": "lammps"},
    )

    assert prop.cal_type == "relaxation"
    assert prop.cal_setting == {
        "relax_pos": False,
        "relax_shape": False,
        "relax_vol": False,
    }
    assert prop.supercell_size == (1, 1, 5)
    assert prop.vacuum_size == 0
    assert prop.add_fix == ["true", "true", "false"]


def test_gamma_surface_resolve_equilibrium_structure_for_vasp_and_abacus(monkeypatch):
    vasp_prop = GammaSurface({"type": "gamma_surface"}, {"type": "vasp"})
    assert vasp_prop._resolve_equilibrium_structure("/work/relax") == (
        "/work/relax/CONTCAR",
        "POSCAR",
    )

    monkeypatch.setattr(
        "apex.core.property.GammaSurface.abacus_utils.final_stru",
        lambda path: "STRU_ION_D",
    )
    abacus_prop = GammaSurface({"type": "gamma_surface"}, {"type": "abacus"})
    assert abacus_prop._resolve_equilibrium_structure("/work/relax") == (
        "/work/relax/STRU_ION_D",
        "STRU",
    )


def test_gamma_surface_resolve_slip_length_numeric_vector_and_invalid():
    prop = GammaSurface({"type": "gamma_surface"})
    resolve = prop._GammaSurface__resolve_slip_length

    assert resolve(2, 3.0, 4.0, 5.0) == 6.0
    assert resolve([1, 1, 0], 3.0, 4.0, 5.0) == pytest.approx(5.0)
    with pytest.raises(RuntimeError, match="Only int"):
        resolve("bad", 3.0, 4.0, 5.0)


def test_gamma_surface_post_process_injects_lammps_setforce(tmp_path):
    task = tmp_path / "task.000000"
    task.mkdir()
    in_lammps = task / "in.lammps"
    in_lammps.write_text(
        "clear\n"
        "min_style       cg\n"
        "delete this line\n"
        "variable        N equal count(all)\n"
    )
    prop = GammaSurface(
        {
            "type": "gamma_surface",
            "plane_miller": [0, 0, 1],
            "slip_direction": [1, 0, 0],
            "add_fix": ["true", "false", "true"],
        },
        {"type": "lammps"},
    )

    prop.post_process([str(task)])

    text = in_lammps.read_text()
    assert "fix             1 all setforce 0 NULL 0" in text


def test_gamma_surface_compute_lower_with_synthetic_results(tmp_path):
    prop_dir = tmp_path / "conf" / "gamma_surface_00"
    task0 = prop_dir / "task.000000"
    task1 = prop_dir / "task.000001"
    equi_dir = tmp_path / "conf" / "relaxation" / "relax_task"
    task0.mkdir(parents=True)
    task1.mkdir(parents=True)
    equi_dir.mkdir(parents=True)

    cell = np.eye(3).tolist()
    dumpfn({"energies": [-2.0], "atom_numbs": [2]}, equi_dir / "result.json")
    for task, energy, frac_x, frac_y in [
        (task0, -2.0, 0.0, 0.0),
        (task1, -1.5, 0.5, 1.0),
    ]:
        dumpfn({"energies": [energy], "atom_numbs": [2], "cells": [cell]}, task / "result_task.json")
        dumpfn([0, 0, 1], task / "miller.json")
        dumpfn({"frac_x": frac_x, "frac_y": frac_y}, task / "displacement.json")
    dumpfn(2.0, task0 / "slip_length_x.json")
    dumpfn(3.0, task0 / "slip_length_y.json")

    prop = GammaSurface(
        {
            "type": "gamma_surface",
            "plane_miller": [0, 0, 1],
            "slip_direction": [1, 0, 0],
        }
    )
    res_data, ptr_data = prop._compute_lower(
        str(prop_dir / "result.json"),
        [str(task1), str(task0)],
        {},
    )

    assert "Stacking_Fault_E" in ptr_data
    assert res_data["0.000000,0.000000"] == [0.0, 0.0, 0.0, -1.0, -1.0]
    assert res_data["0.500000,1.000000"][0:2] == [1.0, 3.0]
    assert res_data["0.500000,1.000000"][2] == pytest.approx(8.01088285)
    assert (prop_dir / "result.json").is_file()


class TestGammaSurfaceCoverage(unittest.TestCase):
    def test_gamma_surface_reproduce_defaults_to_static_calculation(self):
        test_gamma_surface_reproduce_defaults_to_static_calculation()

    def test_gamma_surface_default_cal_setting_fills_missing_values(self):
        test_gamma_surface_default_cal_setting_fills_missing_values()

    def test_gamma_surface_resolve_equilibrium_structure_for_vasp_and_abacus(self):
        monkeypatch = pytest.MonkeyPatch()
        try:
            test_gamma_surface_resolve_equilibrium_structure_for_vasp_and_abacus(
                monkeypatch
            )
        finally:
            monkeypatch.undo()

    def test_gamma_surface_resolve_slip_length_numeric_vector_and_invalid(self):
        test_gamma_surface_resolve_slip_length_numeric_vector_and_invalid()

    def test_gamma_surface_post_process_injects_lammps_setforce(self):
        with tempfile.TemporaryDirectory() as tmp:
            test_gamma_surface_post_process_injects_lammps_setforce(Path(tmp))

    def test_gamma_surface_compute_lower_with_synthetic_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            test_gamma_surface_compute_lower_with_synthetic_results(Path(tmp))


if __name__ == "__main__":
    unittest.main()
