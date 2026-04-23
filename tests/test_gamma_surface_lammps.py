import os
import shutil
import tempfile
import unittest

from apex.core.calculator.Lammps import Lammps
from apex.core.property.GammaSurface.lammps import GammaSurface


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
        self.assertLess(contents.index("min_style       cg"), contents.index("fix             1 all setforce 0 0 NULL"))
        self.assertLess(contents.index("fix             1 all setforce 0 0 NULL"), contents.index("minimize"))

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


if __name__ == "__main__":
    unittest.main()
