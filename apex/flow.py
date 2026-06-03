import os
import glob
import time
import shutil
import re
import copy
import datetime
import json
from typing import (
    Optional,
    Type,
    Union,
    List
)
import dflow
from dflow import (
    Step,
    Task,
    upload_artifact,
    download_artifact,
    Workflow
)
from dflow.python.op import OP
from dflow.plugins.dispatcher import DispatcherExecutor
from apex.superop.RelaxationFlow import RelaxationFlow
from apex.superop.SimplePropertySteps import SimplePropertySteps
from apex.op.relaxation_ops import RelaxMake, RelaxPost
from apex.op.property_ops import PropsMake, PropsPost
from apex.utils import json2dict, handle_prop_suffix

from dflow.python import upload_packages

upload_packages.append(__file__)


class FlowGenerator:
    def __init__(
            self,
            make_image: str,
            run_image: str,
            post_image: str,
            run_command: str,
            calculator: str,
            run_op: Type[OP],
            relax_make_op: Type[OP] = RelaxMake,
            relax_post_op: Type[OP] = RelaxPost,
            props_make_op: Type[OP] = PropsMake,
            props_post_op: Type[OP] = PropsPost,
            group_size: Optional[int] = None,
            pool_size: Optional[int] = None,
            executor: Optional[DispatcherExecutor] = None,
            upload_python_packages: Optional[List[os.PathLike]] = None,
            debug_mode: bool = False,
    ):
        self.download_path = None
        self.upload_path = None
        self.workflow = None
        self.relax_param = None
        self.props_param = None

        self.relax_make_op = relax_make_op
        self.relax_post_op = relax_post_op
        self.props_make_op = props_make_op
        self.props_post_op = props_post_op
        self.run_op = run_op
        self.make_image = make_image
        self.run_image = run_image
        self.post_image = post_image
        self.run_command = run_command
        self.calculator = calculator
        self.group_size = group_size
        self.pool_size = pool_size
        self.executor = executor
        self.upload_python_packages = upload_python_packages
        self.debug_mode = debug_mode

    @staticmethod
    def regulate_name(name):
        """
        Adjusts the given workflow name to conform to RFC 1123 subdomain requirements.
        It ensures the name is lowercase, contains only alphanumeric characters and hyphens,
        and starts and ends with an alphanumeric character.
        """
        # lowercase the name
        name = name.lower()
        # substitute invalid characters with hyphens
        name = re.sub(r'[^a-z0-9\-]', '-', name)
        # make sure the name starts and ends with an alphanumeric character
        name = re.sub(r'^[^a-z0-9]+', '', name)
        name = re.sub(r'[^a-z0-9]+$', '', name)

        return name

    @staticmethod
    def _is_missing_artifact_error(exc: Exception) -> bool:
        return "the artifact does not exist in the storage" in str(exc).lower()

    @staticmethod
    def _is_transient_download_error(exc: Exception) -> bool:
        message = str(exc).lower()
        markers = (
            "connection",
            "connect",
            "timeout",
            "timed out",
            "temporarily unavailable",
            "network",
            "name resolution",
            "dns",
            "reset by peer",
            "remote disconnected",
            "broken pipe",
            "ssl",
            "proxy",
        )
        return any(marker in message for marker in markers)

    @staticmethod
    def _download_artifact_with_retry(artifact, path, retries: int = 3, delay: int = 10):
        last_exc = None
        for attempt in range(1, retries + 1):
            try:
                return download_artifact(artifact=artifact, path=path)
            except Exception as exc:
                last_exc = exc
                if (
                        FlowGenerator._is_missing_artifact_error(exc)
                        or not FlowGenerator._is_transient_download_error(exc)
                ):
                    raise RuntimeError(f"Artifact download failed without retry: {exc}") from exc
                if attempt >= retries:
                    break
                print(
                    f"Artifact download failed ({attempt}/{retries}): {exc}. "
                    f"Retrying in {delay}s..."
                )
                time.sleep(delay)
        raise RuntimeError(
            f"Artifact download failed after {retries} attempt(s): {last_exc}"
        ) from last_exc

    @staticmethod
    def _format_step_failure(
            step,
            fallback_label: str,
            main_log_path: Optional[Union[str, List[str]]] = None,
            main_log_error: Optional[str] = None,
            diagnostic_artifacts: Optional[List[Union[str, List[str]]]] = None,
    ) -> str:
        if step is None:
            return f"{fallback_label}: no step details available"

        detail_keys = [
            "id",
            "key",
            "phase",
            "message",
            "reason",
            "displayName",
            "finishedAt",
            "startedAt",
        ]
        lines = [fallback_label]
        for key in detail_keys:
            value = None
            if isinstance(step, dict):
                value = step.get(key)
            else:
                value = getattr(step, key, None)
            if value not in (None, ""):
                lines.append(f"  {key}: {value}")

        if main_log_path:
            log_paths = main_log_path
            if isinstance(main_log_path, list):
                main_log_path = ", ".join(str(item) for item in main_log_path)
            lines.append(f"  main_logs: {main_log_path}")
            log_excerpt = FlowGenerator._failure_log_excerpt(log_paths)
            if log_excerpt:
                cause = FlowGenerator._failure_cause_from_excerpt(log_excerpt)
                if cause:
                    lines.append(f"  cause: {cause}")
                lines.append("  main_logs_excerpt:")
                lines.extend(f"    {line}" for line in log_excerpt.splitlines())
        elif main_log_error:
            lines.append(f"  main_logs: unavailable ({main_log_error})")

        if diagnostic_artifacts:
            artifact_text = ", ".join(str(item) for item in diagnostic_artifacts)
            lines.append(f"  failed_artifacts: {artifact_text}")
            artifact_excerpt = FlowGenerator._failure_log_excerpt(diagnostic_artifacts)
            if artifact_excerpt:
                cause = FlowGenerator._failure_cause_from_excerpt(artifact_excerpt)
                if cause:
                    lines.append(f"  cause: {cause}")
                lines.append("  failed_artifacts_excerpt:")
                lines.extend(f"    {line}" for line in artifact_excerpt.splitlines())

        if os.environ.get("APEX_SHOW_RAW_STEP", "").lower() in {"1", "true", "yes"}:
            if isinstance(step, dict):
                try:
                    lines.append("  raw_step: " + json.dumps(step, default=str, sort_keys=True))
                except TypeError:
                    lines.append(f"  raw_step: {step!r}")
            else:
                lines.append(f"  raw_step: {step!r}")
        return "\n".join(lines)

    @staticmethod
    def _failure_log_excerpt(paths, max_lines: int = 80, max_chars: int = 12000) -> str:
        if paths is None:
            return ""
        if isinstance(paths, (str, os.PathLike)):
            path_list = [paths]
        else:
            path_list = list(paths)

        chunks = []
        for raw_path in path_list:
            for path in FlowGenerator._failure_log_candidates(raw_path):
                text = FlowGenerator._read_failure_log_tail(path, max_lines=max_lines)
                if not text:
                    continue
                rel_path = os.path.relpath(path, os.getcwd())
                chunks.append(f"--- {rel_path} ---\n{text}")
                if sum(len(chunk) for chunk in chunks) >= max_chars:
                    break
            if sum(len(chunk) for chunk in chunks) >= max_chars:
                break
        result = "\n".join(chunks)
        if len(result) > max_chars:
            result = "<truncated>\n" + result[-max_chars:]
        return result

    @staticmethod
    def _failure_cause_from_excerpt(excerpt: str) -> str:
        if not excerpt:
            return ""
        lines = [line.strip() for line in excerpt.splitlines() if line.strip()]
        exception_re = re.compile(r"^(?:[A-Za-z_][A-Za-z0-9_.]*)(?:Error|Exception|Warning): .+")
        for line in reversed(lines):
            if exception_re.match(line):
                return line
        markers = ("ERROR", "Error", "error", "Failed", "failed")
        for line in reversed(lines):
            if any(marker in line for marker in markers):
                return line
        return lines[-1] if lines else ""

    @staticmethod
    def _failure_log_candidates(raw_path) -> List[str]:
        if isinstance(raw_path, list):
            candidates = []
            for item in raw_path:
                candidates.extend(FlowGenerator._failure_log_candidates(item))
            return candidates

        path = os.fspath(raw_path)
        if not os.path.exists(path):
            return []
        if os.path.isfile(path):
            return [path]

        preferred_names = {
            "main.log",
            "executor.log",
            "stderr",
            "stdout",
            ".debug.log",
            ".debug.stderr",
            ".debug.stdout",
            "apex_task_status.json",
            "failed_lammps_tasks.json",
            "errlog",
            "outlog",
            "log.lammps",
            "run.log",
        }
        preferred_suffixes = (".log", ".out", ".err")
        candidates = []
        for root, _dirs, files in os.walk(path):
            for name in files:
                file_path = os.path.join(root, name)
                if name in preferred_names or name.endswith(preferred_suffixes):
                    candidates.append(file_path)
        candidates.sort(key=lambda item: (
            os.path.basename(item) not in preferred_names,
            item.count(os.sep),
            item,
        ))
        return candidates[:12]

    @staticmethod
    def _read_failure_log_tail(path: str, max_lines: int = 80) -> str:
        try:
            with open(path, "r", errors="replace") as fp:
                lines = fp.readlines()
        except Exception as exc:
            return f"<unable to read {path}: {exc}>"
        cleaned = [line.rstrip("\n") for line in lines if line.strip()]
        if not cleaned:
            return ""

        traceback_start = None
        for index, line in enumerate(cleaned):
            if line.startswith("Traceback (most recent call last):"):
                traceback_start = index
        if traceback_start is not None:
            selected = cleaned[traceback_start:]
        else:
            selected = cleaned[-max_lines:]
        if len(selected) > max_lines:
            selected = selected[-max_lines:]
        return "\n".join(selected)

    @staticmethod
    def _safe_get(obj, key, default=None):
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    @classmethod
    def _step_artifacts(cls, step):
        outputs = cls._safe_get(step, "outputs")
        if outputs is None:
            return {}
        artifacts = cls._safe_get(outputs, "artifacts", {})
        return artifacts or {}

    @staticmethod
    def _sanitize_log_name(name: str) -> str:
        name = re.sub(r'[^a-zA-Z0-9_.-]', '-', str(name)).strip("-")
        return name or "failed-step"

    @staticmethod
    def _artifact_by_name(artifacts, *names):
        for name in names:
            try:
                artifact = artifacts.get(name)
            except AttributeError:
                try:
                    artifact = artifacts[name]
                except (KeyError, TypeError):
                    artifact = None
            if artifact is not None:
                return artifact
        return None

    def _main_logs_artifact(self, step):
        artifacts = self._step_artifacts(step)
        return self._artifact_by_name(artifacts, "main-logs", "main_logs")

    @classmethod
    def _step_id(cls, step):
        return cls._safe_get(step, "id")

    @classmethod
    def _steps_by_id(cls, step_info, step_ids):
        if step_info is None:
            return []
        found = []
        for step_id in step_ids:
            if not step_id:
                continue
            try:
                found.extend(step_info.get_step(id=step_id))
            except Exception:
                continue
        return found

    @classmethod
    def _failed_child_ids_from_step(cls, step):
        child_ids = []
        message = cls._safe_get(step, "message", "")
        if message:
            child_ids.extend(re.findall(r"child '([^']+)' failed", str(message)))
        outbound_nodes = cls._safe_get(step, "outboundNodes", [])
        if isinstance(outbound_nodes, list):
            child_ids.extend(outbound_nodes)
        elif outbound_nodes:
            child_ids.append(outbound_nodes)
        return child_ids

    def _child_steps_with_main_logs(self, step_info, parent_step):
        if step_info is None or parent_step is None:
            return []
        parent_id = self._safe_get(parent_step, "id")
        if not parent_id:
            return []

        related_steps = self._related_child_steps(step_info, parent_step)
        candidates = []
        for child in related_steps:
            if self._main_logs_artifact(child) is None:
                continue
            phase = self._safe_get(child, "phase", "")
            display_name = self._safe_get(child, "displayName", "")
            candidates.append((phase, display_name, child))

        def candidate_key(item):
            phase, display_name, _ = item
            failed_rank = 0 if phase in {"Failed", "Error"} else 1
            display_name = str(display_name).lower()
            op_rank = 0 if display_name in {"relaxmake", "propsmake", "propspost"} else 1
            return failed_rank, op_rank, display_name

        return [child for _, _, child in sorted(candidates, key=candidate_key)]

    def _related_child_steps(self, step_info, parent_step):
        related_steps = []
        try:
            related_steps.extend(
                step_info.get_step(
                    parent_id=self._safe_get(parent_step, "id"),
                    sort_by_generation=True,
                )
            )
        except Exception:
            pass

        related_steps.extend(
            self._steps_by_id(step_info, self._failed_child_ids_from_step(parent_step))
        )

        seen = set()
        queue = list(related_steps)
        while queue:
            current = queue.pop(0)
            current_id = self._step_id(current)
            if not current_id or current_id in seen:
                continue
            seen.add(current_id)
            try:
                nested_steps = step_info.get_step(
                    parent_id=current_id,
                    sort_by_generation=True,
                )
            except Exception:
                nested_steps = []
            related_steps.extend(nested_steps)
            queue.extend(nested_steps)

        return related_steps

    def _download_step_main_logs(self, step, step_label: str, step_info=None):
        artifact = self._main_logs_artifact(step)
        log_label = step_label
        if artifact is None:
            child_steps = self._child_steps_with_main_logs(step_info, step)
            if child_steps:
                child_step = child_steps[0]
                artifact = self._main_logs_artifact(child_step)
                child_name = self._safe_get(child_step, "displayName", "child-step")
                log_label = os.path.join(step_label, str(child_name))

        if artifact is None:
            return None, "main-logs artifact not found on failed step or its child steps"

        log_dir = os.path.join(
            self.download_path or os.getcwd(),
            "main-logs",
            *[self._sanitize_log_name(item) for item in log_label.split(os.sep)],
        )
        os.makedirs(log_dir, exist_ok=True)
        try:
            downloaded_path = self._download_artifact_with_retry(artifact=artifact, path=log_dir)
            return downloaded_path or log_dir, None
        except Exception as exc:
            return None, str(exc)

    def _download_step_diagnostic_artifacts(self, step, step_label: str, step_info=None):
        if not (self.debug_mode or dflow.config.get("mode") == "debug"):
            return []
        diagnostic_names = {
            "backward_dir",
            "retrieve_path",
            "output_all",
            "output_work_path",
            "task_paths",
        }
        steps = [step]
        steps.extend(self._related_child_steps(step_info, step))
        downloaded = []
        seen = set()
        for item in steps:
            display_name = self._safe_get(item, "displayName", "step")
            artifacts = self._step_artifacts(item)
            for name, artifact in artifacts.items():
                if name.startswith("dflow_") or name == "main-logs":
                    continue
                if name not in diagnostic_names:
                    continue
                key = (self._step_id(item), name)
                if key in seen:
                    continue
                seen.add(key)
                out_dir = os.path.join(
                    self.download_path or os.getcwd(),
                    "failed-artifacts",
                    self._sanitize_log_name(step_label),
                    self._sanitize_log_name(display_name),
                    self._sanitize_log_name(name),
                )
                os.makedirs(out_dir, exist_ok=True)
                try:
                    path = self._download_artifact_with_retry(artifact=artifact, path=out_dir)
                    downloaded.append(path)
                except Exception as exc:
                    downloaded.append(f"{display_name}/{name}: unavailable ({exc})")
        return downloaded

    def _terminate_workflow_after_relax_failure(self):
        try:
            self.workflow.terminate()
            return "workflow terminated to stop pending/running property calculations"
        except Exception as exc:
            message = str(exc)
            if "cannot shutdown a completed workflow" in message:
                return "workflow already completed before automatic termination; root cause is the failed relaxation step above"
            return f"failed to terminate workflow automatically: {exc}"

    def _raise_if_failed(self, failed_entries, workflow_kind: str):
        if failed_entries:
            details = "\n\n".join(failed_entries)
            raise RuntimeError(
                f"{workflow_kind} failed with {len(failed_entries)} failed step(s):\n{details}"
            )

    def _monitor_relax(self):
        print('Waiting for relaxation result...')
        failed_detail = None
        while True:
            time.sleep(4)
            step_info = self.workflow.query()
            wf_status = self.workflow.query_status()
            if wf_status == 'Failed':
                if failed_detail is not None:
                    raise RuntimeError(
                        f"Workflow failed (ID: {self.workflow.id}, UID: {self.workflow.uid})\n"
                        f"{failed_detail}"
                    )
                raise RuntimeError(f'Workflow failed (ID: {self.workflow.id}, UID: {self.workflow.uid})')
            try:
                relax_post = step_info.get_step(name='relaxation-cal')[0]
            except IndexError:
                continue
            if relax_post['phase'] == 'Succeeded':
                print(f'Relaxation finished (ID: {self.workflow.id}, UID: {self.workflow.uid})')
                print('Retrieving completed tasks to local...')
                self._download_artifact_with_retry(
                    artifact=relax_post.outputs.artifacts['retrieve_path'],
                    path=self.download_path
                )
                break
            if relax_post['phase'] == 'Failed':
                main_log_path, main_log_error = self._download_step_main_logs(
                    relax_post,
                    "relaxation-cal",
                    step_info=step_info,
                )
                failed_detail = self._format_step_failure(
                    relax_post,
                    f"Relaxation step failed (ID: {self.workflow.id}, UID: {self.workflow.uid})",
                    main_log_path=main_log_path,
                    main_log_error=main_log_error,
                )
                raise RuntimeError(failed_detail)

    def _monitor_props(
            self,
            subprops_key_list: List[str],
    ):
        subprops_left = subprops_key_list.copy()
        subprops_failed_list = []
        failed_details = []
        print(f'Waiting for sub-property results ({len(subprops_left)} left)...')
        while True:
            time.sleep(4)
            step_info = self.workflow.query()
            for kk in subprops_left:
                try:
                    step = step_info.get_step(key=kk)[0]
                except IndexError:
                    continue
                if step['phase'] == 'Succeeded':
                    print(f'Sub-workflow {kk} finished (ID: {self.workflow.id}, UID: {self.workflow.uid})')
                    print('Retrieving completed tasks to local...')
                    self._download_artifact_with_retry(
                        artifact=step.outputs.artifacts['retrieve_path'],
                        path=self.download_path
                    )
                    subprops_left.remove(kk)
                    if subprops_left:
                        print(f'Waiting for sub-property results ({len(subprops_left)} left)...')
                elif step['phase'] == 'Failed':
                    print(f'Sub-workflow {kk} failed (ID: {self.workflow.id}, UID: {self.workflow.uid})')
                    subprops_failed_list.append(kk)
                    main_log_path, main_log_error = self._download_step_main_logs(
                        step,
                        kk,
                        step_info=step_info,
                    )
                    diagnostic_artifacts = self._download_step_diagnostic_artifacts(
                        step,
                        kk,
                        step_info=step_info,
                    )
                    failed_details.append(
                        self._format_step_failure(
                            step,
                            f"Sub-property {kk} failed (ID: {self.workflow.id}, UID: {self.workflow.uid})",
                            main_log_path=main_log_path,
                            main_log_error=main_log_error,
                            diagnostic_artifacts=diagnostic_artifacts,
                        )
                    )
                    subprops_left.remove(kk)
                    if subprops_left:
                        print(f'Waiting for sub-property results ({len(subprops_left)} left)...')
            if not subprops_left:
                print(f'Workflow finished with {len(subprops_failed_list)} sub-property failed '
                      f'(ID: {self.workflow.id}, UID: {self.workflow.uid})')
                self._raise_if_failed(failed_details, "Property workflow")
                break

    def _set_relax_flows(
            self,
            input_work_dir: dflow.common.S3Artifact,
            relax_parameter: dict
    ) -> [List[Step], List[str]]:
        """
        Build per-structure relaxation subflows so finished structures
        can be posted and retrieved without waiting for others.
        """
        confs = relax_parameter["structures"]
        conf_dirs = []
        for conf in confs:
            conf_dirs.extend(glob.glob(conf))
        conf_dirs = list(set(conf_dirs))
        conf_dirs.sort()

        # reuse a single RelaxationFlow template to keep manifest size small
        relaxation_template = RelaxationFlow(
            name='relaxation-flow',
            make_op=self.relax_make_op,
            run_op=self.run_op,
            post_op=self.relax_post_op,
            make_image=self.make_image,
            run_image=self.run_image,
            post_image=self.post_image,
            run_command=self.run_command,
            calculator=self.calculator,
            group_size=self.group_size,
            pool_size=self.pool_size,
            executor=self.executor,
            upload_python_packages=self.upload_python_packages
        )

        relax_list = []
        relax_key_list = []
        for ii in conf_dirs:
            sub_relax_param = copy.deepcopy(relax_parameter)
            sub_relax_param["structures"] = [ii]
            clean_subflow_id = re.sub(r'[^a-zA-Z0-9-]', '-', ii).lower()
            subflow_key = f'relaxcal-{clean_subflow_id}'
            relax_key_list.append(subflow_key)
            relax_list.append(
                Step(
                    name=f'Relaxation-cal-{clean_subflow_id}',
                    template=relaxation_template,
                    artifacts={
                        "input_work_path": input_work_dir
                    },
                    parameters={
                        "flow_id": ii,
                        "parameter": sub_relax_param
                    },
                    key=subflow_key
                )
            )
        return relax_list, relax_key_list

    def _set_relax_tasks(
            self,
            input_work_dir: dflow.common.S3Artifact,
            relax_parameter: dict
    ) -> [List[Task], List[str]]:
        """
        Task-based version for DAG entry so that Argo schedules per-structure
        relaxations independently and exposes their artifacts to downstream
        property tasks without a global barrier.
        """
        if relax_parameter.get("relaxation", {}).get("req_calc", True) is False:
            return [], []

        confs = relax_parameter["structures"]
        conf_dirs = []
        for conf in confs:
            conf_dirs.extend(glob.glob(conf))
        conf_dirs = list(set(conf_dirs))
        conf_dirs.sort()

        relaxation_template = RelaxationFlow(
            name='relaxation-flow',
            make_op=self.relax_make_op,
            run_op=self.run_op,
            post_op=self.relax_post_op,
            make_image=self.make_image,
            run_image=self.run_image,
            post_image=self.post_image,
            run_command=self.run_command,
            calculator=self.calculator,
            group_size=self.group_size,
            pool_size=self.pool_size,
            executor=self.executor,
            upload_python_packages=self.upload_python_packages
        )

        task_list = []
        task_key_list = []
        skip_finished = set(relax_parameter.get("skip_finished_structures", []))
        for ii in conf_dirs:
            sub_relax_param = copy.deepcopy(relax_parameter)
            sub_relax_param["structures"] = [ii]
            clean_subflow_id = re.sub(r'[^a-zA-Z0-9-]', '-', ii).lower()
            subflow_key = f'relaxcal-{clean_subflow_id}'
            if ii in skip_finished:
                print(f"Skip relaxation for {ii} (marked finished; rerun_finished=False)")
                continue
            task_key_list.append(subflow_key)
            task_list.append(
                Task(
                    name=f'Relaxation-cal-{clean_subflow_id}',
                    template=relaxation_template,
                    artifacts={
                        "input_work_path": input_work_dir
                    },
                    parameters={
                        "flow_id": ii,
                        "parameter": sub_relax_param
                    },
                    key=subflow_key
                )
            )
        return task_list, task_key_list

    def _monitor_relax_flows(self, relax_key_list: List[str]):
        relax_left = relax_key_list.copy()
        relax_failed_list = []
        failed_details = []
        print(f'Waiting for relaxation results ({len(relax_left)} left)...')
        last_count = len(relax_left)
        last_log_ts = time.time()
        while True:
            time.sleep(4)
            step_info = self.workflow.query()
            wf_status = self.workflow.query_status()
            if wf_status == 'Failed' and not relax_left:
                break
            for kk in relax_left.copy():
                try:
                    step = step_info.get_step(key=kk)[0]
                except IndexError:
                    continue
                if step['phase'] == 'Succeeded':
                    print(f'Sub relaxation {kk} finished (ID: {self.workflow.id}, UID: {self.workflow.uid})')
                    print('Retrieving completed tasks to local...')
                    self._download_artifact_with_retry(
                        artifact=step.outputs.artifacts['retrieve_path'],
                        path=self.download_path
                    )
                    relax_left.remove(kk)
                    if relax_left:
                        print(f'Waiting for relaxation results ({len(relax_left)} left)...')
                elif step['phase'] == 'Failed':
                    print(f'Sub relaxation {kk} failed (ID: {self.workflow.id}, UID: {self.workflow.uid})')
                    relax_failed_list.append(kk)
                    main_log_path, main_log_error = self._download_step_main_logs(
                        step,
                        kk,
                        step_info=step_info,
                    )
                    failed_details.append(
                        self._format_step_failure(
                            step,
                            f"Sub relaxation {kk} failed (ID: {self.workflow.id}, UID: {self.workflow.uid})",
                            main_log_path=main_log_path,
                            main_log_error=main_log_error,
                        )
                    )
                    relax_left.remove(kk)
            if not relax_left:
                print(f'Workflow finished with {len(relax_failed_list)} sub-relaxation failed '
                      f'(ID: {self.workflow.id}, UID: {self.workflow.uid})')
                self._raise_if_failed(failed_details, "Relaxation workflow")
                break
            # throttled waiting log
            if len(relax_left) != last_count or time.time() - last_log_ts > 30:
                print(f'Waiting for relaxation results ({len(relax_left)} left)...')
                last_count = len(relax_left)
                last_log_ts = time.time()

    def _monitor_joint_flows(self,
                             relax_key_list: List[str],
                             subprops_key_list: List[str]):
        """
        Monitor relaxation and property subflows together, downloading each
        structure's results as soon as its property step finishes. This avoids
        waiting for all relaxations before observing property completion.
        """
        relax_left = relax_key_list.copy()
        relax_failed = []
        relax_failed_details = []
        props_left = subprops_key_list.copy()
        props_failed = []
        props_failed_details = []
        print(f'Waiting for relax/prop results (relax {len(relax_left)}, props {len(props_left)})...')
        last_counts = (len(relax_left), len(props_left))
        last_log_ts = time.time()
        while relax_left or props_left:
            time.sleep(4)
            step_info = self.workflow.query()

            # relax steps
            for kk in relax_left.copy():
                try:
                    step = step_info.get_step(key=kk)[0]
                except IndexError:
                    continue
                if step['phase'] == 'Succeeded':
                    print(f'Sub relaxation {kk} finished')
                    print('Retrieving completed tasks to local...')
                    retrieve = step.get('outputs', {}).get('artifacts', {}).get('retrieve_path', None)
                    if retrieve:
                        self._download_artifact_with_retry(artifact=retrieve, path=self.download_path)
                    relax_left.remove(kk)
                elif step['phase'] == 'Failed':
                    print(f'Sub relaxation {kk} failed')
                    relax_failed.append(kk)
                    main_log_path, main_log_error = self._download_step_main_logs(
                        step,
                        kk,
                        step_info=step_info,
                    )
                    terminate_message = self._terminate_workflow_after_relax_failure()
                    failure_detail = self._format_step_failure(
                        step,
                        f"Sub relaxation {kk} failed (ID: {self.workflow.id}, UID: {self.workflow.uid})",
                        main_log_path=main_log_path,
                        main_log_error=main_log_error,
                    )
                    relax_failed_details.append(f"{failure_detail}\n  action: {terminate_message}")
                    relax_left.remove(kk)
                    self._raise_if_failed(relax_failed_details, "Joint workflow")

            # property steps
            for kk in props_left.copy():
                try:
                    step = step_info.get_step(key=kk)[0]
                except IndexError:
                    continue
                if step['phase'] == 'Succeeded':
                    print(f'Sub property {kk} finished')
                    print('Retrieving completed tasks to local...')
                    retrieve = step.get('outputs', {}).get('artifacts', {}).get('retrieve_path', None)
                    if retrieve:
                        self._download_artifact_with_retry(artifact=retrieve, path=self.download_path)
                    props_left.remove(kk)
                elif step['phase'] == 'Failed':
                    print(f'Sub property {kk} failed')
                    props_failed.append(kk)
                    main_log_path, main_log_error = self._download_step_main_logs(
                        step,
                        kk,
                        step_info=step_info,
                    )
                    diagnostic_artifacts = self._download_step_diagnostic_artifacts(
                        step,
                        kk,
                        step_info=step_info,
                    )
                    props_failed_details.append(
                        self._format_step_failure(
                            step,
                            f"Sub property {kk} failed (ID: {self.workflow.id}, UID: {self.workflow.uid})",
                            main_log_path=main_log_path,
                            main_log_error=main_log_error,
                            diagnostic_artifacts=diagnostic_artifacts,
                        )
                    )
                    props_left.remove(kk)

            if relax_left or props_left:
                counts = (len(relax_left), len(props_left))
                if counts != last_counts or time.time() - last_log_ts > 30:
                    print(f'Waiting... (relax {counts[0]}, props {counts[1]})')
                    last_counts = counts
                    last_log_ts = time.time()

        print(f'Joint monitoring done: {len(relax_failed)} relax failed, {len(props_failed)} property failed '
              f'(ID: {self.workflow.id}, UID: {self.workflow.uid})')
        self._raise_if_failed(relax_failed_details + props_failed_details, "Joint workflow")

    def dump_flow_id(self):
        log_file = os.path.join(self.download_path, '.workflow.log')
        with open(log_file, 'a') as f:
            timestamp = datetime.datetime.now().isoformat()
            workflow_uid = getattr(self.workflow, "uid", "") or ""
            f.write(
                f'{self.workflow.id}\tsubmit\t{timestamp}\t{self.download_path}\t{workflow_uid}\n'
            )

    def _set_relax_flow(
            self,
            input_work_dir: dflow.common.S3Artifact,
            relax_parameter: dict
    ) -> Step:
        relaxationFlow = RelaxationFlow(
            name='relaxation-flow',
            make_op=self.relax_make_op,
            run_op=self.run_op,
            post_op=self.relax_post_op,
            make_image=self.make_image,
            run_image=self.run_image,
            post_image=self.post_image,
            run_command=self.run_command,
            calculator=self.calculator,
            group_size=self.group_size,
            pool_size=self.pool_size,
            executor=self.executor,
            upload_python_packages=self.upload_python_packages
        )
        relaxation = Step(
            name='relaxation-cal',
            template=relaxationFlow,
            artifacts={
                "input_work_path": input_work_dir
            },
            parameters={
                "flow_id": "relaxflow",
                "parameter": relax_parameter
            },
            key="relaxationcal"
        )
        return relaxation

    def _set_props_flow(
            self,
            input_work_dir: dflow.common.S3Artifact,
            props_parameter: dict
    ) -> [List[Step], List[str]]:

        simplePropertySteps = None

        confs = props_parameter["structures"]
        interaction = props_parameter["interaction"]
        properties = props_parameter["properties"]

        conf_dirs = []
        flow_id_list = []
        path_to_prop_list = []
        prop_param_list = []
        do_refine_list = []
        skip_props = set()
        for item in props_parameter.get("skip_finished_properties", []):
            if isinstance(item, (list, tuple)) and len(item) == 2:
                skip_props.add((item[0], item[1]))
        for conf in confs:
            conf_dirs.extend(glob.glob(conf))
        conf_dirs = list(set(conf_dirs))
        conf_dirs.sort()
        for ii in conf_dirs:
            for jj in properties:
                do_refine, suffix = handle_prop_suffix(jj)
                if not suffix:
                    continue
                property_type = jj["type"]
                path_to_prop = os.path.join(ii, property_type + "_" + suffix)
                prop_dir_name = property_type + "_" + suffix
                if (ii, prop_dir_name) in skip_props:
                    print(f"Skip property {prop_dir_name} for {ii} (marked finished; rerun_finished=False)")
                    continue
                path_to_prop_list.append(path_to_prop)
                if os.path.exists(path_to_prop):
                    shutil.rmtree(path_to_prop)
                prop_param_list.append(jj)
                do_refine_list.append(do_refine)
                flow_id_list.append(ii + '-' + property_type + '-' + suffix)

        nflow = len(path_to_prop_list)

        subprops_list = []
        subprops_key_list = []
        for ii in range(nflow):
            clean_subflow_id = re.sub(r'[^a-zA-Z0-9-]', '-', flow_id_list[ii]).lower()
            subflow_key = f'propertycal-{clean_subflow_id}'
            subprops_key_list.append(subflow_key)
            if simplePropertySteps is None:
                simplePropertySteps = SimplePropertySteps(
                    name='property-flow',
                    make_op=self.props_make_op,
                    run_op=self.run_op,
                    post_op=self.props_post_op,
                    make_image=self.make_image,
                    run_image=self.run_image,
                    post_image=self.post_image,
                    run_command=self.run_command,
                    calculator=self.calculator,
                    group_size=self.group_size,
                    pool_size=self.pool_size,
                    executor=self.executor,
                    upload_python_packages=self.upload_python_packages
                )
            subprops_list.append(
                Step(
                    name=f'Subprop-cal-{clean_subflow_id}',
                    template=simplePropertySteps,
                    artifacts={
                        "input_work_path": input_work_dir
                    },
                    parameters={
                        "flow_id": flow_id_list[ii],
                        "path_to_prop": path_to_prop_list[ii],
                        "prop_param": prop_param_list[ii],
                        "inter_param": interaction,
                        "do_refine": do_refine_list[ii]
                    },
                    key=subflow_key
                )
            )

        return subprops_list, subprops_key_list

    def _set_props_tasks(
            self,
            relax_tasks: List[Task],
            props_parameter: dict,
            base_work_artifact,
            pre_relaxed: List[str]
    ) -> [List[Task], List[str]]:
        """
        Task-based property subflows keyed to corresponding relax tasks for DAG scheduling.
        """
        simplePropertySteps = None

        confs = props_parameter["structures"]
        interaction = props_parameter["interaction"]
        properties = props_parameter["properties"]

        conf_dirs = []
        flow_id_list = []
        path_to_prop_list = []
        prop_param_list = []
        do_refine_list = []
        conf_for_prop = []
        for conf in confs:
            conf_dirs.extend(glob.glob(conf))
        conf_dirs = list(set(conf_dirs))
        conf_dirs.sort()

        # map conf to relax task
        relax_map = {}
        for task in relax_tasks:
            flow_id = task.inputs.parameters.get("flow_id", None)
            if flow_id is not None:
                flow_id = getattr(flow_id, "value", flow_id)
            else:
                flow_id = task.name
            relax_map[flow_id] = task

        for ii in conf_dirs:
            for jj in properties:
                do_refine, suffix = handle_prop_suffix(jj)
                if not suffix:
                    continue
                property_type = jj["type"]
                path_to_prop = os.path.join(ii, property_type + "_" + suffix)
                path_to_prop_list.append(path_to_prop)
                if os.path.exists(path_to_prop):
                    shutil.rmtree(path_to_prop)
                prop_param_list.append(jj)
                do_refine_list.append(do_refine)
                flow_id_list.append(ii + '-' + property_type + '-' + suffix)
                conf_for_prop.append(ii)

        subprops_list = []
        subprops_key_list = []
        pre_relaxed_set = set(pre_relaxed or [])
        skip_props = set()
        for item in props_parameter.get("skip_finished_properties", []):
            if isinstance(item, (list, tuple)) and len(item) == 2:
                skip_props.add((item[0], item[1]))

        for ii, path_to_prop, prop_param, do_refine, flow_id in zip(
                conf_for_prop, path_to_prop_list, prop_param_list, do_refine_list, flow_id_list):
            clean_subflow_id = re.sub(r'[^a-zA-Z0-9-]', '-', flow_id).lower()
            subflow_key = f'propertycal-{clean_subflow_id}'

            # choose artifact source: from corresponding relax task if exists; otherwise from base upload (pre-relaxed)
            if ii in relax_map:
                input_artifact = relax_map[ii].outputs.artifacts["output_all"]
            elif ii in pre_relaxed_set:
                # pre-relaxed data exists in uploaded workspace
                input_artifact = base_work_artifact
            else:
                raise RuntimeError(
                    f"No relaxation task or pre-relaxed result is available for {ii}; "
                    "cannot create joint property task."
                )

            # skip property if already finished and rerun_finished=False
            prop_dir_name = os.path.basename(path_to_prop)
            if (ii, prop_dir_name) in skip_props:
                # don't create task; also remove from monitor list by not adding key
                print(f"Skip property {prop_dir_name} for {ii} (marked finished; rerun_finished=False)")
                continue

            subprops_key_list.append(subflow_key)
            if simplePropertySteps is None:
                simplePropertySteps = SimplePropertySteps(
                    name='property-flow',
                    make_op=self.props_make_op,
                    run_op=self.run_op,
                    post_op=self.props_post_op,
                    make_image=self.make_image,
                    run_image=self.run_image,
                    post_image=self.post_image,
                    run_command=self.run_command,
                    calculator=self.calculator,
                    group_size=self.group_size,
                    pool_size=self.pool_size,
                    executor=self.executor,
                    upload_python_packages=self.upload_python_packages
                )
            subprops_list.append(
                Task(
                    name=f'Subprop-cal-{clean_subflow_id}',
                    template=simplePropertySteps,
                    artifacts={
                        "input_work_path": input_artifact
                    },
                    parameters={
                        "flow_id": flow_id,
                        "path_to_prop": path_to_prop,
                        "prop_param": prop_param,
                        "inter_param": interaction,
                        "do_refine": do_refine
                    },
                    key=subflow_key
                )
            )

        return subprops_list, subprops_key_list

    @json2dict
    def submit_relax(
            self,
            upload_path: Union[os.PathLike, str],
            download_path: Union[os.PathLike, str],
            relax_parameter: dict,
            submit_only: bool = False,
            name: Optional[str] = None,
            labels: Optional[dict] = None
    ) -> str:
        self.upload_path = upload_path
        self.download_path = download_path
        self.relax_param = relax_parameter
        flow_name = name if name else self.regulate_name(os.path.basename(download_path))
        flow_name += '-relax'
        self.workflow = Workflow(name=flow_name, labels=labels)
        relaxation_list, relax_key_list = self._set_relax_flows(
            input_work_dir=upload_artifact(upload_path),
            relax_parameter=relax_parameter
        )
        self.workflow.add(relaxation_list)
        self.workflow.submit()
        self.dump_flow_id()
        if not submit_only:
            # Wait for and retrieve relaxation subflows
            self._monitor_relax_flows(relax_key_list)

        return self.workflow.id

    @json2dict
    def submit_props(
            self,
            upload_path: Union[os.PathLike, str],
            download_path: Union[os.PathLike, str],
            props_parameter: dict,
            submit_only: bool = False,
            name: Optional[str] = None,
            labels: Optional[dict] = None
    ) -> str:
        self.upload_path = upload_path
        self.download_path = download_path
        self.props_param = props_parameter
        flow_name = name if name else self.regulate_name(os.path.basename(download_path))
        flow_name += '-props'
        self.workflow = Workflow(name=flow_name, labels=labels)
        subprops_list, subprops_key_list = self._set_props_flow(
            input_work_dir=upload_artifact(upload_path),
            props_parameter=props_parameter
        )
        self.workflow.add(subprops_list)
        self.workflow.submit()
        self.dump_flow_id()
        if not submit_only:
            # wait for and retrieve sub-property flows
            self._monitor_props(subprops_key_list)

        return self.workflow.id

    @json2dict
    def submit_joint(
            self,
            upload_path: Union[os.PathLike, str],
            download_path: Union[os.PathLike, str],
            relax_parameter: dict,
            props_parameter: dict,
            submit_only: bool = False,
            name: Optional[str] = None,
            labels: Optional[dict] = None
    ) -> str:
        self.upload_path = upload_path
        self.download_path = download_path
        self.relax_param = relax_parameter
        self.props_param = props_parameter
        flow_name = name if name else self.regulate_name(os.path.basename(download_path))
        flow_name += '-joint'
        self.workflow = Workflow(name=flow_name, labels=labels)
        base_artifact = upload_artifact(upload_path)

        # per-structure relaxation subflows as DAG tasks
        relaxation_tasks, relax_key_list = self._set_relax_tasks(
            input_work_dir=base_artifact,
            relax_parameter=self.relax_param
        )

        # per-structure property tasks depending on corresponding relaxation task
        subprops_list, subprops_key_list = self._set_props_tasks(
            relax_tasks=relaxation_tasks,
            props_parameter=self.props_param,
            base_work_artifact=base_artifact,
            pre_relaxed=self.props_param.get("pre_relaxed_structures", [])
        )

        if not relaxation_tasks and not subprops_list:
            raise RuntimeError(
                "No joint workflow tasks to submit. All requested relaxations and "
                "properties appear to be finished, or no structures matched the "
                "submitted patterns."
            )
        if relaxation_tasks:
            self.workflow.add(relaxation_tasks)
        if subprops_list:
            self.workflow.add(subprops_list)
        self.workflow.submit()
        self.dump_flow_id()
        if not submit_only:
            # Wait for and retrieve relaxation subflows
            self._monitor_joint_flows(relax_key_list, subprops_key_list)

        return self.workflow.id
