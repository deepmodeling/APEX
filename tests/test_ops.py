import unittest
import sys
import os
import glob
import shutil
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path
from dflow.python import (
    OP,
    OPIO,
    OPIOSign,
    Artifact,
    TransientError,
)
from monty.serialization import dumpfn, loadfn

from apex.op.relaxation_ops import RelaxMake, _check_relaxation_outputs
from apex.op.property_ops import (
    PropsMake,
    PropsPost,
    PropsRepairStatusCheck,
    TASK_FAILURE_TOLERANT_TYPES,
    _is_failed_task_status,
)
from apex.op.RunLAMMPS import RunLAMMPS
from apex.superop.SimplePropertySteps import SimplePropertySteps
from apex.task_failure import (
    REMOTE_LAMMPS_STARTUP_FAILURE,
    classify_apex_task_status,
    classify_lammps_exit_code,
    is_header_only_lammps_failure,
    is_lammps_header_only_log,
    load_and_classify_task_status,
)
from apex.utils import apex_task_succeeded, all_apex_task_status_succeeded
from apex.core.property.Property import is_failed_task_result
try:
    from context import write_poscar
except ModuleNotFoundError:
    from tests.context import write_poscar

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
__package__ = "tests"


class TestTaskStatusHelpers(unittest.TestCase):
    def test_is_failed_task_result_accepts_dict_and_mapping_like(self):
        self.assertTrue(is_failed_task_result(None))
        self.assertTrue(is_failed_task_result({"failed": True, "energies": [1.0]}))
        self.assertTrue(is_failed_task_result({"atom_numbs": [1]}))
        self.assertFalse(is_failed_task_result({"energies": [-1.0], "atom_numbs": [1]}))

        class MappingLike:
            def __getitem__(self, key):
                if key == "energies":
                    return [-1.0]
                raise KeyError(key)

        self.assertFalse(is_failed_task_result(MappingLike()))

    def test_task_failure_helpers_cover_error_branches(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            task_dir = Path(tmpdir)
            (task_dir / "log.lammps").write_text("LAMMPS (29 Aug 2024)\n")
            self.assertTrue(is_lammps_header_only_log(task_dir / "log.lammps"))
            self.assertTrue(is_header_only_lammps_failure(task_dir, 1))

            (task_dir / "CONTCAR").write_text("finished\n")
            self.assertFalse(is_header_only_lammps_failure(task_dir, 1))

            broken_status = task_dir / "broken_status.json"
            broken_status.write_text("{not-json")
            self.assertEqual(
                load_and_classify_task_status(broken_status)["reason"],
                "invalid_task_status",
            )

            valid_status = task_dir / "apex_task_status.json"
            valid_status.write_text('{"state": "failed", "exit_code": "bad"}')
            self.assertEqual(
                load_and_classify_task_status(valid_status)["reason"],
                "unknown_failure",
            )

        self.assertEqual(classify_lammps_exit_code(None)["reason"], "unknown_failure")
        self.assertEqual(classify_lammps_exit_code(126)["reason"], "command_not_executable")
        self.assertEqual(classify_lammps_exit_code(129)["reason"], "killed_or_oom")
        self.assertEqual(classify_apex_task_status(None)["reason"], "invalid_task_status")
        self.assertEqual(
            classify_apex_task_status({"state": "failed", "exit_code": "not-int"})["reason"],
            "unknown_failure",
        )
        self.assertEqual(
            classify_apex_task_status({"state": "failed", "exit_code": 0})["reason"],
            "invalid_task_status",
        )

    def test_lammps_header_only_log_treats_read_error_as_not_header_only(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "log.lammps"
            log_path.write_text("LAMMPS (29 Aug 2024)\n")
            with patch("apex.task_failure.Path.read_text", side_effect=OSError("boom")):
                self.assertFalse(is_lammps_header_only_log(log_path))

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

    def test_props_repair_status_check_summarizes_remote_startup_failures(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            input_all = root / "all"
            input_post = root / "post"
            prop_dir = input_post / "confs" / "std-bcc" / "elastic_00"
            task_dir = prop_dir / "task.000003"
            (input_all / "confs").mkdir(parents=True)
            task_dir.mkdir(parents=True)
            (task_dir / "apex_task_status.json").write_text(
                '{"state": "failed", "reason": "nonzero_exit", "exit_code": 1, '
                '"retry_reason": "header_only_lammps_log_after_nonzero_exit"}'
            )
            (task_dir / "log.lammps").write_text("LAMMPS (29 Aug 2024)\n")

            op = PropsRepairStatusCheck()
            out = op.execute(OPIO({
                "input_post": input_post,
                "input_all": input_all,
                "task_names": ["confs/std-bcc/elastic_00/task.000003"],
                "path_to_prop": "confs/std-bcc/elastic_00",
            }))

            self.assertEqual(out["checked_post"], input_post)
            summary = loadfn(prop_dir / "run_status_check.json")
            self.assertEqual(
                summary["retry_eligible_tasks"][0]["reason"],
                REMOTE_LAMMPS_STARTUP_FAILURE,
            )

    def test_props_repair_status_check_short_circuits_empty_or_missing_inputs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            input_all = root / "all"
            input_post = root / "post"
            (input_all / "confs").mkdir(parents=True)
            input_post.mkdir()

            op = PropsRepairStatusCheck()
            empty_out = op.execute(OPIO({
                "input_post": input_post,
                "input_all": input_all,
                "task_names": [],
                "path_to_prop": "confs/std-bcc/eos_00",
            }))
            self.assertEqual(empty_out["checked_post"], input_post)

            missing_src_out = op.execute(OPIO({
                "input_post": input_post,
                "input_all": input_all,
                "task_names": ["confs/std-bcc/eos_00/task.000000"],
                "path_to_prop": "confs/std-bcc/eos_00",
            }))
            self.assertEqual(missing_src_out["checked_post"], input_post)

    def test_props_post_reports_lammps_status_failures_before_compute(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = os.getcwd()
            root = Path(tmpdir)
            input_all = root / "all"
            input_post = root / "post"
            prop_dir = input_post / "confs" / "std-bcc" / "elastic_00"
            task_dir = prop_dir / "task.000000"
            (input_all / "confs").mkdir(parents=True)
            task_dir.mkdir(parents=True)
            (task_dir / "apex_task_status.json").write_text(
                '{"state": "failed", "reason": "nonzero_exit", "exit_code": 7}'
            )

            try:
                with self.assertRaisesRegex(RuntimeError, "LAMMPS failed for property task"):
                    PropsPost().execute(OPIO({
                        "input_post": input_post,
                        "input_all": input_all,
                        "prop_param": {"type": "elastic"},
                        "inter_param": {"type": "deepmd", "model": "model.pb"},
                        "task_names": ["confs/std-bcc/elastic_00/task.000000"],
                        "path_to_prop": "confs/std-bcc/elastic_00",
                    }))
            finally:
                os.chdir(cwd)

    def test_props_post_tolerates_failed_tasks_for_eos(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = os.getcwd()
            root = Path(tmpdir)
            input_all = root / "all"
            input_post = root / "post"
            prop_dir = input_post / "confs" / "std-bcc" / "eos_00"
            task_dir = prop_dir / "task.000000"
            (input_all / "confs").mkdir(parents=True)
            task_dir.mkdir(parents=True)
            (task_dir / "apex_task_status.json").write_text(
                '{"state": "failed", "reason": "nonzero_exit", "exit_code": 7}'
            )

            class FakeProp:
                parameter = {"type": "eos"}

                def compute(self, output_file, print_file, path_to_work):
                    dumpfn({10.0: float("nan")}, output_file)
                    Path(print_file).write_text("ok\n")

            self.assertIn("eos", TASK_FAILURE_TOLERANT_TYPES)
            try:
                with patch(
                    "apex.core.common_prop.make_property_instance",
                    return_value=FakeProp(),
                ):
                    PropsPost().execute(OPIO({
                        "input_post": input_post,
                        "input_all": input_all,
                        "prop_param": {"type": "eos"},
                        "inter_param": {"type": "deepmd", "model": "model.pb"},
                        "task_names": ["confs/std-bcc/eos_00/task.000000"],
                        "path_to_prop": "confs/std-bcc/eos_00",
                    }))
                candidates = list(Path(tmpdir).rglob("failed_lammps_tasks.json"))
                self.assertTrue(candidates, "failed_lammps_tasks.json was not written")
                payload = loadfn(candidates[0])
                self.assertEqual(len(payload["failed_tasks"]), 1)
            finally:
                os.chdir(cwd)


class TestSimplePropertySteps(unittest.TestCase):
    def test_lammps_repair_step_feeds_checked_post_to_post_step(self):
        import apex.superop.SimplePropertySteps as simple_steps

        added_steps = []

        class FakeTemplate:
            def __init__(self, op, **kwargs):
                self.op = op
                self.kwargs = kwargs

        class FakeStep:
            def __init__(self, name, template=None, artifacts=None, parameters=None,
                         with_param=None, key=None, executor=None):
                self.name = name
                self.template = template
                self.artifacts = artifacts or {}
                self.parameters = parameters or {}
                self.with_param = with_param
                self.key = key
                self.executor = executor
                self.outputs = SimpleNamespace(
                    artifacts={
                        "task_paths": f"{name}-task_paths",
                        "output_work_path": f"{name}-output_work_path",
                        "backward_dir": f"{name}-backward_dir",
                        "checked_post": f"{name}-checked_post",
                        "retrieve_path": f"{name}-retrieve_path",
                    },
                    parameters={
                        "task_names": f"{name}-task_names",
                        "njobs": f"{name}-njobs",
                    },
                )

        def fake_add(self, step):
            added_steps.append(step)

        obj = SimplePropertySteps.__new__(SimplePropertySteps)
        object.__setattr__(obj, "inputs", SimpleNamespace(
            parameters={
                "prop_param": "prop-param",
                "inter_param": "inter-param",
                "do_refine": False,
                "path_to_prop": "confs/std-bcc/eos_00",
            },
            artifacts={"input_work_path": "input-work"},
        ))
        object.__setattr__(obj, "outputs", SimpleNamespace(
            artifacts={"retrieve_path": SimpleNamespace(_from=None)}
        ))
        object.__setattr__(obj, "step_keys", {
            "make": "props-make",
            "run": "props-run",
            "post": "props-post",
        })

        with patch.object(simple_steps, "Step", FakeStep), \
                patch.object(simple_steps, "PythonOPTemplate", FakeTemplate), \
                patch.object(simple_steps, "Slices", lambda *args, **kwargs: ("slices", args, kwargs)), \
                patch.object(simple_steps, "argo_range", lambda value: f"range:{value}"), \
                patch.object(simple_steps, "argo_len", lambda value: f"len:{value}"), \
                patch.object(SimplePropertySteps, "add", fake_add):
            obj._build(
                "step",
                make_op=object(),
                run_op=object(),
                post_op=object(),
                make_image="make-image",
                run_image="run-image",
                post_image="post-image",
                run_command="lmp -in in.lammps",
                calculator="lammps",
                upload_python_packages=[],
                group_size=1,
                pool_size=1,
                executor=None,
                repair_op=object(),
            )

        self.assertEqual(
            [step.name for step in added_steps],
            ["Props-make", "PropsLAMMPS-Cal", "Props-run-status-check", "Props-post"],
        )
        post_step = added_steps[-1]
        self.assertEqual(
            post_step.artifacts["input_post"],
            "Props-run-status-check-checked_post",
        )
        self.assertEqual(
            obj.outputs.artifacts["retrieve_path"]._from,
            "Props-post-retrieve_path",
        )


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
            self.assertEqual(status["reason"], "nonzero_lammps_error")
            self.assertEqual(status["exit_code"], 7)
            self.assertEqual(status["debug_log"], ".debug.log")

    def test_run_lammps_classifies_common_exit_codes(self):
        self.assertEqual(RunLAMMPS._classify_exit_code(127)["reason"], "command_not_found")
        self.assertEqual(RunLAMMPS._classify_exit_code(124)["reason"], "timeout")
        self.assertEqual(RunLAMMPS._classify_exit_code(137)["reason"], "killed_or_oom")
        self.assertEqual(RunLAMMPS._classify_exit_code(143)["reason"], "terminated")
        self.assertEqual(
            RunLAMMPS._runtime_int_option(
                "APEX_LAMMPS_HEADER_RETRY=3 lmp -in in.lammps",
                "APEX_LAMMPS_HEADER_RETRY",
                2,
            ),
            3,
        )
        self.assertEqual(
            RunLAMMPS._runtime_int_option(
                "APEX_LAMMPS_HEADER_RETRY=bad lmp -in in.lammps",
                "APEX_LAMMPS_HEADER_RETRY",
                2,
            ),
            2,
        )
        self.assertEqual(
            RunLAMMPS._runtime_float_option(
                "APEX_LAMMPS_HEADER_RETRY_DELAY=bad lmp -in in.lammps",
                "APEX_LAMMPS_HEADER_RETRY_DELAY",
                5.0,
            ),
            5.0,
        )
        self.assertFalse(RunLAMMPS._is_lammps_header_only_log(Path("missing-log")))

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
            with patch.dict(os.environ, {"APEX_LAMMPS_HEADER_RETRY_DELAY": "0"}):
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
            self.assertEqual(status["retry_classification"], REMOTE_LAMMPS_STARTUP_FAILURE)

    def test_run_lammps_classifies_persistent_header_only_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            task_dir = Path(tmpdir)
            script = task_dir / "always_header_only.py"
            script.write_text(
                "from pathlib import Path\n"
                "Path('log.lammps').write_text('LAMMPS (29 Aug 2024)\\n')\n"
                "Path('outlog').write_text('LAMMPS (29 Aug 2024)\\n')\n"
                "raise SystemExit(1)\n"
            )
            op = RunLAMMPS()
            with patch.dict(os.environ, {"APEX_LAMMPS_HEADER_RETRY_DELAY": "0"}):
                op.execute(OPIO({
                    "input_lammps": task_dir,
                    "run_command": f"{sys.executable} {script.name}",
                }))

            status = loadfn(task_dir / "apex_task_status.json")
            self.assertEqual(status["state"], "failed")
            self.assertEqual(status["reason"], REMOTE_LAMMPS_STARTUP_FAILURE)
            self.assertEqual(status["attempts"], 2)


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
