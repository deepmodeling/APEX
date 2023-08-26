import os
from monty.serialization import loadfn
from pathlib import Path
import fpop
from fpop.vasp import RunVasp
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
from dflow.python import upload_packages
from dflow.plugins.dispatcher import DispatcherExecutor, update_dict
from apex.op.relaxation_ops import RelaxMake, RelaxPost
from apex.op.property_ops import (
    DistributeProps,
    CollectProps,
    PropsMake,
    PropsPost
)
from apex.superop.SimplePropertySteps import SimplePropertySteps
from apex.TestFlow import TestFlow

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
        work_dir = Path(cwd)

        relaxmake = Step(
            name="Relaxmake",
            template=PythonOPTemplate(RelaxMake,
                                      image=self.apex_image_name,
                                      command=["python3"]),
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
            template=PythonOPTemplate(RelaxPost,
                                      image=self.apex_image_name,
                                      command=["python3"]),
            artifacts={"input_post": self.relaxcal.outputs.artifacts["backward_dir"],
                       "input_all": self.relaxmake.outputs.artifacts["output"],
                       "param": upload_artifact(self.relax_param)},
            parameters={"path": work_dir},
            key="vasp-relaxpost"
        )
        self.relaxpost = relaxpost

        if self.flow_type == 'joint':
            input_work_path = relaxpost.outputs.artifacts["output_all"]
            distributeProps = Step(
                name="Distributor",
                template=PythonOPTemplate(DistributeProps,
                                          image=self.apex_image_name,
                                          command=["python3"]),
                artifacts={"input_work_path": input_work_path,
                           "param": upload_artifact(self.props_param)},
                key="distributor"
            )
        else:
            input_work_path = upload_artifact(work_dir)
            distributeProps = Step(
                name="PropsDistributor",
                template=PythonOPTemplate(DistributeProps,
                                          image=self.apex_image_name,
                                          command=["python3"]),
                artifacts={"input_work_path": input_work_path,
                           "param": upload_artifact(self.props_param)},
                key="distributor"
            )
        self.distributeProps = distributeProps

        simple_property_steps = SimplePropertySteps(
            name='simple-property-flow',
            make_op=PropsMake,
            run_op=RunVasp,
            post_op=PropsPost,
            make_image=self.apex_image_name,
            run_image=self.vasp_image_name,
            post_image=self.apex_image_name,
            run_command=self.vasp_run_command,
            calculator="vasp",
            executor=self.executor,
            upload_python_packages=self.upload_python_packages
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
                "input_work_path": distributeProps.outputs.artifacts["orig_work_path"]
            },
            parameters={
                "flow_id": distributeProps.outputs.parameters["flow_id"],
                "path_to_prop": distributeProps.outputs.parameters["path_to_prop"],
                "prop_param": distributeProps.outputs.parameters["prop_param"],
                "inter_param": distributeProps.outputs.parameters["inter_param"],
                "do_refine": distributeProps.outputs.parameters["do_refine"]
            },
            with_param=argo_range(distributeProps.outputs.parameters["nflows"]),
            key="propscal-{{item}}"
        )
        self.propscal = propscal

        collectProps = Step(
            name="PropsCollector",
            template=PythonOPTemplate(CollectProps,
                                      image=self.apex_image_name,
                                      command=["python3"]),
            artifacts={"input_all": input_work_path,
                       "input_post": propscal.outputs.artifacts["output_post"],
                       "param": upload_artifact(self.props_param)},
            key="collector"
        )
        self.collectProps = collectProps
