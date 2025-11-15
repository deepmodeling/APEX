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
from apex.core.calculator import LAMMPS_INTER_TYPE
from apex.utils import recursive_search

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
        from ..core.common_equi import make_equi

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
        conf_dirs = list(set(conf_dirs))
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
        from ..core.common_equi import post_equi
        cwd = os.getcwd()
        param_argv = op_in['param']
        inter_param = param_argv["interaction"]
        inter_type = inter_param["type"]
        conf_list = param_argv["structures"]
        copy_dir_list_input = [conf.split('/')[0] for conf in conf_list]
        os.chdir(op_in['input_all'])
        copy_dir_list = []
        for ii in copy_dir_list_input:
            copy_dir_list.extend(glob.glob(ii))

        # find path of finished tasks
        os.chdir(op_in['input_post'])
        src_path = recursive_search(copy_dir_list)
        if not src_path:
            raise RuntimeError(f'Fail to find input work path after slices!')

        os.chdir(op_in['input_all'])
        if inter_type in ['vasp', 'abacus']:
            shutil.copytree(op_in['input_post'], './', dirs_exist_ok=True)
            post_equi(conf_list, inter_param)
        else:
            # src_path = str(input_post) + str(local_path)
            shutil.copytree(src_path, './', dirs_exist_ok=True)
            post_equi(conf_list, inter_param)
            conf_dirs = []
            for conf in conf_list:
                conf_dirs.extend(glob.glob(conf))
            conf_dirs = list(set(conf_dirs))
            conf_dirs.sort()

            # remove potential files
            inter_files_name = []
            if inter_type in LAMMPS_INTER_TYPE:
                if type(inter_param["model"]) is str:
                    inter_files_name = [inter_param["model"]]
                elif type(inter_param["model"]) is list:
                    inter_files_name.extend(inter_param["model"])
            elif inter_type == 'vasp':
                inter_files_name = ['POTCAR']

            for ii in conf_dirs:
                cmd = 'rm -f'
                for jj in inter_files_name:
                    cmd += f' {jj}'
                os.chdir(ii)
                subprocess.call(cmd, shell=True)
                os.chdir(op_in['input_all'])
                os.chdir(os.path.join(ii, 'relaxation/relax_task'))
                subprocess.call(cmd, shell=True)
                os.chdir(op_in['input_all'])

        os.chdir(cwd)
        for ii in copy_dir_list:
            src_path = str(op_in['input_all']) + f'/{ii}'
            shutil.copytree(src_path, f'./{ii}', dirs_exist_ok=True)
        post_path = [Path(ii) for ii in copy_dir_list]

        op_out = OPIO({
            'retrieve_path': post_path,
            'output_all': op_in['input_all']
        })
        return op_out
