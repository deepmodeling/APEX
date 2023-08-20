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
    from apex.lib.utils import return_prop_list
    from apex.core.common_equi import (make_equi, post_equi)
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
            'param': Artifact(Path)
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
        structures = loadfn(param_argv)["structures"]
        inter_parameter = loadfn(param_argv)["interaction"]
        parameter = loadfn(param_argv)["relaxation"]
        calculator = inter_parameter["type"]

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
            'input_all': Artifact(Path, sub_path=False),
            'param': Artifact(Path),
            'path': str
        })

    @classmethod
    def get_output_sign(cls):
        return OPIOSign({
            'output_post': Artifact(List[Path], sub_path=False),
            'output_all': Artifact(Path, sub_path=False)
        })

    @OP.exec_sign_check
    def execute(self, op_in: OPIO) -> OPIO:
        from apex.core.common_equi import post_equi

        cwd = os.getcwd()
        os.chdir(str(op_in['input_all']) + op_in['path'])
        shutil.copytree(str(op_in['input_post']), './', dirs_exist_ok=True)

        param_argv = op_in['param']
        inter_param = loadfn(param_argv)["interaction"]
        calculator = inter_param["type"]
        conf_list = loadfn(param_argv)["structures"]
        copy_dir_list = [conf.split('/')[0] for conf in conf_list]
        post_equi(conf_list, inter_param)

        # remove potential files inside md tasks
        if not calculator in ['vasp', 'abacus']:
            conf_dirs = []
            for conf in conf_list:
                conf_dirs.extend(glob.glob(conf))
            conf_dirs.sort()

            for ii in conf_dirs:
                os.chdir(os.path.join(ii, 'relaxation/relax_task'))
                cmd = 'rm *.pb'
                subprocess.call(cmd, shell=True)
                os.chdir("../../../../")


        os.chdir(cwd)
        for ii in copy_dir_list:
            shutil.copytree(str(op_in['input_all']) + op_in['path'] + f'/{ii}',
                            f'./{ii}', dirs_exist_ok = True)

        post_path = [Path(ii) for ii in copy_dir_list]

        op_out = OPIO({
            'output_post': post_path,
            'output_all': Path(str(op_in['input_all']) + op_in['path'])
        })
        return op_out


