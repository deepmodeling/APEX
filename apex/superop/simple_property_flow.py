import os
from copy import (
    deepcopy,
)
from pathlib import (
    Path,
)
from typing import (
    List,
    Optional,
    Set,
    Type,
)
from dflow import (
    InputArtifact,
    InputParameter,
    Inputs,
    OutputArtifact,
    OutputParameter,
    Outputs,
    Step,
    Steps,
    Workflow,
    argo_len,
    argo_range,
    argo_sequence,
    download_artifact,
    upload_artifact,
)
from dflow.python import (
    OP,
    OPIO,
    Artifact,
    OPIOSign,
    PythonOPTemplate,
    Slices,
)
from dflow.plugins.dispatcher import DispatcherExecutor


class SimplePropertyFlow(Steps):
    def __int__(
        self,
        name: str,
        make_op: Type[OP],
        run_op: Type[OP],
        post_op: Type[OP],
        make_image: str,
        run_image: str,
        post_image: str,
        run_command: str,
        calculator: str,
        executor: Optional[DispatcherExecutor] = None,
        upload_python_packages: Optional[List[os.PathLike]] = None,
    ):

        self._input_parameters = {
            "flow_id": InputParameter(type=str, value=""),
            "prop_param": InputParameter(type=dict),
            "inter_param": InputParameter(type=dict),
            "do_refine": InputParameter(type=bool)
        }
        self._input_artifacts = {
            "path_to_work": InputArtifact(type=Path),
            "path_to_equi": InputArtifact(type=Path)
        }
        self._output_parameters = {}
        self._output_artifacts = {
            "output_post": OutputArtifact(type=Path)
        }

        super().__init__(
            name=name,
            inputs=Inputs(
                parameters=self._input_parameters,
                artifacts=self._input_artifacts
            ),
            outputs=Outputs(
                parameters=self._output_parameters,
                artifacts=self._output_artifacts
            ),
        )

        self._keys = ["make", "run", "post"]
        self.step_keys = {}
        key = "make"
        self.step_keys[key] = '--'.join(
            [self.inputs.parameters["flow_id"], key]
        )
        key = "run"
        self.step_keys[key] = '--'.join(
            [self.inputs.parameters["flow_id"], key + "-{{item}}"]
        )
        key = "post"
        self.step_keys[key] = '--'.join(
            [self.inputs.parameters["flow_id"], key]
        )

        self._build()
    @property
    def input_parameters(self):
        return self._input_parameters

    @property
    def input_artifacts(self):
        return self._input_artifacts

    @property
    def output_parameters(self):
        return self._output_parameters

    @property
    def output_artifacts(self):
        return self._output_artifacts

    @property
    def keys(self):
        return self._keys

    def _build(
        self,
        name: str,
        make_op: Type[OP],
        run_op: Type[OP],
        post_op: Type[OP],
        make_image: str,
        run_image: str,
        post_image: str,
        run_command: str,
        calculator: str,
        executor: Optional[DispatcherExecutor] = None,
        upload_python_packages: Optional[List[os.PathLike]] = None,
    ):
        make = Step(
            name="propsmake",
            template=PythonOPTemplate(PropsMakeFp, image=self.apex_image_name, command=["python3"]),
            artifacts={"input": upload_artifact(work_dir),
                       "param": upload_artifact(self.props_param)},
            key="vasp-propsmake"
        )

