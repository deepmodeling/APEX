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
    from apex.core.common_prop import make_property_instance
except:
    pass

upload_packages.append(__file__)


class DistributeProps(OP):
    """
    OP class for distribution
    of individual property test steps
    """
    def __init__(self):
        pass

    @classmethod
    def get_input_sign(cls):
        return OPIOSign({
            "input_work_path": Artifact(Path),
            "param": dict
        })

    @classmethod
    def get_output_sign(cls):
        return OPIOSign({
            "orig_work_path": Artifact(List[Path]),
            "flow_id": List[str],
            "path_to_prop": List[str],
            "prop_param": List[dict],
            "inter_param": List[dict],
            "do_refine": List[bool],
            "nflows": int
        })

    @OP.exec_sign_check
    def execute(
            self,
            op_in: OPIO,
    ) -> OPIO:
        input_work_path = op_in["input_work_path"]
        param = op_in["param"]

        cwd = Path.cwd()
        os.chdir(input_work_path)
        confs = param["structures"]
        interaction = param["interaction"]
        properties = param["properties"]

        conf_dirs = []
        flow_id_list = []
        path_to_prop_list = []
        prop_param_list = []
        do_refine_list = []
        for conf in confs:
            conf_dirs.extend(glob.glob(conf))
        conf_dirs.sort()
        for ii in conf_dirs:
            for jj in properties:
                if jj.get('skip', False):
                    continue
                if 'init_from_suffix' and 'output_suffix' in jj:
                    do_refine = True
                    suffix = jj['output_suffix']
                elif 'reproduce' in jj and jj['reproduce']:
                    do_refine = False
                    suffix = 'reprod'
                elif 'suffix' in ii and jj['suffix']:
                    suffix = str(jj['suffix'])
                else:
                    do_refine = False
                    suffix = '00'

                property_type = jj["type"]
                path_to_prop_list.append(os.path.join(ii, property_type + "_" + suffix))
                prop_param_list.append(jj)
                do_refine_list.append(do_refine)
                flow_id_list.append(ii + '-' + property_type + '-' + suffix)

        nflow = len(path_to_prop_list)
        orig_work_path_list = [input_work_path] * nflow
        inter_param_list = [interaction] * nflow

        op_out = OPIO({
            "orig_work_path": orig_work_path_list,
            "flow_id": flow_id_list,
            "path_to_prop": path_to_prop_list,
            "prop_param": prop_param_list,
            "inter_param": inter_param_list,
            "do_refine": do_refine_list,
            "nflows": nflow
        })
        return op_out


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
        from apex.core.common_prop import make_property_instance
        from apex.core.calculator.calculator import make_calculator

        input_work_path = op_in["input_work_path"]
        path_to_prop = op_in["path_to_prop"]
        prop_param = op_in["prop_param"]
        inter_param = op_in["inter_param"]
        do_refine = op_in["do_refine"]

        cwd = Path.cwd()
        os.chdir(input_work_path)
        abs_path_to_prop = input_work_path / path_to_prop
        conf_path = abs_path_to_prop.parent
        prop_name = abs_path_to_prop.name
        path_to_equi = conf_path / "relaxation" / "relax_task"
        prop = make_property_instance(prop_param, inter_param)
        task_list = prop.make_confs(abs_path_to_prop, path_to_equi, do_refine)
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
        os.chdir(input_work_path)
        task_list_str = glob.glob(path_to_prop + '/' + 'task.*')

        all_jobs = task_list
        njobs = len(all_jobs)
        jobs = []
        for job in all_jobs:
            jobs.append(pathlib.Path(job))

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
            'input_post': Artifact(Path),
            'input_all': Artifact(Path),
            'prop_param': dict,
            'inter_param': dict,
            'task_names': List[str],
            'local_path': os.PathLike,
            'path_to_prop': str
        })

    @classmethod
    def get_output_sign(cls):
        return OPIOSign({
            'output_post': Artifact(Path)
        })

    @OP.exec_sign_check
    def execute(self, op_in: OPIO) -> OPIO:
        from apex.core.common_prop import make_property_instance

        input_post = op_in["input_post"]
        input_all = op_in["input_all"]
        prop_param = op_in["prop_param"]
        inter_param = op_in["inter_param"]
        task_names = op_in["task_names"]
        local_path = op_in["local_path"]
        path_to_prop = op_in["path_to_prop"]
        calculator = inter_param["type"]

        cwd = os.getcwd()
        if calculator in ['vasp', 'abacus']:
            os.chdir(input_post)
            for ii in task_names:
                shutil.copytree(os.path.join(ii, "backward_dir"), ii, dirs_exist_ok=True)
                shutil.rmtree(os.path.join(ii, "backward_dir"))
            os.chdir(input_all)
            shutil.copytree(input_post, './', dirs_exist_ok=True)
        else:
            os.chdir(input_all)
            src_path = str(input_post) + str(local_path)
            shutil.copytree(src_path, './', dirs_exist_ok=True)

        if ("cal_setting" in prop_param
                and "overwrite_interaction" in prop_param["cal_setting"]):
            inter_param = prop_param["cal_setting"]["overwrite_interaction"]

        abs_path_to_prop = Path.cwd() / path_to_prop

        prop = make_property_instance(prop_param, inter_param)
        prop.compute(
            os.path.join(abs_path_to_prop, "result.json"),
            os.path.join(abs_path_to_prop, "result.out"),
            abs_path_to_prop,
        )
        # remove potential files in each md task
        os.chdir(abs_path_to_prop)
        cmd = "for kk in task.*; do cd $kk; rm *.pb; cd ..; done"
        subprocess.call(cmd, shell=True)


        os.chdir(cwd)
        out_path = Path(cwd) / 'retrieve_pool'
        os.mkdir(out_path)
        shutil.copytree(input_all / path_to_prop,
                        out_path / path_to_prop, dirs_exist_ok=True)

        op_out = OPIO({
            'output_post': out_path
        })
        return op_out


class CollectProps(OP):
    """
    OP class for collect property tasks
    """

    def __init__(self):
        pass

    @classmethod
    def get_input_sign(cls):
        return OPIOSign({
            'input_post': Artifact(Path),
            'input_all': Artifact(Path),
            'param': dict
        })

    @classmethod
    def get_output_sign(cls):
        return OPIOSign({
            'retrieve_path': Artifact(List[Path])
        })

    @OP.exec_sign_check
    def execute(self, op_in: OPIO) -> OPIO:
        input_post = op_in["input_post"]
        input_all = op_in["input_all"]
        param = op_in["param"]
        confs = param["structures"]

        retrieve_conf_list = [conf.split('/')[0] for conf in confs]
        shutil.copytree(input_post / 'retrieve_pool', input_all, dirs_exist_ok=True)

        for ii in retrieve_conf_list:
            shutil.copytree(input_all / ii, ii, dirs_exist_ok=True)

        retrieve_path = [Path(ii) for ii in retrieve_conf_list]

        op_out = OPIO({
            'retrieve_path': retrieve_path
        })
        return op_out
