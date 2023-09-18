import inspect
from dflow import config, s3_config
from dflow.python import upload_packages
from dflow.plugins import bohrium
from dflow.plugins.bohrium import TiefblueClient
from dflow.plugins.dispatcher import DispatcherExecutor
from apex.utils import update_dict
upload_packages.append(__file__)


class Configer:
    """
    The class config basic APEX workflow from input dict
    """
    def __init__(self, config_data: dict):
        # dflow config
        self._dflow_config = config_data.get("dflow_config", None)
        self._dflow_host = config_data.get("dflow_host", "https://127.0.0.1:2746")
        self._k8s_api_server = config_data.get("k8s_api_server", "https://127.0.0.1:2746")
        self.is_bohrium_dflow = False
        try:
            assert self._dflow_config["host"] == "https://workflows.deepmodeling.com"
        except (AssertionError, TypeError):
            if self._dflow_host == "https://workflows.deepmodeling.com":
                self.is_bohrium_dflow = True
        else:
            self.is_bohrium_dflow = True

        # dflow s3 config
        self._dflow_s3_config = config_data.get("dflow_s3_config", None)

        # bohrium config
        self._bohrium_config = config_data.get("bohrium_config", None)
        self._phone = config_data.get("phone", None)
        self._email = config_data.get("email", None)
        self._password = config_data.get("password", None)
        self._program_id = config_data.get("program_id", None)

        # dispachter
        self._dispatcher = config_data.get("dispatcher", None)
        self._dispatcher_image = config_data.get("dispatcher_image", None)
        self._image_pull_policy = config_data.get("image_pull_policy", "IfNotPresent")
        self._scass_type = config_data.get("scass_type", None)
        self._machine = config_data.get("machine", None)
        self._resources = config_data.get("resources", None)
        self._task = config_data.get("task", None)
        self._context_type = config_data.get("context_type", None)
        self._batch_type = config_data.get("batch_type", None)
        self._clean_asynchronously = config_data.get("clean_asynchronously", True)
        self._local_root = config_data.get("local_root", '.')
        self._remote_root = config_data.get("remote_root", None)
        self._remote_host = config_data.get("remote_host", None)
        self._remote_username = config_data.get("remote_username", None)
        self._remote_password = config_data.get("remote_password", None)
        self._port = config_data.get("port", 22)
        if not (self._context_type or self._machine):
            self.machine_dict = None
        elif self._context_type in ["Bohrium", "bohrium",
                                    "BohriumContext", "boriumcontext"]:
            self.machine_dict = {
                "batch_type": self._batch_type,
                "context_type": self._context_type,
                "remote_profile": {
                    "email": self._email,
                    "password": self._password,
                    "program_id": self._program_id,
                    "input_data": {
                        "job_type": "container",
                        "platform": "ali",
                        "scass_type": self._scass_type,
                    },
                },
            }
            if self._machine:
                update_dict(self.machine_dict, self._machine)
        elif self._context_type in ["SSHContext", "sshcontext",
                                    "SSH", "ssh"]:
            self.machine_dict = {
                "batch_type": self._batch_type,
                "context_type": self._context_type,
                "local_root": self._local_root,
                "remote_root": self._remote_root,
                "clean_asynchronously": self._clean_asynchronously,
                "remote_profile": {
                    "hostname": self._remote_host,
                    "username": self._remote_username,
                    "password": self._remote_password,
                    "port": self._port,
                    "timeout": 10
                }
            }
            if self._machine:
                update_dict(self.machine_dict, self._machine)
        elif self._context_type in ["LocalContext", "localcontext"
                                    "Local", "local"]:
            self.machine_dict = {
                "batch_type": self._batch_type,
                "context_type": self._context_type,
                "local_root": self._local_root,
                "remote_root": self._remote_root,
                "clean_asynchronously": self._clean_asynchronously
            }
            if self._machine:
                update_dict(self.machine_dict, self._machine)
        elif self._context_type in ["LazyLocalContext", "lazylocalcontext"
                                    "LazyLocal", "lazylocal"]:
            self.machine_dict = {
                "batch_type": self._batch_type,
                "context_type": self._context_type,
                "local_root": self._local_root,
                "clean_asynchronously": self._clean_asynchronously
            }
            if self._machine:
                update_dict(self.machine_dict, self._machine)
        elif self._machine:
            self.machine_dict = self._machine
        else:
            raise RuntimeError(
                f'please provide a nested machine dictionary '
                f'for context type: {self._context_type}'
            )

        # calculator config
        self._run_image_name = config_data.get("run_image_name", None)
        self._run_command = config_data.get("run_command", None)
        self._apex_image_name = config_data.get("apex_image_name", "zhuoyli/apex_amd64")
        self._group_size = config_data.get("group_size", None)
        self._pool_size = config_data.get("pool_size", None)
        self._upload_python_packages = config_data.get("upload_python_packages", [])
        if self._group_size and not self._pool_size:
            self._pool_size = 1
        if self._upload_python_packages and not isinstance(self._upload_python_packages, list):
            raise TypeError(
                "Value of 'upload_python_packages' must be a list!"
            )
        # lammps
        self._lammps_image_name = config_data.get("lammps_image_name", None)
        self._lammps_run_command = config_data.get("lammps_run_command", None)
        # vasp
        self._vasp_image_name = config_data.get("vasp_image_name", None)
        self._vasp_run_command = config_data.get("vasp_run_command", None)
        # abacus
        self._abacus_image_name = config_data.get("abacus_image_name", None)
        self._abacus_run_command = config_data.get("abacus_run_command", None)

    @property
    def dflow_config(self):
        dflow_config = {
            "host": self._dflow_host,
            "k8s_api_server": self._k8s_api_server
        }
        if self._dflow_host:
            update_dict(dflow_config, self._dflow_config)
        return dflow_config

    @property
    def dflow_s3_config(self):
        dflow_s3_config = {}
        if self.is_bohrium_dflow:
            dflow_s3_config = {
                "repo_key": "oss-bohrium",
                "storage_client": TiefblueClient()
            }
        if self._dflow_s3_config:
            update_dict(dflow_s3_config, self._dflow_s3_config)
        return dflow_s3_config

    @property
    def bohrium_config(self):
        bohrium_config = {
            "username": self._email,
            "phone": self._phone,
            "password": self._password,
            "program_id": self._program_id
        }
        if self._bohrium_config:
            update_dict(bohrium_config, self._bohrium_config)
        return bohrium_config

    @property
    def dispatcher_config(self):
        dispatcher_config = {
            "image": self._dispatcher_image,
            "image_pull_policy": self._image_pull_policy,
            "machine_dict": self.machine_dict,
            "resources_dict": self._resources,
            "task_dict": self._task
        }
        if self._dispatcher:
            update_dict(dispatcher_config, self._dispatcher)
        return dispatcher_config

    @property
    def basic_config(self):
        basic_config = {
            "run_image_name": self._run_image_name,
            "run_command": self._run_command,
            "apex_image_name": self._apex_image_name,
            "group_size": self._group_size,
            "pool_size": self._pool_size,
            "upload_python_packages": self._upload_python_packages,
            "lammps_image_name": self._lammps_image_name,
            "lammps_run_command": self._lammps_run_command,
            "vasp_image_name": self._vasp_image_name,
            "vasp_run_command": self._vasp_run_command,
            "abacus_image_name": self._abacus_image_name,
            "abacus_run_command": self._abacus_run_command
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
        if not (self._context_type or self._machine):
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
