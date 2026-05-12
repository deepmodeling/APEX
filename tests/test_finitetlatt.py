import glob
import os
import shutil
import sys
import unittest

from monty.serialization import loadfn

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
__package__ = "tests"

from apex.core.property.FiniteTlatt import FiniteTlatt
from apex.core.calculator.Lammps import Lammps


class TestFiniteTlatt(unittest.TestCase):
    def setUp(self):
        base = {
            "structures": ["confs/hcp-Ti"],
            "interaction": {
                "type": "meam_spline",
                "model": "lammps_input/Ti.meam.spline",
                "type_map": {"Ti": 0}
            },
            "properties": [
                {
                    "type": "finite_t_latt",
                    "supercell_size":  [2, 2, 2],
                    "cal_setting":{
                        "temperature": [400, 600],
                        "equi_step": 4000,
                        "N_every": 100,
                        "N_repeat": 5,
                        "N_freq": 1000,
                        "ave_step": 4000
                    }
                }
            ],
        }

        self.equi_path = "confs/hcp-Ti/relaxation/relax_task"
        self.source_path = "equi/lammps"
        self.target_path = "confs/hcp-Ti/FiniteTlatt_00"

        if not os.path.exists(self.equi_path):
            os.makedirs(self.equi_path)

        self.confs = base["structures"]
        self.inter_param = base["interaction"]
        self.prop_param = base["properties"]

        self.finite = FiniteTlatt(self.prop_param[0])
        self.lammps = Lammps(
            self.inter_param, os.path.join(self.source_path, "hcp-Ti-CONTCAR")
        )

    def tearDown(self):
        if os.path.exists(os.path.abspath(os.path.join(self.equi_path, ".."))):
            shutil.rmtree(os.path.abspath(os.path.join(self.equi_path, "..")))
        if os.path.exists(self.equi_path):
            shutil.rmtree(self.equi_path)
        if os.path.exists(self.target_path):
            shutil.rmtree(self.target_path)

    def test_task_type(self):
        self.assertEqual("finite_t_latt", self.finite.task_type())

    def test_task_param(self):
        self.assertEqual(self.prop_param[0], self.finite.task_param())

    def test_make_potential_files(self):
        cwd = os.getcwd()
        abs_equi_path = os.path.abspath(self.equi_path)
        self.lammps.make_potential_files(abs_equi_path)
        self.assertTrue(os.path.islink(os.path.join(self.equi_path, "Ti.meam.spline")))
        self.assertTrue(os.path.isfile(os.path.join(self.equi_path, "inter.json")))
        ret = loadfn(os.path.join(self.equi_path, "inter.json"))
        self.assertEqual(self.inter_param, ret)
        os.chdir(cwd)

    def test_make_confs(self):
        if not os.path.exists(os.path.join(self.equi_path, "CONTCAR")):
            with self.assertRaises(RuntimeError):
                self.finite.make_confs(self.target_path, self.equi_path)
        shutil.copy(
            os.path.join(self.source_path, "hcp-Ti-CONTCAR"),
            os.path.join(self.equi_path, "CONTCAR"),
        )

        task_list = self.finite.make_confs(self.target_path, self.equi_path)
        self.assertEqual(len(task_list), 2)
        dfm_dirs = glob.glob(os.path.join(self.target_path, "task.*"))
        num = 0
        dfm_dirs.sort()

        for ii in dfm_dirs:
            self.assertTrue(os.path.isfile(os.path.join(ii, "POSCAR")))
            self.assertFalse(os.path.exists(os.path.join(ii, "POSCAR.tmp")))
            FiniteTlatt_json_file = os.path.join(ii, "FiniteTlatt.json")
            self.assertTrue(os.path.isfile(FiniteTlatt_json_file))
            variable_FiniteTlatt_file = os.path.join(ii, "variable_FiniteTlatt.in")
            self.assertTrue(os.path.isfile(variable_FiniteTlatt_file))
            with open(variable_FiniteTlatt_file, 'r') as file:
                lines = file.readlines()
                temp = lines[1].strip()
            self.assertEqual(temp, "variable temperature equal %.2f" % self.prop_param[0]["cal_setting"]["temperature"][num])
            num += 1

    def test_forward_common_files(self):
        fc_files = ["in.lammps", "variable_FiniteTlatt.in", "Ti.meam.spline"]
        self.assertEqual(self.lammps.forward_common_files(self.prop_param[0]["type"]), fc_files)

    def test_backward_files(self):
        backward_files = [
            "log.lammps",
            "outlog",
            "apex_task_status.json",
            ".debug.log",
            ".debug.stdout",
            ".debug.stderr",
            "dump.relax",
            "average_box.txt",
        ]
        self.assertEqual(self.lammps.backward_files(self.prop_param[0]["type"]), backward_files)

