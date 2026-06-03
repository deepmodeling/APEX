import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import pytest

from apex import flow


class FakeTemplate:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class FakeIO:
    def __init__(self, parameters=None, artifacts=None):
        self.parameters = parameters or {}
        self.artifacts = artifacts or {}


class FakeNode:
    def __init__(self, name, template, parameters=None, artifacts=None, key=None, **kwargs):
        self.name = name
        self.template = template
        self.key = key
        self.inputs = FakeIO(parameters=parameters, artifacts=artifacts)
        self.outputs = FakeIO(artifacts={"output_all": f"out-{key or name}"})
        self.kwargs = kwargs


class FakeWorkflow:
    instances = []

    def __init__(self, name, labels=None):
        self.name = name
        self.labels = labels
        self.id = f"id-{name}"
        self.uid = f"uid-{name}"
        self.added = []
        self.submitted = False
        self.terminated = False
        FakeWorkflow.instances.append(self)

    def add(self, item):
        self.added.append(item)

    def submit(self):
        self.submitted = True

    def terminate(self):
        self.terminated = True


class FakeStepInfo:
    def __init__(self, by_id=None, by_parent=None):
        self.by_id = by_id or {}
        self.by_parent = by_parent or {}

    def get_step(self, id=None, parent_id=None, **kwargs):
        if id is not None:
            value = self.by_id.get(id, [])
        elif parent_id is not None:
            value = self.by_parent.get(parent_id, [])
        else:
            value = []
        return value if isinstance(value, list) else [value]


def make_generator(**kwargs):
    defaults = {
        "make_image": "make-img",
        "run_image": "run-img",
        "post_image": "post-img",
        "run_command": "run-cmd",
        "calculator": "lammps",
        "run_op": object,
        "group_size": 2,
        "pool_size": 3,
        "executor": "executor",
        "upload_python_packages": ["pkg"],
    }
    defaults.update(kwargs)
    return flow.FlowGenerator(**defaults)


def patch_dflow_builders(monkeypatch):
    FakeWorkflow.instances.clear()
    monkeypatch.setattr(flow, "Workflow", FakeWorkflow)
    monkeypatch.setattr(flow, "Step", FakeNode)
    monkeypatch.setattr(flow, "Task", FakeNode)
    monkeypatch.setattr(flow, "RelaxationFlow", lambda **kwargs: FakeTemplate(**kwargs))
    monkeypatch.setattr(flow, "SimplePropertySteps", lambda **kwargs: FakeTemplate(**kwargs))
    monkeypatch.setattr(flow, "upload_artifact", lambda path: f"uploaded:{path}")


def make_structure_dirs(tmp_path):
    conf_a = tmp_path / "conf A"
    conf_b = tmp_path / "conf_B"
    conf_a.mkdir()
    conf_b.mkdir()
    return conf_a, conf_b


def clean_flow_key(path):
    return str(path).replace("/", "-").replace(" ", "-").replace("_", "-").lower()


def props_parameter(pattern, **extra):
    data = {
        "structures": [str(pattern)],
        "interaction": {"type": "deepmd", "model": "frozen.pb"},
        "properties": [
            {"type": "eos", "suffix": "01"},
            {"type": "elastic", "skip": True},
            {"type": "vacancy", "reproduce": True},
            {"type": "surface", "req_calc": False},
        ],
    }
    data.update(extra)
    return data


def test_flow_static_helpers_and_failure_formatting():
    assert flow.FlowGenerator.regulate_name(" APEX_Run.01_ ") == "apex-run-01"
    assert flow.FlowGenerator._sanitize_log_name(" bad/name!* ") == "bad-name"
    assert flow.FlowGenerator._sanitize_log_name("!!!") == "failed-step"
    assert flow.FlowGenerator._is_missing_artifact_error(
        RuntimeError("the artifact does not exist in the storage")
    )
    assert flow.FlowGenerator._is_transient_download_error(RuntimeError("SSL timeout"))
    assert not flow.FlowGenerator._is_transient_download_error(RuntimeError("bad input"))
    assert flow.FlowGenerator._safe_get({"a": 1}, "a") == 1
    assert flow.FlowGenerator._safe_get(SimpleNamespace(a=2), "a") == 2

    formatted = flow.FlowGenerator._format_step_failure(
        {
            "phase": "Failed",
            "message": "child 'abc' failed",
            "displayName": "PropsMake",
        },
        "fallback",
        main_log_path=["log-a", "log-b"],
        diagnostic_artifacts=["debug-artifact"],
    )
    assert "fallback" in formatted
    assert "phase: Failed" in formatted
    assert "main_logs: log-a, log-b" in formatted
    assert "failed_artifacts: debug-artifact" in formatted
    assert "raw_step:" not in formatted

    assert flow.FlowGenerator._failed_child_ids_from_step(
        {"message": "child 'a' failed", "outboundNodes": ["b"]}
    ) == ["a", "b"]


