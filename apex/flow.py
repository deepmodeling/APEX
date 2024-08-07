import os
import glob
import time
import shutil
import re
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
        relaxation = self._set_relax_flow(
            input_work_dir=upload_artifact(upload_path),
            relax_parameter=relax_parameter
        )
        self.workflow.add(relaxation)
        self.workflow.submit()
        self.dump_flow_id()
        if not submit_only:
            # Wait for and retrieve relaxation
            self._monitor_relax()

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
        relaxation = self._set_relax_flow(
            input_work_dir=upload_artifact(upload_path),
            relax_parameter=self.relax_param
        )
        subprops_list, subprops_key_list = self._set_props_flow(
            input_work_dir=relaxation.outputs.artifacts["output_all"],
            props_parameter=self.props_param
        )
        self.workflow.add(relaxation)
        self.workflow.add(subprops_list)
        self.workflow.submit()
        self.dump_flow_id()
        if not submit_only:
            # Wait for and retrieve relaxation
            self._monitor_relax()
            # Wait for and retrieve sub-property flows
            self._monitor_props(subprops_key_list)

        return self.workflow.id
