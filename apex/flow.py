import os
import glob
import time
import shutil
import re
import copy
import datetime
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

    def _monitor_relax(self):
        print('Waiting for relaxation result...')
        while True:
            time.sleep(4)
            step_info = self.workflow.query()
            wf_status = self.workflow.query_status()
            if wf_status == 'Failed':
                raise RuntimeError(f'Workflow failed (ID: {self.workflow.id}, UID: {self.workflow.uid})')
            try:
                relax_post = step_info.get_step(name='relaxation-cal')[0]
            except IndexError:
                continue
            if relax_post['phase'] == 'Succeeded':
                print(f'Relaxation finished (ID: {self.workflow.id}, UID: {self.workflow.uid})')
                print('Retrieving completed tasks to local...')
                download_artifact(
                    artifact=relax_post.outputs.artifacts['retrieve_path'],
                    path=self.download_path
                )
                break

    def _monitor_props(
            self,
            subprops_key_list: List[str],
    ):
        subprops_left = subprops_key_list.copy()
        subprops_failed_list = []
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
                    download_artifact(
                        artifact=step.outputs.artifacts['retrieve_path'],
                        path=self.download_path
                    )
                    subprops_left.remove(kk)
                    if subprops_left:
                        print(f'Waiting for sub-property results ({len(subprops_left)} left)...')
                elif step['phase'] == 'Failed':
                    print(f'Sub-workflow {kk} failed (ID: {self.workflow.id}, UID: {self.workflow.uid})')
                    subprops_failed_list.append(kk)
                    subprops_left.remove(kk)
                    if subprops_left:
                        print(f'Waiting for sub-property results ({len(subprops_left)} left)...')
            if not subprops_left:
                print(f'Workflow finished with {len(subprops_failed_list)} sub-property failed '
                      f'(ID: {self.workflow.id}, UID: {self.workflow.uid})')
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
                    download_artifact(
                        artifact=step.outputs.artifacts['retrieve_path'],
                        path=self.download_path
                    )
                    relax_left.remove(kk)
                    if relax_left:
                        print(f'Waiting for relaxation results ({len(relax_left)} left)...')
                elif step['phase'] == 'Failed':
                    print(f'Sub relaxation {kk} failed (ID: {self.workflow.id}, UID: {self.workflow.uid})')
                    relax_failed_list.append(kk)
                    relax_left.remove(kk)
            if not relax_left:
                print(f'Workflow finished with {len(relax_failed_list)} sub-relaxation failed '
                      f'(ID: {self.workflow.id}, UID: {self.workflow.uid})')
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
        props_left = subprops_key_list.copy()
        props_failed = []
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
                        download_artifact(artifact=retrieve, path=self.download_path)
                    relax_left.remove(kk)
                elif step['phase'] == 'Failed':
                    print(f'Sub relaxation {kk} failed')
                    relax_failed.append(kk)
                    relax_left.remove(kk)

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
                        download_artifact(artifact=retrieve, path=self.download_path)
                    props_left.remove(kk)
                elif step['phase'] == 'Failed':
                    print(f'Sub property {kk} failed')
                    props_failed.append(kk)
                    props_left.remove(kk)

            if relax_left or props_left:
                counts = (len(relax_left), len(props_left))
                if counts != last_counts or time.time() - last_log_ts > 30:
                    print(f'Waiting... (relax {counts[0]}, props {counts[1]})')
                    last_counts = counts
                    last_log_ts = time.time()

        print(f'Joint monitoring done: {len(relax_failed)} relax failed, {len(props_failed)} property failed '
              f'(ID: {self.workflow.id}, UID: {self.workflow.uid})')

    def dump_flow_id(self):
        log_file = os.path.join(self.download_path, '.workflow.log')
        with open(log_file, 'a') as f:
            timestamp = datetime.datetime.now().isoformat()
            f.write(f'{self.workflow.id}\tsubmit\t{timestamp}\t{self.download_path}\n')

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
            else:
                # pre-relaxed data exists in uploaded workspace
                input_artifact = base_work_artifact

            # skip property if already finished and rerun_finished=False
            prop_dir_name = os.path.basename(path_to_prop)
            if (ii, prop_dir_name) in skip_props:
                # don't create task; also remove from monitor list by not adding key
                print(f"Skip property {prop_dir_name} for {ii} (marked finished; rerun_finished=False)")
                continue

            subprops_key_list.append(subflow_key)
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
        self.workflow.add(relaxation_tasks)

        # per-structure property tasks depending on corresponding relaxation task
        subprops_list, subprops_key_list = self._set_props_tasks(
            relax_tasks=relaxation_tasks,
            props_parameter=self.props_param,
            base_work_artifact=base_artifact,
            pre_relaxed=self.props_param.get("pre_relaxed_structures", [])
        )

        self.workflow.add(subprops_list)
        self.workflow.submit()
        self.dump_flow_id()
        if not submit_only:
            # Wait for and retrieve relaxation subflows
            self._monitor_joint_flows(relax_key_list, subprops_key_list)

        return self.workflow.id
