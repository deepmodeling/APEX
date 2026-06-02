import os
import shutil
import tempfile
import unittest
from pathlib import Path

import pytest
from monty.serialization import loadfn

from apex.core.calculator.lib import lammps_utils
from apex.core.property.Annealing import Annealing


TEST_CONTCAR = os.path.join(
    os.path.dirname(__file__), "equi", "lammps", "hcp-Ti-CONTCAR"
)
TYPE_MAP = {"Ti": 0}
PARAM = {"type": "deepmd"}


def dummy_interaction(param):
    return "pair_style dummy\npair_coeff * * dummy Ti\n"


def make_equi_dir(tmp_path):
    equi_dir = tmp_path / "relax_task"
    equi_dir.mkdir()
    shutil.copy(TEST_CONTCAR, equi_dir / "CONTCAR")
    return equi_dir


def test_annealing_default_parameter_parsing():
    prop = Annealing({"type": "annealing"})

    assert prop.task_type() == "annealing"
    assert prop.start_temp == 300.0
    assert prop.target_temp == 800.0
    assert prop.end_temp == 300.0
    assert prop.equi_step == 10000
    assert prop.ramp_step == 20000
    assert prop.hold_step == 0
    assert prop.cool_step == 20000
    assert prop.thermostat == "nose_hoover"
    assert prop.ensemble == "npt"
    assert prop.supercell_size == [2, 2, 2]

    task_param = prop.task_param()
    assert task_param["cal_type"] == "annealing"
    assert task_param["cal_setting"]["rdf_bins"] == 200
    assert task_param["cal_setting"]["timestep"] == 0.002


def test_annealing_custom_cal_setting_override():
    prop = Annealing(
        {
            "type": "annealing",
            "supercell_size": [3, 2, 1],
            "cal_setting": {
                "start_temp": 250,
                "target_temp": [600, 900],
                "end_temp": 350,
                "equi_step": 12,
                "ramp_step": 34,
                "hold_step": 56,
                "cool_step": 78,
                "thermostat": "langevin",
                "ensemble": "nve",
                "tdamp": "tdamp_var",
                "pdamp": "pdamp_var",
                "velocity_seed": 2468,
                "dump_step": 111,
                "timestep": 0.004,
                "rdf_bins": 42,
                "rdf_cutoff": 6.5,
                "rdf_interval": 9,
            },
        }
    )

    task_param = prop.task_param()
    assert prop.target_temp == 600.0
    assert task_param["supercell_size"] == [3, 2, 1]
    assert task_param["cal_setting"]["target_temp"] == 600.0
    assert task_param["cal_setting"]["thermostat"] == "langevin"
    assert task_param["cal_setting"]["ensemble"] == "nve"
    assert task_param["cal_setting"]["tdamp"] == "tdamp_var"
    assert task_param["cal_setting"]["pdamp"] == "pdamp_var"
    assert task_param["cal_setting"]["dump_step"] == 111


