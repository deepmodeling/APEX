import os, glob, pathlib, shutil, subprocess, logging
from pathlib import Path
from monty.serialization import loadfn
from typing import List
from dflow.python import (
    OP,
    OPIO,
    OPIOSign,
    Artifact,
    upload_packages
)
from monty.serialization import dumpfn
from apex.utils import recursive_search
from apex.core.lib.utils import create_path
from apex.core.calculator import LAMMPS_INTER_TYPE

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
            'input_work_path': Artifact(Path),
            'path_to_prop': str,
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
        from ..core.common_prop import make_property_instance
        from ..core.calculator.calculator import make_calculator

        input_work_path = op_in["input_work_path"]
        path_to_prop = op_in["path_to_prop"]
        prop_param = op_in["prop_param"]
        inter_param = op_in["inter_param"]
        do_refine = op_in["do_refine"]

        cwd = Path.cwd()
        os.chdir(input_work_path)
        abs_path_to_prop = input_work_path / path_to_prop
        if os.path.exists(abs_path_to_prop):
            shutil.rmtree(abs_path_to_prop)
        create_path(str(abs_path_to_prop))
        conf_path = abs_path_to_prop.parent
        prop_name = abs_path_to_prop.name

        # break subworkflow if mismatch stop is set
        path_to_equi = conf_path / "relaxation" / "relax_task"
        try:
            structure_dict = loadfn(os.path.join(path_to_equi, "structure.json"))
        except FileNotFoundError:
            structure_dict = {}
        mismatch = structure_dict.get("mismatch", False)
        skip_mismatch = prop_param.get("skip_mismatch", False)
        if mismatch and skip_mismatch:
            print("Skipped due to mismatched relaxed structure")
            return OPIO({
                'output_work_path': abs_path_to_prop,
                'task_names': [],
                'njobs': 0,
                'task_paths': []
            })

        inter_param_prop = inter_param
        if "cal_setting" in prop_param and "overwrite_interaction" in prop_param["cal_setting"]:
            inter_param_prop = prop_param["cal_setting"]["overwrite_interaction"]

        prop = make_property_instance(prop_param, inter_param_prop)
        task_list = prop.make_confs(abs_path_to_prop, path_to_equi, do_refine)
        for kk in task_list:
            poscar = os.path.join(kk, "POSCAR")
            inter = make_calculator(inter_param_prop, poscar)
            inter.make_potential_files(kk)
            logging.debug(prop.task_type())  ### debug
            inter.make_input_file(kk, prop.task_type(), prop.task_param())
        prop.post_process(
            task_list
        )  # generate same KPOINTS file for elastic when doing VASP

        task_list.sort()
        os.chdir(path_to_prop)
        task_list_name = {'task_list': glob.glob('task.*').sort()}
        dumpfn(task_list_name, 'task_list.json')
        os.chdir(input_work_path)
        task_list_str = glob.glob(path_to_prop + '/' + 'task.*')
        task_list_str.sort()

        all_jobs = task_list
        njobs = len(all_jobs)
        jobs = [pathlib.Path(job) for job in task_list]

        os.chdir(cwd)
        op_out = OPIO({
            "output_work_path": input_work_path,
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
            'input_all': Artifact(Path),
            'prop_param': dict,
            'inter_param': dict,
            'task_names': List[str],
            'path_to_prop': str
        })

    @classmethod
    def get_output_sign(cls):
        return OPIOSign({
            'retrieve_path': Artifact(List[Path])
        })

    @OP.exec_sign_check
    def execute(self, op_in: OPIO) -> OPIO:
        from ..core.common_prop import make_property_instance
        cwd = os.getcwd()
        input_post = op_in["input_post"]
        input_all = op_in["input_all"]
        prop_param = op_in["prop_param"]
        inter_param = op_in["inter_param"]
        task_names = op_in["task_names"]
        path_to_prop = op_in["path_to_prop"]
        inter_type = inter_param["type"]

        if len(task_names) == 0:
            print("Skip post property")
            return OPIO({
                'retrieve_path': []
            })

        copy_dir_list_input = [path_to_prop.split('/')[0]]
        os.chdir(input_all)
        copy_dir_list = []
        for ii in copy_dir_list_input:
            copy_dir_list.extend(glob.glob(ii))
        copy_dir_list = list(set(copy_dir_list))
        copy_dir_list.sort()

        # find path of finished tasks
        os.chdir(op_in['input_post'])
        src_path = recursive_search(copy_dir_list)
        if not src_path:
            raise RuntimeError(f'Fail to find input work path after slices!')

        if inter_type in ['vasp', 'abacus']:
            os.chdir(input_post)
            for ii in task_names:
                shutil.copytree(os.path.join(ii, "backward_dir"), ii, dirs_exist_ok=True)
                shutil.rmtree(os.path.join(ii, "backward_dir"))
            os.chdir(input_all)
            shutil.copytree(input_post, './', dirs_exist_ok=True)
        else:
            os.chdir(input_all)
            # src_path = str(input_post) + str(local_path)
            shutil.copytree(src_path, './', dirs_exist_ok=True)

        if ("cal_setting" in prop_param
                and "overwrite_interaction" in prop_param["cal_setting"]):
            inter_param = prop_param["cal_setting"]["overwrite_interaction"]

        abs_path_to_prop = Path.cwd() / path_to_prop
        prop = make_property_instance(prop_param, inter_param)
        param_json = os.path.join(abs_path_to_prop, "param.json")
        param_dict = prop.parameter
        param_dict.setdefault("skip", False) # default of "skip" is False
        param_dict.pop("skip")
        dumpfn(param_dict, param_json)
        prop.compute(
            os.path.join(abs_path_to_prop, "result.json"),
            os.path.join(abs_path_to_prop, "result.out"),
            abs_path_to_prop,
        )
        # remove potential files in each task
        if inter_type in LAMMPS_INTER_TYPE:
            os.chdir(abs_path_to_prop)
            inter_files_name = []
            if type(inter_param["model"]) is str:
                inter_files_name = [inter_param["model"]]
            elif type(inter_param["model"]) is list:
                inter_files_name.extend(inter_param["model"])
            for file in inter_files_name:
                cmd = f"rm -f ../{file}"
                subprocess.call(cmd, shell=True)
                cmd = f"for kk in task.*; do rm -f $kk/{file}; done"
                subprocess.call(cmd, shell=True)
        elif inter_type == 'vasp':
            os.chdir(abs_path_to_prop)
            cmd = "rm -f ../POTCAR"
            subprocess.call(cmd, shell=True)
            cmd = f"for kk in task.*; do rm -f $kk/POTCAR; done"
            subprocess.call(cmd, shell=True)

        os.chdir(cwd)
        for ii in copy_dir_list:
            shutil.copytree(input_all / ii, ii, dirs_exist_ok=True)
        retrieve_path = [Path(ii) for ii in copy_dir_list]
        # out_path = Path(cwd) / 'retrieve_pool'
        # os.mkdir(out_path)
        # shutil.copytree(input_all / path_to_prop,
        #                out_path / path_to_prop, dirs_exist_ok=True)

        op_out = OPIO({
            'retrieve_path': retrieve_path
        })
        return op_out
