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
from dflow.plugins.dispatcher import DispatcherExecutor, update_dict
from apex.fp_OPs import (
    RelaxMakeFp,
    RelaxPostFp,
    PropsMakeFp,
    PropsPostFp
)
import fpop
from apex.TestFlow import TestFlow
from fpop.vasp import RunVasp

upload_packages.append(__file__)
upload_python_packages=list(fpop.__path__)


class VASPFlow(TestFlow):
    """
    Generate autotest workflow and submit automatically for VASP Calculations.
    """
    def __init__(self, flow_type, relax_param, props_param):
        super().__init__(flow_type, relax_param, props_param)
        # initiate params defined in global.json
        global_param = loadfn("global.json")
        self.global_param = global_param
        self.work_dir = global_param.get("work_dir", None)
        self.email = global_param.get("email", None)
        self.password = global_param.get("password", None)
        self.program_id = global_param.get("program_id", None)
        self.apex_image_name = global_param.get("apex_image_name", None)
        self.vasp_image_name = global_param.get("vasp_image_name", None)
        self.cpu_scass_type = global_param.get("cpu_scass_type", None)
        self.gpu_scass_type = global_param.get("gpu_scass_type", None)
        self.batch_type = global_param.get("batch_type", None)
        self.context_type = global_param.get("context_type", None)
        self.vasp_run_command = global_param.get("vasp_run_command", None)
        self.upload_python_packages = upload_python_packages
        #self.upload_python_packages = global_param.get("upload_python_packages", None)
        self.host = global_param.get("host", None)
        self.port = global_param.get("port", 22)
        self.username = global_param.get("username", "root")
        self.host_password = global_param.get("host_password", None)
        self.queue_name = global_param.get("queue_name", None)
        self.private_key_file = global_param.get("private_key_file", None)
        self.dispatcher_image = global_param.get("dispatcher_image", None)
        self.dispatcher_image_pull_policy = global_param.get("dispatcher_image_pull_policy", "IfNotPresent")
        self.remote_root = global_param.get("remote_root", None)
        self.machine = global_param.get("machine", None)
        self.resources = global_param.get("resources", None)
        self.task = global_param.get("task", None)

        if self.context_type is None:
            self.executor = None
        else:
            if self.context_type == "Bohrium":
                machine_dict = {
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
                }
                if self.machine is not None:
                    update_dict(machine_dict, self.machine)
                self.machine = machine_dict

            self.executor = DispatcherExecutor(
                host=self.host, port=self.port, username=self.username,
                password=self.host_password, queue_name=self.queue_name,
                private_key_file=self.private_key_file,
                remote_root=self.remote_root, image=self.dispatcher_image,
                image_pull_policy=self.dispatcher_image_pull_policy,
                machine_dict=self.machine, resources_dict=self.resources,
                task_dict=self.task)

    def init_steps(self):
        cwd = os.getcwd()
        work_dir = cwd

        relaxmake = Step(
            name="Relaxmake",
            template=PythonOPTemplate(RelaxMakeFp, image=self.apex_image_name, command=["python3"]),
            artifacts={"input": upload_artifact(work_dir),
                       "param": upload_artifact(self.relax_param)},
            key="vasp-relaxmake"
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
            key="vasp-relaxcal-{{item}}",
            executor=self.executor,
        )
        self.relaxcal = relaxcal

        relaxpost = Step(
            name="Relaxpost",
            template=PythonOPTemplate(RelaxPostFp, image=self.apex_image_name, command=["python3"]),
            artifacts={"input_post": self.relaxcal.outputs.artifacts["backward_dir"], "input_all": self.relaxmake.outputs.artifacts["output"],
                       "param": upload_artifact(self.relax_param)},
            parameters={"path": work_dir},
            key="vasp-relaxpost"
        )
        self.relaxpost = relaxpost

        if self.flow_type == 'joint':
            propsmake = Step(
                name="Propsmake",
                template=PythonOPTemplate(PropsMakeFp, image=self.apex_image_name, command=["python3"]),
                artifacts={"input": relaxpost.outputs.artifacts["output_all"],
                           "param": upload_artifact(self.props_param)},
                key="vasp-propsmake"
            )
        else:
            propsmake = Step(
                name="Propsmake",
                template=PythonOPTemplate(PropsMakeFp, image=self.apex_image_name, command=["python3"]),
                artifacts={"input": upload_artifact(work_dir),
                           "param": upload_artifact(self.props_param)},
                key="vasp-propsmake"
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
            key="vasp-propscal-{{item}}",
            executor=self.executor,
        )
        self.propscal = propscal

        propspost = Step(
            name="Propspost",
            template=PythonOPTemplate(PropsPostFp, image=self.apex_image_name, command=["python3"]),
            artifacts={"input_post": propscal.outputs.artifacts["backward_dir"], "input_all": self.propsmake.outputs.artifacts["output"],
                       "param": upload_artifact(self.props_param)},
            parameters={"path": work_dir, "task_names": propsmake.outputs.parameters["task_names"]},
            key="vasp-propspost"
        )
        self.propspost = propspost