def test_format_step_failure_can_include_raw_step_when_requested(monkeypatch):
    monkeypatch.setenv("APEX_SHOW_RAW_STEP", "1")

    formatted = flow.FlowGenerator._format_step_failure(
        {"phase": "Failed", "displayName": "PropsMake", "inputs": {"large": "payload"}},
        "fallback",
    )

    assert "raw_step:" in formatted
    assert "large" in formatted


def test_download_artifact_with_retry_retries_transient_and_stops_on_missing(monkeypatch):
    calls = []

    def fake_download(artifact, path):
        calls.append((artifact, path))
        if len(calls) == 1:
            raise RuntimeError("network timeout")
        return "downloaded"

    monkeypatch.setattr(flow, "download_artifact", fake_download)
    monkeypatch.setattr(flow.time, "sleep", lambda delay: None)

    assert flow.FlowGenerator._download_artifact_with_retry("a", "p", retries=2, delay=0) == "downloaded"
    assert len(calls) == 2

    monkeypatch.setattr(
        flow,
        "download_artifact",
        lambda artifact, path: (_ for _ in ()).throw(
            RuntimeError("the artifact does not exist in the storage")
        ),
    )
    with pytest.raises(RuntimeError, match="without retry"):
        flow.FlowGenerator._download_artifact_with_retry("a", "p", retries=3, delay=0)


def test_step_artifact_lookup_and_main_log_download_from_child(tmp_path, monkeypatch):
    generator = make_generator(debug_mode=True)
    generator.download_path = str(tmp_path)

    parent = {
        "id": "parent",
        "displayName": "parent step",
        "outputs": {"artifacts": {}},
    }
    child = {
        "id": "child",
        "phase": "Failed",
        "displayName": "PropsMake",
        "outputs": {"artifacts": {"main-logs": "main-log-artifact"}},
    }
    step_info = FakeStepInfo(by_parent={"parent": [child]})
    downloads = []

    def fake_download(artifact, path, retries=3, delay=10):
        downloads.append((artifact, path))
        return path

    monkeypatch.setattr(flow.FlowGenerator, "_download_artifact_with_retry", staticmethod(fake_download))

    path, error = generator._download_step_main_logs(parent, "property/step", step_info)

    assert error is None
    assert path.endswith(os.path.join("main-logs", "property", "step", "PropsMake"))
    assert downloads[0][0] == "main-log-artifact"

    no_path, no_error = generator._download_step_main_logs({"outputs": {"artifacts": {}}}, "missing")
    assert no_path is None
    assert "main-logs artifact not found" in no_error


def test_main_log_download_uses_target_dir_when_download_returns_none(tmp_path, monkeypatch):
    generator = make_generator(debug_mode=True)
    generator.download_path = str(tmp_path)
    step = {
        "id": "failed",
        "displayName": "PropsMake",
        "outputs": {"artifacts": {"main-logs": "main-log-artifact"}},
    }

    def fake_download(artifact, path, retries=3, delay=10):
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, "main.log"), "w", encoding="utf-8") as fp:
            fp.write("ValueError: bad annealing temperature\n")
        return None

    monkeypatch.setattr(flow.FlowGenerator, "_download_artifact_with_retry", staticmethod(fake_download))

    path, error = generator._download_step_main_logs(step, "prop-key")
    formatted = flow.FlowGenerator._format_step_failure(
        {"phase": "Failed", "displayName": "PropsMake"},
        "property failed",
        main_log_path=path,
        main_log_error=error,
    )

    assert error is None
    assert path.endswith(os.path.join("main-logs", "prop-key"))
    assert "cause: ValueError: bad annealing temperature" in formatted
    assert "main_logs_excerpt:" in formatted


