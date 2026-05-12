import os
import shutil
import sys
import tempfile
import unittest

import numpy as np
from monty.serialization import loadfn

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
__package__ = "tests"

from apex.core.property.FiniteTelastic import (
    FiniteTelastic,
    _block_average,
    _compute_pair_delta,
    _derive_moduli_from_voigt_gpa,
    _fit_elastic_tensor_bar,
    _read_stress_timeseries,
    _voigt_strain,
    _voigt_strain_tensor,
    normalize_strain_components,
)
from apex.reporter.DashReportApp import DashReportApp, return_prop_class, return_prop_type
from apex.reporter.property_report import FiniteTelasticReport

TEST_DIR = os.path.dirname(__file__)


class TestFiniteTelasticHelpers(unittest.TestCase):
    def test_component_normalization(self):
        self.assertEqual(normalize_strain_components(["xx", "yy", "xy"]), [0, 1, 5])

    def test_stress_parser(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "stress_timeseries.txt"), "w") as fp:
                fp.write("# step pxx pyy pzz pxy pxz pyz\n")
                fp.write("0 1 2 3 4 5 6\n")
                fp.write("\n")
                fp.write("100 7 8 9 10 11 12\n")
            data = _read_stress_timeseries(tmp)
        self.assertEqual(sorted(data), [0, 100])
        np.testing.assert_allclose(data[0], [[1, 4, 5], [4, 2, 6], [5, 6, 3]])

    def test_sign_unit_conversion_from_pair_delta(self):
        with tempfile.TemporaryDirectory() as tmp:
            ref = os.path.join(tmp, "ref")
            strained = os.path.join(tmp, "strained")
            os.makedirs(ref)
            os.makedirs(strained)
            with open(os.path.join(ref, "stress_timeseries.txt"), "w") as fp:
                fp.write("# step pxx pyy pzz pxy pxz pyz\n")
                fp.write("0 100 0 0 0 0 0\n")
            with open(os.path.join(strained, "stress_timeseries.txt"), "w") as fp:
                fp.write("# step pxx pyy pzz pxy pxz pyz\n")
                fp.write("0 90 0 0 0 0 0\n")
            mean, stderr, block_means, n_samples, warnings = _compute_pair_delta(ref, strained, 10)
        self.assertEqual(n_samples, 1)
        self.assertEqual(warnings, [])
        self.assertEqual(mean[0, 0], 10.0)
        self.assertEqual(stderr[0, 0], 0.0)
        self.assertEqual(block_means.shape[0], 1)

    def test_block_average(self):
        values = np.array([1.0, 2.0, 3.0, 4.0])
        block_means, stderr = _block_average(values, 2)
        np.testing.assert_allclose(block_means, [1.5, 3.5])
        self.assertAlmostEqual(stderr, 1.0)

    def test_least_squares_tensor_fitting(self):
        c_known = np.array(
            [
                [100, 10, 10, 0, 0, 0],
                [10, 110, 10, 0, 0, 0],
                [10, 10, 120, 0, 0, 0],
                [0, 0, 0, 50, 0, 0],
                [0, 0, 0, 0, 60, 0],
                [0, 0, 0, 0, 0, 70],
            ],
            dtype=float,
        )
        strain_rows = []
        for component in range(6):
            strain_rows.append(_voigt_strain(component, 0.001))
            strain_rows.append(_voigt_strain(component, -0.001))
        strain_rows = np.asarray(strain_rows)
        stress_rows = strain_rows @ c_known.T
        c_raw, c_sym, rank = _fit_elastic_tensor_bar(strain_rows, stress_rows)
        self.assertEqual(rank, 6)
        np.testing.assert_allclose(c_raw, c_known, atol=1e-10)
        np.testing.assert_allclose(c_sym, c_known, atol=1e-10)

    def test_shear_convention(self):
        tensor = _voigt_strain_tensor(5, 0.002)
        self.assertEqual(tensor[0, 1], 0.001)
        self.assertEqual(tensor[1, 0], 0.001)
        voigt = _voigt_strain(5, 0.002)
        self.assertEqual(voigt[5], 0.002)

    def test_derive_moduli_accepts_voigt_matrix(self):
        c_voigt = np.array(
            [
                [100, 50, 50, 0, 0, 0],
                [50, 100, 50, 0, 0, 0],
                [50, 50, 100, 0, 0, 0],
                [0, 0, 0, 25, 0, 0],
                [0, 0, 0, 0, 25, 0],
                [0, 0, 0, 0, 0, 25],
            ],
            dtype=float,
        )
        bulk, shear, young, poisson = _derive_moduli_from_voigt_gpa(c_voigt)
        self.assertAlmostEqual(bulk, 200.0 / 3.0)
        self.assertAlmostEqual(shear, 25.0)
        self.assertGreater(young, 0.0)
        self.assertGreater(poisson, 0.0)


