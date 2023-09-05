import os
import time
from abc import ABC, abstractmethod
from typing import (
    Literal,
    Optional,
    Type,
    Union,
    List
)
from monty.serialization import loadfn
import dflow
from dflow import (
    Step,
    upload_artifact,
    download_artifact,
    Workflow
)
from dflow.python.op import Artifact, OP
from dflow.plugins.dispatcher import DispatcherExecutor
from fpop.vasp import RunVasp
from fpop.abacus import RunAbacus
from apex.superop.RelaxationFlow import RelaxationFlow
from apex.superop.PropertyFlow import PropertyFlow
from apex.op.relaxation_ops import RelaxMake, RelaxPost
from apex.op.property_ops import PropsMake, PropsPost
from apex.op.RunLAMMPS import RunLAMMPS


def check_submit(function):
    def wrapper(*args, **kwargs):
        # check input parameter and convert to dict
        parameter = kwargs['parameter']
        if isinstance(parameter, os.PathLike):
            kwargs['parameter'] = loadfn(parameter)
        elif isinstance(parameter, dict):
            pass
        else:
            raise TypeError(f'Wrong type input for parameter: {type(parameter)}. '
                            f'Should be either a dictionary or a Pathlike type.')
        # check input work direction
        work_path = kwargs['work_path']
        if not isinstance(work_path, os.PathLike):
            raise TypeError(f'Wrong type of indication for work_path: {type(work_path)}. '
                            f'Should be a Pathlike type.')
        function(*args, **kwargs)
    return wrapper


class FlowFactory:
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
        executor: Optional[DispatcherExecutor] = None,
        upload_python_packages: Optional[List[os.PathLike]] = None,
    ):
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
        self.executor = executor
        self.upload_python_packages = upload_python_packages

    @staticmethod
    def assertion(wf, step_name: str, artifacts_key: str):
        while wf.query_status() in ["Pending", "Running"]:
            time.sleep(4)
        assert (wf.query_status() == 'Succeeded')
        step = wf.query_step(name=step_name)[0]
        download_artifact(step.outputs.artifacts[artifacts_key])

    def _set_relax_flow(
            self,
            input_work_dir: dflow.common.S3Artifact,
            local_path: os.PathLike,
            relax_parameter: dict
    ) -> Step:
        relaxation_flow = RelaxationFlow(
            name='relaxation-flow',
            make_op=self.relax_make_op,
            run_op=self.run_op,
            post_op=self.relax_post_op,
            make_image=self.make_image,
            run_image=self.run_image,
            post_image=self.post_image,
            run_command=self.run_command,
            calculator=self.calculator,
            local_path=local_path,
            executor=self.executor,
            upload_python_packages=self.upload_python_packages
        )
        relaxation = Step(
            name='relaxation-cal',
            template=relaxation_flow,
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
            local_path: os.PathLike,
            props_parameter: dict
    ) -> Step:
        property_flow = PropertyFlow(
            name='property-flow',
            make_op=self.props_make_op,
            run_op=self.run_op,
            post_op=self.props_post_op,
            make_image=self.make_image,
            run_image=self.run_image,
            post_image=self.post_image,
            run_command=self.run_command,
            calculator=self.calculator,
            local_path=local_path,
            executor=self.executor,
            upload_python_packages=self.upload_python_packages
        )
        property = Step(
            name='property-cal',
            template=property_flow,
            artifacts={
                "input_work_path": input_work_dir
            },
            parameters={
                "flow_id": "propertyflow",
                "parameter": props_parameter
            },
            key="propertycal"
        )
        return property

    @check_submit
    def submit_relax(
            self,
            work_path: Union[os.PathLike, str],
            parameter: Union[os.PathLike, str, dict]
    ):
        wf = Workflow(name='relaxation')
        relaxation = self._set_relax_flow(
            input_work_dir=upload_artifact(work_path),
            local_path=work_path,
            relax_parameter=parameter
        )
        wf.add(relaxation)
        wf.submit()
        self.assertion(wf, step_name='relaxation-cal',
                       artifacts='retrieve_path')

    @check_submit
    def submit_props(
            self,
            work_path: Union[os.PathLike, str],
            parameter: Union[os.PathLike, str, dict]
    ):
        wf = Workflow(name='property')
        property = self._set_props_flow(
            input_work_dir=upload_artifact(work_path),
            local_path=work_path,
            props_parameter=parameter
        )
        wf.add(property)
        wf.submit()
        self.assertion(wf, step_name='property-cal',
                       artifacts='retrieve_path')

    @check_submit
    def submit_joint(
            self,
            work_path: Union[os.PathLike, str],
            relax_parameter: Union[os.PathLike, str, dict],
            props_parameter: Union[os.PathLike, str, dict],
    ):
        wf = Workflow(name='joint(relax+props)')
        relaxation = self._set_relax_flow(
            input_work_dir=upload_artifact(work_path),
            local_path=work_path,
            relax_parameter=relax_parameter
        )
        property = self._set_props_flow(
            input_work_dir=relaxation.outputs.artifacts["output_all"],
            local_path=work_path,
            props_parameter=props_parameter
        )
        wf.add(relaxation)
        wf.add(property)
        wf.submit()
        self.assertion(wf, step_name='property-cal',
                       artifacts='retrieve_path')

