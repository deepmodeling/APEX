import glob
import json
import os
import shutil
import sys
import tempfile
import unittest
import warnings
from pathlib import Path

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

    def _make_lammps_eval_input(self):
        self.Lammps.set_model_param()
        return lammps_utils.make_lammps_eval(
            "conf.lmp",
            self.Lammps.type_map,
            self.Lammps.inter_func,
            self.Lammps.model_param,
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

    def test_local_dpa4_pt2_model_is_linked_and_forwarded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            model = root / "alloytongqi.pt2"
            model.write_bytes(b"pt2-test-model")
            output_dir = root / "case" / "relaxation" / "relax_task"
            output_dir.mkdir(parents=True)
            calculator = Lammps(
                {
                    "type": "deepmd",
                    "model": str(model),
                    "type_map": {"Al": 0},
                    "deepmd_runtime": "dpa4_pt2",
                    "deepmd_version": "3.2.0b0",
                },
                os.path.join(self.source_path, "Al-fcc.vasp"),
            )

            calculator.set_model_param()
            calculator.make_potential_files(str(output_dir))

            linked_model = output_dir / model.name
            self.assertTrue(linked_model.is_symlink())
            self.assertEqual(linked_model.read_bytes(), b"pt2-test-model")
            self.assertEqual(calculator.model_param["model_name"], [model.name])
            self.assertEqual(
                calculator.forward_files(), ["conf.lmp", "in.lammps", model.name]
            )
            self.assertEqual(
                calculator.forward_common_files(), ["in.lammps", model.name]
            )

    def test_image_resident_dpa4_pt2_model_keeps_absolute_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp).resolve() / "case" / "relaxation" / "relax_task"
            output_dir.mkdir(parents=True)
            model = "/opt/dpa4-runtime/models/DPA4-alloytongqi/alloytongqi.t4-sm75.pt2"
            interaction = {
                "type": "deepmd",
                "model": model,
                "model_in_image": True,
                "type_map": {"Al": 0},
                "deepmd_runtime": "dpa4_pt2",
                "deepmd_version": "3.2.0b0",
            }
            calculator = Lammps(
                interaction,
                os.path.join(self.source_path, "Al-fcc.vasp"),
            )

            calculator.set_model_param()
            calculator.make_potential_files(str(output_dir))

            self.assertEqual(calculator.model_param["model_name"], [model])
            self.assertIn(model, inter_deepmd(calculator.model_param))
            self.assertFalse((output_dir / Path(model).name).exists())
            self.assertEqual(loadfn(output_dir / "inter.json"), interaction)
            self.assertEqual(calculator.forward_files(), ["conf.lmp", "in.lammps"])
            self.assertEqual(calculator.forward_common_files(), ["in.lammps"])

    def test_image_resident_model_validation_fails_closed(self):
        base = {
            "type": "deepmd",
            "model": "/opt/dpa4-runtime/model.pt2",
            "model_in_image": True,
            "type_map": {"Al": 0},
            "deepmd_runtime": "dpa4_pt2",
        }
        invalid_cases = [
            ({**base, "deepmd_runtime": "legacy"}, "only supported"),
            ({**base, "model": "relative/model.pt2"}, "absolute .pt2"),
            ({**base, "model": "/opt/dpa4-runtime/model.pb"}, "absolute .pt2"),
            ({**base, "model_in_image": "true"}, "must be a boolean"),
        ]

        for interaction, message in invalid_cases:
            with self.subTest(interaction=interaction), self.assertRaisesRegex(
                ValueError, message
            ):
                Lammps(interaction, os.path.join(self.source_path, "Al-fcc.vasp"))

    def test_custom_dpa4_pt2_input_rejects_legacy_plugin_load(self):
        with tempfile.TemporaryDirectory(dir=".") as tmp:
            root = Path(tmp).resolve()
            output_dir = root / "relaxation" / "relax_task"
            output_dir.mkdir(parents=True)
            shutil.copy(
                os.path.join(self.source_path, "Al-fcc.vasp"),
                output_dir / "POSCAR",
            )
            custom_input = root / "custom.in.lammps"
            custom_input.write_text(
                "clear\natom_style atomic\natom_modify map yes\n"
                "read_data conf.lmp\nplugin load libdeepmd_lmp.so\n"
                "pair_style deepmd local.pt2\npair_coeff * * Al\n"
            )
            calculator = Lammps(
                {
                    "type": "deepmd",
                    "model": str(root / "local.pt2"),
                    "in_lammps": str(custom_input),
                    "type_map": {"Al": 0},
                    "deepmd_runtime": "dpa4_pt2",
                    "deepmd_version": "3.2.0b0",
                },
                os.path.join(self.source_path, "Al-fcc.vasp"),
            )

            with self.assertRaisesRegex(ValueError, "LAMMPS_PLUGIN_PATH auto-loading"):
                calculator.make_input_file(
                    str(output_dir), "relaxation", self.relax_param
                )

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

    def test_compute_skips_annealing_without_relax_dump_warning(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dumpfn({"type": "annealing"}, os.path.join(tmpdir, "task.json"))
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                result = self.Lammps.compute(tmpdir)

        self.assertIsNone(result)
        self.assertFalse(
            any("dump.relax" in str(item.message) for item in caught)
        )

    def test_compute_reports_failed_runtime_status_before_parsing_dump(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dumpfn({"cal_type": "relaxation"}, os.path.join(tmpdir, "task.json"))
            dumpfn(
                {
                    "state": "failed",
                    "reason": "nonzero_lammps_error",
                    "exit_code": 1,
                    "message": "Command exited with non-zero code 1.",
                },
                os.path.join(tmpdir, "apex_task_status.json"),
            )
            with self.assertRaisesRegex(
                RuntimeError,
                "LAMMPS task failed before post-processing.*exit_code=1",
            ):
                self.Lammps.compute(tmpdir)

    def test_parse_dump_file_rejects_empty_dump(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dump_path = os.path.join(tmpdir, "dump.relax")
            with open(dump_path, "w", encoding="utf-8"):
                pass
            with self.assertRaisesRegex(RuntimeError, "no TIMESTEP frames"):
                self.Lammps._parse_dump_file(dump_path, [], [], [], [])
