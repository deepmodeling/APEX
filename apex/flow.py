import os
import time
from typing import (
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
from dflow.python.op import OP
from dflow.plugins.dispatcher import DispatcherExecutor
from apex.superop.RelaxationFlow import RelaxationFlow
from apex.superop.PropertyFlow import PropertyFlow
from apex.op.relaxation_ops import RelaxMake, RelaxPost
from apex.op.property_ops import PropsMake, PropsPost



def json2dict(function):
    def wrapper(*args, **kwargs):
        # check input parameter and try to convert to dict if is json file
        for k, v in kwargs.items():
            if isinstance(v, os.PathLike) or isinstance(v, str):
                try:
                    kwargs[k] = loadfn(v)
                except Exception:
                    pass
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
        group_size: Optional[int] = None,
        pool_size: Optional[int] = None,
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
        self.group_size = group_size
        self.pool_size = pool_size
        self.executor = executor
        self.upload_python_packages = upload_python_packages

    @staticmethod
    def download(
            wf,
            step_name: str,
            artifacts_key: str,
            work_dir: Union[os.PathLike, str] = '.'
    ):
        while wf.query_status() in ["Pending", "Running"]:
            time.sleep(4)
        assert (wf.query_status() == 'Succeeded')
        print(f'Workflow is finished (ID: {wf.id}, UID: {wf.uid})')
        print('Retrieving finished tasks to local...')
        step = wf.query_step(name=step_name)[0]
        download_artifact(
            step.outputs.artifacts[artifacts_key],
            path=work_dir
        )

    def _set_relax_flow(
            self,
            input_work_dir: dflow.common.S3Artifact,
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
            group_size=self.group_size,
            pool_size=self.pool_size,
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
            group_size=self.group_size,
            pool_size=self.pool_size,
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

    @json2dict
    def submit_relax(
            self,
            work_dir: Union[os.PathLike, str],
            relax_parameter: dict
    ):
        wf = Workflow(name='relaxation')
        relaxation = self._set_relax_flow(
            input_work_dir=upload_artifact(work_dir),
            relax_parameter=relax_parameter
        )
        wf.add(relaxation)
        wf.submit()
        self.download(
            wf, step_name='relaxation-cal',
            artifacts_key='retrieve_path',
            work_dir=work_dir
        )

    @json2dict
    def submit_props(
            self,
            work_dir: Union[os.PathLike, str],
            props_parameter: dict
    ):
        wf = Workflow(name='property')
        property = self._set_props_flow(
            input_work_dir=upload_artifact(work_dir),
            props_parameter=props_parameter
        )
        wf.add(property)
        wf.submit()
        self.download(
            wf, step_name='property-cal',
            artifacts_key='retrieve_path',
            work_dir=work_dir
        )

    @json2dict
    def submit_joint(
            self,
            work_dir: Union[os.PathLike, str],
            relax_parameter: dict,
            props_parameter: dict
    ):
        wf = Workflow(name='joint')
        relaxation = self._set_relax_flow(
            input_work_dir=upload_artifact(work_dir),
            relax_parameter=relax_parameter
        )
        property = self._set_props_flow(
            input_work_dir=relaxation.outputs.artifacts["output_all"],
            props_parameter=props_parameter
        )
        wf.add(relaxation)
        wf.add(property)
        wf.submit()
        self.download(
            wf, step_name='property-cal',
            artifacts_key='retrieve_path',
            work_dir=work_dir
        )

