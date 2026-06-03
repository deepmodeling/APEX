import os
import shutil
import tempfile
import unittest
from pathlib import Path

import pytest
from monty.serialization import dumpfn, loadfn

from apex.archive import ResultStorage
from apex.core.calculator.lib import lammps_utils
from apex.core.property.Annealing import Annealing
from apex.reporter.DashReportApp import DashReportApp, return_prop_class, return_prop_type
from apex.reporter.property_report import AnnealingReport


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
    assert prop.start_temp == 4.0
    assert prop.target_temp == 300.0
    assert prop.end_temp == 4.0
    assert prop.temp_ramp_rate == 1000
    assert prop.equi_step == 20000
    assert prop.init_thermo_equil_step == 20000
    assert prop.final_thermo_equil_step == 20000
    assert prop.ramp_step == 0
    assert prop.hold_step == 20000
    assert prop.cool_step == 0
    assert prop.thermostat == "nose_hoover"
    assert prop.ensemble == "npt"
    assert prop.supercell_size == [2, 2, 2]

    task_param = prop.task_param()
    assert task_param["cal_type"] == "annealing"
    assert task_param["cal_setting"]["rdf_bins"] == 100
    assert task_param["cal_setting"]["rdf_cutoff"] == 6.0
    assert task_param["cal_setting"]["req_compute_rdf"] is True
    assert task_param["cal_setting"]["req_compute_msd"] is True
    assert task_param["cal_setting"]["timestep"] == 0.001


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
    assert first_meta["start_temp"] == 300.0
    assert first_meta["target_temp"] == 700.0
    assert first_meta["temp"] == 700.0
    assert first_meta["end_temp"] == 400.0
    assert first_meta["temp_ramp_rate"] == 1000
    assert first_meta["supercell_size"] == [2, 3, 4]
    assert second_meta["target_temp"] == 900.0

    variable_text = (first_task / "variable_Annealing.in").read_text()
    assert "variable nx equal 2" in variable_text
    assert "variable ny equal 3" in variable_text
    assert "variable nz equal 4" in variable_text
    assert "variable start_temp equal 300.00" in variable_text
    assert "variable target_temp equal 700.00" in variable_text
    assert "variable temp equal 700.00" in variable_text
    assert "variable end_temp equal 400.00" in variable_text
    assert "variable temp_ramp_rate equal 1000" in variable_text
    assert "variable equi_step equal 10" in variable_text
    assert "variable ramp_step equal 20" in variable_text
    assert "variable temp_ramp_step equal 20" in variable_text
    assert "variable hold_step equal 30" in variable_text
    assert "variable cool_step equal 40" in variable_text
    assert "variable temp_decline_step equal 40" in variable_text
    assert "variable init_thermo_equil_step equal 10" in variable_text
    assert "variable final_thermo_equil_step equal 30" in variable_text
    assert "variable rdf_bins equal 64" in variable_text
    assert "variable rdf_cutoff equal 8.0" in variable_text
    assert "variable rdf_interval equal 5" in variable_text
    assert "variable rdf_nevery equal 5" in variable_text
    assert "variable rdf_nfreq equal 5" in variable_text
    assert "variable req_compute_msd equal true" in variable_text
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

    assert ptr_data.startswith(str(tmp_path) + "\n")
    assert "task.000000: rdf=[], msd=[], volume_temperature=[]" in ptr_data
    assert res_data["property"] == "annealing"
    assert res_data["tasks"]["task.000000"]["task"] == "task.000000"
    assert res_data["tasks"]["task.000001"]["summary"]["rdf_stages"] == []
    assert (tmp_path / "result.json").is_file()