class TestFiniteTelasticProperty(unittest.TestCase):
    def setUp(self):
        self.equi_path = os.path.join(TEST_DIR, "confs/hcp-Ti/relaxation/relax_task")
        self.source_path = os.path.join(TEST_DIR, "equi/lammps")
        self.target_path = os.path.join(TEST_DIR, "confs/hcp-Ti/finite_t_elastic_00")
        os.makedirs(self.equi_path, exist_ok=True)
        self.parameter = {
            "type": "finite_t_elastic",
            "supercell_size": [2, 2, 2],
            "cal_setting": {
                "temperature": [300],
                "strain": 0.001,
                "strain_components": ["xx", "xy"],
                "equi_step": 10,
                "response_step": 10,
            },
        }

    def tearDown(self):
        if os.path.exists(os.path.abspath(os.path.join(self.equi_path, ".."))):
            shutil.rmtree(os.path.abspath(os.path.join(self.equi_path, "..")))
        if os.path.exists(self.target_path):
            shutil.rmtree(self.target_path)

    def test_make_confs_generates_equi_and_paired_tasks(self):
        prop = FiniteTelastic(self.parameter, {"type": "meam_spline"})
        shutil.copy(
            os.path.join(self.source_path, "hcp-Ti-CONTCAR"),
            os.path.join(self.equi_path, "CONTCAR"),
        )
        task_list = prop.make_confs(self.target_path, self.equi_path)
        self.assertEqual(len(task_list), 9)
        self.assertTrue(os.path.isfile(os.path.join(task_list[0], "FiniteTelastic.json")))
        self.assertTrue(os.path.isfile(os.path.join(task_list[0], "variable_FiniteTelastic.in")))
        self.assertTrue(os.path.isfile(os.path.join(task_list[1], "deform_FiniteTelastic.in")))
        self.assertFalse(os.path.exists(os.path.join(task_list[0], "POSCAR.tmp")))
        response_meta = loadfn(os.path.join(task_list[1], "FiniteTelastic.json"))
        self.assertEqual(response_meta["restart_source"], "finite_t_elastic.equi.restart")

    def test_rejects_vasp(self):
        with self.assertRaises(TypeError):
            FiniteTelastic({"type": "finite_t_elastic"}, {"type": "vasp"})


class TestFiniteTelasticReport(unittest.TestCase):
    def test_reporter_registered_for_numbered_property_name(self):
        self.assertEqual(return_prop_type("finite_t_elastic_00"), "finite_t_elastic")
        self.assertIs(return_prop_class("finite_t_elastic"), FiniteTelasticReport)

    def test_reporter_builds_graph_and_table(self):
        res_data = {
            "temperatures": {
                "300": {
                    "B": 70.0,
                    "G": 30.0,
                    "E": 80.0,
                    "u": 0.3,
                    "rank": 6,
                    "number_of_paired_responses": 12,
                    "elastic_tensor": [
                        [1.0 if ii == jj else 0.0 for jj in range(6)]
                        for ii in range(6)
                    ],
                }
            }
        }

        traces, layout = FiniteTelasticReport.plotly_graph(res_data, "test")
        table, df = FiniteTelasticReport.dash_table(res_data)

        self.assertGreaterEqual(len(traces), 3)
        self.assertEqual(layout.title.text, "Finite-Temperature Elastic Constants")
        self.assertIn("C11 (GPa)", df.columns)
        self.assertEqual(table.data[0]["Temperature (K)"], 300)

    def test_dash_report_uses_pattern_matching_clipboard_ids(self):
        datasets = {
            "work": {
                "conf": {
                    "finite_t_elastic_00": {
                        "result": {
                            "temperatures": {
                                "300": {
                                    "B": 70.0,
                                    "G": 30.0,
                                    "E": 80.0,
                                    "u": 0.3,
                                    "elastic_tensor": [
                                        [1.0 if ii == jj else 0.0 for jj in range(6)]
                                        for ii in range(6)
                                    ],
                                }
                            }
                        }
                    }
                }
            }
        }
        app = DashReportApp(datasets)
        table_container = app.update_table("finite_t_elastic_00", "conf")
        table_div = table_container.children[0]
        clipboard = table_div.children[1]
        table = table_div.children[2]

        self.assertEqual(clipboard.id, {"type": "clip", "index": 0})
        self.assertEqual(table.id, {"type": "table", "index": 0})
        self.assertIn("Temperature (K)", app.csv_copy(1, table.data))


if __name__ == "__main__":
    unittest.main()
