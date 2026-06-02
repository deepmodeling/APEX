import os
import shutil
import sys
import tempfile
import unittest

import numpy as np
import pytest
from monty.serialization import dumpfn, loadfn

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
__package__ = "tests"

from apex.core.property.FiniteTelastic import (
    EQUI_RESTART,
    FiniteTelastic,
    _block_average,
    _compute_pair_delta,
    _deform_include,
    _derive_moduli_from_voigt_gpa,
    _fit_elastic_tensor_bar,
    _format_temperature,
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


def make_tmp_equi_dir(tmp_path):
    equi_dir = tmp_path / "relax_task"
    equi_dir.mkdir()
    shutil.copy(os.path.join(TEST_DIR, "equi/lammps/hcp-Ti-CONTCAR"), equi_dir / "CONTCAR")
    return equi_dir


def test_finite_t_elastic_generates_metadata_and_include_files(tmp_path):
    equi_dir = make_tmp_equi_dir(tmp_path)
    work_dir = tmp_path / "finite_t_elastic"
    prop = FiniteTelastic(
        {
            "type": "finite_t_elastic",
            "supercell_size": [1, 2, 3],
            "cal_setting": {
                "temperature": [300],
                "strain": 0.002,
                "strain_components": ["xx"],
                "equi_step": 11,
                "response_step": 22,
                "stress_output_every": 3,
                "timestep": 0.004,
                "tdamp": 0.5,
                "pdamp": 5.0,
                "seed": 2468,
                "n_blocks": 4,
            },
        },
        {"type": "lammps"},
    )

    task_list = prop.make_confs(str(work_dir), str(equi_dir))

    assert len(task_list) == 5
    roles = [
        loadfn(work_dir / f"task.{idx:06d}" / "FiniteTelastic.json")["role"]
        for idx in range(5)
    ]
    assert roles == ["equi", "reference", "strained", "reference", "strained"]

    equi_meta = loadfn(work_dir / "task.000000" / "FiniteTelastic.json")
    ref_meta = loadfn(work_dir / "task.000001" / "FiniteTelastic.json")
    strained_meta = loadfn(work_dir / "task.000002" / "FiniteTelastic.json")

    assert equi_meta["property"] == "finite_t_elastic"
    assert equi_meta["role"] == "equi"
    assert equi_meta["strain_label"] == "none"
    assert equi_meta["restart_source"] is None
    assert equi_meta["supercell_size"] == [1, 2, 3]
    assert equi_meta["equi_step"] == 11
    assert equi_meta["response_step"] == 22
    assert equi_meta["stress_output_every"] == 3
    assert equi_meta["langevin_seed"] == 2468

    assert ref_meta["role"] == "reference"
    assert ref_meta["is_reference"] is True
    assert ref_meta["pair_id"] == "T300_c0_m"
    assert ref_meta["strain_component"] == 0
    assert ref_meta["strain_value"] == 0.0
    assert ref_meta["restart_source"] == EQUI_RESTART

    assert strained_meta["role"] == "strained"
    assert strained_meta["is_reference"] is False
    assert strained_meta["pair_id"] == "T300_c0_m"
    assert strained_meta["strain_component"] == 0
    assert strained_meta["strain_value"] == -0.002
    assert strained_meta["restart_source"] == EQUI_RESTART

    variable_text = (work_dir / "task.000002" / "variable_FiniteTelastic.in").read_text()
    assert "variable role string strained" in variable_text
    assert "variable temperature equal 300" in variable_text
    assert "variable nx equal 1" in variable_text
    assert "variable ny equal 2" in variable_text
    assert "variable nz equal 3" in variable_text
    assert "variable timestep equal 0.004" in variable_text
    assert "variable tdamp equal 0.5" in variable_text
    assert "variable pdamp equal 5" in variable_text
    assert "variable seed equal 2468" in variable_text
    assert "variable equi_step equal 11" in variable_text
    assert "variable response_step equal 22" in variable_text
    assert "variable stress_output_every equal 3" in variable_text
    assert "variable strain equal -0.002" in variable_text
    assert f"variable restart_source string {EQUI_RESTART}" in variable_text

    reference_deform = (work_dir / "task.000001" / "deform_FiniteTelastic.in").read_text()
    strained_deform = (work_dir / "task.000002" / "deform_FiniteTelastic.in").read_text()
    output_text = (work_dir / "task.000002" / "output_FiniteTelastic.in").read_text()
    assert "no strain deformation" in reference_deform
    assert "variable scale_x equal 1.0+${strain}" in strained_deform
    assert "change_box all x scale ${scale_x} remap units box" in strained_deform
    assert "fix stress_out all ave/time ${stress_output_every}" in output_text
    assert "file stress_timeseries.txt" in output_text


def test_finite_t_elastic_defaults_task_type_and_scalar_temperature(tmp_path):
    equi_dir = make_tmp_equi_dir(tmp_path)
    work_dir = tmp_path / "finite_t_elastic_scalar"
    prop = FiniteTelastic(
        {
            "cal_setting": {
                "temperature": 350,
                "strain_components": ["xy"],
                "equi_step": 1,
                "response_step": 1,
            }
        },
        {"type": "lammps"},
    )

    task_list = prop.make_confs(str(work_dir), str(equi_dir))

    assert prop.task_type() == "finite_t_elastic"
    assert prop.task_param()["cal_type"] == "finite_t_elastic"
    assert prop._temperatures() == [350.0]
    assert len(task_list) == 5
    strained_meta = loadfn(work_dir / "task.000002" / "FiniteTelastic.json")
    assert strained_meta["pair_id"] == "T350_c5_m"
    assert strained_meta["strain_label"] == "xy"


@pytest.mark.parametrize(
    ("component", "expected"),
    [
        (1, "change_box all y scale ${scale_y} remap units box"),
        (2, "change_box all z scale ${scale_z} remap units box"),
        (3, "change_box all yz delta ${d_yz} remap units box"),
        (4, "change_box all xz delta ${d_xz} remap units box"),
        (5, "change_box all xy delta ${d_xy} remap units box"),
    ],
)
def test_finite_t_elastic_deform_include_components(component, expected):
    assert expected in _deform_include(component, 0.001)


def test_finite_t_elastic_helper_error_paths(tmp_path):
    prop = FiniteTelastic({"type": "finite_t_elastic"}, {"type": "lammps"})

    with pytest.raises(RuntimeError, match="missing FiniteTelastic.json"):
        prop._load_task_records([str(tmp_path / "missing_task")])

    bad_meta_task = tmp_path / "bad_meta"
    bad_meta_task.mkdir()
    dumpfn({"property": "other"}, bad_meta_task / "FiniteTelastic.json")
    with pytest.raises(RuntimeError, match="unexpected property metadata"):
        prop._load_task_records([str(bad_meta_task)])

    with pytest.raises(RuntimeError, match="missing pair_id"):
        prop._paired_records([{"task_dir": "task", "metadata": {"role": "reference"}}])

    with pytest.raises(RuntimeError, match="invalid FiniteTelastic role"):
        prop._paired_records(
            [{"task_dir": "task", "metadata": {"role": "bad", "pair_id": "p0"}}]
        )

    duplicate_records = [
        {"task_dir": "task0", "metadata": {"role": "reference", "pair_id": "p0"}},
        {"task_dir": "task1", "metadata": {"role": "reference", "pair_id": "p0"}},
    ]
    with pytest.raises(RuntimeError, match="duplicate reference task"):
        prop._paired_records(duplicate_records)

    with pytest.raises(RuntimeError, match="missing strained task"):
        prop._paired_records(
            [{"task_dir": "task0", "metadata": {"role": "reference", "pair_id": "p0"}}]
        )

    with pytest.raises(RuntimeError, match="missing reference task"):
        prop._paired_records(
            [{"task_dir": "task0", "metadata": {"role": "strained", "pair_id": "p0"}}]
        )

    empty_stress_task = tmp_path / "empty_stress"
    empty_stress_task.mkdir()
    (empty_stress_task / "stress_timeseries.txt").write_text("# only comments\n")
    with pytest.raises(RuntimeError, match="empty or unparsable"):
        _read_stress_timeseries(str(empty_stress_task))

    with pytest.raises(RuntimeError, match="cannot block-average"):
        _block_average([], 2)

    with pytest.raises(ValueError, match="unsupported strain component"):
        _deform_include(9, 0.001)

    with pytest.raises(RuntimeError, match="strain matrix"):
        _fit_elastic_tensor_bar(np.zeros((2, 5)), np.zeros((2, 6)))

    with pytest.raises(RuntimeError, match="stress matrix"):
        _fit_elastic_tensor_bar(np.zeros((2, 6)), np.zeros((2, 5)))

    with pytest.raises(RuntimeError, match="insufficient independent"):
        _fit_elastic_tensor_bar(np.zeros((2, 6)), np.zeros((2, 6)))

    with pytest.raises(RuntimeError, match="elastic Voigt matrix"):
        _derive_moduli_from_voigt_gpa(np.zeros((3, 3)))


def write_stress(path, rows):
    path.write_text(
        "# step pxx pyy pzz pxy pxz pyz\n"
        + "\n".join(
            f"{step} {pxx} {pyy} {pzz} {pxy} {pxz} {pyz}"
            for step, pxx, pyy, pzz, pxy, pxz, pyz in rows
        )
        + "\n"
    )


def pressure_rows_from_stress(stress_voigt, include_extra=False):
    pxx, pyy, pzz, pyz, pxz, pxy = -np.asarray(stress_voigt, dtype=float)
    rows = [
        (0, pxx, pyy, pzz, pxy, pxz, pyz),
        (100, pxx, pyy, pzz, pxy, pxz, pyz),
    ]
    if include_extra:
        rows.append((200, pxx, pyy, pzz, pxy, pxz, pyz))
    return rows


def test_finite_t_elastic_compute_lower_with_synthetic_stress_data(tmp_path):
    equi_dir = make_tmp_equi_dir(tmp_path)
    work_dir = tmp_path / "finite_t_elastic_compute"
    prop = FiniteTelastic(
        {
            "type": "finite_t_elastic",
            "cal_setting": {
                "temperature": [300],
                "strain": 0.001,
                "strain_components": [0, 1, 2, 3, 4, 5],
                "n_blocks": 2,
            },
        },
        {"type": "lammps"},
    )
    task_list = prop.make_confs(str(work_dir), str(equi_dir))
    c_known_bar = np.diag([10000.0, 11000.0, 12000.0, 5000.0, 6000.0, 7000.0])

    for task_dir in task_list:
        task_path = work_dir / os.path.basename(task_dir)
        metadata = loadfn(task_path / "FiniteTelastic.json")
        if metadata["role"] == "equi":
            continue
        if metadata["role"] == "reference":
            rows = pressure_rows_from_stress(np.zeros(6), include_extra=True)
        else:
            strain = _voigt_strain(metadata["strain_component"], metadata["strain_value"])
            stress_voigt = c_known_bar @ strain
            rows = pressure_rows_from_stress(stress_voigt)
        write_stress(task_path / "stress_timeseries.txt", rows)

    res_data, ptr_data = prop._compute_lower(str(tmp_path / "result.json"), task_list, {})

    temp_data = res_data["temperatures"]["300"]
    assert res_data["property"] == "finite_t_elastic"
    assert temp_data["rank"] == 6
    assert temp_data["number_of_paired_responses"] == 12
    np.testing.assert_allclose(temp_data["elastic_tensor"], c_known_bar / 10000.0)
    assert "dropped" in temp_data["warnings"][0]
    assert "Warnings:" in ptr_data
    assert (tmp_path / "result.json").is_file()


def test_finite_t_elastic_missing_contcar_raises(tmp_path):
    prop = FiniteTelastic({"type": "finite_t_elastic"}, {"type": "lammps"})

    with pytest.raises(RuntimeError, match="missing CONTCAR"):
        prop.make_confs(str(tmp_path / "work"), str(tmp_path / "missing_relax"))


def test_finite_t_elastic_invalid_method_and_component_raise(tmp_path):
    with pytest.raises(ValueError, match="paired_langevin"):
        FiniteTelastic(
            {"type": "finite_t_elastic", "cal_setting": {"method": "other"}},
            {"type": "lammps"},
        )

    with pytest.raises(ValueError, match="unsupported strain component"):
        FiniteTelastic(
            {
                "type": "finite_t_elastic",
                "cal_setting": {"strain_components": ["bad-component"]},
            },
            {"type": "lammps"},
        )

    with pytest.raises(TypeError, match="LAMMPS interactions"):
        FiniteTelastic({"type": "finite_t_elastic"}, {"type": "abacus"})

    with pytest.raises(NotImplementedError, match="refine requires"):
        FiniteTelastic({"type": "finite_t_elastic"}, {"type": "lammps"}).make_confs(
            str(tmp_path / "unused-finite-t-elastic-refine"),
            str(tmp_path / "unused-equi"),
            refine=True,
        )


def test_format_temperature_for_integer_and_fractional_values():
    assert _format_temperature(300.0) == "300"
    assert _format_temperature(300.5) == "300p5"


if __name__ == "__main__":
    unittest.main()