def write_annealing_analysis_files(task_dir: Path):
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "Annealing.json").write_text(
        '{"start_temp": 300, "target_temp": 1500, "end_temp": 300}',
        encoding="utf-8",
    )
    (task_dir / "rdf.T_ramp_300K_1500K.txt").write_text(
        "# Time-averaged data for fix rdf_ramp\n"
        "# TimeStep Number-of-rows\n"
        "# Row c_myRDF[1] c_myRDF[2] c_myRDF[3]\n"
        "0 2\n"
        "1 0.1 1.0 0.2\n"
        "2 0.2 2.0 0.4\n"
        "100 2\n"
        "1 0.1 1.5 0.3\n"
        "2 0.2 2.5 0.5\n",
        encoding="utf-8",
    )
    (task_dir / "msd.T_ramp_300K_1500K.txt").write_text(
        "# Time-averaged data for fix msd_ramp\n"
        "# TimeStep Number-of-rows\n"
        "# Row c_myMSD_ramp\n"
        "0 4\n"
        "1 0.0\n"
        "2 0.0\n"
        "3 0.0\n"
        "4 0.0\n"
        "200 4\n"
        "1 0.1\n"
        "2 0.2\n"
        "3 0.3\n"
        "4 0.6\n",
        encoding="utf-8",
    )
    (task_dir / "heating_interval_2000.dat").write_text(
        "# Time-averaged data for fix heat_log\n"
        "# TimeStep v_N v_Temp v_Vatom v_pote v_Etotal v_Press\n"
        "2000 128 400 15.5 -1 -2 10\n"
        "4000 128 800 16.0 -1 -2 20\n",
        encoding="utf-8",
    )
    (task_dir / "cooling_interval_2000.dat").write_text(
        "# Time-averaged data for fix cool_log\n"
        "# TimeStep v_N v_Temp v_Vatom v_pote v_Etotal v_Press\n"
        "2000 128 1200 16.5 -1 -2 30\n"
        "4000 128 600 15.8 -1 -2 40\n",
        encoding="utf-8",
    )


def test_annealing_compute_lower_extracts_rdf_msd_and_volume_temperature(tmp_path):
    task_dir = tmp_path / "task.000000"
    write_annealing_analysis_files(task_dir)
    prop = Annealing({"type": "annealing"})

    res_data, ptr_data = prop._compute_lower(
        str(tmp_path / "result.json"),
        [str(task_dir)],
        {},
    )

    task = res_data["tasks"]["task.000000"]
    assert "T_ramp_300K_1500K" in task["rdf"]
    assert task["rdf"]["T_ramp_300K_1500K"]["radius"] == [0.1, 0.2]
    assert task["rdf"]["T_ramp_300K_1500K"]["g_r"] == [1.5, 2.5]
    assert task["msd"]["T_ramp_300K_1500K"]["msd_total"] == [0.0, 0.6]
    assert task["volume_temperature"]["heating"]["temperature"] == [400.0, 800.0]
    assert task["volume_temperature"]["heating"]["total_volume"] == [1984.0, 2048.0]
    assert "volume_temperature=['cooling', 'heating']" in ptr_data
    assert loadfn(tmp_path / "result.json")["tasks"]["task.000000"]["summary"]["rdf_points"] == {
        "T_ramp_300K_1500K": 2
    }


def test_annealing_report_registered_and_builds_graph_table(tmp_path):
    task_dir = tmp_path / "task.000000"
    write_annealing_analysis_files(task_dir)
    prop = Annealing({"type": "annealing"})
    res_data, _ptr_data = prop._compute_lower(
        str(tmp_path / "result.json"),
        [str(task_dir)],
        {},
    )

    assert return_prop_type("annealing_00") == "annealing"
    assert return_prop_class("annealing") is AnnealingReport
    traces, layout = AnnealingReport.plotly_graph(res_data, "work")
    table, df = AnnealingReport.dash_table(res_data)

    assert len(traces) == 4
    assert layout.title.text == "Annealing RDF, MSD, and Volume-Temperature Response"
    assert "RDF points" in df.columns
    assert set(df["Stage"]) == {"T_ramp_300K_1500K", "cooling", "heating"}
    assert table.data


