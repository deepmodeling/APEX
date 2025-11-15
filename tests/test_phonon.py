import glob
import os
import shutil
import sys
import unittest

import dpdata
import numpy as np
from monty.serialization import loadfn
from pymatgen.io.vasp import Incar
from apex.core.property.Phonon import Phonon

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
__package__ = "tests"


class TestPhonon(unittest.TestCase):
    def setUp(self):
        _jdata = {
            "structures": ["confs/std-bcc"],
            "interaction": {
                "type": "vasp",
                "potcar_prefix": "vasp_input",
                "potcars": {"Mo": "POTCAR_Mo"},
            },
            "properties": [
                {
                    "type": "phonon",
                    "skip": False,
                    "BAND": "0.0000 0.0000 0.5000  0.0000 0.0000 0.0000  0.5000 -0.5000 0.5000  0.25000 0.2500 0.2500  0 0 0",
                    "supercell_matrix": [2, 2, 2]
                },
            ],
        }

        self.equi_path = "confs/hp-Mo/relaxation/relax_task"
        self.source_path = "equi/vasp"
        self.target_path = "confs/hp-Mo/phonon_00"
        self.res_data = "output/phonon_00/result.json"
        self.ptr_data = "output/phonon_00/result.out"

        if not os.path.exists(self.equi_path):
            os.makedirs(self.equi_path)
        if not os.path.exists(self.target_path):
            os.makedirs(self.target_path)

        self.confs = _jdata["structures"]
        self.inter_param = _jdata["interaction"]
        self.prop_param = _jdata["properties"]

        self.phonon = Phonon(_jdata["properties"][0])

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
        self.assertEqual("phonon", self.phonon.task_type())

    def test_task_param(self):
        self.assertEqual(self.prop_param[0], self.phonon.task_param())

    def test_make_phonon_conf(self):
        if not os.path.exists(os.path.join(self.equi_path, "CONTCAR")):
            with self.assertRaises(RuntimeError):
                self.phonon.make_confs(self.target_path, self.equi_path)
        shutil.copy(
            os.path.join(self.source_path, "CONTCAR_Mo_bcc"),
            os.path.join(self.equi_path, "CONTCAR"),
        )
        task_list = self.phonon.make_confs(self.target_path, self.equi_path)
        dfm_dirs = glob.glob(os.path.join(self.target_path, "task.*"))
        self.assertEqual(len(dfm_dirs), 1)
        self.assertTrue(os.path.isfile(os.path.join(self.target_path, "phonopy_disp.yaml")))
        self.assertTrue(os.path.isfile(os.path.join(self.target_path, "task.000000/band.conf")))
