from dflow import (
    Step,
    argo_range,
    argo_len,
    upload_artifact
)
from dflow.python import (
    PythonOPTemplate,
    Slices,
)
import os
from monty.serialization import loadfn
from dflow.python import upload_packages
from apex.VASP_OPs import (
    RelaxMakeVASP,
    RelaxPostVASP,
    PropsMakeVASP,
    PropsPostVASP
)
from apex.TestFlow import TestFlow
from fpop.vasp import PrepVasp, VaspInputs, RunVasp
from fpop.utils.step_config import (
    init_executor
)

upload_packages.append(__file__)


class VASPFlow(TestFlow):
    """
    Generate autotest workflow and submit automatically for VASP Calculations.
    """
    def __init__(self, args):
        super().__init__(args)
        # initiate params defined in global.json
        global_param = loadfn("global.json")
        self.args = args
        self.global_param = global_param
        self.work_dir = global_param.get("work_dir", None)
        self.email = global_param.get("email", None)
        self.password = global_param.get("password", None)
        self.program_id = global_param.get("program_id", None)
        self.dpgen_image_name = global_param.get("dpgen_image_name", None)
        self.vasp_image_name = global_param.get("vasp_image_name", None)
        self.cpu_scass_type = global_param.get("cpu_scass_type", None)
        self.gpu_scass_type = global_param.get("gpu_scass_type", None)
        self.batch_type = global_param.get("batch_type", None)
        self.context_type = global_param.get("context_type", None)
        self.vasp_run_command = global_param.get("vasp_run_command", None)
        self.upload_python_packages = global_param.get("upload_python_packages", None)

        self.run_step_config_relax = {
            "executor": {
                "type": "dispatcher",
                "image_pull_policy": "IfNotPresent",
                "machine_dict": {
                    "batch_type": self.batch_type,
                    "context_type": self.context_type,
                    "remote_profile": {
                        "email": self.email,
                        "password": self.password,
                        "program_id": self.program_id,
                        "input_data": {
                            "job_type": "container",
                            "platform": "ali",
                            "scass_type": self.cpu_scass_type,
                        }
                    }
                }
            }
        }

        self.run_step_config_props = {
            "executor": {
                "type": "dispatcher",
                "image_pull_policy": "IfNotPresent",
                "machine_dict": {
                    "batch_type": self.batch_type,
                    "context_type": self.context_type,
                    "remote_profile": {
                        "email": self.email,
                        "password": self.password,
                        "program_id": self.program_id,
                        "input_data": {
                            "job_type": "container",
                            "platform": "ali",
                            "scass_type": self.cpu_scass_type,
                        }
                    }
                }
            }
        }

    def init_steps(self):
        cwd = os.getcwd()
        work_dir = cwd

        relaxmake = Step(
            name="Relaxmake",
            template=PythonOPTemplate(RelaxMakeVASP, image=self.dpgen_image_name, command=["python3"]),
            artifacts={"input": upload_artifact(work_dir),
                       "param": upload_artifact(self.relax_param)},
        )
        self.relaxmake = relaxmake

        relax = PythonOPTemplate(RunVasp,
                                 slices=Slices("{{item}}",
                                               input_parameter=["task_name"],
                                               input_artifact=["task_path"],
                                               output_artifact=["backward_dir"]),
                                 python_packages=self.upload_python_packages,
                                 image=self.vasp_image_name
                                 )

        relaxcal = Step(
            name="RelaxVASP-Cal",
            template=relax,
            parameters={
                "run_image_config": {"command": self.vasp_run_command},
                "task_name": relaxmake.outputs.parameters["task_names"],
                "backward_list": ["INCAR", "POSCAR", "OUTCAR", "CONTCAR"],
                "backward_dir_name": "relax_task"
            },
            artifacts={
                "task_path": relaxmake.outputs.artifacts["task_paths"]
            },
            with_param=argo_range(argo_len(relaxmake.outputs.parameters["task_names"])),
            key="RelaxVASP-Cal-{{item}}",
            executor=init_executor(self.run_step_config_relax.pop("executor")),
            **self.run_step_config_relax
        )
        self.relaxcal = relaxcal

        relaxpost = Step(
            name="Relaxpost",
            template=PythonOPTemplate(RelaxPostVASP, image=self.dpgen_image_name, command=["python3"]),
            artifacts={"input_post": self.relaxcal.outputs.artifacts["backward_dir"], "input_all": self.relaxmake.outputs.artifacts["output"],
                       "param": upload_artifact(self.relax_param)},
            parameters={"path": work_dir}
        )
        self.relaxpost = relaxpost

        if self.do_relax:
            propsmake = Step(
                name="Propsmake",
                template=PythonOPTemplate(PropsMakeVASP, image=self.dpgen_image_name, command=["python3"]),
                artifacts={"input": relaxpost.outputs.artifacts["output_all"],
                           "param": upload_artifact(self.props_param)},
            )
        else:
            propsmake = Step(
                name="Propsmake",
                template=PythonOPTemplate(PropsMakeVASP, image=self.dpgen_image_name, command=["python3"]),
                artifacts={"input": upload_artifact(work_dir),
                           "param": upload_artifact(self.props_param)},
            )
        self.propsmake = propsmake

        props = PythonOPTemplate(RunVasp,
                                 slices=Slices("{{item}}",
                                               input_parameter=["task_name"],
                                               input_artifact=["task_path"],
                                               output_artifact=["backward_dir"]),
                                 python_packages=self.upload_python_packages,
                                 image=self.vasp_image_name
                                 )

        propscal = Step(
            name="PropsVASP-Cal",
            template=props,
            parameters={
                "run_image_config": {"command": self.vasp_run_command},
                "task_name": propsmake.outputs.parameters["task_names"],
                "backward_list": ["INCAR", "POSCAR", "OUTCAR", "CONTCAR"]
            },
            artifacts={
                "task_path": propsmake.outputs.artifacts["task_paths"]
            },
            with_param=argo_range(argo_len(propsmake.outputs.parameters["task_names"])),
            key="PropsVASP-Cal-{{item}}",
            executor=init_executor(self.run_step_config_props.pop("executor")),
            **self.run_step_config_props
        )
        self.propscal = propscal

        propspost = Step(
            name="Propspost",
            template=PythonOPTemplate(PropsPostVASP, image=self.dpgen_image_name, command=["python3"]),
            artifacts={"input_post": propscal.outputs.artifacts["backward_dir"], "input_all": self.propsmake.outputs.artifacts["output"],
                       "param": upload_artifact(self.props_param)},
            parameters={"path": work_dir, "task_names": propsmake.outputs.parameters["task_names"]}
        )
        self.propspost = propspost
