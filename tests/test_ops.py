import unittest
import sys
import os
import glob
import shutil
import tempfile
from pathlib import Path
from dflow.python import (
    OP,
    OPIO,
    OPIOSign,
    Artifact,
    TransientError,
)
from monty.serialization import loadfn

from apex.op.relaxation_ops import RelaxMake, _check_relaxation_outputs
from apex.op.property_ops import PropsMake, _is_failed_task_status
from apex.op.RunLAMMPS import RunLAMMPS
from apex.utils import apex_task_succeeded, all_apex_task_status_succeeded
try:
    from context import write_poscar
except ModuleNotFoundError:
    from tests.context import write_poscar

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
__package__ = "tests"


class TestTaskStatusHelpers(unittest.TestCase):
    def test_failed_status_uses_apex_task_status_fields(self):
        self.assertFalse(_is_failed_task_status({
            "state": "succeeded",
            "exit_code": 0,
        }))
        self.assertTrue(_is_failed_task_status({
            "state": "failed",
            "reason": "nonzero_exit",
            "exit_code": 7,
        }))
        self.assertTrue(_is_failed_task_status({
            "state": "succeeded",
            "exit_code": 7,
        }))

    def test_rerun_finished_helpers_match_status_state(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            task0 = work_dir / "task.000000"
            task1 = work_dir / "task.000001"
            task0.mkdir()
            task1.mkdir()
            (task0 / "apex_task_status.json").write_text('{"state": "succeeded", "exit_code": 0}')
            (task1 / "apex_task_status.json").write_text('{"state": "failed", "exit_code": 7}')

            self.assertTrue(apex_task_succeeded(task0))
            self.assertFalse(apex_task_succeeded(task1))
            self.assertFalse(all_apex_task_status_succeeded(work_dir))

            (task1 / "apex_task_status.json").write_text('{"state": "succeeded", "exit_code": 7}')
            self.assertTrue(all_apex_task_status_succeeded(work_dir))


class TestRunLAMMPSDebug(unittest.TestCase):
    def test_run_lammps_writes_debug_log_on_success(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            task_dir = Path(tmpdir)
            (task_dir / "custom_property.log").write_text("custom log line\n")
            (task_dir / "task.json").write_text('{"type": "custom_lammps_property"}')
            op = RunLAMMPS()
            op.execute(OPIO({
                "input_lammps": task_dir,
                "run_command": "python -c 'print(\"ok\")'",
            }))
            debug_log = task_dir / ".debug.log"
            self.assertTrue(debug_log.is_file())
            text = debug_log.read_text()
            self.assertIn("## Command", text)
            self.assertIn("## Metadata summary", text)
            self.assertIn("custom_lammps_property", text)
            self.assertIn("custom_property.log", text)
            self.assertIn("custom log line", text)
            self.assertIn("exit_code=0", text)
            self.assertTrue((task_dir / ".debug.stdout").is_file())
            self.assertTrue((task_dir / ".debug.stderr").is_file())
            status = loadfn(task_dir / "apex_task_status.json")
            self.assertEqual(status["state"], "succeeded")
            self.assertEqual(status["exit_code"], 0)
            self.assertEqual(status["reason"], "command_exit_zero")

    def test_run_lammps_writes_failed_status_with_debug_log(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            task_dir = Path(tmpdir)
            op = RunLAMMPS()
            op.execute(OPIO({
                "input_lammps": task_dir,
                "run_command": "python -c 'import sys; sys.exit(7)'",
            }))
            self.assertTrue((task_dir / ".debug.log").is_file())
            status = loadfn(task_dir / "apex_task_status.json")
            self.assertEqual(status["state"], "failed")
            self.assertEqual(status["reason"], "nonzero_exit")
            self.assertEqual(status["exit_code"], 7)
            self.assertEqual(status["debug_log"], ".debug.log")

    def test_run_lammps_classifies_common_exit_codes(self):
        self.assertEqual(RunLAMMPS._classify_exit_code(127)["reason"], "command_not_found")
        self.assertEqual(RunLAMMPS._classify_exit_code(137)["reason"], "killed_or_oom")
        self.assertEqual(RunLAMMPS._classify_exit_code(143)["reason"], "terminated")

    def test_run_lammps_retries_header_only_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            task_dir = Path(tmpdir)
            script = task_dir / "retry_once.py"
            script.write_text(
                "from pathlib import Path\n"
                "count_file = Path('count.txt')\n"
                "count = int(count_file.read_text()) if count_file.exists() else 0\n"
                "count_file.write_text(str(count + 1))\n"
                "Path('log.lammps').write_text('LAMMPS (29 Aug 2024)\\n')\n"
                "Path('outlog').write_text('LAMMPS (29 Aug 2024)\\n')\n"
                "if count == 0:\n"
                "    raise SystemExit(1)\n"
                "Path('stress_timeseries.txt').write_text('0 0 0 0 0 0 0\\n')\n"
            )
            op = RunLAMMPS()
            op.execute(OPIO({
                "input_lammps": task_dir,
                "run_command": f"{sys.executable} {script.name}",
            }))

            self.assertEqual((task_dir / "count.txt").read_text(), "2")
            self.assertTrue((task_dir / "log.lammps.attempt1").is_file())
            status = loadfn(task_dir / "apex_task_status.json")
            self.assertEqual(status["state"], "succeeded")
            self.assertEqual(status["attempts"], 2)
            self.assertEqual(status["retry_reason"], "header_only_lammps_log_after_nonzero_exit")


class TestMakeRelaxOPs(unittest.TestCase):
    def setUp(self) -> None:
        cwd = os.getcwd()
        self.path = cwd
        self.vasp_dir = cwd/Path('vasp_input')
        self.abacus_dir = cwd/Path('abacus_input')
        self.lammps_dir = cwd/Path('lammps_input')

        os.chdir(self.vasp_dir)
        shutil.copytree(os.path.join(cwd, 'confs/confs_example0/'), './confs/', dirs_exist_ok=True)
        self.vasp_confs = self.vasp_dir/'confs'
        os.chdir(cwd)

        os.chdir(self.abacus_dir)
        shutil.copytree(os.path.join(cwd, 'confs/confs_abacus_example0/'), './confs/', dirs_exist_ok=True)
        self.abacus_confs = self.abacus_dir/'confs'
        os.chdir(cwd)

        os.chdir(self.lammps_dir)
        shutil.copytree(os.path.join(cwd, 'confs/confs_example0/'), './confs/', dirs_exist_ok=True)
        self.lammps_confs = self.lammps_dir/'confs'
        os.chdir(cwd)

    def tearDown(self) -> None:
        shutil.rmtree(self.vasp_confs)
        shutil.rmtree(self.abacus_confs)
        shutil.rmtree(self.lammps_confs)

    def test_vasp_make_equi(self):
        os.chdir(self.vasp_dir)
        op = RelaxMake()
        out = op.execute(
            OPIO({
            'input': self.vasp_dir,
            'param': loadfn('param_joint.json')
        }))
        os.chdir('..')
        self.assertTrue(os.path.exists(self.vasp_dir/'confs'))
        self.assertTrue(os.path.exists(self.vasp_dir/'confs/std-bcc/relaxation/relax_task'))
        self.assertEqual(out['task_paths'], [self.vasp_dir/'confs/std-bcc/relaxation/relax_task'])

    def test_abacus_make_equi(self):
        os.chdir(self.abacus_dir)
        op = RelaxMake()
        out = op.execute(
            OPIO({
            'input': self.abacus_dir,
            'param': loadfn('param_joint.json')
        }))
        os.chdir('..')
        self.assertTrue(os.path.exists(self.abacus_dir/'confs'))
        self.assertTrue(os.path.exists(self.abacus_dir/'confs/fcc-Al/relaxation/relax_task'))
        self.assertEqual(out['task_paths'], [self.abacus_dir/'confs/fcc-Al/relaxation/relax_task'])

    def test_lammps_make_equi(self):
        os.chdir(self.lammps_dir)
        op = RelaxMake()
        out = op.execute(
            OPIO({
            'input': self.lammps_dir,
            'param': loadfn('param_joint.json')
        }))
        os.chdir('..')
        self.assertTrue(os.path.exists(self.lammps_dir/'confs'))
        self.assertTrue(os.path.exists(self.lammps_dir/'confs/std-bcc/relaxation/relax_task'))
        self.assertEqual(out['task_paths'], [self.lammps_dir/'confs/std-bcc/relaxation/relax_task'])

    def test_check_relaxation_outputs_accepts_complete_task(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            task_dir = Path(tmpdir) / "conf" / "relaxation" / "relax_task"
            task_dir.mkdir(parents=True)
            (task_dir / "CONTCAR").write_text("ok")
            (task_dir / "result.json").write_text("{}")

            _check_relaxation_outputs([str(Path(tmpdir) / "conf")])

    def test_check_relaxation_outputs_reports_failed_task_status(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            task_dir = Path(tmpdir) / "conf" / "relaxation" / "relax_task"
            task_dir.mkdir(parents=True)
            (task_dir / "apex_task_status.json").write_text(
                '{"state": "failed", "reason": "nonzero_exit", "exit_code": 7}'
            )

            with self.assertRaisesRegex(RuntimeError, "apex_task_status.json"):
                _check_relaxation_outputs([str(Path(tmpdir) / "conf")])

    def test_check_relaxation_outputs_reports_missing_contcar(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            task_dir = Path(tmpdir) / "conf" / "relaxation" / "relax_task"
            task_dir.mkdir(parents=True)

            with self.assertRaisesRegex(RuntimeError, "missing CONTCAR"):
                _check_relaxation_outputs([str(Path(tmpdir) / "conf")])


class TestMakePropsOPs(unittest.TestCase):
    @staticmethod
    def _expected_eos_task_count(prop_param):
        vol_start = prop_param["vol_start"]
        vol_end = prop_param["vol_end"]
        vol_step = prop_param["vol_step"]
        return int(round((vol_end - vol_start) / vol_step)) + 1

    def setUp(self) -> None:
        cwd = os.getcwd()
        self.path = cwd
        self.vasp_dir = cwd/Path('vasp_input')
        self.abacus_dir = cwd/Path('abacus_input')
        self.lammps_dir = cwd/Path('lammps_input')

        os.chdir(self.vasp_dir)
        shutil.copytree(os.path.join(cwd, 'confs/confs_example2/'), './confs/', dirs_exist_ok=True)
        self.vasp_confs = self.vasp_dir/'confs'
        os.chdir(cwd)

        os.chdir(self.abacus_dir)
        shutil.copytree(os.path.join(cwd, 'confs/confs_abacus_example2/'), './confs/', dirs_exist_ok=True)
        self.abacus_confs = self.abacus_dir/'confs'
        os.chdir(cwd)

        os.chdir(self.lammps_dir)
        shutil.copytree(os.path.join(cwd, 'confs/confs_example2/'), './confs/', dirs_exist_ok=True)
        self.lammps_confs = self.lammps_dir/'confs'
        os.chdir(cwd)

    def tearDown(self) -> None:
        shutil.rmtree(self.vasp_confs)
        shutil.rmtree(self.abacus_confs)
        shutil.rmtree(self.lammps_confs)

    def test_vasp_make_props(self):
        os.chdir(self.vasp_dir)
        param = loadfn('param_joint.json')
        op = PropsMake()
        out = op.execute(
            OPIO({
                'input_work_path': self.vasp_dir,
                'path_to_prop': 'confs/std-bcc/eos_00',
                'prop_param': param['properties'][0],
                'inter_param': param['interaction'],
                'do_refine': False
            }))
        os.chdir('..')
        self.assertTrue(os.path.exists(self.vasp_dir/'confs'))
        self.assertTrue(os.path.exists(self.vasp_dir/'confs/std-bcc/eos_00'))
        self.assertEqual(
            len(out['task_paths']),
            self._expected_eos_task_count(param['properties'][0])
        )

    def test_abacus_make_props(self):
        os.chdir(self.abacus_dir)
        param = loadfn('param_joint.json')
        op = PropsMake()
        out = op.execute(
            OPIO({
                'input_work_path': self.abacus_dir,
                'path_to_prop': 'confs/fcc-Al/eos_00',
                'prop_param': param['properties'][0],
                'inter_param': param['interaction'],
                'do_refine': False
            }))
        os.chdir('..')
        self.assertTrue(os.path.exists(self.abacus_dir/'confs'))
        self.assertTrue(os.path.exists(self.abacus_dir/'confs/fcc-Al/eos_00'))
        self.assertEqual(
            len(out['task_paths']),
            self._expected_eos_task_count(param['properties'][0])
        )

    def test_lammps_make_props(self):
        os.chdir('lammps_input')
        param = loadfn('param_joint.json')
        op = PropsMake()
        out = op.execute(
            OPIO({
                'input_work_path': self.lammps_dir,
                'path_to_prop': 'confs/std-bcc/eos_00',
                'prop_param': param['properties'][0],
                'inter_param': param['interaction'],
                'do_refine': False
            }))
        os.chdir('..')
        self.assertTrue(os.path.exists(self.lammps_dir/'confs'))
        self.assertTrue(os.path.exists(self.lammps_dir/'confs/std-bcc/eos_00'))
        self.assertEqual(
            len(out['task_paths']),
            self._expected_eos_task_count(param['properties'][0])
        )
