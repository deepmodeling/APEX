import os
from pathlib import (
    Path,
)
from typing import (
    List,
    Optional,
    Type,
)
from dflow import (
    InputArtifact,
    InputParameter,
    Inputs,
    OutputArtifact,
    Outputs,
    Step,
    Steps,
    argo_range,
)
from dflow.python import (
    OP,
    PythonOPTemplate,
    Slices,
)
from dflow.plugins.dispatcher import DispatcherExecutor
from apex.op.property_ops import DistributeProps, CollectProps
from apex.superop.SimplePropertySteps import SimplePropertySteps


class PropertyFlow(Steps):

    def __init__(
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
        group_size: Optional[int] = None,
        pool_size: Optional[int] = None,
        executor: Optional[DispatcherExecutor] = None,
        upload_python_packages: Optional[List[os.PathLike]] = None,
    ):
        self._input_parameters = {
            "flow_id": InputParameter(type=str, value=""),
            "parameter": InputParameter(type=dict)
        }
        self._input_artifacts = {
            "input_work_path": InputArtifact(type=Path)
        }
        self._output_parameters = {

        }
        self._output_artifacts = {
            "retrieve_path": OutputArtifact(type=List[Path])
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

        self._keys = ["distributor", "propcal", "collector"]
        self.step_keys = {}
        key = "distributor"
        self.step_keys[key] = '--'.join(
            [str(self.inputs.parameters["flow_id"]), key]
        )
        key = "propcal"
        self.step_keys[key] = '--'.join(
            [str(self.inputs.parameters["flow_id"]), key]
        )
        key = "collector"
        self.step_keys[key] = '--'.join(
            [str(self.inputs.parameters["flow_id"]), key]
        )

        self._build(
            name,
            make_op,
            run_op,
            post_op,
            make_image,
            run_image,
            post_image,
            run_command,
            calculator,
            group_size,
            pool_size,
            executor,
            upload_python_packages
        )

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
        group_size: Optional[int] = None,
        pool_size: Optional[int] = None,
        executor: Optional[DispatcherExecutor] = None,
        upload_python_packages: Optional[List[os.PathLike]] = None,
    ):
        distributor = Step(
            name="Distributor",
            template=PythonOPTemplate(DistributeProps,
                                      image=make_image,
                                      command=["python3"]),
            artifacts={"input_work_path": self.inputs.artifacts["input_work_path"]},
            parameters={"param": self.inputs.parameters["parameter"]},
            key="distributor"
        )
        self.add(distributor)

        simple_property_steps = SimplePropertySteps(
            name='simple-property-flow',
            make_op=make_op,
            run_op=run_op,
            post_op=post_op,
            make_image=make_image,
            run_image=run_image,
            post_image=post_image,
            run_command=run_command,
            calculator=calculator,
            group_size=group_size,
            pool_size=pool_size,
            executor=executor,
            upload_python_packages=upload_python_packages
        )

        propscal = Step(
            name="Prop-Cal",
            template=simple_property_steps,
            slices=Slices(
                slices="{{item}}",
                input_parameter=[
                    "flow_id",
                    "path_to_prop",
                    "prop_param",
                    "inter_param",
                    "do_refine"
                ],
                input_artifact=["input_work_path"],
                output_artifact=["output_post"],
            ),
            artifacts={
                "input_work_path": distributor.outputs.artifacts["orig_work_path"]
            },
            parameters={
                "flow_id": distributor.outputs.parameters["flow_id"],
                "path_to_prop": distributor.outputs.parameters["path_to_prop"],
                "prop_param": distributor.outputs.parameters["prop_param"],
                "inter_param": distributor.outputs.parameters["inter_param"],
                "do_refine": distributor.outputs.parameters["do_refine"]
            },
            with_param=argo_range(distributor.outputs.parameters["nflows"]),
            key="propscal-{{item}}"
        )
        self.add(propscal)

        collector = Step(
            name="PropsCollector",
            template=PythonOPTemplate(CollectProps,
                                      image=make_image,
                                      command=["python3"]),
            artifacts={"input_all": self.inputs.artifacts["input_work_path"],
                       "input_post": propscal.outputs.artifacts["output_post"]},
            parameters={"param": self.inputs.parameters["parameter"]},
            key="collector"
        )
        self.add(collector)

        self.outputs.artifacts["retrieve_path"]._from \
            = collector.outputs.artifacts["retrieve_path"]
