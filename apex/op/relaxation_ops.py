import os, glob, pathlib, shutil, subprocess
from pathlib import Path
from typing import List
from dflow.python import (
    OP,
    OPIO,
    OPIOSign,
    Artifact,
    upload_packages
)
try:
    from monty.serialization import loadfn
    from apex.utils import return_prop_list
    from apex.core.common_equi import (make_equi, post_equi)
    from .utils import recursive_search
except:
    pass

upload_packages.append(__file__)

class RelaxMake(OP):
    """
    OP class for making calculation tasks
    """

    def __init__(self):
        pass

    @classmethod
    def get_input_sign(cls):
        return OPIOSign({
            'input': Artifact(Path),
            'param': dict
        })

    @classmethod
    def get_output_sign(cls):
        return OPIOSign({
            'output': Artifact(Path),
            'njobs': int,
            'task_names': List[str],
            'task_paths': Artifact(List[Path])
        })

    @OP.exec_sign_check
    def execute(
            self,
            op_in: OPIO,
    ) -> OPIO:
        from apex.core.common_equi import make_equi

        cwd = os.getcwd()
        os.chdir(op_in["input"])
        work_d = os.getcwd()
        param_argv = op_in["param"]
        structures = param_argv["structures"]
        inter_parameter = param_argv["interaction"]
        parameter = param_argv["relaxation"]

        make_equi(structures, inter_parameter, parameter)

        conf_dirs = []
        for conf in structures:
            conf_dirs.extend(glob.glob(conf))
        conf_dirs.sort()

        task_list = []
        task_list_str = []
        for ii in conf_dirs:
            conf_dir_global = os.path.join(work_d, ii)
            task_list.append(os.path.join(conf_dir_global, 'relaxation/relax_task'))
            task_list_str.append(os.path.join(ii, 'relaxation'))

        all_jobs = task_list
        njobs = len(all_jobs)
        jobs = []
        for job in all_jobs:
            jobs.append(pathlib.Path(job))

        os.chdir(cwd)
        op_out = OPIO({
            "output": op_in["input"],
            "task_names": task_list_str,
            "njobs": njobs,
            "task_paths": jobs
        })
        return op_out


class RelaxPost(OP):
    """
    OP class for analyzing calculation results
    """

    def __init__(self):
        pass

    @classmethod
    def get_input_sign(cls):
        return OPIOSign({
            'input_post': Artifact(Path, sub_path=False),
            'input_all': Artifact(Path),
            'param': dict
        })

    @classmethod
    def get_output_sign(cls):
        return OPIOSign({
            'retrieve_path': Artifact(List[Path]),
            'output_all': Artifact(Path)
        })

    @OP.exec_sign_check
    def execute(self, op_in: OPIO) -> OPIO:
        from apex.core.common_equi import post_equi

        param_argv = op_in['param']
        inter_param = param_argv["interaction"]
        calculator = inter_param["type"]
        conf_list = param_argv["structures"]
        copy_dir_list = [conf.split('/')[0] for conf in conf_list]

        cwd = os.getcwd()
        # find path of finished tasks
        os.chdir(op_in['input_post'])
        if not recursive_search(copy_dir_list):
            raise RuntimeError(f'Fail to find input work path after slices!')
        else:
            src_path = os.getcwd()

        os.chdir(op_in['input_all'])
        if calculator in ['vasp', 'abacus']:
            shutil.copytree(op_in['input_post'], './', dirs_exist_ok=True)
            post_equi(conf_list, inter_param)
        else:
            # src_path = str(input_post) + str(local_path)
            shutil.copytree(src_path, './', dirs_exist_ok=True)
            post_equi(conf_list, inter_param)
            conf_dirs = []
            for conf in conf_list:
                conf_dirs.extend(glob.glob(conf))
            conf_dirs.sort()
            for ii in conf_dirs:
                cmd = 'rm *.pb'
                os.chdir(ii)
                subprocess.call(cmd, shell=True)
                os.chdir(op_in['input_all'])
                os.chdir(os.path.join(ii, 'relaxation/relax_task'))
                subprocess.call(cmd, shell=True)
                os.chdir(op_in['input_all'])

        os.chdir(cwd)
        for ii in copy_dir_list:
            src_path = str(op_in['input_all']) + f'/{ii}'
            print(src_path)
            shutil.copytree(src_path, f'./{ii}', dirs_exist_ok=True)
        post_path = [Path(ii) for ii in copy_dir_list]

        op_out = OPIO({
            'retrieve_path': post_path,
            'output_all': op_in['input_all']
        })
        return op_out


