import json
import os
import shutil
import tempfile
import unittest

from monty.serialization import loadfn

from apex.core.common_prop import make_property_instance
from apex.core.property.MeltingPoint import (
    MeltingPoint,
    _aggregate_temperatures,
    _infer_bracket,
    _snapshot_projection,
    render_melting_point_lammps_input,
)
from apex.core.calculator.lib import lammps_utils
from apex.core.calculator.Lammps import Lammps
from apex.reporter.DashReportApp import return_prop_class, return_prop_type
from apex.reporter.property_report import MeltingPointReport


POSCAR = """Ti
1.0
3.0 0.0 0.0
0.0 3.0 0.0
0.0 0.0 6.0
Ti
4
Direct
0.0 0.0 0.10
0.5 0.5 0.35
0.0 0.5 0.65
0.5 0.0 0.85
"""


class TestMeltingPointProperty(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.equi = os.path.join(self.tmp.name, "relaxation", "relax_task")
        os.makedirs(self.equi)
        with open(os.path.join(self.equi, "CONTCAR"), "w") as fp:
            fp.write(POSCAR)
        self.work = os.path.join(self.tmp.name, "melting_point_00")
        self.param = {
            "type": "melting_point",
            "method": "two_phase",
            "supercell_size": [1, 1, 2],
            "cal_setting": {
                "temperature": [1600, 1700],
                "replicas": 2,
                "premelt_steps": 2,
                "conditioning_steps": 2,
                "production_steps": 10,
                "dump_step": 1,
                "thermo_step": 1,
                "restart_interval": 2,
                "timestep": 1.0,
            },
        }

    def tearDown(self):
        self.tmp.cleanup()

    def test_registered_and_generates_temperature_replica_matrix(self):
        prop = make_property_instance(self.param, {"type": "deepmd"})
        self.assertIsInstance(prop, MeltingPoint)
        tasks = prop.make_confs(self.work, self.equi)
        self.assertEqual(4, len(tasks))
        metadata = [loadfn(os.path.join(task, "MeltingPoint.json")) for task in tasks]
        self.assertEqual([1600.0, 1600.0, 1700.0, 1700.0], [row["temperature_K"] for row in metadata])
        self.assertNotEqual(metadata[0]["velocity_seeds"], metadata[1]["velocity_seeds"])
        self.assertEqual(4, metadata[0]["release_step"])
        self.assertEqual(2, metadata[0]["restart_interval"])
        with open(os.path.join(tasks[0], "variable_MeltingPoint.in")) as fp:
            self.assertIn("variable restart_interval equal 2", fp.read())

    def test_renderer_matches_validated_two_phase_protocol(self):
        prop = MeltingPoint(self.param, {"type": "deepmd"})
        text = render_melting_point_lammps_input(
            "conf.lmp",
            {"Ti": 0},
            lammps_utils.inter_deepmd,
            {
                "type": "deepmd",
                "model_name": ["model.pb"],
                "param_type": {"Ti": 0},
                "deepmd_version": "2.1.1",
            },
            prop.task_param(),
        )
        self.assertIn("compute q6 all orientorder/atom", text)
        self.assertIn("fix pin_solid solid_seed setforce", text)
        self.assertIn("fix melt_liquid liquid_seed nvt", text)
        self.assertIn("unfix pin_solid", text)
        self.assertIn("fix condition_all all nvt", text)
        self.assertNotIn("fix condition_liquid liquid_seed nvt", text)
        self.assertIn("fix coexistence all npt", text)
        self.assertIn("iso ${target_pressure} ${target_pressure} ${pdamp}", text)
        self.assertIn("dump.melting id type xs ys zs c_q6[1]", text)
        self.assertIn(
            "restart ${restart_interval} restart.melting.1 restart.melting.2",
            text,
        )
        self.assertIn("write_restart restart.melting.final", text)

    def test_lammps_calculator_writes_property_input_and_manifests(self):
        model = os.path.join(self.tmp.name, "model.pb")
        with open(model, "wb") as fp:
            fp.write(b"test model placeholder")
        prop = MeltingPoint(self.param, {"type": "deepmd"})
        task = prop.make_confs(self.work, self.equi)[0]
        calculator = Lammps(
            {
                "type": "deepmd",
                "model": model,
                "type_map": {"Ti": 0},
            },
            os.path.join(task, "POSCAR"),
        )
        calculator.make_input_file(task, "melting_point", prop.task_param())
        with open(os.path.join(task, "in.lammps")) as fp:
            text = fp.read()
        self.assertIn("APEX_MELTING_STAGE coexistence_release", text)
        self.assertEqual(
            ["in.lammps", "variable_MeltingPoint.in", "MeltingPoint.json", "model.pb"],
            calculator.forward_files("melting_point"),
        )
        self.assertIn("dump.melting", calculator.backward_files("melting_point"))
        self.assertIn(
            "restart.melting.*", calculator.backward_files("melting_point")
        )

    def test_restart_files_are_copied_per_temperature_and_forwarded(self):
        restart_dir = os.path.join(self.tmp.name, "restart_inputs")
        os.makedirs(restart_dir)
        restart_files = []
        for temperature in self.param["cal_setting"]["temperature"]:
            path = os.path.join(restart_dir, f"restart.{temperature}")
            with open(path, "wb") as fp:
                fp.write(str(temperature).encode("ascii"))
            restart_files.append(path)

        parameter = json.loads(json.dumps(self.param))
        parameter["cal_setting"]["restart_files"] = restart_files
        prop = MeltingPoint(parameter, {"type": "deepmd"})
        tasks = prop.make_confs(self.work, self.equi)

        for index, task in enumerate(tasks):
            temperature_index = index // parameter["cal_setting"]["replicas"]
            with open(os.path.join(task, "restart.coexistence.start"), "rb") as fp:
                self.assertEqual(
                    str(parameter["cal_setting"]["temperature"][temperature_index]).encode("ascii"),
                    fp.read(),
                )

        model = os.path.join(self.tmp.name, "restart-model.pb")
        with open(model, "wb") as fp:
            fp.write(b"test model placeholder")
        calculator = Lammps(
            {"type": "deepmd", "model": model, "type_map": {"Ti": 0}},
            os.path.join(tasks[0], "POSCAR"),
        )
        self.assertIn(
            "restart.coexistence.start",
            calculator.forward_files("melting_point", prop.task_param()),
        )
        self.assertNotIn(
            "restart.coexistence.start",
            calculator.forward_files("finite_t_latt", prop.task_param()),
        )

        text = render_melting_point_lammps_input(
            "conf.lmp",
            {"Ti": 0},
            lammps_utils.inter_deepmd,
            {
                "type": "deepmd",
                "model_name": ["model.pb"],
                "param_type": {"Ti": 0},
                "deepmd_version": "2.1.1",
            },
            prop.task_param(),
        )
        self.assertIn("read_restart restart.coexistence.start", text)
        self.assertIn("reset_timestep 0", text)
        self.assertIn("run 0", text)
        self.assertNotIn("read_data conf.lmp", text)
        self.assertNotIn("run ${premelt_steps}", text)
        metadata = loadfn(os.path.join(tasks[0], "MeltingPoint.json"))
        self.assertTrue(metadata["restart_mode"])
        self.assertEqual(0, metadata["reference_step"])
        self.assertEqual(0, metadata["release_step"])

    def test_restart_files_require_one_existing_file_per_temperature(self):
        parameter = json.loads(json.dumps(self.param))
        parameter["cal_setting"]["restart_files"] = ["only-one-restart"]
        with self.assertRaisesRegex(ValueError, "one entry per temperature"):
            MeltingPoint(parameter, {"type": "deepmd"}).make_confs(
                self.work, self.equi
            )

    def test_rejects_dft_and_invalid_axis(self):
        with self.assertRaises(NotImplementedError):
            MeltingPoint(self.param, {"type": "vasp"})
        bad = json.loads(json.dumps(self.param))
        bad["cal_setting"]["interface_axis"] = "w"
        with self.assertRaises(ValueError):
            MeltingPoint(bad, {"type": "deepmd"})

    def test_rejects_non_positive_velocity_seeds(self):
        for invalid_seed in (0, -1):
            bad = json.loads(json.dumps(self.param))
            bad["cal_setting"]["velocity_seeds"] = {
                "premelt": invalid_seed,
                "condition": 2,
                "release": 3,
            }
            with self.subTest(seed=invalid_seed), self.assertRaisesRegex(
                ValueError, "positive integers"
            ):
                MeltingPoint(bad, {"type": "deepmd"})

    def test_rejects_fractional_integer_settings(self):
        for field, value in (
            ("supercell_size", [1.5, 1, 2]),
            ("production_steps", 10.5),
            ("dump_step", True),
        ):
            bad = json.loads(json.dumps(self.param))
            if field == "supercell_size":
                bad[field] = value
            else:
                bad["cal_setting"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                MeltingPoint(bad, {"type": "deepmd"})

        bad = json.loads(json.dumps(self.param))
        bad["cal_setting"]["velocity_seeds"] = {
            "premelt": 1.5,
            "condition": 2,
            "release": 3,
        }
        with self.assertRaisesRegex(ValueError, "positive integers"):
            MeltingPoint(bad, {"type": "deepmd"})

    def test_snapshot_projection_contains_requested_interface_axis(self):
        self.assertEqual((0, 2, "fractional x", "fractional z"), _snapshot_projection("z"))
        self.assertEqual((0, 1, "fractional x", "fractional y"), _snapshot_projection("y"))
        self.assertEqual((1, 0, "fractional y", "fractional x"), _snapshot_projection("x"))


class TestMeltingPointAnalysis(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.task = self.tmp.name
        self.meta = {
            "schema": "apex.melting_point.task/v1",
            "property": "melting_point",
            "method": "two_phase",
            "temperature_K": 1600.0,
            "replica": 1,
            "interface_axis": "z",
            "liquid_fraction": 0.5,
            "premelt_steps": 2,
            "conditioning_steps": 2,
            "release_step": 4,
            "production_steps": 10,
            "timestep_ps": 1.0,
            "analysis_stride": 1,
            "analysis_block_ps": 2.0,
            "minimum_q6_gap": 0.03,
            "minimum_directional_change": 0.02,
            "spatial_bins": 4,
            "velocity_seeds": {"premelt": 1, "condition": 2, "release": 3},
        }
        with open(os.path.join(self.task, "MeltingPoint.json"), "w") as fp:
            json.dump(self.meta, fp)

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def _write_frame(fp, step, q6):
        coords = [(0.1, 0.1, 0.10), (0.6, 0.6, 0.35), (0.1, 0.6, 0.65), (0.6, 0.1, 0.85)]
        fp.write("ITEM: TIMESTEP\n%d\n" % step)
        fp.write("ITEM: NUMBER OF ATOMS\n4\n")
        fp.write("ITEM: BOX BOUNDS pp pp pp\n0 3\n0 3\n0 6\n")
        fp.write("ITEM: ATOMS id type xs ys zs c_q6[1]\n")
        for index, ((x, y, z), value) in enumerate(zip(coords, q6), 1):
            fp.write(f"{index} 1 {x} {y} {z} {value}\n")

    def test_q6_interface_motion_and_bracket(self):
        with open(os.path.join(self.task, "dump.melting"), "w") as fp:
            self._write_frame(fp, 0, [0.5, 0.5, 0.5, 0.5])
            self._write_frame(fp, 2, [0.5, 0.5, 0.1, 0.1])
            for step in range(4, 15):
                liquid_q6 = 0.1 + 0.035 * (step - 4)
                self._write_frame(fp, step, [0.5, 0.5, liquid_q6, liquid_q6])
        with open(os.path.join(self.task, "log.lammps"), "w") as fp:
            fp.write("Step Temp Press PotEng KinEng TotEng\n")
            for step in range(4, 15):
                fp.write(f"{step} 1600 0 -10 2 -8\n")
        prop = MeltingPoint(
            {
                "type": "melting_point",
                "cal_setting": {
                    "temperature": [1600],
                    "premelt_steps": 2,
                    "conditioning_steps": 2,
                    "production_steps": 10,
                    "dump_step": 1,
                    "thermo_step": 1,
                    "timestep": 1.0,
                },
            },
            {"type": "deepmd"},
        )
        result, _ = prop._compute_lower(
            os.path.join(self.tmp.name, "result.json"), [self.task], []
        )
        self.assertEqual([], result["failed_tasks"])
        point = result["points"][0]
        self.assertEqual("solid_growth", point["interface_motion"]["outcome"])
        self.assertGreater(point["interface_motion"]["interface_velocity_A_per_ps"], 0)
        self.assertAlmostEqual(0.4, point["reference_q6_gap"])
        bracket = _infer_bracket([
            {"temperature_K": 1600, "consensus_outcome": "solid_growth"},
            {"temperature_K": 1700, "consensus_outcome": "liquid_growth"},
        ])
        self.assertEqual("bracketed", bracket["status"])
        self.assertEqual(1650, bracket["estimated_melting_temperature_K"])

    def test_incomplete_replicas_cannot_form_a_bracket(self):
        points = [
            {
                "temperature_K": 1600,
                "replica": 1,
                "interface_motion": {
                    "outcome": "solid_growth",
                    "interface_velocity_A_per_ps": 0.1,
                },
            },
            {
                "temperature_K": 1700,
                "replica": 1,
                "interface_motion": {
                    "outcome": "liquid_growth",
                    "interface_velocity_A_per_ps": -0.1,
                },
            },
        ]
        rows = _aggregate_temperatures(
            points,
            expected_replicas=2,
            expected_temperatures=[1600, 1700, 1800],
        )

        self.assertEqual(["inconclusive"] * 3, [
            row["consensus_outcome"] for row in rows
        ])
        self.assertFalse(any(row["replicas_complete"] for row in rows))
        self.assertEqual(0, rows[-1]["replica_count"])
        self.assertIsNone(rows[-1]["interface_velocity_mean_A_per_ps"])
        self.assertEqual("inconclusive_or_unbracketed", _infer_bracket(rows)["status"])


class TestMeltingPointReporter(unittest.TestCase):
    def test_reporter_registration(self):
        self.assertEqual("melting_point", return_prop_type("melting_point_00"))
        self.assertIs(MeltingPointReport, return_prop_class("melting_point"))


if __name__ == "__main__":
    unittest.main()
