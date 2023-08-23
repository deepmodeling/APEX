from dflow import (
    Step,
    argo_range,
    upload_artifact
)
from dflow.python import (
    PythonOPTemplate,
    Slices
)
import os
from monty.serialization import loadfn
from dflow.plugins.dispatcher import DispatcherExecutor, update_dict
from dflow.python import upload_packages
from apex.LAMMPS_OPs import (
    RelaxMakeLAMMPS,
    RelaxPostLAMMPS,
    PropsMakeLAMMPS,
    PropsPostLAMMPS,
    RunLAMMPS
)
from apex.TestFlow import TestFlow

upload_packages.append(__file__)

class LAMMPSFlow(TestFlow):
    """
    Generate autotest workflow and automatically submit lammps jobs according to user input arguments.
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
        self.dpmd_image_name = global_param.get("dpmd_image_name", None)
        self.cpu_scass_type = global_param.get("cpu_scass_type", None)
        self.gpu_scass_type = global_param.get("gpu_scass_type", None)
        self.batch_type = global_param.get("batch_type", None)
        self.context_type = global_param.get("context_type", None)
        self.lammps_run_command = global_param.get("lammps_run_command", None)
        self.upload_python_packages = global_param.get("upload_python_packages", None)
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
                            "scass_type": self.gpu_scass_type,
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
            template=PythonOPTemplate(RelaxMakeLAMMPS, image=self.apex_image_name, command=["python3"]),
            artifacts={"input": upload_artifact(work_dir),
                       "param": upload_artifact(self.relax_param)},
            key="lammps-relaxmake"
        )
        self.relaxmake = relaxmake

        relax = PythonOPTemplate(RunLAMMPS,
                                       slices=Slices("{{item}}", input_artifact=["input_lammps"],
                                                     output_artifact=["output_lammps"]),
                                       image=self.dpmd_image_name, command=["python3"])

        relaxcal = Step(
            name="RelaxLAMMPS-Cal",
            template=relax,
            artifacts={"input_lammps": relaxmake.outputs.artifacts["task_paths"]},
            parameters={"run_command": self.lammps_run_command},
            with_param=argo_range(relaxmake.outputs.parameters["njobs"]),
            key="lammps-relaxcal-{{item}}",
            executor=self.executor,
        )
        self.relaxcal = relaxcal

        relaxpost = Step(
            name="Relaxpost",
            template=PythonOPTemplate(RelaxPostLAMMPS, image=self.apex_image_name, command=["python3"]),
            artifacts={"input_post": relaxcal.outputs.artifacts["output_lammps"],
                       "input_all": relaxmake.outputs.artifacts["output"],
                       "param": upload_artifact(self.relax_param)},
            parameters={"path": cwd},
            key="lammps-relaxpost"
        )
        self.relaxpost = relaxpost

        if self.flow_type == 'joint':
            propsmake = Step(
                name="Propsmake",
                template=PythonOPTemplate(PropsMakeLAMMPS, image=self.apex_image_name, command=["python3"]),
                artifacts={"input": relaxpost.outputs.artifacts["output_all"],
                           "param": upload_artifact(self.props_param)},
                key="lammps-propsmake"
            )
        else:
            propsmake = Step(
                name="Propsmake",
                template=PythonOPTemplate(PropsMakeLAMMPS, image=self.apex_image_name, command=["python3"]),
                artifacts={"input": upload_artifact(work_dir),
                           "param": upload_artifact(self.props_param)},
                key="lammps-propsmake"
            )
        self.propsmake = propsmake

        props = PythonOPTemplate(RunLAMMPS,
                                 slices=Slices("{{item}}", input_artifact=["input_lammps"],
                                               output_artifact=["output_lammps"]), image=self.dpmd_image_name, command=["python3"])

        propscal = Step(
            name="PropsLAMMPS-Cal",
            template=props,
            artifacts={"input_lammps": propsmake.outputs.artifacts["task_paths"]},
            parameters={"run_command": self.lammps_run_command},
            with_param=argo_range(propsmake.outputs.parameters["njobs"]),
            key="lammps-propscal-{{item}}",
            executor=self.executor,
        )
        self.propscal = propscal

        propspost = Step(
            name="Propspost",
            template=PythonOPTemplate(PropsPostLAMMPS, image=self.apex_image_name, command=["python3"]),
            artifacts={"input_post": propscal.outputs.artifacts["output_lammps"],
                       "input_all": propsmake.outputs.artifacts["output"],
                       "param": upload_artifact(self.props_param)},
            parameters={"path": cwd},
            key="lammps-propspost"
        )
        self.propspost = propspost