def test_format_step_failure_includes_traceback_excerpt(tmp_path):
    log_dir = tmp_path / "main-logs"
    log_dir.mkdir()
    (log_dir / "main.log").write_text(
        "setup line\n"
        "Traceback (most recent call last):\n"
        "  File \"/work/apex/core/property/Phonon.py\", line 360, in make_confs\n"
        "    raise RuntimeError('phonopy failed')\n"
        "RuntimeError: phonopy failed\n",
        encoding="utf-8",
    )

    formatted = flow.FlowGenerator._format_step_failure(
        {"phase": "Failed", "displayName": "PropsMake"},
        "property failed",
        main_log_path=str(log_dir),
    )

    assert "main_logs_excerpt:" in formatted
    assert "cause: RuntimeError: phonopy failed" in formatted
    assert "Traceback (most recent call last):" in formatted
    assert "apex/core/property/Phonon.py" in formatted
    assert "RuntimeError: phonopy failed" in formatted


def test_format_step_failure_includes_lammps_diagnostic_excerpt(tmp_path):
    task_dir = tmp_path / "failed-artifacts" / "prop-key" / "RunLAMMPS" / "backward_dir" / "task.000000"
    task_dir.mkdir(parents=True)
    (task_dir / "apex_task_status.json").write_text(
        '{\n'
        '    "state": "failed",\n'
        '    "reason": "command_not_found",\n'
        '    "exit_code": 127\n'
        '}\n',
        encoding="utf-8",
    )
    (task_dir / ".debug.log").write_text(
        "# APEX LAMMPS debug log\n"
        "## Command\n"
        "lmp -in in.lammps\n",
        encoding="utf-8",
    )

    formatted = flow.FlowGenerator._format_step_failure(
        {"phase": "Failed", "displayName": "PropsPost"},
        "property failed",
        diagnostic_artifacts=[str(tmp_path / "failed-artifacts")],
    )

    assert "failed_artifacts_excerpt:" in formatted
    assert "apex_task_status.json" in formatted
    assert '"reason": "command_not_found"' in formatted
    assert "lmp -in in.lammps" in formatted


def test_diagnostic_artifact_downloads_only_allowed_debug_artifacts(tmp_path, monkeypatch):
    generator = make_generator(debug_mode=True)
    generator.download_path = str(tmp_path)
    step = {
        "id": "step",
        "displayName": "PropsPost",
        "outputs": {
            "artifacts": {
                "retrieve_path": "retrieve-art",
                "main-logs": "main-log",
                "dflow_internal": "skip",
                "unrelated": "skip",
            }
        },
    }
    downloads = []

    def fake_download(artifact, path, retries=3, delay=10):
        downloads.append((artifact, path))
        return path

    monkeypatch.setattr(flow.FlowGenerator, "_download_artifact_with_retry", staticmethod(fake_download))

    paths = generator._download_step_diagnostic_artifacts(step, "prop-key")

    assert len(paths) == 1
    assert downloads[0][0] == "retrieve-art"
    assert paths[0].endswith(os.path.join("failed-artifacts", "prop-key", "PropsPost", "retrieve_path"))


def test_set_relax_flows_and_tasks_expand_globs_and_skip_finished(tmp_path, monkeypatch):
    patch_dflow_builders(monkeypatch)
    conf_a, conf_b = make_structure_dirs(tmp_path)
    generator = make_generator()
    relax_param = {
        "structures": [str(tmp_path / "conf*")],
        "relaxation": {"cal_type": "relaxation"},
        "skip_finished_structures": [str(conf_b)],
    }

    flows, flow_keys = generator._set_relax_flows("artifact", relax_param)
    tasks, task_keys = generator._set_relax_tasks("artifact", relax_param)

    assert flow_keys == [
        f"relaxcal-{clean_flow_key(conf_a)}",
        f"relaxcal-{clean_flow_key(conf_b)}",
    ]
    assert len(flows) == 2
    assert flows[0].inputs.parameters["parameter"]["structures"] == [str(conf_a)]
    assert flows[0].template.kwargs["make_image"] == "make-img"

    assert task_keys == [flow_keys[0]]
    assert len(tasks) == 1
    assert tasks[0].inputs.parameters["flow_id"] == str(conf_a)

    skipped, skipped_keys = generator._set_relax_tasks(
        "artifact",
        {"structures": [str(tmp_path / "conf*")], "relaxation": {"req_calc": False}},
    )
    assert skipped == []
    assert skipped_keys == []


