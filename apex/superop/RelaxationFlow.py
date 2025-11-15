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
    Workflow,
    argo_len,
    argo_range,
    upload_artifact,
)
from dflow.python import (
    OP,
    PythonOPTemplate,
    Slices,
)
from dflow.plugins.dispatcher import DispatcherExecutor


class RelaxationFlow(Steps):

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
            "input_work_path": InputArtifact(type=Path),
        }
        self._output_parameters = {}
        self._output_artifacts = {
            "output_all": OutputArtifact(type=Path),
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

        self._keys = ["make", "run", "post"]
        self.step_keys = {}
        key = "make"
        self.step_keys[key] = '--'.join(
            [str(self.inputs.parameters["flow_id"]), 'relax', key]
        )
        key = "run"
        self.step_keys[key] = '--'.join(
            [str(self.inputs.parameters["flow_id"]), 'relax', key + "-{{item}}"]
        )
        key = "post"
        self.step_keys[key] = '--'.join(
            [str(self.inputs.parameters["flow_id"]), 'relax', key]
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
        make = Step(
            name="Relaxmake",
            template=PythonOPTemplate(make_op,
                                      image=make_image,
                                      python_packages=upload_python_packages,
                                      command=["python3"]),
            artifacts={"input": self.inputs.artifacts["input_work_path"]},
            parameters={"param": self.inputs.parameters["parameter"]},
            key=self.step_keys["make"]
        )
        self.add(make)

        # Step for Relaxation run
        if calculator in ['vasp', 'abacus']:
            run_fp = PythonOPTemplate(
                run_op,
                slices=Slices(
                    "{{item}}",
                    input_parameter=["task_name"],
                    input_artifact=["task_path"],
                    output_artifact=["backward_dir"],
                    group_size=group_size,
                    pool_size=pool_size
                ),
                python_packages=upload_python_packages,
                image=run_image
            )
        if calculator == 'vasp':
            runcal = Step(
                name="RelaxVASP-Cal",
                template=run_fp,
                parameters={
                    "run_image_config": {"command": run_command},
                    "task_name": make.outputs.parameters["task_names"],
                    "backward_list": ["INCAR", "POSCAR", "OUTCAR", "CONTCAR"],
                    "backward_dir_name": "relax_task"
                },
                artifacts={
                    "task_path": make.outputs.artifacts["task_paths"]
                },
                with_param=argo_range(argo_len(make.outputs.parameters["task_names"])),
                key=self.step_keys["run"] + '-vasp',
                executor=executor,
            )
        elif calculator == 'abacus':
            runcal = Step(
                name="RelaxABACUS-Cal",
                template=run_fp,
                parameters={
                    "run_image_config": {"command": run_command},
                    "task_name": make.outputs.parameters["task_names"],
                    "backward_list": ["OUT.ABACUS", "log"],
                    "log_name": "log",
                    "backward_dir_name": "relax_task"
                },
                artifacts={
                    "task_path": make.outputs.artifacts["task_paths"],
                    "optional_artifact": upload_artifact({"pp_orb": "./"})
                },
                with_param=argo_range(argo_len(make.outputs.parameters["task_names"])),
                key=self.step_keys["run"] + '-abacus',
                executor=executor,
            )
        elif calculator == 'lammps':
            run_lmp = PythonOPTemplate(
                run_op,
                slices=Slices(
                    "{{item}}",
                    input_artifact=["input_lammps"],
                    output_artifact=["backward_dir"],
                    group_size=group_size,
                    pool_size=pool_size
                ),
                image=run_image,
                python_packages=upload_python_packages,
                command=["python3"]
            )
            runcal = Step(
                name="RelaxLAMMPS-Cal",
                template=run_lmp,
                artifacts={"input_lammps": make.outputs.artifacts["task_paths"]},
                parameters={"run_command": run_command},
                with_param=argo_range(make.outputs.parameters["njobs"]),
                key=self.step_keys["run"] + '-lammps',
                executor=executor,
            )
        else:
            raise RuntimeError(f'Incorrect calculator type to initiate step: {calculator}')
        self.add(runcal)

        post = Step(
            name="Relaxpost",
            template=PythonOPTemplate(post_op,
                                      image=post_image,
                                      command=["python3"],
                                      python_packages=upload_python_packages,),
            artifacts={"input_post": runcal.outputs.artifacts["backward_dir"],
                       "input_all": make.outputs.artifacts["output"]},
            parameters={"param": self.inputs.parameters["parameter"]},
            key=self.step_keys["post"]
        )
        self.add(post)

        self.outputs.artifacts["output_all"]._from \
            = post.outputs.artifacts["output_all"]
        self.outputs.artifacts["retrieve_path"]._from \
            = post.outputs.artifacts["retrieve_path"]


