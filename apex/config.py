import inspect
from dataclasses import dataclass, field

from dflow import config, s3_config
from dflow.plugins import bohrium
from dflow.plugins.bohrium import TiefblueClient
from dflow.plugins.dispatcher import DispatcherExecutor

from apex.utils import update_dict


@dataclass
class Config:
    # dflow config
    dflow_config: dict = None
    dflow_host: str = "https://127.0.0.1:2746"
    k8s_api_server: str = "https://127.0.0.1:2746"
    mode: str = 'default'
    debug_copy_method: str = "copy"
    debug_pool_workers: int = 1
    debug_workdir: str = None

    # dflow s3 config
    dflow_s3_config: dict = None

    # Bohrium config
    bohrium_config: dict = None
    phone: str = None
    email: str = None
    password: str = None
    program_id: int = None
    job_type: str = "container"
    platform: str = "ali"

    # DispachterExecutor config
    dispatcher_config: dict = None
    dispatcher_image: str = None
    dispatcher_command: str = "python3"
    dispatcher_remote_command: str = None
    image_pull_policy: str = "IfNotPresent"
    scass_type: str = None
    machine: dict = None
    resources: dict = None
    task: dict = None
    context_type: str = None
    batch_type: str = None
    clean_asynchronously: bool = True
    local_root: str = '.'
    remote_root: str = None
    remote_host: str = None
    remote_username: str = None
    remote_password: str = None
    port: int = 22

    # basic run config
    run_image_name: str = None
    run_command: str = None
    apex_image_name: str = "zhuoyli/apex_amd64"
    group_size: int = None
    pool_size: int = None
    upload_python_packages: list = field(default_factory=list)
    exclude_upload_files: list = field(default_factory=list)
    lammps_image_name: str = None
    lammps_run_command: str = None
    vasp_image_name: str = None
    vasp_run_command: str = None
    abacus_image_name: str = None
    abacus_run_command: str = None

    # common APEX config
    is_bohrium_dflow: bool = False
    submit_only: bool = False
    flow_name: str = None

    database_type: str = 'local'
    archive_method: str = 'sync'
    archive_key: str = None
    archive_tasks: bool = False

    # MongoDB config
    mongodb_config: dict = None
    mongodb_host: str = "localhost"
    mongodb_port: int = 27017
    mongodb_database: str = "apex_results"
    mongodb_collection: str = "default_collection"

    # DynamoDB config
    dynamodb_config: dict = None
    dynamodb_table_name: str = "apex_results"

    def __post_init__(self):
        # judge if running dflow on the Bohrium
        try:
            assert self.dflow_config["host"] == "https://workflows.deepmodeling.com"
        except (AssertionError, TypeError):
            if self.dflow_host == "https://workflows.deepmodeling.com":
                self.is_bohrium_dflow = True
        else:
            self.is_bohrium_dflow = True

        # pre-define machine dictionaries for dpdispatcher
        if not (self.context_type or self.machine):
            self.machine_dict = None
        elif self.context_type in ["Bohrium", "bohrium",
                                   "BohriumContext", "boriumcontext"]:
            self.machine_dict = {
                "batch_type": self.batch_type,
                "context_type": self.context_type,
                "remote_profile": {
                    "email": self.email,
                    "password": self.password,
                    "program_id": self.program_id,
                    "input_data": {
                        "job_type": self.job_type,
                        "platform": self.platform,
                        "scass_type": self.scass_type,
                    },
                },
            }
            if self.machine:
                update_dict(self.machine_dict, self.machine)
        elif self.context_type in ["SSHContext", "sshcontext",
                                   "SSH", "ssh"]:
            self.machine_dict = {
                "batch_type": self.batch_type,
                "context_type": self.context_type,
                "local_root": self.local_root,
                "remote_root": self.remote_root,
                "clean_asynchronously": self.clean_asynchronously,
                "remote_profile": {
                    "hostname": self.remote_host,
                    "username": self.remote_username,
                    "password": self.remote_password,
                    "port": self.port,
                    "timeout": 10
                }
            }
            if self.machine:
                update_dict(self.machine_dict, self.machine)
        elif self.context_type in ["LocalContext", "localcontext",
                                                   "Local", "local"]:
            self.machine_dict = {
                "batch_type": self.batch_type,
                "context_type": self.context_type,
                "local_root": self.local_root,
                "remote_root": self.remote_root,
                "clean_asynchronously": self.clean_asynchronously
            }
            if self.machine:
                update_dict(self.machine_dict, self.machine)
        elif self.context_type in ["LazyLocalContext", "lazylocalcontext",
                                                       "LazyLocal", "lazylocal"]:
            self.machine_dict = {
                "batch_type": self.batch_type,
                "context_type": self.context_type,
                "local_root": self.local_root,
                "clean_asynchronously": self.clean_asynchronously
            }
            if self.machine:
                update_dict(self.machine_dict, self.machine)
        elif self.machine:
            self.machine_dict = self.machine
        else:
            raise RuntimeError(
                f'please provide a nested machine dictionary '
                f'for context type: {self.context_type}'
            )

        # default pool_size as two size must be indicated simultaneously
        if self.group_size and not self.pool_size:
            self.pool_size = 1

    @property
    def dflow_config_dict(self):
        dflow_config = {
            "host": self.dflow_host,
            "k8s_api_server": self.k8s_api_server,
            "mode": self.mode,
            "debug_copy_method": self.debug_copy_method,
            "debug_pool_workers": self.debug_pool_workers,
            "debug_workdir": self.debug_workdir
        }
        if self.dflow_host:
            update_dict(dflow_config, self.dflow_config)
        return dflow_config

    @property
    def dflow_s3_config_dict(self):
        dflow_s3_config = {}
        if self.is_bohrium_dflow:
            dflow_s3_config = {
                "repo_key": "oss-bohrium",
                "storage_client": TiefblueClient()
            }
        if self.dflow_s3_config:
            update_dict(dflow_s3_config, self.dflow_s3_config)
        return dflow_s3_config

    @property
    def bohrium_config_dict(self):
        bohrium_config = {
            "username": self.email,
            "phone": self.phone,
            "password": self.password,
            "project_id": self.program_id
        }
        if self.bohrium_config:
            update_dict(bohrium_config, self.bohrium_config)
        return bohrium_config

    @property
    def dispatcher_config_dict(self):
        dispatcher_config = {
            "image": self.dispatcher_image,
            "image_pull_policy": self.image_pull_policy,
            "machine_dict": self.machine_dict,
            "resources_dict": self.resources,
            "task_dict": self.task,
            "command": self.dispatcher_command,
            "remote_command": self.dispatcher_remote_command
        }
        if self.dispatcher_config:
            update_dict(dispatcher_config, self.dispatcher_config)
        return dispatcher_config

    @property
    def mongodb_config_dict(self):
        mongodb_config = {
            "host": self.mongodb_host,
            "port": self.mongodb_port
        }
        if self.mongodb_config:
            update_dict(mongodb_config, self.mongodb_config)
        return mongodb_config

    @property
    def dynamodb_config_dict(self):
        dynamodb_config = {}
        if self.mongodb_config:
            update_dict(dynamodb_config, self.dynamodb_config)
        return dynamodb_config

    @property
    def basic_config_dict(self):
        basic_config = {
            "run_image_name": self.run_image_name,
            "run_command": self.run_command,
            "apex_image_name": self.apex_image_name,
            "group_size": self.group_size,
            "pool_size": self.pool_size,
            "upload_python_packages": self.upload_python_packages,
            "lammps_image_name": self.lammps_image_name,
            "lammps_run_command": self.lammps_run_command,
            "vasp_image_name": self.vasp_image_name,
            "vasp_run_command": self.vasp_run_command,
            "abacus_image_name": self.abacus_image_name,
            "abacus_run_command": self.abacus_run_command
        }
        return basic_config

    @staticmethod
    def config_dflow(dflow_config_data: dict) -> None:
        dflow_s3_config = {}
        for kk in dflow_config_data.keys():
            if kk[:3] == "s3_":
                dflow_s3_config[kk[3:]] = dflow_config_data.pop(kk)
        for kk in dflow_config_data.keys():
            config[kk] = dflow_config_data[kk]
        for kk in dflow_s3_config.keys():
            s3_config[kk] = dflow_s3_config[kk]

    @staticmethod
    def config_s3(dflow_s3_config_data: dict) -> None:
        for kk in dflow_s3_config_data.keys():
            s3_config[kk] = dflow_s3_config_data[kk]

    @staticmethod
    def config_bohrium(bohrium_config_data: dict) -> None:
        for kk in bohrium_config_data.keys():
            bohrium.config[kk] = bohrium_config_data[kk]

    def get_executor(
            self,
            dispatcher_config: dict
    ) -> DispatcherExecutor:
        if not (self.context_type or self.machine or self.dispatcher_config):
            executor = None
        else:
            # get arguments for instantiation of the DispatcherExecutor
            sig = inspect.signature(DispatcherExecutor.__init__)
            # pop out 'self'
            defined_parameters = list(sig.parameters.keys())
            defined_parameters.pop(0)
            # filter the dispatcher_config
            filtered_parameters = {
                k: v for k, v in dispatcher_config.items() if k in defined_parameters
            }
            executor = DispatcherExecutor(**filtered_parameters)

        return executor
