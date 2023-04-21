from dflow import (
    Workflow,
    Step,
    argo_range,
    SlurmRemoteExecutor,
    upload_artifact,
    download_artifact,
    InputArtifact,
    OutputArtifact,
    ShellOPTemplate
)
from dflow.python import (
    PythonOPTemplate,
    OP,
    OPIO,
    OPIOSign,
    Artifact,
    Slices,
    upload_packages
)
import os
from monty.serialization import loadfn
from dflow.plugins.dispatcher import DispatcherExecutor
from dflow.python import upload_packages
from apex.ABACUS_OPs import (
    RelaxMakeABACUS,
    RelaxPostABACUS,
    PropsMakeABACUS,
    PropsPostABACUS,
    RunABACUS
)
from apex.TestFlow import TestFlow

upload_packages.append(__file__)


class ABACUSFlow(TestFlow):
    """
    Generate autotest workflow and automatically submit abacus jobs according to user input arguments.
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
        self.abacus_image_name = global_param.get("abacus_image_name", None)
        self.cpu_scass_type = global_param.get("cpu_scass_type", None)
        self.gpu_scass_type = global_param.get("gpu_scass_type", None)
        self.batch_type = global_param.get("batch_type", None)
        self.context_type = global_param.get("context_type", None)
        self.abacus_run_command = global_param.get("abacus_run_command", None)
        self.upload_python_packages = global_param.get("upload_python_packages", None)

        dispatcher_executor_cpu = DispatcherExecutor(
            machine_dict={
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
                    },
                },
            },
            image_pull_policy="IfNotPresent"
        )

        dispatcher_executor_gpu = DispatcherExecutor(
            machine_dict={
                "batch_type": self.batch_type,
                "context_type": self.context_type,
                "remote_profile": {
                    "email": self.email,
                    "password": self.password,
                    "program_id": self.program_id,
                    "input_data": {
                        "job_type": "container",
                        "platform": "ali",
                        "scass_type": self.gpu_scass_type,
                    },
                },
            },
            image_pull_policy="IfNotPresent"
        )
        self.dispatcher_executor = dispatcher_executor_cpu

    def init_steps(self):
        cwd = os.getcwd()
        work_dir = cwd

        relaxmake = Step(
            name="Relaxmake",
            template=PythonOPTemplate(RelaxMakeABACUS, image=self.dpgen_image_name, command=["python3"]),
            artifacts={"input": upload_artifact(work_dir),
                       "param": upload_artifact(self.relax_param)},
        )
        self.relaxmake = relaxmake

        relax = PythonOPTemplate(RunABACUS,
                                       slices=Slices("{{item}}", input_artifact=["input_abacus"],
                                                     output_artifact=["output_abacus"]),
                                       image=self.abacus_image_name, command=["python3"])

        relaxcal = Step(
            name="RelaxABACUS-Cal",
            template=relax,
            artifacts={"input_abacus": relaxmake.outputs.artifacts["task_paths"]},
            parameters={"run_command": self.abacus_run_command},
            with_param=argo_range(relaxmake.outputs.parameters["njobs"]),
            key="ABACUS-Cal-{{item}}",
            executor=self.dispatcher_executor
        )
        self.relaxcal = relaxcal

        relaxpost = Step(
            name="Relaxpost",
            template=PythonOPTemplate(RelaxPostABACUS, image=self.dpgen_image_name, command=["python3"]),
            artifacts={"input_post": relaxcal.outputs.artifacts["output_abacus"],
                       "input_all": relaxmake.outputs.artifacts["output"],
                       "param": upload_artifact(self.relax_param)},
            parameters={"path": cwd}
        )
        self.relaxpost = relaxpost

        if self.do_relax:
            propsmake = Step(
                name="Propsmake",
                template=PythonOPTemplate(PropsMakeABACUS, image=self.dpgen_image_name, command=["python3"]),
                artifacts={"input": relaxpost.outputs.artifacts["output_all"],
                           "param": upload_artifact(self.props_param)},
            )
            self.propsmake = propsmake
        else:
            propsmake = Step(
                name="Propsmake",
                template=PythonOPTemplate(PropsMakeABACUS, image=self.dpgen_image_name, command=["python3"]),
                artifacts={"input": upload_artifact(work_dir),
                           "param": upload_artifact(self.props_param)},
            )
            self.propsmake = propsmake

        props = PythonOPTemplate(RunABACUS,
                                 slices=Slices("{{item}}", input_artifact=["input_abacus"],
                                               output_artifact=["output_abacus"]), image=self.abacus_image_name, command=["python3"])

        propscal = Step(
            name="PropsABACUS-Cal",
            template=props,
            artifacts={"input_abacus": propsmake.outputs.artifacts["task_paths"]},
            parameters={"run_command": self.abacus_run_command},
            with_param=argo_range(propsmake.outputs.parameters["njobs"]),
            key="ABACUS-Cal-{{item}}",
            executor=self.dispatcher_executor
        )
        self.propscal = propscal

        propspost = Step(
            name="Propspost",
            template=PythonOPTemplate(PropsPostABACUS, image=self.dpgen_image_name, command=["python3"]),
            artifacts={"input_post": propscal.outputs.artifacts["output_abacus"],
                       "input_all": propsmake.outputs.artifacts["output"],
                       "param": upload_artifact(self.props_param)},
            parameters={"path": cwd}
        )
        self.propspost = propspost