def test_set_props_flow_expands_properties_skips_and_removes_existing_dir(tmp_path, monkeypatch):
    patch_dflow_builders(monkeypatch)
    conf_a, conf_b = make_structure_dirs(tmp_path)
    existing = conf_a / "eos_01"
    existing.mkdir()
    (existing / "old.txt").write_text("old")
    parameter = props_parameter(
        tmp_path / "conf*",
        skip_finished_properties=[(str(conf_b), "eos_01")],
    )
    generator = make_generator()

    steps, keys = generator._set_props_flow("artifact", parameter)

    assert len(steps) == 3
    assert len(keys) == 3
    assert not existing.exists()
    assert steps[0].inputs.parameters["path_to_prop"].endswith("eos_01")
    assert steps[0].inputs.parameters["prop_param"]["type"] == "eos"
    assert steps[0].inputs.parameters["inter_param"] == parameter["interaction"]
    assert steps[0].inputs.parameters["do_refine"] is False
    assert steps[1].inputs.parameters["path_to_prop"].endswith("vacancy_reprod")
    assert all("elastic" not in key and "surface" not in key for key in keys)


def test_set_props_tasks_uses_relax_output_or_pre_relaxed_base_and_errors(tmp_path, monkeypatch):
    patch_dflow_builders(monkeypatch)
    conf_a, conf_b = make_structure_dirs(tmp_path)
    generator = make_generator()
    relax_task = FakeNode(
        name="relax-a",
        template=FakeTemplate(),
        parameters={"flow_id": str(conf_a)},
        key="relax-a",
    )
    relax_task.outputs.artifacts["output_all"] = "relaxed-artifact-a"
    parameter = props_parameter(
        tmp_path / "conf*",
        properties=[{"type": "eos", "suffix": "01"}],
        skip_finished_properties=[(str(conf_b), "eos_01")],
    )

    tasks, keys = generator._set_props_tasks(
        [relax_task],
        parameter,
        base_work_artifact="base-artifact",
        pre_relaxed=[str(conf_b)],
    )

    assert len(tasks) == 1
    assert keys == [f"propertycal-{clean_flow_key(conf_a)}-eos-01"]
    assert tasks[0].inputs.artifacts["input_work_path"] == "relaxed-artifact-a"

    with pytest.raises(RuntimeError, match="No relaxation task or pre-relaxed result"):
        generator._set_props_tasks([], parameter, base_work_artifact="base", pre_relaxed=[])


def test_submit_relax_props_and_joint_submit_only_paths(tmp_path, monkeypatch):
    patch_dflow_builders(monkeypatch)
    conf_a, _ = make_structure_dirs(tmp_path)
    generator = make_generator()
    relax_param = {"structures": [str(tmp_path / "conf*")], "relaxation": {"cal_type": "relaxation"}}
    props_param = props_parameter(tmp_path / "conf*", properties=[{"type": "eos", "suffix": "01"}])
    (tmp_path / "download relax").mkdir()
    (tmp_path / "download-props").mkdir()
    (tmp_path / "download-joint").mkdir()
    (tmp_path / "download-empty").mkdir()

    relax_id = generator.submit_relax(
        str(tmp_path),
        str(tmp_path / "download relax"),
        relax_param,
        submit_only=True,
        name="My Flow",
        labels={"kind": "relax"},
    )
    assert relax_id == "id-My Flow-relax"
    assert FakeWorkflow.instances[-1].submitted is True
    assert FakeWorkflow.instances[-1].labels == {"kind": "relax"}
    assert (tmp_path / "download relax" / ".workflow.log").read_text().split("\t")[0] == relax_id

    props_id = generator.submit_props(
        str(tmp_path),
        str(tmp_path / "download-props"),
        props_param,
        submit_only=True,
    )
    assert props_id.endswith("-props")
    assert FakeWorkflow.instances[-1].added

    joint_id = generator.submit_joint(
        str(tmp_path),
        str(tmp_path / "download-joint"),
        relax_param,
        props_param,
        submit_only=True,
    )
    assert joint_id.endswith("-joint")
    assert len(FakeWorkflow.instances[-1].added) == 2

    empty_relax = {
        "structures": [str(conf_a)],
        "relaxation": {"req_calc": False},
    }
    empty_props = props_parameter(
        conf_a,
        properties=[{"type": "eos", "suffix": "01"}],
        skip_finished_properties=[(str(conf_a), "eos_01")],
        pre_relaxed_structures=[str(conf_a)],
    )
    with pytest.raises(RuntimeError, match="No joint workflow tasks to submit"):
        generator.submit_joint(
            str(tmp_path),
            str(tmp_path / "download-empty"),
            empty_relax,
            empty_props,
            submit_only=True,
        )


