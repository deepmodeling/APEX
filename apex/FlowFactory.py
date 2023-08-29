import os
import time
from abc import ABC, abstractmethod
from typing import (
    Literal,
    Optional,
    Type,
    Union
)

import dflow
from dflow import (
    Step,
    upload_artifact,
    download_artifact,
    Workflow
)
from dflow.python.op import Artifact


class FlowFactory:
    def __init__(self):
        pass

    @classmethod
    def from_dict(cls, d):
        kwargs = {}
        return cls(**kwargs)

    @staticmethod
    def assertion(wf, step_name: str, artifacts_key: str):
        while wf.query_status() in ["Pending", "Running"]:
            time.sleep(4)
        assert (wf.query_status() == 'Succeeded')
        step = wf.query_step(name=step_name)[0]
        download_artifact(step.outputs.artifacts[artifacts_key])

    def _set_relax_flow(
            self,
            input_work_path: dflow.common.S3Artifact
    ) -> Step:
        relaxation_flow = RelaxationFlow(
            name='relaxation-flow',
            make_op=RelaxMake,
            run_op=RunLAMMPS,
            post_op=RelaxPost,
            make_image=self.apex_image_name,
            run_image=self.dpmd_image_name,
            post_image=self.apex_image_name,
            run_command=self.lammps_run_command,
            calculator="lammps",
            executor=self.executor,
            upload_python_packages=self.upload_python_packages
        )
        relaxation = Step(
            name='relaxation-cal',
            template=relaxation_flow,
            artifacts={
                "input_work_path": input_work_path
            },
            parameters={
                "flow_id": "relaxflow",
                "parameter": relax_param
            },
            key="relaxationcal"
        )
        return relaxation

    def _set_props_flow(
            self,
            input_work_path: dflow.common.S3Artifact
    ) -> Step:
        property_flow = PropertyFlow(
            name='property-flow',
            make_op=PropsMake,
            run_op=RunLAMMPS,
            post_op=PropsPost,
            make_image=self.apex_image_name,
            run_image=self.dpmd_image_name,
            post_image=self.apex_image_name,
            run_command=self.lammps_run_command,
            calculator="lammps",
            executor=self.executor,
            upload_python_packages=self.upload_python_packages
        )
        property = Step(
            name='property-cal',
            template=property_flow,
            artifacts={
                "input_work_path": input_work_path
            },
            parameters={
                "flow_id": "propertyflow",
                "parameter": prop_param
            },
            key="propertycal"
        )
        return property

    def submit_relax(self):
        wf = Workflow(name='relaxation')
        relaxation = self._set_relax_flow(
            input_work_path=upload_artifact(work_path)
        )
        wf.add(relaxation)
        wf.submit()
        self.assertion(wf, step_name='relaxation-cal',
                       artifacts='retrieve_path')

    def submit_props(self):
        wf = Workflow(name='property')
        property = self._set_props_flow(
            input_work_path=upload_artifact(work_path)
        )
        wf.add(property)
        wf.submit()
        self.assertion(wf, step_name='property-cal',
                       artifacts='retrieve_path')

    def submit_joint(self):
        wf = Workflow(name='joint(relax+props)')
        relaxation = self._set_relax_flow(
            input_work_path=upload_artifact(work_path)
        )
        property = self._set_props_flow(
            input_work_path=relaxation.outputs.artifacts["output_all"]
        )
        wf.add(relaxation)
        wf.add(property)
        wf.submit()
        self.assertion(wf, step_name='property-cal', artifacts='retrieve_path')
