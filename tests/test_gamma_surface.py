import glob
import os
import shutil
import sys
import unittest

from monty.serialization import loadfn

from apex.core.property.GammaSurface.vasp import GammaSurface

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


if __name__ == "__main__":
    unittest.main()
