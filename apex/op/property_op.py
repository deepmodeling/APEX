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
    from apex.core.common_prop import make_property_instance
except:
    pass

upload_packages.append(__file__)


class PropsMake(OP):
    """
    OP class for making calculation tasks (make property)
    """
    def __init__(self):
        pass

    @classmethod
    def get_input_sign(cls):
        return OPIOSign({
            'path_to_work': Artifact(Path),
            'path_to_equi': Artifact(Path),
            'prop_param': dict,
            'inter_param': dict,
            'do_refine': bool
        })

    @classmethod
    def get_output_sign(cls):
        return OPIOSign({
            'output_work_path': Artifact(Path),
            'task_names': List[str],
            'njobs': int,
            'task_paths': Artifact(List[Path])
        })

    @OP.exec_sign_check
    def execute(
            self,
            op_in: OPIO,
    ) -> OPIO:
        from apex.core.common_prop import make_property_instance
        from apex.calculator.calculator import make_calculator

        path_to_work = op_in["path_to_work"]
        path_to_equi = op_in["path_to_equi"]
        prop_param = op_in["prop_param"]
        inter_param = op_in["inter_param"]
        do_refine = op_in["do_refine"]

        cwd = os.getcwd()
        os.chdir(path_to_work)
        prop = make_property_instance(prop_param, inter_param)
        task_list = prop.make_confs(path_to_work, path_to_equi, do_refine)
        for kk in task_list:
            poscar = os.path.join(kk, "POSCAR")
            inter = make_calculator(inter_param, poscar)
            inter.make_potential_files(kk)
            # dlog.debug(prop.task_type())  ### debug
            inter.make_input_file(kk, prop.task_type(), prop.task_param())
        prop.post_process(
            task_list
        )  # generate same KPOINTS file for elastic when doing VASP

        task_list.sort()
        task_list_str = task_list

        all_jobs = task_list
        njobs = len(all_jobs)
        jobs = []
        for job in all_jobs:
            jobs.append(pathlib.Path(job))

        os.chdir(cwd)
        op_out = OPIO({
            "output": path_to_work,
            "task_names": task_list_str,
            "njobs": njobs,
            "task_paths": jobs
        })
        return op_out


class PropsPost(OP):
    """
    OP class for analyzing calculation results (post property)
    """

    def __init__(self):
        pass

    @classmethod
    def get_input_sign(cls):
        return OPIOSign({
            'input_post': Artifact(Path, sub_path=False),
            'input_all': Artifact(Path, sub_path=False),
            'prop_param': dict,
            'inter_param': dict,
            'task_names': List[str]
        })

    @classmethod
    def get_output_sign(cls):
        return OPIOSign({
            'output_post': Artifact(Path, sub_path=False)
        })

    @OP.exec_sign_check
    def execute(self, op_in: OPIO) -> OPIO:
        input_post = op_in["input_post"]
        input_all = op_in["input_all"]
        prop_param = op_in["prop_param"]
        inter_param = op_in["inter_param"]
        task_names = op_in["task_names"]
        calculator = inter_param["type"]

        cwd = os.getcwd()
        if calculator in ['vasp', 'abacus']:
            os.chdir(str(input_post))
            for ii in op_in['task_names']:
                shutil.copytree(os.path.join(ii, "backward_dir"), ii, dirs_exist_ok=True)
                shutil.rmtree(os.path.join(ii, "backward_dir"))

        os.chdir(str(input_all))
        shutil.copytree(str(input_post), './', dirs_exist_ok=True)

        if ("cal_setting" in prop_param
                and "overwrite_interaction" in prop_param["cal_setting"]):
            inter_param = prop_param["cal_setting"]["overwrite_interaction"]

        prop = make_property_instance(prop_param, inter_param)
        prop.compute(
            os.path.join(input_all, "result.json"),
            os.path.join(input_all, "result.out"),
            input_all,
        )
        # remove potential files in each md task
        cmd = "for kk in task.*; do cd $kk; rm *.pb; cd ..; done"
        subprocess.call(cmd, shell=True)

        os.chdir(cwd)
        op_out = OPIO({
            'output_post': input_all
        })
        return op_out