def test_annealing_archive_sync_props_extracts_result(tmp_path):
    prop_dir = tmp_path / "confs" / "mo-bcc" / "annealing_00"
    prop_dir.mkdir(parents=True)
    result_payload = {
        "property": "annealing",
        "tasks": {
            "task.000000": {
                "summary": {
                    "rdf_stages": ["eq_300K"],
                    "msd_stages": ["eq_300K"],
                    "volume_temperature_stages": ["heating"],
                }
            }
        },
    }
    param_payload = {"type": "annealing"}
    dumpfn(result_payload, prop_dir / "result.json")
    dumpfn(param_payload, prop_dir / "param.json")

    storage = ResultStorage(tmp_path)
    storage.sync_props(
        {
            "structures": ["confs/*"],
            "interaction": {"type": "deepmd"},
            "properties": [{"type": "annealing"}],
        }
    )

    archived = storage.result_data["confs/mo-bcc"]["annealing_00"]
    assert archived["parameter"] == param_payload
    assert archived["result"]["tasks"]["task.000000"]["summary"]["rdf_stages"] == ["eq_300K"]


def test_annealing_dash_report_app_builds_graph_and_table(tmp_path):
    task_dir = tmp_path / "task.000000"
    write_annealing_analysis_files(task_dir)
    res_data, _ptr_data = Annealing({"type": "annealing"})._compute_lower(
        str(tmp_path / "result.json"),
        [str(task_dir)],
        {},
    )
    app = DashReportApp(
        {
            "work": {
                "conf": {
                    "annealing_00": {
                        "result": res_data,
                    }
                }
            }
        }
    )

    figure = app.update_graph("annealing_00", "conf")
    table_container = app.update_table("annealing_00", "conf")
    table_div = table_container.children[0]
    table = table_div.children[2]

    assert len(figure.data) == 4
    assert figure.layout.title.text == "Annealing RDF, MSD, and Volume-Temperature Response"
    assert table.id == {"type": "table", "index": 0}
    assert "RDF points" in app.csv_copy(1, table.data)


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

    assert "fix eq_nh all npt temp ${start_temp} ${start_temp} tdamp_var" in script
    assert "fix ramp_nh all npt temp ${start_temp} ${temp} tdamp_var" in script
    assert "fix decline_nh all npt temp ${temp} ${end_temp} tdamp_var" in script
    assert "fix final_eq_nh all npt temp ${end_temp} ${end_temp} tdamp_var" in script
    assert "run ${init_thermo_equil_step}" in script
    assert "run ${temp_ramp_remain_step}" in script
    assert "run ${temp_decline_remain_step}" in script
    assert "run ${final_thermo_equil_remain_step}" in script
    assert "variable        rdf_comm_cutoff equal ${rdf_cutoff}+2.0" in script
    assert "comm_modify    cutoff ${rdf_comm_cutoff}" in script
    assert "compute         myRDF all rdf ${rdf_bins} cutoff ${rdf_cutoff}" in script
    assert "file rdf.T_ramp_${start_temp}K_${temp}K.txt mode vector" in script
    assert "file rdf.T_decline_${temp}K_${end_temp}K.txt mode vector" in script
    assert "file rdf.final_eq_${end_temp}K.txt mode vector" in script
    assert "file msd.T_ramp_${start_temp}K_${temp}K.txt mode vector" in script
    assert "file heating_interval_${thermo_interval}.dat" in script
    assert "file cooling_interval_${thermo_interval}.dat" in script
    assert "dump.T_ramp_nh_${start_temp}K_${temp}K.*" in script
    assert "dump.T_decline_nh_${temp}K_${end_temp}K.*" in script


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

    def test_annealing_compute_lower_extracts_rdf_msd_and_volume_temperature(self):
        with tempfile.TemporaryDirectory() as tmp:
            test_annealing_compute_lower_extracts_rdf_msd_and_volume_temperature(Path(tmp))

    def test_annealing_report_registered_and_builds_graph_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            test_annealing_report_registered_and_builds_graph_table(Path(tmp))

    def test_annealing_archive_sync_props_extracts_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            test_annealing_archive_sync_props_extracts_result(Path(tmp))

    def test_annealing_dash_report_app_builds_graph_and_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            test_annealing_dash_report_app_builds_graph_and_table(Path(tmp))

    def test_annealing_lammps_input_contains_all_md_stages_and_outputs(self):
        test_annealing_lammps_input_contains_all_md_stages_and_outputs()