def test_annealing_make_confs_writes_task_files_and_variables(tmp_path):
    equi_dir = make_equi_dir(tmp_path)
    work_dir = tmp_path / "annealing"
    prop = Annealing(
        {
            "type": "annealing",
            "supercell_size": [2, 3, 4],
            "cal_setting": {
                "start_temp": 300,
                "target_temp": [700, 900],
                "end_temp": 400,
                "equi_step": 10,
                "ramp_step": 20,
                "hold_step": 30,
                "cool_step": 40,
                "tdamp": 0.5,
                "pdamp": 5.0,
                "velocity_seed": 13579,
                "dump_step": 25,
                "timestep": 0.001,
                "rdf_bins": 64,
                "rdf_cutoff": 8.0,
                "rdf_interval": 5,
            },
        }
    )

    task_list = prop.make_confs(str(work_dir), str(equi_dir))

    assert len(task_list) == 2
    first_task = work_dir / "task.000000"
    second_task = work_dir / "task.000001"
    assert first_task.as_posix() in task_list
    assert (first_task / "POSCAR").is_file()
    assert (first_task / "Annealing.json").is_file()
    assert (first_task / "variable_Annealing.in").is_file()

    first_meta = loadfn(first_task / "Annealing.json")
    second_meta = loadfn(second_task / "Annealing.json")
    assert first_meta == {
        "start_temp": 300.0,
        "target_temp": 700.0,
        "end_temp": 400.0,
        "supercell_size": [2, 3, 4],
    }
    assert second_meta["target_temp"] == 900.0

    variable_text = (first_task / "variable_Annealing.in").read_text()
    assert "variable nx equal 2" in variable_text
    assert "variable ny equal 3" in variable_text
    assert "variable nz equal 4" in variable_text
    assert "variable start_temp equal 300.00" in variable_text
    assert "variable target_temp equal 700.00" in variable_text
    assert "variable end_temp equal 400.00" in variable_text
    assert "variable equi_step equal 10" in variable_text
    assert "variable ramp_step equal 20" in variable_text
    assert "variable hold_step equal 30" in variable_text
    assert "variable cool_step equal 40" in variable_text
    assert "variable rdf_bins equal 64" in variable_text
    assert "variable rdf_cutoff equal 8.0" in variable_text
    assert "variable rdf_interval equal 5" in variable_text
    assert "variable tdamp equal 0.5" in variable_text
    assert "variable pdamp equal 5.0" in variable_text
    assert "variable velocity_seed equal 13579" in variable_text
    assert "variable dump_step equal 25" in variable_text


def test_annealing_supercell_length_derives_replication_and_task_param(tmp_path):
    equi_dir = make_equi_dir(tmp_path)
    work_dir = tmp_path / "annealing_length"
    prop = Annealing(
        {
            "type": "annealing",
            "supercell_length": [6.0, 6.0, 8.0],
            "cal_setting": {"target_temp": 600},
        }
    )

    task_list = prop.make_confs(str(work_dir), str(equi_dir))
    task_param = prop.task_param()

    assert len(task_list) == 1
    assert prop.supercell_size == [3, 3, 2]
    assert task_param["supercell_length"] == [6.0, 6.0, 8.0]
    variable_text = (work_dir / "task.000000" / "variable_Annealing.in").read_text()
    assert "variable nx equal 3" in variable_text
    assert "variable ny equal 3" in variable_text
    assert "variable nz equal 2" in variable_text


def test_annealing_rate_controls_derive_ramp_and_cool_steps(tmp_path):
    equi_dir = make_equi_dir(tmp_path)
    work_dir = tmp_path / "annealing_rates"
    prop = Annealing(
        {
            "type": "annealing",
            "cal_setting": {
                "start_temp": 300,
                "target_temp": [500, 700],
                "end_temp": 400,
                "ramp_rate": 100,
                "cool_rate": [50, 100],
                "ramp_step": 999,
                "cool_step": 888,
                "timestep": 1.0,
            },
        }
    )

    task_list = prop.make_confs(str(work_dir), str(equi_dir))

    first_variable = (work_dir / "task.000000" / "variable_Annealing.in").read_text()
    second_variable = (work_dir / "task.000001" / "variable_Annealing.in").read_text()
    assert len(task_list) == 2
    assert "variable ramp_step equal 2000" in first_variable
    assert "variable cool_step equal 2000" in first_variable
    assert "variable ramp_step equal 4000" in second_variable
    assert "variable cool_step equal 3000" in second_variable


def test_annealing_rate_fallback_and_missing_relaxation_error(tmp_path):
    equi_dir = make_equi_dir(tmp_path)
    work_dir = tmp_path / "annealing_bad_rates"
    prop = Annealing(
        {
            "type": "annealing",
            "cal_setting": {
                "target_temp": [500],
                "ramp_rate": "not-a-number",
                "cool_rate": "not-a-number",
                "ramp_step": 77,
                "cool_step": 88,
            },
        }
    )

    prop.make_confs(str(work_dir), str(equi_dir))
    variable_text = (work_dir / "task.000000" / "variable_Annealing.in").read_text()
    assert "variable ramp_step equal 77" in variable_text
    assert "variable cool_step equal 88" in variable_text

    with pytest.raises(RuntimeError, match="please finish relaxation before annealing"):
        prop.make_confs(str(tmp_path / "missing_work"), str(tmp_path / "missing_equi"))