def test_terminate_workflow_and_raise_if_failed():
    generator = make_generator()
    generator.workflow = FakeWorkflow("wf")
    assert "workflow terminated" in generator._terminate_workflow_after_relax_failure()
    assert generator.workflow.terminated is True

    class CompletedWorkflow:
        def terminate(self):
            raise RuntimeError("cannot shutdown a completed workflow")

    generator.workflow = CompletedWorkflow()
    assert "workflow already completed" in generator._terminate_workflow_after_relax_failure()

    class BrokenWorkflow:
        def terminate(self):
            raise RuntimeError("permission denied")

    generator.workflow = BrokenWorkflow()
    assert "failed to terminate" in generator._terminate_workflow_after_relax_failure()

    with pytest.raises(RuntimeError, match="Property workflow failed with 1 failed step"):
        generator._raise_if_failed(["detail"], "Property workflow")


class TestFlowCoverage(unittest.TestCase):
    def run_with_tmp_and_monkeypatch(self, func):
        monkeypatch = pytest.MonkeyPatch()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                return func(Path(tmp), monkeypatch)
        finally:
            monkeypatch.undo()

    def run_with_monkeypatch(self, func):
        monkeypatch = pytest.MonkeyPatch()
        try:
            return func(monkeypatch)
        finally:
            monkeypatch.undo()

    def test_flow_static_helpers_and_failure_formatting(self):
        test_flow_static_helpers_and_failure_formatting()

    def test_format_step_failure_can_include_raw_step_when_requested(self):
        self.run_with_monkeypatch(
            test_format_step_failure_can_include_raw_step_when_requested
        )

    def test_format_step_failure_includes_traceback_excerpt(self):
        with tempfile.TemporaryDirectory() as tmp:
            test_format_step_failure_includes_traceback_excerpt(Path(tmp))

    def test_format_step_failure_includes_lammps_diagnostic_excerpt(self):
        with tempfile.TemporaryDirectory() as tmp:
            test_format_step_failure_includes_lammps_diagnostic_excerpt(Path(tmp))

    def test_download_artifact_with_retry_retries_transient_and_stops_on_missing(self):
        self.run_with_monkeypatch(
            test_download_artifact_with_retry_retries_transient_and_stops_on_missing
        )

    def test_step_artifact_lookup_and_main_log_download_from_child(self):
        self.run_with_tmp_and_monkeypatch(
            test_step_artifact_lookup_and_main_log_download_from_child
        )

    def test_main_log_download_uses_target_dir_when_download_returns_none(self):
        self.run_with_tmp_and_monkeypatch(
            test_main_log_download_uses_target_dir_when_download_returns_none
        )

    def test_diagnostic_artifact_downloads_only_allowed_debug_artifacts(self):
        self.run_with_tmp_and_monkeypatch(
            test_diagnostic_artifact_downloads_only_allowed_debug_artifacts
        )

    def test_set_relax_flows_and_tasks_expand_globs_and_skip_finished(self):
        self.run_with_tmp_and_monkeypatch(
            test_set_relax_flows_and_tasks_expand_globs_and_skip_finished
        )

    def test_set_props_flow_expands_properties_skips_and_removes_existing_dir(self):
        self.run_with_tmp_and_monkeypatch(
            test_set_props_flow_expands_properties_skips_and_removes_existing_dir
        )

    def test_set_props_tasks_uses_relax_output_or_pre_relaxed_base_and_errors(self):
        self.run_with_tmp_and_monkeypatch(
            test_set_props_tasks_uses_relax_output_or_pre_relaxed_base_and_errors
        )

    def test_submit_relax_props_and_joint_submit_only_paths(self):
        self.run_with_tmp_and_monkeypatch(
            test_submit_relax_props_and_joint_submit_only_paths
        )

    def test_terminate_workflow_and_raise_if_failed(self):
        test_terminate_workflow_and_raise_if_failed()
