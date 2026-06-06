import os, glob, pathlib, shutil, subprocess
from pathlib import Path
from typing import List
from monty.serialization import loadfn
from dflow.python import (
    OP,
    OPIO,
    OPIOSign,
    Artifact,
    upload_packages
)
from apex.core.calculator import LAMMPS_INTER_TYPE
from apex.utils import recursive_search, apex_task_succeeded

upload_packages.append(__file__)


def _load_task_status(status_path: str):
    if not os.path.isfile(status_path):
        return None
    try:
        return loadfn(status_path)
    except Exception as exc:
        return {
            "state": "failed",
            "reason": "invalid_task_status",
            "message": f"Could not parse apex_task_status.json: {exc}",
            "exit_code": None,
        }


def _is_failed_task_status(status) -> bool:
    if status is None:
        return False
    return status.get("state") != "succeeded" or status.get("exit_code") != 0


def _check_relaxation_outputs(conf_dirs: List[str]) -> None:
    failed = []
    for conf_dir in conf_dirs:
        task_dir = os.path.join(conf_dir, "relaxation", "relax_task")
        status_path = os.path.join(task_dir, "apex_task_status.json")
        contcar = os.path.join(task_dir, "CONTCAR")
        result = os.path.join(task_dir, "result.json")
        status = _load_task_status(status_path)
        if _is_failed_task_status(status):
            reason = status.get("reason", "unknown")
            exit_code = status.get("exit_code")
            failed.append(
                f"{task_dir} (LAMMPS failed: state={status.get('state')}, "
                f"reason={reason}, exit_code={exit_code}; see apex_task_status.json, "
                ".debug.log, and log.lammps)"
            )
        elif not os.path.isfile(contcar):
            failed.append(f"{task_dir} (missing CONTCAR)")
        elif not os.path.isfile(result):
            failed.append(f"{task_dir} (missing result.json)")
    if failed:
        raise RuntimeError(
            "Relaxation failed or did not produce required output for task(s): "
            + "; ".join(failed)
            + ". Property steps require relaxation/relax_task/CONTCAR."
        )


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
        rerun_finished = inter_parameter.get("rerun_finished", True)
        for ii in conf_dirs:
            conf_dir_global = os.path.join(work_d, ii)
            task_dir = os.path.join(conf_dir_global, 'relaxation/relax_task')
            if (not rerun_finished) and apex_task_succeeded(task_dir):
                print(f"Skip running completed relaxation task {task_dir} (apex_task_status.json state=succeeded, rerun_finished=False)")
                continue
            task_list.append(task_dir)
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
            _check_relaxation_outputs(conf_dirs)

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
