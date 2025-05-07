import glob
import os
import shutil
import sys
import unittest

import dpdata
import numpy as np
from monty.serialization import loadfn
from pymatgen.io.vasp import Incar
from apex.core.property.Cohesive import Cohesive

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
__package__ = "tests"

from apex.reporter.property_report import CohesiveReport

class TestCohesive(unittest.TestCase):
    def setUp(self):
        _jdata = {
            "structures": ["confs/std-fcc"],
            "interaction": {
                "type": "vasp",
                "incar": "vasp_input/INCAR.rlx",
                "potcar_prefix": ".",
                "potcars": {"Li": "vasp_input/POTCAR"},
            },
            "properties": [
                {
                    "type": "cohesive",
                    "skip": False,
                    "latt_start": 0.8,
                    "latt_end": 1.2,
                    "latt_step": 0.01,
                    "latt_abs": False,
                    "cal_setting": {
                        "relax_pos": False,
                        "relax_shape": False,
                        "relax_vol": False,
                        "overwrite_interaction": {
                            "type": "deepmd",
                            "model": "lammps_input/frozen_model.pb",
                            "type_map": {"Al": 0},
                        },
                    },
                }
            ],
        }

        self.equi_path = "confs/std-fcc/relaxation/relax_task"
        self.source_path = "equi/vasp"
        self.target_path = "confs/std-fcc/cohesive_00"
        if not os.path.exists(self.equi_path):
            os.makedirs(self.equi_path)

        self.confs = _jdata["structures"]
        self.inter_param = _jdata["interaction"]
        self.prop_param = _jdata["properties"]

        self.cohesive = Cohesive(_jdata["properties"][0])

    def tearDown(self):
        if os.path.exists(self.equi_path):
            shutil.rmtree(self.equi_path)
        if os.path.exists(self.target_path):
            shutil.rmtree(self.target_path)

    def test_task_type(self):
        self.assertEqual("cohesive", self.cohesive.task_type())

    def test_task_param(self):
        self.assertEqual(self.prop_param[0], self.cohesive.task_param())

    def test_make_confs_0(self):

        if not os.path.exists(os.path.join(self.equi_path, "CONTCAR")):
            with self.assertRaises(RuntimeError):
                self.cohesive.make_confs(self.target_path, self.equi_path)
        shutil.copy(
            os.path.join(self.source_path, "CONTCAR"),
            os.path.join(self.equi_path, "CONTCAR"),
        )
        dfm_dirs = glob.glob(os.path.join(self.target_path, "task.*"))

        incar0 = Incar.from_file(os.path.join("vasp_input", "INCAR.rlx"))
        incar0["ISIF"] = 2
        incar0["NSW"] = 0

        for ii in dfm_dirs:
            self.assertTrue(os.path.isfile(os.path.join(ii, "POSCAR")))
            cohesive_json_file = os.path.join(ii, "cohesive.json")
            self.assertTrue(os.path.isfile(cohesive_json_file))
            cohesive_json = loadfn(cohesive_json_file)
            self.assertEqual(
                os.path.realpath(os.path.join(ii, "POSCAR.orig")),
                os.path.realpath(os.path.join(self.equi_path, "CONTCAR")),
            )
            sys = dpdata.System(os.path.join(ii, "POSCAR"))
            self.assertAlmostEqual(
                cohesive_json["lattice"], np.linalg.norm(sys["cells"][0], axis=1)[0]
            )

    def test_make_confs_1(self):
        self.cohesive.reprod = True
        shutil.copy(
            os.path.join(self.source_path, "CONTCAR"),
            os.path.join(self.equi_path, "CONTCAR"),
        )
        with self.assertRaises(RuntimeError):
            self.cohesive.make_confs(self.target_path, self.equi_path)

class TestCohesiveReport(unittest.TestCase):
    def setUp(self):
        self.res_data = {
            "3.5": {"total_energy": -3.0, "cohesive_energy": -5.0},
            "3.6": {"total_energy": -3.2, "cohesive_energy": -5.2},
            "3.7": {"total_energy": -3.5, "cohesive_energy": -5.5},
            "3.8": {"total_energy": -3.8, "cohesive_energy": -5.8},
            "3.9": {"total_energy": -3.6, "cohesive_energy": -5.6},
            "4.0": {"total_energy": -3.4, "cohesive_energy": -5.4},
        }
        
        self.formatted_data = {}
        for k, v in self.res_data.items():
            if isinstance(v, dict) and "cohesive_energy" in v:
                self.formatted_data[k] = v["cohesive_energy"]
            else:
                self.formatted_data[k] = v

    def test_plotly_graph(self):
        traces, layout = CohesiveReport.plotly_graph(self.res_data, "test_material")
        
        self.assertEqual(len(traces), 2)
        self.assertEqual(traces[0].name, "test_material")
        self.assertEqual(traces[0].mode, "lines+markers")
        
        cohesive_values = list(self.formatted_data.values())
        self.assertEqual(list(traces[0].y), cohesive_values)
        
        self.assertEqual(layout.title.text, "Cohesive Energy")
        self.assertIn("Scaled Lattice Parameter", layout.xaxis.title.text)
        self.assertIn("Cohesive Energy", layout.yaxis.title.text)

    def test_dash_table(self):
        table, df = CohesiveReport.dash_table(self.res_data)
        
        self.assertEqual(len(df), len(self.formatted_data))
        self.assertEqual(len(df.columns), 2)
        
        self.assertIn("Scaled Lattice Parameter (a/a0)", df.columns)
        self.assertIn("Cohesive Energy (eV/atom)", df.columns)
        
        self.assertEqual(len(table.data), len(self.formatted_data))