import glob
import json
import os
import shutil
import sys
import unittest

import dpdata
import numpy as np
from monty.serialization import dumpfn, loadfn

from apex.core.calculator.Lammps import Lammps
from apex.core.calculator.lib import lammps_utils
from apex.core.calculator.lib.lammps_utils import inter_deepmd

#from .context import make_kspacing_kpoints, setUpModule

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
__package__ = "tests"


class TestLammps(unittest.TestCase):
    def setUp(self):
        self.jdata = {
            "structures": ["confs/std-fcc"],
            "interaction": {
                "type": "deepmd",
                "model": "lammps_input/frozen_model.pb",
                "deepmd_version": "1.1.0",
                "type_map": {"Al": 0},
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

        self.equi_path = "confs/std-fcc/relaxation/relax_task"
        self.source_path = "equi/lammps"

        if not os.path.exists(self.equi_path):
            os.makedirs(self.equi_path)

        if not os.path.isfile(os.path.join(self.equi_path, "POSCAR")):
            shutil.copy(
                os.path.join(self.source_path, "Al-fcc.vasp"),
                os.path.join("confs/std-fcc", "POSCAR"),
            )

        self.confs = self.jdata["structures"]
        self.inter_param = self.jdata["interaction"]
        self.relax_param = self.jdata["relaxation"]
        self.Lammps = Lammps(
            self.inter_param, os.path.join(self.source_path, "Al-fcc.vasp")
        )

    def tearDown(self):
        if os.path.exists("confs/std-fcc/relaxation"):
            shutil.rmtree("confs/std-fcc/relaxation")

    def test_set_inter_type_func(self):
        self.Lammps.set_inter_type_func()
        self.assertEqual(inter_deepmd, self.Lammps.inter_func)

    def test_set_model_param(self):
        self.Lammps.set_model_param()
        model_param = {
            "type": "deepmd",
            "model_name": ["frozen_model.pb"],
            "param_type": {"Al": 0},
            "deepmd_version": "1.1.0",
        }
        self.assertEqual(model_param, self.Lammps.model_param)

    def test_make_potential_files(self):
        cwd = os.getcwd()
        abs_equi_path = os.path.abspath(self.equi_path)
        self.Lammps.make_potential_files(abs_equi_path)
        self.assertTrue(os.path.islink(os.path.join(self.equi_path, "frozen_model.pb")))
        self.assertTrue(os.path.isfile(os.path.join(self.equi_path, "inter.json")))
        ret = loadfn(os.path.join(self.equi_path, "inter.json"))
        self.assertEqual(self.inter_param, ret)
        os.chdir(cwd)

    def test_make_input_file(self):
        cwd = os.getcwd()
        abs_equi_path = os.path.abspath("confs/std-fcc/relaxation/relax_task")
        shutil.copy(
            os.path.join("confs/std-fcc", "POSCAR"),
            os.path.join(self.equi_path, "POSCAR"),
        )
        self.Lammps.make_input_file(abs_equi_path, "relaxation", self.relax_param)
        self.assertTrue(os.path.isfile(os.path.join(abs_equi_path, "conf.lmp")))
        self.assertTrue(os.path.islink(os.path.join(abs_equi_path, "in.lammps")))
        self.assertTrue(os.path.isfile(os.path.join(abs_equi_path, "task.json")))
        with open(os.path.join(abs_equi_path, "in.lammps"), "r") as fp:
            contents = fp.read()
        self.assertIn("variable        N equal count(all)", contents)
        self.assertNotIn("variable        N equal step", contents)

    def test_make_static_eval_input_does_not_require_annealing_variables(self):
        self.Lammps.set_model_param()
        contents = lammps_utils.make_lammps_eval(
            "conf.lmp",
            self.Lammps.type_map,
            self.Lammps.inter_func,
            self.Lammps.model_param,
        )

        self.assertIn(
            "thermo_style    custom step pe pxx pyy pzz pxy pxz pyz lx ly lz vol c_mype",
            contents,
        )
        self.assertIn("variable        N equal count(all)", contents)
        self.assertNotIn("variable        N equal step", contents)
        self.assertNotIn("timestep ${timestep}", contents)
        self.assertNotIn("variable        stepVal equal step", contents)
        self.assertNotIn("compute         myRDF", contents)

    def test_make_annealing_input_defines_logged_variables_and_rdf_fixes(self):
        self.Lammps.set_model_param()
        contents = lammps_utils.make_lammps_annealing(
            "conf.lmp",
            self.Lammps.type_map,
            self.Lammps.inter_func,
            self.Lammps.model_param,
            {},
        )

        self.assertIn("timestep ${timestep}", contents)
        self.assertIn("variable        N equal count(all)", contents)
        self.assertIn("variable        Vatom equal v_V/count(all)", contents)
        self.assertIn("variable        Temp equal temp", contents)
        self.assertIn("variable        stepVal equal step", contents)
        self.assertIn(
            "compute         myRDF all rdf ${rdf_bins} cutoff ${rdf_cutoff}",
            contents,
        )
        self.assertIn("fix rdf_ramp all ave/time", contents)
        self.assertIn("fix heat_log all ave/time", contents)
        self.assertIn("fix rdf_cool all ave/time", contents)
        self.assertIn("fix cool_log all ave/time", contents)
        self.assertNotIn("fix heat_log all print", contents)
        self.assertNotIn("fix cool_log all print", contents)
        self.assertLess(contents.index("fix rdf_ramp"), contents.index("unfix rdf_ramp"))
        self.assertLess(contents.index("fix rdf_cool"), contents.index("unfix rdf_cool"))

    def test_forward_common_files(self):
        fc_files = ["in.lammps", "frozen_model.pb"]
        self.assertEqual(self.Lammps.forward_common_files(), fc_files)

    def test_backward_files(self):
        backward_files = [
            "log.lammps",
            "outlog",
            "apex_task_status.json",
            ".debug.log",
            ".debug.stdout",
            ".debug.stderr",
            "dump.relax",
        ]
        self.assertEqual(self.Lammps.backward_files(), backward_files)

    def test_annealing_backward_files_include_generated_artifacts(self):
        backward_files = self.Lammps.backward_files("annealing")
        for filename in [
            "heating_interval.dat",
            "cooling_interval.dat",
            "rdf_ramp.dat",
            "rdf_cool.dat",
        ]:
            self.assertIn(filename, backward_files)
