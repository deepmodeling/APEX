import unittest
import sys
import os
import glob
import shutil
import subprocess
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
from apex.op.RunVASP import RunVASP
from apex.superop.SimplePropertySteps import SimplePropertySteps
from apex.core.lib.vasp_runtime import build_kpoint_aware_vasp_command
from apex.core.lib import dispatcher as dispatcher_module
from apex.task_failure import (
    REMOTE_LAMMPS_STARTUP_FAILURE,
    TRANSIENT_LAMMPS_RETRY_REASON,
    classify_apex_task_status,
    classify_lammps_exit_code,
    is_header_only_lammps_failure,
    is_lammps_header_only_log,
    load_and_classify_task_status,
)
from apex.utils import (
    all_apex_task_status_succeeded,
    apex_task_succeeded,
    get_task_type,
)
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

        # Calculator compute() returns dpdata as_dict with nested data.energies
        as_dict = {
            "@module": "dpdata.system",
            "@class": "LabeledSystem",
            "data": {
                "atom_numbs": [2],
                "energies": {
                    "@module": "numpy",
                    "@class": "array",
                    "dtype": "float64",
                    "data": [-1.0],
                },
            },
        }
        self.assertFalse(is_failed_task_result(as_dict))
        self.assertTrue(
            is_failed_task_result(
                {
                    "@module": "dpdata.system",
                    "@class": "LabeledSystem",
                    "data": {"atom_numbs": [2]},
                }
            )
        )

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
    def test_dispatcher_applies_kpoint_selector_to_vasp_tasks(self):
        captured = {}

        def fake_task(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(**kwargs)

        with tempfile.TemporaryDirectory() as tmpdir:
            task_dir = Path(tmpdir) / "task.000000"
            task_dir.mkdir()
            (task_dir / "KPOINTS").write_text(
                "Automatic mesh\n0\nGamma\n1 1 1\n0 0 0\n"
            )
            with patch.object(
                dispatcher_module.Machine,
                "load_from_dict",
                return_value=object(),
            ), patch.object(
                dispatcher_module.Resources,
                "load_from_dict",
                return_value=object(),
            ), patch.object(
                dispatcher_module, "Task", fake_task
            ), patch.object(
                dispatcher_module,
                "Submission",
                side_effect=lambda **kwargs: SimpleNamespace(**kwargs),
            ):
                dispatcher_module.make_submission(
                    mdata_machine={},
                    mdata_resources={},
                    commands=["mpirun -n 4 /opt/vasp/bin/vasp_std"],
                    work_path=tmpdir,
                    run_tasks=["task.000000"],
                    group_size=1,
                    forward_common_files=[],
                    forward_files=[],
                    backward_files=[],
                    outlog="outlog",
                    errlog="errlog",
                )
        self.assertIn("vasp_gam", captured["command"])
        self.assertIn("vasp_std", captured["command"])
        self.assertIn("KPOINTS", captured["command"])

    def test_vasp_runtime_selects_executable_from_actual_kpoints(self):
        command = build_kpoint_aware_vasp_command(
            "printf vasp_std > selected"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            task_dir = Path(tmpdir)
            gamma = (
                "Automatic mesh\n0\nGamma\n1 1 1\n0 0 0\n"
            )
            (task_dir / "KPOINTS").write_text(gamma)
            subprocess.run(
                ["bash", "-c", command], cwd=task_dir, check=True
            )
            self.assertEqual(
                (task_dir / "selected").read_text(), "vasp_gam"
            )

            non_gamma = (
                "Automatic mesh\n0\nGamma\n2 1 1\n0 0 0\n"
            )
            (task_dir / "KPOINTS").write_text(non_gamma)
            subprocess.run(
                ["bash", "-c", command], cwd=task_dir, check=True
            )
            self.assertEqual(
                (task_dir / "selected").read_text(), "vasp_std"
            )

    def test_vasp_runtime_requires_switchable_executable(self):
        with self.assertRaisesRegex(ValueError, "vasp_std or vasp_gam"):
            build_kpoint_aware_vasp_command("mpirun vasp_ncl")

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
                        "backward_list": f"{name}-backward_list",
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

        added_steps.clear()
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
                run_command="mpirun vasp_std",
                calculator="vasp",
                upload_python_packages=[],
            )

        vasp_run = next(step for step in added_steps if step.name == "PropsVASP-Cal")
        self.assertEqual(
            vasp_run.parameters["backward_list"],
            "Props-make-backward_list",
        )
        self.assertEqual(vasp_run.parameters["log_name"], "outlog")
        self.assertIn(
            "APEX_RUN_COMMAND=",
            vasp_run.parameters["run_image_config"]["command"],
        )
        self.assertIn(
            "vasp_gam",
            vasp_run.parameters["run_image_config"]["command"],
        )
        self.assertIn(
            "vasp_std",
            vasp_run.parameters["run_image_config"]["command"],
        )


class TestRunVASP(unittest.TestCase):
    @staticmethod
    def _write_common_inputs(task_dir):
        (task_dir / "POSCAR").write_text("original-poscar\n")
        (task_dir / "INCAR").write_text("NSW = 1\n")
        (task_dir / "POTCAR").write_text("potcar\n")
        (task_dir / "KPOINTS").write_text(
            "Automatic mesh\n0\nGamma\n1 1 1\n0 0 0\n"
        )
        (task_dir / "fake_vasp.py").write_text(
            "from pathlib import Path\n"
            "import re\n"
            "incar = Path('INCAR').read_text()\n"
            "match = re.search(r'NSW\\s*=\\s*(\\d+)', incar)\n"
            "nsw = match.group(1) if match else 'unset'\n"
            "step_count = int(nsw) if nsw != 'unset' else 0\n"
            "with Path('calls.txt').open('a') as stream:\n"
            "    stream.write(nsw + '\\n')\n"
            "Path('OUTCAR').write_text(\n"
            "    ''.join(\n"
            "        ' POSITION                                       "
            "TOTAL-FORCE (eV/Angst)\\n'\n"
            "        for _ in range(step_count)\n"
            "    )\n"
            "    + 'General timing and accounting informations for this job:\\n'\n"
            "    + 'Total CPU time used (sec): 1.0\\n'\n"
            "    + 'Elapsed time (sec): 1.0\\n'\n"
            "    + 'Voluntary context switches: 1\\n'\n"
            "    + 'extra wrapper line after the VASP footer\\n'\n"
            ")\n"
            "Path('OSZICAR').write_text(\n"
            "    ''.join(f'{step} T= 300 E= 0\\n' "
            "for step in range(1, step_count + 1))\n"
            ")\n"
            "Path('CONTCAR').write_text('contcar NSW=' + nsw + '\\n')\n"
            "Path('XDATCAR').write_text('xdatcar NSW=' + nsw + '\\n')\n"
        )

    @staticmethod
    def _op_input(task_dir, task_name, command, backward_list=None):
        return OPIO({
            "task_name": task_name,
            "task_path": task_dir,
            "backward_list": backward_list or [
                "OUTCAR", "CONTCAR", "XDATCAR"
            ],
            "log_name": "outlog",
            "backward_dir_name": "backward_dir",
            "run_image_config": {"command": command},
            "optional_artifact": None,
            "optional_input": {},
        })

    def test_vasp_backend_is_wired_to_apex_run_op(self):
        task_type, run_op = get_task_type({"interaction": {"type": "vasp"}})
        self.assertEqual(task_type, "vasp")
        self.assertIs(run_op, RunVASP)

    def test_single_stage_vasp_still_runs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            task_dir = root / "input"
            task_dir.mkdir()
            self._write_common_inputs(task_dir)
            cwd = os.getcwd()
            try:
                os.chdir(root)
                result = RunVASP().execute(self._op_input(
                    task_dir, "single", "python fake_vasp.py"
                ))
            finally:
                os.chdir(cwd)

            backward = root / result["backward_dir"]
            self.assertTrue((backward / "OUTCAR").is_file())
            status = loadfn(backward / "apex_vasp_stage_status.json")
            self.assertEqual(status["state"], "succeeded")
            self.assertEqual(status["task_type"], "single_stage")
            self.assertTrue(status["stages"][0]["footer_complete"])
            self.assertEqual(
                (root / "single" / "calls.txt").read_text().splitlines(),
                ["1"],
            )

    def test_finite_t_latt_runs_both_writable_incar_stages(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            task_dir = root / "input"
            task_dir.mkdir()
            self._write_common_inputs(task_dir)
            original_poscar = (task_dir / "POSCAR").read_text()
            (task_dir / "task.json").write_text(
                '{"type": "finite_t_latt"}\n'
            )
            (task_dir / "INCAR.equi").write_text("NSW = 100\n")
            (task_dir / "INCAR.production").write_text("NSW = 300\n")
            (task_dir / "run_command").write_text(
                "set -e\n"
                "cp INCAR.equi INCAR\n"
                'eval "$APEX_RUN_COMMAND"\n'
                "mv OUTCAR OUTCAR.equi\n"
                "[ ! -f XDATCAR ] || mv XDATCAR XDATCAR.equi\n"
                "cp CONTCAR POSCAR\n"
                "cp INCAR.production INCAR\n"
                'eval "$APEX_RUN_COMMAND"\n'
            )
            cwd = os.getcwd()
            try:
                os.chdir(root)
                result = RunVASP().execute(self._op_input(
                    task_dir,
                    "finite",
                    "APEX_RUN_COMMAND='python fake_vasp.py' bash run_command",
                ))
            finally:
                os.chdir(cwd)

            backward = root / result["backward_dir"]
            self.assertEqual(
                (root / "finite" / "calls.txt").read_text().splitlines(),
                ["100", "300"],
            )
            self.assertTrue((backward / "OUTCAR.equi").is_file())
            self.assertTrue((backward / "XDATCAR.equi").is_file())
            status = loadfn(backward / "apex_vasp_stage_status.json")
            self.assertEqual(status["state"], "succeeded")
            self.assertEqual(
                [stage["name"] for stage in status["stages"]],
                ["equi", "production"],
            )
            self.assertEqual(
                [stage["observed_ionic_steps"] for stage in status["stages"]],
                [100, 300],
            )
            self.assertTrue(all(
                stage["footer_complete"] for stage in status["stages"]
            ))
            # Stage switching must mutate only the OP working copy.
            self.assertEqual((task_dir / "INCAR").read_text(), "NSW = 1\n")
            self.assertEqual((task_dir / "POSCAR").read_text(), original_poscar)

    def test_finite_t_latt_runs_nvt_before_npt_stages(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            task_dir = root / "input"
            task_dir.mkdir()
            self._write_common_inputs(task_dir)
            (task_dir / "task.json").write_text(
                '{"type": "finite_t_latt"}\n'
            )
            (task_dir / "INCAR.nvt").write_text("NSW = 50\nISIF = 2\n")
            (task_dir / "INCAR.equi").write_text("NSW = 100\nISIF = 3\n")
            (task_dir / "INCAR.production").write_text(
                "NSW = 300\nISIF = 3\n"
            )
            (task_dir / "run_command").write_text(
                "set -e\n"
                "cp INCAR.nvt INCAR\n"
                'eval "$APEX_RUN_COMMAND"\n'
                "mv OUTCAR OUTCAR.nvt\n"
                "[ ! -f XDATCAR ] || mv XDATCAR XDATCAR.nvt\n"
                "cp CONTCAR POSCAR\n"
                "cp INCAR.equi INCAR\n"
                'eval "$APEX_RUN_COMMAND"\n'
                "mv OUTCAR OUTCAR.equi\n"
                "[ ! -f XDATCAR ] || mv XDATCAR XDATCAR.equi\n"
                "cp CONTCAR POSCAR\n"
                "cp INCAR.production INCAR\n"
                'eval "$APEX_RUN_COMMAND"\n'
            )
            cwd = os.getcwd()
            try:
                os.chdir(root)
                result = RunVASP().execute(self._op_input(
                    task_dir,
                    "finite-three-stage",
                    "APEX_RUN_COMMAND='python fake_vasp.py' bash run_command",
                ))
            finally:
                os.chdir(cwd)

            backward = root / result["backward_dir"]
            self.assertEqual(
                (
                    root / "finite-three-stage" / "calls.txt"
                ).read_text().splitlines(),
                ["50", "100", "300"],
            )
            self.assertTrue((backward / "OUTCAR.nvt").is_file())
            self.assertTrue((backward / "OUTCAR.equi").is_file())
            status = loadfn(backward / "apex_vasp_stage_status.json")
            self.assertEqual(status["state"], "succeeded")
            self.assertEqual(
                [stage["name"] for stage in status["stages"]],
                ["nvt", "equi", "production"],
            )
            self.assertEqual(
                [stage["expected_ionic_steps"] for stage in status["stages"]],
                [50, 100, 300],
            )
            self.assertEqual(
                [stage["observed_ionic_steps"] for stage in status["stages"]],
                [50, 100, 300],
            )

    def test_failed_vasp_preserves_current_stage_evidence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            task_dir = root / "input"
            task_dir.mkdir()
            self._write_common_inputs(task_dir)
            (task_dir / "task.json").write_text(
                '{"type": "finite_t_latt"}\n'
            )
            (task_dir / "INCAR.nvt").write_text("NSW = 50\nISIF = 2\n")
            (task_dir / "INCAR.equi").write_text("NSW = 100\nISIF = 3\n")
            (task_dir / "INCAR.production").write_text(
                "NSW = 300\nISIF = 3\n"
            )
            (task_dir / "fake_fail.py").write_text(
                "from pathlib import Path\n"
                "import re\n"
                "incar = Path('INCAR').read_text()\n"
                "nsw = int(re.search(r'NSW\\s*=\\s*(\\d+)', incar).group(1))\n"
                "if nsw == 50:\n"
                "    Path('OUTCAR').write_text(\n"
                "        ''.join(' POSITION TOTAL-FORCE (eV/Angst)\\n' "
                "for _ in range(nsw))\n"
                "        + 'General timing and accounting informations\\n'\n"
                "        + 'Total CPU time used (sec): 1\\n'\n"
                "        + 'Elapsed time (sec): 1\\n'\n"
                "    )\n"
                "    Path('OSZICAR').write_text(\n"
                "        ''.join(f'{step} T= 300 E= 0\\n' "
                "for step in range(1, nsw + 1))\n"
                "    )\n"
                "    Path('CONTCAR').write_text('completed nvt structure\\n')\n"
                "    Path('XDATCAR').write_text('completed nvt trajectory\\n')\n"
                "else:\n"
                "    Path('OUTCAR').write_text('partial ionic step\\n')\n"
                "    Path('OSZICAR').write_text('DAV: 1\\n')\n"
                "    Path('CONTCAR').write_text('partial structure\\n')\n"
                "    raise SystemExit(7)\n"
            )
            (task_dir / "run_command").write_text(
                "set -e\n"
                "cp INCAR.nvt INCAR\n"
                'eval "$APEX_RUN_COMMAND"\n'
                "mv OUTCAR OUTCAR.nvt\n"
                "mv OSZICAR OSZICAR.nvt\n"
                "cp CONTCAR CONTCAR.nvt\n"
                "mv XDATCAR XDATCAR.nvt\n"
                "cp CONTCAR POSCAR\n"
                "cp INCAR.equi INCAR\n"
                'eval "$APEX_RUN_COMMAND"\n'
                "mv OUTCAR OUTCAR.equi\n"
                "cp CONTCAR POSCAR\n"
                "cp INCAR.production INCAR\n"
                'eval "$APEX_RUN_COMMAND"\n'
            )
            dflow_tmp = root / "dflow-tmp"
            (dflow_tmp / "inputs" / "artifacts").mkdir(parents=True)
            (dflow_tmp / "outputs" / "artifacts").mkdir(parents=True)
            op = RunVASP()
            op.tmp_root = str(dflow_tmp)
            cwd = os.getcwd()
            try:
                os.chdir(root)
                with self.assertRaises(TransientError):
                    op.execute(self._op_input(
                        task_dir,
                        "finite-failed",
                        (
                            "APEX_RUN_COMMAND='python fake_fail.py' "
                            "bash run_command"
                        ),
                    ))
            finally:
                os.chdir(cwd)

            local_evidence = (
                root / "finite-failed" / "backward_dir"
            )
            packed_evidence = (
                dflow_tmp / "outputs" / "artifacts" / "backward_dir"
            )
            for evidence in (local_evidence, packed_evidence):
                self.assertTrue((evidence / "OUTCAR").is_file())
                self.assertTrue((evidence / "OSZICAR").is_file())
                self.assertTrue((evidence / "INCAR").is_file())
                self.assertTrue((evidence / "outlog").is_file())
                self.assertTrue((evidence / "OUTCAR.nvt").is_file())
                self.assertTrue((evidence / "OSZICAR.nvt").is_file())
                self.assertTrue((evidence / "CONTCAR.nvt").is_file())
                self.assertTrue((evidence / "XDATCAR.nvt").is_file())
                failure = loadfn(evidence / "apex_vasp_failure.json")
                self.assertEqual(
                    failure["current_or_next_stage"], "equi"
                )
                self.assertEqual(failure["error_type"], "TransientError")
                status = loadfn(
                    evidence / "apex_vasp_stage_status.json"
                )
                self.assertEqual(status["state"], "failed")
                self.assertEqual(
                    status["missing_or_incomplete_stages"],
                    ["equi", "production"],
                )

    def test_finite_t_latt_rejects_short_stage_with_complete_footer(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            task_dir = root / "input"
            task_dir.mkdir()
            self._write_common_inputs(task_dir)
            (task_dir / "task.json").write_text(
                '{"type": "finite_t_latt"}\n'
            )
            (task_dir / "INCAR.equi").write_text("NSW = 3\n")
            (task_dir / "INCAR.production").write_text("NSW = 5\n")
            (task_dir / "fake_short.py").write_text(
                "from pathlib import Path\n"
                "Path('OUTCAR').write_text(\n"
                "    ' POSITION TOTAL-FORCE (eV/Angst)\\n'\n"
                "    'General timing and accounting informations\\n'\n"
                "    'Total CPU time used (sec): 1\\n'\n"
                "    'Elapsed time (sec): 1\\n'\n"
                ")\n"
                "Path('OSZICAR').write_text('1 T= 300 E= 0\\n')\n"
                "Path('CONTCAR').write_text('partial\\n')\n"
                "Path('XDATCAR').write_text('partial\\n')\n"
            )
            (task_dir / "run_command").write_text(
                "set -e\n"
                "cp INCAR.equi INCAR\n"
                'eval "$APEX_RUN_COMMAND"\n'
                "mv OUTCAR OUTCAR.equi\n"
                "mv OSZICAR OSZICAR.equi\n"
                "cp CONTCAR POSCAR\n"
                "cp INCAR.production INCAR\n"
                'eval "$APEX_RUN_COMMAND"\n'
            )
            cwd = os.getcwd()
            try:
                os.chdir(root)
                with self.assertRaises(TransientError):
                    RunVASP().execute(self._op_input(
                        task_dir,
                        "finite-short",
                        "APEX_RUN_COMMAND='python fake_short.py' bash run_command",
                    ))
            finally:
                os.chdir(cwd)

            status = loadfn(
                root / "finite-short" / "backward_dir"
                / "apex_vasp_stage_status.json"
            )
            self.assertEqual(status["state"], "failed")
            self.assertEqual(
                status["stages"][0]["observed_ionic_steps"], 1
            )
            self.assertIn(
                "ionic_step_count_mismatch",
                status["stages"][0]["failure_reasons"][0],
            )

    def test_single_stage_allows_early_convergence_with_normal_footer(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            task_dir = root / "input"
            task_dir.mkdir()
            self._write_common_inputs(task_dir)
            (task_dir / "INCAR").write_text("NSW = 20\n")
            (task_dir / "fake_vasp.py").write_text(
                (task_dir / "fake_vasp.py").read_text().replace(
                    "range(step_count)", "range(2)"
                )
            )
            cwd = os.getcwd()
            try:
                os.chdir(root)
                result = RunVASP().execute(self._op_input(
                    task_dir, "relax-early", "python fake_vasp.py"
                ))
            finally:
                os.chdir(cwd)

            status = loadfn(
                root / result["backward_dir"]
                / "apex_vasp_stage_status.json"
            )
            self.assertEqual(status["state"], "succeeded")
            self.assertEqual(
                status["stages"][0]["expected_ionic_steps"], 20
            )
            self.assertEqual(
                status["stages"][0]["observed_ionic_steps"], 2
            )

    def test_single_stage_rejects_missing_normal_footer(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            task_dir = root / "input"
            task_dir.mkdir()
            self._write_common_inputs(task_dir)
            (task_dir / "fake_no_footer.py").write_text(
                "from pathlib import Path\n"
                "Path('OUTCAR').write_text(' POSITION TOTAL-FORCE\\n')\n"
                "Path('CONTCAR').write_text('partial\\n')\n"
                "Path('XDATCAR').write_text('partial\\n')\n"
            )
            cwd = os.getcwd()
            try:
                os.chdir(root)
                with self.assertRaises(TransientError):
                    RunVASP().execute(self._op_input(
                        task_dir, "no-footer", "python fake_no_footer.py"
                    ))
            finally:
                os.chdir(cwd)

            status = loadfn(
                root / "no-footer" / "backward_dir"
                / "apex_vasp_stage_status.json"
            )
            self.assertFalse(status["stages"][0]["footer_complete"])
            self.assertTrue(
                status["stages"][0]["failure_reasons"][0].startswith(
                    "missing_footer_markers:"
                )
            )

    def test_single_stage_rejects_footer_outside_tail_region(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            task_dir = root / "input"
            task_dir.mkdir()
            self._write_common_inputs(task_dir)
            (task_dir / "fake_stale_footer.py").write_text(
                "from pathlib import Path\n"
                "footer = (\n"
                "    'General timing and accounting informations\\n'\n"
                "    'Total CPU time used (sec): 1\\n'\n"
                "    'Elapsed time (sec): 1\\n'\n"
                ")\n"
                "Path('OUTCAR').write_text(\n"
                "    footer + ''.join(f'incomplete tail {i}\\n' "
                "for i in range(300))\n"
                ")\n"
                "Path('OSZICAR').write_text('1 T= 300 E= 0\\n')\n"
                "Path('CONTCAR').write_text('partial\\n')\n"
                "Path('XDATCAR').write_text('partial\\n')\n"
            )
            cwd = os.getcwd()
            try:
                os.chdir(root)
                with self.assertRaises(TransientError):
                    RunVASP().execute(self._op_input(
                        task_dir,
                        "stale-footer",
                        "python fake_stale_footer.py",
                    ))
            finally:
                os.chdir(cwd)

            status = loadfn(
                root / "stale-footer" / "backward_dir"
                / "apex_vasp_stage_status.json"
            )
            self.assertFalse(status["stages"][0]["footer_complete"])

    def test_failure_destination_discovers_real_pythonop_tmp_ancestor(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dflow_tmp = Path(tmpdir) / "tmp"
            (dflow_tmp / "inputs" / "artifacts").mkdir(parents=True)
            (dflow_tmp / "outputs" / "artifacts").mkdir(parents=True)
            work_dir = (
                dflow_tmp
                / "confs"
                / "hcp_Ti_36"
                / "finite_t_latt_00"
                / "task.000000"
            )
            work_dir.mkdir(parents=True)
            cwd = os.getcwd()
            try:
                os.chdir(work_dir)
                destinations = RunVASP()._failure_destinations(
                    "backward_dir"
                )
            finally:
                os.chdir(cwd)

            self.assertIn(
                (
                    dflow_tmp
                    / "outputs"
                    / "artifacts"
                    / "backward_dir"
                ).resolve(),
                [path.resolve() for path in destinations],
            )

    def test_single_stage_cannot_pass_finite_t_latt_validation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            task_dir = root / "input"
            task_dir.mkdir()
            self._write_common_inputs(task_dir)
            (task_dir / "task.json").write_text(
                '{"type": "finite_t_latt"}\n'
            )
            cwd = os.getcwd()
            try:
                os.chdir(root)
                with self.assertRaisesRegex(
                    TransientError, "equi"
                ):
                    RunVASP().execute(self._op_input(
                        task_dir, "incomplete", "python fake_vasp.py"
                    ))
            finally:
                os.chdir(cwd)

            status = loadfn(
                root / "incomplete" / "backward_dir"
                / "apex_vasp_stage_status.json"
            )
            self.assertEqual(status["state"], "failed")
            self.assertEqual(
                status["missing_or_incomplete_stages"],
                ["equi", "production"],
            )

    def test_annealing_validates_every_outcar_stage(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            task_dir = root / "input"
            task_dir.mkdir()
            self._write_common_inputs(task_dir)
            (task_dir / "task.json").write_text('{"type": "annealing"}\n')
            (task_dir / "INCAR.eq").write_text("NSW = 10\n")
            (task_dir / "INCAR.production").write_text("NSW = 20\n")
            (task_dir / "run_command").write_text(
                "set -e\n"
                "rm -f OUTCAR.apex XDATCAR.apex\n"
                "cp INCAR.eq INCAR\n"
                'eval "$APEX_RUN_COMMAND"\n'
                "printf '\\nAPEX_STAGE eq\\n' >> OUTCAR.apex\n"
                "cat OUTCAR >> OUTCAR.apex\n"
                "cp CONTCAR POSCAR\n"
                "cp INCAR.production INCAR\n"
                'eval "$APEX_RUN_COMMAND"\n'
                "printf '\\nAPEX_STAGE production\\n' >> OUTCAR.apex\n"
                "cat OUTCAR >> OUTCAR.apex\n"
                "mv OUTCAR.apex OUTCAR\n"
            )
            cwd = os.getcwd()
            try:
                os.chdir(root)
                result = RunVASP().execute(self._op_input(
                    task_dir,
                    "annealing",
                    "APEX_RUN_COMMAND='python fake_vasp.py' bash run_command",
                ))
            finally:
                os.chdir(cwd)

            backward = root / result["backward_dir"]
            status = loadfn(backward / "apex_vasp_stage_status.json")
            self.assertEqual(status["state"], "succeeded")
            self.assertEqual(
                [stage["name"] for stage in status["stages"]],
                ["eq", "production"],
            )
            self.assertEqual(
                [stage["observed_ionic_steps"] for stage in status["stages"]],
                [10, 20],
            )
            self.assertEqual(
                (root / "annealing" / "calls.txt").read_text().splitlines(),
                ["10", "20"],
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

    def test_run_lammps_retries_transient_sigkill_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            task_dir = Path(tmpdir)
            script = task_dir / "sigkill_once.py"
            script.write_text(
                "from pathlib import Path\n"
                "count_file = Path('count.txt')\n"
                "count = int(count_file.read_text()) if count_file.exists() else 0\n"
                "count_file.write_text(str(count + 1))\n"
                "Path('log.lammps').write_text(f'attempt {count + 1}\\n')\n"
                "Path('dump.melting').write_text(f'attempt {count + 1}\\n')\n"
                "raise SystemExit(137 if count == 0 else 0)\n"
            )
            op = RunLAMMPS()
            with patch.dict(os.environ, {"APEX_LAMMPS_HEADER_RETRY_DELAY": "0"}):
                op.execute(OPIO({
                    "input_lammps": task_dir,
                    "run_command": (
                        "APEX_LAMMPS_TRANSIENT_RETRY=1 "
                        f"{sys.executable} {script.name}"
                    ),
                }))

            self.assertEqual((task_dir / "count.txt").read_text(), "2")
            self.assertTrue((task_dir / "log.lammps.attempt1").is_file())
            self.assertTrue((task_dir / "dump.melting.attempt1").is_file())
            status = loadfn(task_dir / "apex_task_status.json")
            self.assertEqual(status["state"], "succeeded")
            self.assertEqual(status["attempts"], 2)
            self.assertEqual(
                status["retry_reason"],
                TRANSIENT_LAMMPS_RETRY_REASON,
            )


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
