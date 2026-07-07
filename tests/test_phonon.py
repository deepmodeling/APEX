import glob
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import dpdata
import numpy as np
from monty.serialization import loadfn
from pymatgen.io.vasp import Incar
from apex.core.property.Phonon import Phonon
from apex.core.calculator.calculator import LAMMPS_INTER_TYPE

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
__package__ = "tests"


class TestPhonon(unittest.TestCase):
    def setUp(self):
        tests_dir = Path(__file__).resolve().parent
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
                    "supercell_size": [2, 2, 2]
                },
            ],
        }

        self.equi_path = "confs/hp-Mo/relaxation/relax_task"
        self.source_path = tests_dir / "equi" / "vasp"
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
        self.assertEqual(self.phonon.task_param()["BAND_POINTS"], 51)
        self.assertEqual(self.phonon.task_param()["PRIMITIVE_AXES"], "P")

    def test_phonopy_setup_command_prefers_v4_setup_tool(self):
        with patch("apex.core.property.Phonon.shutil.which", return_value="/usr/bin/phonopy-init"):
            self.assertEqual(
                Phonon.phonopy_setup_command("-d --dim='2 2 2' -c POSCAR"),
                "phonopy-init -d --dim='2 2 2' -c POSCAR",
            )
            self.assertEqual(
                Phonon.phonopy_command("band.conf"),
                "phonopy band.conf",
            )

        with patch("apex.core.property.Phonon.shutil.which", return_value=None):
            self.assertEqual(
                Phonon.phonopy_setup_command("-d --dim='2 2 2' -c POSCAR"),
                "phonopy -d --dim='2 2 2' -c POSCAR",
            )

    def test_phonopy_writefc_commands_collapse_for_phonopy_2(self):
        with patch("apex.core.property.Phonon.shutil.which", return_value=None):
            self.assertEqual(
                Phonon.phonopy_writefc_commands("phonopy_disp.yaml --writefc"),
                ["phonopy phonopy_disp.yaml --writefc"],
            )

    def test_phonopy_load_commands_prefer_yaml_config_and_keep_legacy_fallback(self):
        work_dir = Path("output/phonopy_load_commands")
        shutil.rmtree(work_dir, ignore_errors=True)
        work_dir.mkdir(parents=True)
        cwd = os.getcwd()
        try:
            os.chdir(work_dir)
            Path("phonopy_disp.yaml").write_text("phonopy yaml\n")
            commands = Phonon.phonopy_load_commands(
                supercell_size=[2, 2, 2],
                cell_file="POSCAR-unitcell",
            )
            self.assertEqual(commands[0], "phonopy phonopy_disp.yaml --config band.conf")
            self.assertIn(
                'phonopy --dim="2 2 2" -c POSCAR-unitcell band.conf',
                commands,
            )
        finally:
            os.chdir(cwd)
            shutil.rmtree(work_dir, ignore_errors=True)

    def test_phonopy_load_commands_can_create_yaml_before_v4_load(self):
        work_dir = Path("output/phonopy_load_commands_no_yaml")
        shutil.rmtree(work_dir, ignore_errors=True)
        work_dir.mkdir(parents=True)
        cwd = os.getcwd()
        try:
            os.chdir(work_dir)
            commands = Phonon.phonopy_load_commands(
                supercell_size=[2, 2, 2],
                cell_file="POSCAR",
            )
            self.assertEqual(
                commands[0],
                Phonon.phonopy_setup_command('-d --dim="2 2 2" -c POSCAR')
                + " && phonopy phonopy_disp.yaml --config band.conf",
            )
            self.assertEqual(commands[-1], 'phonopy --dim="2 2 2" -c POSCAR band.conf')
        finally:
            os.chdir(cwd)
            shutil.rmtree(work_dir, ignore_errors=True)

    def test_phonopy_writefc_load_commands_prefer_yaml_load_mode(self):
        work_dir = Path("output/phonopy_writefc_load_commands")
        shutil.rmtree(work_dir, ignore_errors=True)
        work_dir.mkdir(parents=True)
        cwd = os.getcwd()
        try:
            os.chdir(work_dir)
            Path("phonopy_disp.yaml").write_text("phonopy yaml\n")
            commands = Phonon.phonopy_writefc_load_commands(
                '--dim="2 2 2" -c POSCAR-unitcell --writefc'
            )
            self.assertEqual(commands[0], "phonopy phonopy_disp.yaml --writefc")
            self.assertIn(
                Phonon.phonopy_setup_command('--dim="2 2 2" -c POSCAR-unitcell --writefc'),
                commands,
            )
        finally:
            os.chdir(cwd)
            shutil.rmtree(work_dir, ignore_errors=True)

    def test_writefc_command_falls_back_to_phonopy(self):
        work_dir = Path("output/phonopy_writefc_fallback")
        shutil.rmtree(work_dir, ignore_errors=True)
        work_dir.mkdir(parents=True)
        calls = []
        cwd = os.getcwd()

        def fake_check_call(command, shell):
            self.assertTrue(shell)
            calls.append(command)
            if command.startswith("phonopy-init"):
                raise subprocess.CalledProcessError(2, command)
            Path("FORCE_CONSTANTS").write_text("fake force constants\n")

        try:
            os.chdir(work_dir)
            with patch("apex.core.property.Phonon.shutil.which", return_value="/usr/bin/phonopy-init"), \
                    patch("apex.core.property.Phonon.subprocess.check_call", side_effect=fake_check_call):
                Phonon.run_first_success(
                    Phonon.phonopy_writefc_commands("phonopy_disp.yaml --writefc"),
                    required_file="FORCE_CONSTANTS",
                )
            self.assertEqual(
                calls,
                [
                    "phonopy-init phonopy_disp.yaml --writefc",
                    "phonopy phonopy_disp.yaml --writefc",
                ],
            )
            self.assertTrue(Path("FORCE_CONSTANTS").is_file())
        finally:
            os.chdir(cwd)
            shutil.rmtree(work_dir, ignore_errors=True)

    def test_run_first_success_raises_last_error_when_all_commands_fail(self):
        with patch(
            "apex.core.property.Phonon.subprocess.check_call",
            side_effect=[
                subprocess.CalledProcessError(2, "phonopy-init --writefc"),
                subprocess.CalledProcessError(2, "phonopy --writefc"),
            ],
        ):
            with self.assertRaises(subprocess.CalledProcessError) as context:
                Phonon.run_first_success(["phonopy-init --writefc", "phonopy --writefc"])
        self.assertEqual(context.exception.cmd, "phonopy --writefc")

    def test_run_first_success_requires_output_file_before_accepting_command(self):
        calls = []

        def fake_check_call(command, shell):
            self.assertTrue(shell)
            calls.append(command)
            if command == "second":
                Path("FORCE_CONSTANTS").write_text("created\n")

        work_dir = Path("output/phonopy_required_file")
        shutil.rmtree(work_dir, ignore_errors=True)
        work_dir.mkdir(parents=True)
        cwd = os.getcwd()
        try:
            os.chdir(work_dir)
            with patch("apex.core.property.Phonon.subprocess.check_call", side_effect=fake_check_call):
                Phonon.run_first_success(["first", "second"], required_file="FORCE_CONSTANTS")
            self.assertEqual(calls, ["first", "second"])
        finally:
            os.chdir(cwd)
            shutil.rmtree(work_dir, ignore_errors=True)

    def test_write_band_dat_accepts_nonzero_exit_with_output(self):
        work_dir = Path("output/phonopy_bandplot_nonzero")
        shutil.rmtree(work_dir, ignore_errors=True)
        work_dir.mkdir(parents=True)
        cwd = os.getcwd()

        def fake_run(command, stdout, stderr, text):
            self.assertEqual(command, ["phonopy-bandplot", "--gnuplot", "band.yaml"])
            stdout.write("# distance frequency\n")
            return subprocess.CompletedProcess(command, 1, stderr="warning")

        try:
            os.chdir(work_dir)
            Path("band.yaml").write_text("phonon: []\n")
            with patch("apex.core.property.Phonon.subprocess.run", side_effect=fake_run):
                Phonon.write_band_dat()
            self.assertGreater(Path("band.dat").stat().st_size, 0)
        finally:
            os.chdir(cwd)
            shutil.rmtree(work_dir, ignore_errors=True)

    def test_write_band_dat_accepts_zero_exit_and_raises_on_empty_output(self):
        work_dir = Path("output/phonopy_bandplot_branches")
        shutil.rmtree(work_dir, ignore_errors=True)
        work_dir.mkdir(parents=True)
        cwd = os.getcwd()
        try:
            os.chdir(work_dir)
            Path("band.yaml").write_text("phonon: []\n")

            def successful_run(command, stdout, stderr, text):
                stdout.write("# distance frequency\n")
                return subprocess.CompletedProcess(command, 0, stderr="")

            with patch("apex.core.property.Phonon.subprocess.run", side_effect=successful_run):
                Phonon.write_band_dat()
            self.assertGreater(Path("band.dat").stat().st_size, 0)

            def empty_failed_run(command, stdout, stderr, text):
                return subprocess.CompletedProcess(command, 1, stderr="empty")

            with patch("apex.core.property.Phonon.subprocess.run", side_effect=empty_failed_run):
                with self.assertRaises(subprocess.CalledProcessError):
                    Phonon.write_band_dat()
        finally:
            os.chdir(cwd)
            shutil.rmtree(work_dir, ignore_errors=True)

    def test_write_band_dat_requires_band_yaml(self):
        work_dir = Path("output/phonopy_bandplot_missing_yaml")
        shutil.rmtree(work_dir, ignore_errors=True)
        work_dir.mkdir(parents=True)
        cwd = os.getcwd()
        try:
            os.chdir(work_dir)
            with self.assertRaises(FileNotFoundError):
                Phonon.write_band_dat()
        finally:
            os.chdir(cwd)
            shutil.rmtree(work_dir, ignore_errors=True)

    def _write_phonon_compute_common(self, work_dir):
        work_dir.mkdir(parents=True, exist_ok=True)
        (work_dir / "band_path.json").write_text("[]\n")
        (work_dir / "band.conf").write_text("BAND = 0 0 0  0.5 0 0\n")
        (work_dir / "phonopy_disp.yaml").write_text("displacements: []\n")

    def _write_band_dat_for_compute(self):
        Path("band.dat").write_text("# phonopy bandplot\n#   G X\n\n")

    def test_compute_lower_resolves_task_paths_and_restores_cwd_on_reproduce_error(self):
        work_dir = Path("output/phonon_reproduce_missing_init")
        shutil.rmtree(work_dir, ignore_errors=True)
        self._write_phonon_compute_common(work_dir)
        existing_rel = Path("output/phonon_existing_relative_task")
        existing_rel.mkdir(parents=True, exist_ok=True)
        abs_task = (work_dir / "abs_task").absolute()
        abs_task.mkdir(parents=True)
        cwd = Path.cwd()

        try:
            phonon = Phonon(
                {"type": "phonon", "reproduce": True, "init_from_suffix": "old", "output_suffix": "new"},
                inter_param={"type": "vasp"},
            )
            with self.assertRaisesRegex(RuntimeError, "initial data path"):
                phonon._compute_lower(
                    str(work_dir / "result.json"),
                    [str(abs_task), str(existing_rel), "bare_missing_task", "nested/missing_task"],
                    [],
                )
            self.assertEqual(Path.cwd(), cwd)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)
            shutil.rmtree(existing_rel, ignore_errors=True)

    def test_compute_lower_reproduce_success_and_malformed_band_errors(self):
        work_dir = Path("output/phonon_reproduce_success")
        shutil.rmtree(work_dir, ignore_errors=True)
        self._write_phonon_compute_common(work_dir)
        init_dir = work_dir / "init"
        init_dir.mkdir()

        def fake_post_repro(init_data_path, init_from_suffix, all_tasks, ptr_data, reprod_last_frame):
            self.assertEqual(init_data_path, str(init_dir.absolute()))
            self.assertEqual(init_from_suffix, "old")
            self.assertTrue(reprod_last_frame)
            (work_dir / "band.dat").write_text("# phonopy bandplot\n#   G X\n\n")
            return {"reproduced": True}, ptr_data

        try:
            phonon = Phonon(
                {
                    "type": "phonon",
                    "reproduce": True,
                    "init_from_suffix": "old",
                    "output_suffix": "new",
                    "init_data_path": str(init_dir),
                },
                inter_param={"type": "vasp"},
            )
            with patch("apex.core.property.Phonon.post_repro", side_effect=fake_post_repro):
                result, ptr = phonon._compute_lower(str(work_dir / "result.json"), [], [])
            self.assertTrue(result["reproduced"])
            self.assertIn("G", result["segment"])

            (work_dir / "band.dat").write_text("only one line")
            with patch("apex.core.property.Phonon.post_repro", return_value=({}, "")):
                with self.assertRaisesRegex(ValueError, "empty or malformed"):
                    phonon._compute_lower(str(work_dir / "result2.json"), [], [])
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def test_compute_lower_reproduce_requires_band_dat_from_post_repro(self):
        work_dir = Path("output/phonon_reproduce_missing_band")
        shutil.rmtree(work_dir, ignore_errors=True)
        self._write_phonon_compute_common(work_dir)
        init_dir = work_dir / "init"
        init_dir.mkdir()

        try:
            phonon = Phonon(
                {
                    "type": "phonon",
                    "reproduce": True,
                    "init_from_suffix": "old",
                    "output_suffix": "new",
                    "init_data_path": str(init_dir),
                },
                inter_param={"type": "vasp"},
            )
            with patch("apex.core.property.Phonon.post_repro", return_value=({}, "")):
                with self.assertRaisesRegex(FileNotFoundError, "band.dat was not created"):
                    phonon._compute_lower(str(work_dir / "result.json"), [], [])
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def test_compute_lower_abacus_uses_phonopy_init_for_forces_and_phonopy_for_band(self):
        work_dir = Path("output/phonon_abacus_compute")
        shutil.rmtree(work_dir, ignore_errors=True)
        self._write_phonon_compute_common(work_dir)
        task_dir = work_dir / "task.000000"
        (task_dir / "OUT.ABACUS").mkdir(parents=True)
        (task_dir / "band.conf").write_text((work_dir / "band.conf").read_text())
        (task_dir / "STRU.ori").write_text("STRU\n")
        (work_dir / "STRU").write_text("STRU\n")
        (task_dir / "phonopy_disp.yaml").write_text((work_dir / "phonopy_disp.yaml").read_text())
        (task_dir / "OUT.ABACUS" / "running_scf.log").write_text("force log\n")
        calls = []

        def fake_check_call(command, shell):
            self.assertTrue(shell)
            calls.append(command)
            if command.startswith(Phonon.phonopy_setup_command("-f")):
                Path("FORCE_SETS").write_text("fake force sets\n")
            elif command in Phonon.phonopy_load_commands():
                Path("band.yaml").write_text("phonon: []\n")

        try:
            phonon = Phonon({"type": "phonon"}, inter_param={"type": "abacus"})
            with patch("apex.core.property.Phonon.subprocess.check_call", side_effect=fake_check_call), \
                    patch.object(Phonon, "write_band_dat", side_effect=self._write_band_dat_for_compute):
                phonon._compute_lower(str(work_dir / "result.json"), [str(task_dir)], [])
            self.assertEqual(calls[0], Phonon.phonopy_setup_command("-f task.0*/OUT.ABACUS/running_scf.log"))
            self.assertEqual(calls[1], Phonon.phonopy_command("phonopy_disp.yaml --config band.conf"))
            self.assertFalse(any("--abacus" in command and command.startswith("phonopy band.conf") for command in calls))
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def test_compute_lower_abacus_requires_force_sets_after_setup(self):
        work_dir = Path("output/phonon_abacus_missing_force_sets")
        shutil.rmtree(work_dir, ignore_errors=True)
        self._write_phonon_compute_common(work_dir)
        task_dir = work_dir / "task.000000"
        (task_dir / "OUT.ABACUS").mkdir(parents=True)
        (task_dir / "band.conf").write_text((work_dir / "band.conf").read_text())
        (task_dir / "STRU.ori").write_text("STRU\n")
        (work_dir / "STRU").write_text("STRU\n")
        (task_dir / "phonopy_disp.yaml").write_text((work_dir / "phonopy_disp.yaml").read_text())
        (task_dir / "OUT.ABACUS" / "running_scf.log").write_text("force log\n")

        try:
            phonon = Phonon({"type": "phonon"}, inter_param={"type": "abacus"})
            with patch("apex.core.property.Phonon.subprocess.check_call", return_value=0):
                with self.assertRaisesRegex(FileNotFoundError, "FORCE_SETS was not created"):
                    phonon._compute_lower(str(work_dir / "result.json"), [str(task_dir)], [])
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def test_compute_lower_vasp_linear_uses_setup_for_fc_and_phonopy_for_band(self):
        work_dir = Path("output/phonon_vasp_linear_compute")
        shutil.rmtree(work_dir, ignore_errors=True)
        self._write_phonon_compute_common(work_dir)
        task_dir = work_dir / "task.000000"
        task_dir.mkdir(parents=True)
        (task_dir / "band.conf").write_text((work_dir / "band.conf").read_text())
        (task_dir / "POSCAR-unitcell").write_text("POSCAR\n")
        (task_dir / "vasprun.xml").write_text("<modeling />\n")
        calls = []

        def fake_check_call(command, shell):
            self.assertTrue(shell)
            calls.append(command)
            if command == Phonon.phonopy_setup_command("--fc vasprun.xml"):
                Path("FORCE_CONSTANTS").write_text("fake force constants\n")
            elif command in Phonon.phonopy_load_commands(
                supercell_size=[2, 2, 2],
                cell_file="POSCAR-unitcell",
            ):
                Path("band.yaml").write_text("phonon: []\n")

        try:
            phonon = Phonon({"type": "phonon", "supercell_size": [2, 2, 2]}, inter_param={"type": "vasp"})
            with patch("apex.core.property.Phonon.subprocess.check_call", side_effect=fake_check_call), \
                    patch.object(Phonon, "write_band_dat", side_effect=self._write_band_dat_for_compute):
                phonon._compute_lower(str(work_dir / "result.json"), [str(task_dir)], [])
            self.assertEqual(calls[0], Phonon.phonopy_setup_command("--fc vasprun.xml"))
            self.assertEqual(
                calls[1],
                Phonon.phonopy_setup_command('-d --dim="2 2 2" -c POSCAR-unitcell')
                + " && phonopy phonopy_disp.yaml --config band.conf",
            )
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def test_compute_lower_vasp_displacement_uses_setup_for_force_sets_and_phonopy_for_band(self):
        work_dir = Path("output/phonon_vasp_displacement_compute")
        shutil.rmtree(work_dir, ignore_errors=True)
        self._write_phonon_compute_common(work_dir)
        task_dir = work_dir / "task.000000"
        task_dir.mkdir(parents=True)
        (task_dir / "band.conf").write_text((work_dir / "band.conf").read_text())
        (task_dir / "phonopy_disp.yaml").write_text((work_dir / "phonopy_disp.yaml").read_text())
        (work_dir / "POSCAR-unitcell").write_text("POSCAR\n")
        (task_dir / "vasprun.xml").write_text("<modeling />\n")
        calls = []

        def fake_check_call(command, shell):
            self.assertTrue(shell)
            calls.append(command)
            if command == Phonon.phonopy_setup_command("-f task.0*/vasprun.xml"):
                Path("FORCE_SETS").write_text("fake force sets\n")
            elif command in Phonon.phonopy_load_commands(
                supercell_size=[2, 2, 2],
                cell_file="POSCAR-unitcell",
            ):
                Path("band.yaml").write_text("phonon: []\n")

        try:
            phonon = Phonon(
                {"type": "phonon", "supercell_size": [2, 2, 2], "approach": "displacement"},
                inter_param={"type": "vasp"},
            )
            with patch("apex.core.property.Phonon.subprocess.check_call", side_effect=fake_check_call), \
                    patch.object(Phonon, "write_band_dat", side_effect=self._write_band_dat_for_compute):
                phonon._compute_lower(str(work_dir / "result.json"), [str(task_dir)], [])
            self.assertEqual(calls[0], Phonon.phonopy_setup_command("-f task.0*/vasprun.xml"))
            self.assertEqual(calls[1], Phonon.phonopy_command("phonopy_disp.yaml --config band.conf"))
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def test_compute_lower_vasp_displacement_requires_force_sets_after_setup(self):
        work_dir = Path("output/phonon_vasp_displacement_missing_force_sets")
        shutil.rmtree(work_dir, ignore_errors=True)
        self._write_phonon_compute_common(work_dir)
        task_dir = work_dir / "task.000000"
        task_dir.mkdir(parents=True)
        (task_dir / "band.conf").write_text((work_dir / "band.conf").read_text())
        (task_dir / "phonopy_disp.yaml").write_text((work_dir / "phonopy_disp.yaml").read_text())
        (work_dir / "POSCAR-unitcell").write_text("POSCAR\n")
        (task_dir / "vasprun.xml").write_text("<modeling />\n")

        try:
            phonon = Phonon(
                {"type": "phonon", "supercell_size": [2, 2, 2], "approach": "displacement"},
                inter_param={"type": "vasp"},
            )
            with patch("apex.core.property.Phonon.subprocess.check_call", return_value=0):
                with self.assertRaisesRegex(FileNotFoundError, "FORCE_SETS was not created"):
                    phonon._compute_lower(str(work_dir / "result.json"), [str(task_dir)], [])
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def test_compute_lower_lammps_uses_phonopy_for_band(self):
        work_dir = Path("output/phonon_lammps_compute")
        shutil.rmtree(work_dir, ignore_errors=True)
        self._write_phonon_compute_common(work_dir)
        task_dir = work_dir / "task.000000"
        task_dir.mkdir(parents=True)
        (task_dir / "FORCE_CONSTANTS").write_text("fake force constants\n")
        calls = []

        def fake_check_call(command, shell):
            self.assertTrue(shell)
            calls.append(command)
            if command in Phonon.phonopy_load_commands(
                supercell_size=[2, 2, 2],
                cell_file="POSCAR",
            ):
                Path("band.yaml").write_text("phonon: []\n")

        try:
            phonon = Phonon({"type": "phonon", "supercell_size": [2, 2, 2]}, inter_param={"type": LAMMPS_INTER_TYPE[0]})
            with patch("apex.core.property.Phonon.subprocess.check_call", side_effect=fake_check_call), \
                    patch.object(Phonon, "write_band_dat", side_effect=self._write_band_dat_for_compute):
                phonon._compute_lower(str(work_dir / "result.json"), [str(task_dir)], [])
            self.assertEqual(
                calls,
                [
                    Phonon.phonopy_setup_command('-d --dim="2 2 2" -c POSCAR')
                    + " && phonopy phonopy_disp.yaml --config band.conf"
                ],
            )
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def test_make_phonon_conf(self):
        if not os.path.exists(os.path.join(self.equi_path, "CONTCAR")):
            with self.assertRaises(RuntimeError):
                self.phonon.make_confs(self.target_path, self.equi_path)
        shutil.copy(
            self.source_path / "CONTCAR_Mo_bcc",
            os.path.join(self.equi_path, "CONTCAR"),
        )
        task_list = self.phonon.make_confs(self.target_path, self.equi_path)
        dfm_dirs = glob.glob(os.path.join(self.target_path, "task.*"))
        self.assertEqual(len(dfm_dirs), 1)
        self.assertTrue(os.path.isfile(os.path.join(self.target_path, "phonopy_disp.yaml")))
        self.assertTrue(os.path.isfile(os.path.join(self.target_path, "task.000000/band.conf")))
        with open(os.path.join(self.target_path, "task.000000/band.conf")) as fp:
            self.assertIn("PRIMITIVE_AXES = P", fp.read())

    def test_post_process_injects_deepmd_plugin_for_phonon(self):
        deepmd_phonon = Phonon(
            {"type": "phonon", "supercell_size": [2, 2, 2]},
            inter_param={"type": "deepmd"},
        )
        task_dir = Path("output/phonon_deepmd_post/task.000000")
        shutil.rmtree(task_dir.parent, ignore_errors=True)
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "in.lammps").write_text(
            "clear\npair_style deepmd frozen_model.pth\npair_coeff * * Cu O\nrun 0\n"
        )

        try:
            deepmd_phonon.post_process([str(task_dir)])
            rewritten = (task_dir / "in.lammps").read_text()
            self.assertIn("plugin load libdeepmd_lmp.so", rewritten)
            self.assertIn("pair_style deepmd frozen_model.pth", rewritten)
            self.assertNotIn("run 0", rewritten)
        finally:
            shutil.rmtree(task_dir.parent, ignore_errors=True)
