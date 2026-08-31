import glob
import json
import os
import shutil
import sys
import tempfile
import unittest

import numpy as np
from monty.serialization import loadfn
from pymatgen.io.vasp import Incar

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
__package__ = "tests"

from apex.core.calculator.VASP import VASP
from apex.core.calculator.lib.vasp_utils import (
    incar_upper,
    regulate_poscar,
    sort_poscar,
)


class TestVASP(unittest.TestCase):
    def setUp(self):
        self.jdata = {
            "structures": ["confs/hp-*"],
            "interaction": {
                "type": "vasp",
                "incar": "vasp_input/INCAR",
                "potcar_prefix": ".",
                "potcars": {"Li": "vasp_input/POTCAR"},
            },
            "relaxation": {
                "cal_type": "relaxation",
                "cal_setting": {
                    "relax_pos": True,
                    "relax_shape": True,
                    "relax_vol": True,
                },
            },
        }

        self.conf_path = "confs/hp-Li"
        self.equi_path = "confs/hp-Li/relaxation/relax_task"
        self.source_path = "equi/vasp"
        if not os.path.exists(self.equi_path):
            os.makedirs(self.equi_path)

        self.confs = self.jdata["structures"]
        inter_param = self.jdata["interaction"]
        self.task_param = self.jdata["relaxation"]
        self.VASP = VASP(inter_param, os.path.join(self.conf_path, "POSCAR"))

    def tearDown(self):
        if os.path.exists("confs/hp-Li/relaxation"):
            shutil.rmtree("confs/hp-Li/relaxation")
        if os.path.exists("inter.json"):
            os.remove("inter.json")
        if os.path.exists("POTCAR"):
            os.remove("POTCAR")

    def test_make_potential_files(self):
        if not os.path.exists(os.path.join(self.equi_path, "POSCAR")):
            with self.assertRaises(FileNotFoundError):
                self.VASP.make_potential_files(self.equi_path)
        shutil.copy(
            os.path.join(self.conf_path, "POSCAR"),
            os.path.join(self.equi_path, "POSCAR"),
        )
        self.VASP.make_potential_files(self.equi_path)
        self.assertTrue(os.path.isfile(os.path.join(self.equi_path, "POTCAR")))
        self.assertTrue(os.path.isfile(os.path.join(self.equi_path, "inter.json")))

    def test_make_input_file_1(self):
        param = self.task_param.copy()
        param["cal_setting"] = {
            "relax_pos": True,
            "relax_shape": True,
            "relax_vol": False,
        }
        self.VASP.make_input_file(self.equi_path, "relaxation", param)
        incar = incar_upper(Incar.from_file(os.path.join(self.equi_path, "INCAR")))
        self.assertEqual(incar["ISIF"], 4)

    def test_make_input_file_2(self):
        self.VASP.make_input_file(self.equi_path, "relaxation", self.task_param)
        self.assertTrue(os.path.isfile(os.path.join(self.equi_path, "task.json")))
        self.assertTrue(os.path.isfile(os.path.join(self.equi_path, "KPOINTS")))
        self.assertTrue(os.path.isfile(os.path.join(self.equi_path, "INCAR")))
        incar = incar_upper(Incar.from_file(os.path.join(self.equi_path, "INCAR")))
        self.assertEqual(incar["ISIF"], 3)

    def test_make_input_file_3(self):
        param = self.task_param.copy()
        param["cal_setting"] = {
            "relax_pos": True,
            "relax_shape": False,
            "relax_vol": False,
        }
        self.VASP.make_input_file(self.equi_path, "relaxation", param)
        incar = incar_upper(Incar.from_file(os.path.join(self.equi_path, "INCAR")))
        self.assertEqual(incar["ISIF"], 2)

    def test_make_input_file_4(self):
        param = self.task_param.copy()
        param["cal_setting"] = {
            "relax_pos": False,
            "relax_shape": True,
            "relax_vol": False,
        }
        self.VASP.make_input_file(self.equi_path, "relaxation", param)
        incar = incar_upper(Incar.from_file(os.path.join(self.equi_path, "INCAR")))
        self.assertEqual(incar["ISIF"], 5)

    def test_make_input_file_5(self):
        param = self.task_param.copy()
        param["cal_setting"] = {
            "relax_pos": False,
            "relax_shape": True,
            "relax_vol": True,
        }
        self.VASP.make_input_file(self.equi_path, "relaxation", param)
        incar = incar_upper(Incar.from_file(os.path.join(self.equi_path, "INCAR")))
        self.assertEqual(incar["ISIF"], 6)

    def test_make_input_file_5(self):
        param = self.task_param.copy()
        param["cal_setting"] = {
            "relax_pos": False,
            "relax_shape": True,
            "relax_vol": True,
            "kspacing": 0.01,
        }
        self.VASP.make_input_file(self.equi_path, "relaxation", param)
        incar = incar_upper(Incar.from_file(os.path.join(self.equi_path, "INCAR")))
        self.assertEqual(incar["ISIF"], 6)
        self.assertEqual(incar["KSPACING"], 0.01)

    def test_make_input_file_gruneisen_linear_uses_dfpt_incar(self):
        param = {
            "type": "gruneisen",
            "cal_type": "static",
            "approach": "linear",
            "cal_setting": {
                "relax_pos": True,
                "relax_shape": False,
                "relax_vol": False,
                "encut": 400,
                "ediff": 1e-6,
                "kspacing": 0.25,
                "kgamma": False,
            },
        }
        shutil.copy(
            os.path.join(self.conf_path, "POSCAR"),
            os.path.join(self.equi_path, "POSCAR"),
        )

        self.VASP.make_input_file(self.equi_path, "gruneisen", param)

        incar = incar_upper(Incar.from_file(os.path.join(self.equi_path, "INCAR")))
        self.assertEqual(incar["IBRION"], 8)
        self.assertEqual(incar["NSW"], 1)
        self.assertEqual(incar["ISIF"], 2)
        self.assertEqual(incar["ENCUT"], 400)
        self.assertEqual(incar["KSPACING"], 0.25)
        self.assertFalse(incar["KGAMMA"])

    def test_compute(self):
        ret = self.VASP.compute(os.path.join(self.conf_path, "relaxation"))
        self.assertIsNone(ret)
        shutil.copy(
            os.path.join(self.source_path, "OUTCAR"),
            os.path.join(self.equi_path, "OUTCAR"),
        )
        ret = self.VASP.compute(self.equi_path)
        ret_ref = loadfn(os.path.join(self.source_path, "outcar.json"))

        def compare_dict(dict1, dict2):
            self.assertEqual(dict1.keys(), dict2.keys())
            for key in dict1:
                if key == "stress":
                    self.assertTrue((np.array(dict1[key]["data"]) == dict2[key]).all())
                elif type(dict1[key]) is dict:
                    compare_dict(dict1[key], dict2[key])
                else:
                    if type(dict1[key]) is np.ndarray:
                        self.assertTrue((dict1[key] == dict2[key]).all())
                    else:
                        self.assertTrue(dict1[key] == dict2[key])

        compare_dict(ret, ret_ref.as_dict())

    def test_backward_files(self):
        backward_files = ["OUTCAR", "outlog", "CONTCAR", "OSZICAR", "XDATCAR"]
        self.assertEqual(self.VASP.backward_files(), backward_files)
        self.assertEqual(
            self.VASP.backward_files("gruneisen"),
            ["OUTCAR", "outlog", "CONTCAR", "OSZICAR", "XDATCAR", "vasprun.xml"],
        )


class TestVASPPoscarUtilities(unittest.TestCase):
    def test_regulate_and_sort_preserve_selective_dynamics(self):
        contents = """TiV selective
1.0
2 0 0
0 2 0
0 0 2
V Ti V
1 1 1
Selective dynamics
Direct
0.0 0.0 0.0 F F T V
0.5 0.5 0.5 F F T Ti
0.25 0.25 0.25 F F T V
"""
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, "POSCAR.in")
            regulated = os.path.join(tmp, "POSCAR.regulated")
            sorted_path = os.path.join(tmp, "POSCAR.sorted")
            with open(source, "w") as fp:
                fp.write(contents)

            regulate_poscar(source, regulated)
            sort_poscar(regulated, sorted_path, ["Ti", "V"])

            with open(sorted_path) as fp:
                lines = fp.read().splitlines()
            self.assertEqual(lines[5], "Ti V")
            self.assertEqual(lines[6], "1 2")
            self.assertEqual(lines[7], "Selective dynamics")
            self.assertEqual(lines[8], "Direct")
            self.assertEqual(lines[9].split()[-4:], ["F", "F", "T", "Ti"])
            self.assertTrue(all(line.split()[-4:-1] == ["F", "F", "T"] for line in lines[9:12]))