def test_annealing_compute_lower_returns_task_notes(tmp_path):
    prop = Annealing({"type": "annealing"})

    res_data, ptr_data = prop._compute_lower(
        str(tmp_path / "result.json"),
        [str(tmp_path / "task.000000"), str(tmp_path / "task.000001")],
        {},
    )

    assert ptr_data == str(tmp_path) + "\n"
    assert res_data["task.000000"]["task"] == "task.000000"
    assert "inspect log.lammps" in res_data["task.000001"]["note"]


def test_annealing_lammps_input_contains_all_md_stages_and_outputs():
    prop = Annealing(
        {
            "type": "annealing",
            "cal_setting": {
                "start_temp": 300,
                "target_temp": 800,
                "end_temp": 350,
                "equi_step": 10,
                "ramp_step": 20,
                "hold_step": 30,
                "cool_step": 40,
                "thermostat": "nose_hoover",
                "ensemble": "npt",
                "tdamp": "tdamp_var",
                "pdamp": "pdamp_var",
                "velocity_seed": 123,
                "dump_step": 50,
                "rdf_interval": 5,
            },
        }
    )

    script = lammps_utils.make_lammps_annealing(
        "conf.lmp",
        TYPE_MAP,
        dummy_interaction,
        PARAM,
        prop.task_param()["cal_setting"],
    )

    assert "fix 1 all npt temp ${start_temp} ${start_temp} tdamp_var" in script
    assert "fix 1 all npt temp ${start_temp} ${target_temp} tdamp_var" in script
    assert (
        'if "${hold_step} > 0" then "fix 1 all nvt temp ${target_temp} '
        '${target_temp} tdamp_var" "run ${hold_step}" "unfix 1"'
    ) in script
    assert "fix 1 all npt temp ${target_temp} ${end_temp} tdamp_var" in script
    assert "run ${equi_step}" in script
    assert "run ${ramp_step}" in script
    assert "run ${cool_step}" in script
    assert "compute         myRDF all rdf ${rdf_bins} cutoff ${rdf_cutoff}" in script
    assert "file rdf_ramp.dat mode vector" in script
    assert "file rdf_cool.dat mode vector" in script
    assert "file heating_interval.dat" in script
    assert "file cooling_interval.dat" in script
    assert "dump.anneal_ramp" in script
    assert "dump.anneal_cool" in script


class TestAnnealingCoverage(unittest.TestCase):
    def test_annealing_default_parameter_parsing(self):
        test_annealing_default_parameter_parsing()

    def test_annealing_custom_cal_setting_override(self):
        test_annealing_custom_cal_setting_override()

    def test_annealing_make_confs_writes_task_files_and_variables(self):
        with tempfile.TemporaryDirectory() as tmp:
            test_annealing_make_confs_writes_task_files_and_variables(Path(tmp))

    def test_annealing_supercell_length_derives_replication_and_task_param(self):
        with tempfile.TemporaryDirectory() as tmp:
            test_annealing_supercell_length_derives_replication_and_task_param(Path(tmp))

    def test_annealing_rate_controls_derive_ramp_and_cool_steps(self):
        with tempfile.TemporaryDirectory() as tmp:
            test_annealing_rate_controls_derive_ramp_and_cool_steps(Path(tmp))

    def test_annealing_rate_fallback_and_missing_relaxation_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            test_annealing_rate_fallback_and_missing_relaxation_error(Path(tmp))

    def test_annealing_compute_lower_returns_task_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            test_annealing_compute_lower_returns_task_notes(Path(tmp))

    def test_annealing_lammps_input_contains_all_md_stages_and_outputs(self):
        test_annealing_lammps_input_contains_all_md_stages_and_outputs()
