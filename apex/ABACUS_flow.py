import os
from monty.serialization import loadfn
from pathlib import Path
import fpop
from fpop.abacus import RunAbacus
from dflow import (
    Step,
    upload_artifact
)
from dflow.python import upload_packages
from dflow.plugins.dispatcher import DispatcherExecutor, update_dict
from apex.op.relaxation_ops import RelaxMake, RelaxPost
from apex.op.property_ops import (
    PropsMake,
    PropsPost
)
from apex.superop.RelaxationFlow import RelaxationFlow
from apex.superop.PropertyFlow import PropertyFlow
from apex.TestFlow import TestFlow

upload_packages.append(__file__)
upload_python_packages=list(fpop.__path__)


class ABACUSFlow(TestFlow):
    """
    Generate autotest workflow and automatically submit abacus jobs according to user input arguments.
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
        self.abacus_image_name = global_param.get("abacus_image_name", None)
        self.cpu_scass_type = global_param.get("cpu_scass_type", None)
        self.gpu_scass_type = global_param.get("gpu_scass_type", None)
        self.batch_type = global_param.get("batch_type", None)
        self.context_type = global_param.get("context_type", None)
        self.abacus_run_command = global_param.get("abacus_run_command", None)
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
        work_dir = Path(cwd)

        try:
            relax_param = loadfn(self.relax_param)
        except:
            relax_param = {}
        try:
            prop_param = loadfn(self.props_param)
        except:
            prop_param = {}

        relaxation_flow = RelaxationFlow(
            name='relaxation-flow',
            make_op=RelaxMake,
            run_op=RunAbacus,
            post_op=RelaxPost,
            make_image=self.apex_image_name,
            run_image=self.abacus_image_name,
            post_image=self.apex_image_name,
            run_command=self.abacus_run_command,
            calculator="abacus",
            executor=self.executor,
            upload_python_packages=self.upload_python_packages
        )

        relaxation = Step(
            name='relaxation-cal',
            template=relaxation_flow,
            artifacts={
                "input_work_path": upload_artifact(work_dir)
            },
            parameters={
                "flow_id": "relaxflow",
                "parameter": relax_param
            },
            key="relaxationcal"
        )
        self.relaxation = relaxation

        if self.flow_type == 'props':
            input_work_path = upload_artifact(work_dir)
        elif self.flow_type == 'joint':
            input_work_path = relaxation.outputs.artifacts["output_all"]

        try:
            property_flow = PropertyFlow(
                name='property-flow',
                make_op=PropsMake,
                run_op=RunAbacus,
                post_op=PropsPost,
                make_image=self.apex_image_name,
                run_image=self.abacus_image_name,
                post_image=self.apex_image_name,
                run_command=self.abacus_run_command,
                calculator="abaucs",
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
            self.property = property
        except UnboundLocalError:
            pass
