from dflow.python import (
    OP,
    OPIO,
    OPIOSign,
    Artifact
)

import subprocess, os, shutil, glob, pathlib
from pathlib import Path
from typing import List
from monty.serialization import loadfn
from dflow.python import upload_packages

from apex.lib.utils import return_prop_list
try:
    from apex.property.common_equi import (make_equi, post_equi)
    from apex.property.common_prop import (make_property, post_property)
except:
    pass

upload_packages.append(__file__)

class RelaxMakeLAMMPS(OP):
    """
    class for making calculation tasks
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
            'task_paths': Artifact(List[Path])
        })

    @OP.exec_sign_check
    def execute(
            self,
            op_in: OPIO,
    ) -> OPIO:
        from apex.property.common_equi import make_equi

        cwd = os.getcwd()
        os.chdir(op_in["input"])
        work_d = os.getcwd()
        param_argv = op_in["param"]
        structures = loadfn(param_argv)["structures"]
        inter_parameter = loadfn(param_argv)["interaction"]
        parameter = loadfn(param_argv)["relaxation"]

        make_equi(structures, inter_parameter, parameter)

        conf_dirs = []
        for conf in structures:
            conf_dirs.extend(glob.glob(conf))
        conf_dirs.sort()

        task_list = []
        for ii in conf_dirs:
            conf_dir_global = os.path.join(work_d, ii)
            task_list.append(os.path.join(conf_dir_global, 'relaxation/relax_task'))

        all_jobs = task_list
        njobs = len(all_jobs)
        jobs = []
        for job in all_jobs:
            jobs.append(pathlib.Path(job))

        os.chdir(cwd)
        op_out = OPIO({
            "output": op_in["input"],
            "njobs": njobs,
            "task_paths": jobs
        })
        return op_out


class RunLAMMPS(OP):
    """
    class for LAMMPS calculation
    """
    def __init__(self, infomode=1):
        self.infomode = infomode

    @classmethod
    def get_input_sign(cls):
        return OPIOSign({
            'input_lammps': Artifact(Path),
            'run_command': str
        })

    @classmethod
    def get_output_sign(cls):
        return OPIOSign({
            'output_lammps': Artifact(Path, sub_path=False)
        })

    @OP.exec_sign_check
    def execute(self, op_in: OPIO) -> OPIO:
        cwd = os.getcwd()
        os.chdir(op_in["input_lammps"])
        cmd = op_in["run_command"]
        exit_code = subprocess.call(cmd, shell=True)
        if exit_code == 0:
            print("Call Lammps command successfully!")
        else:
            print("Call Lammps command failed with exit code:", exit_code)

        os.chdir(cwd)
        op_out = OPIO({
            "output_lammps": op_in["input_lammps"]
        })
        return op_out


class RelaxPostLAMMPS(OP):
    """
    class for analyzing calculation results
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
            'output_all': Artifact(Path, sub_path=False),
            'output_post': Artifact(Path, sub_path=False)
        })

    @OP.exec_sign_check
    def execute(self, op_in: OPIO) -> OPIO:
        from apex.property.common_equi import post_equi

        cwd = os.getcwd()
        os.chdir(str(op_in['input_all'])+op_in['path'])
        shutil.copytree(str(op_in['input_post']) + op_in['path'], './', dirs_exist_ok=True)

        param_argv = op_in['param']
        post_equi(loadfn(param_argv)["structures"], loadfn(param_argv)["interaction"])

        conf_dirs = []
        for conf in loadfn(param_argv)["structures"]:
            conf_dirs.extend(glob.glob(conf))
        conf_dirs.sort()

        for ii in conf_dirs:
            os.chdir(os.path.join(ii, 'relaxation/relax_task'))
            cmd = 'rm *.pb'
            subprocess.call(cmd, shell=True)
            os.chdir("../../../../")

        os.chdir(cwd)
        shutil.copytree(str(op_in['input_all']) + op_in['path'] + '/confs', './confs', dirs_exist_ok = True)

        op_out = OPIO({
            'output_all': Path(str(op_in["input_all"])+op_in['path']),
            'output_post': Path('./confs')
        })
        return op_out


class PropsMakeLAMMPS(OP):
    """
    class for making calculation tasks
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
            'task_paths': Artifact(List[Path])
        })

    @OP.exec_sign_check
    def execute(
            self,
            op_in: OPIO,
    ) -> OPIO:
        from apex.property.common_prop import make_property

        cwd = os.getcwd()
        os.chdir(op_in["input"])
        work_d = os.getcwd()
        param_argv = op_in["param"]
        structures = loadfn(param_argv)["structures"]
        inter_parameter = loadfn(param_argv)["interaction"]
        parameter = loadfn(param_argv)["properties"]
        make_property(structures, inter_parameter, parameter)

        conf_dirs = []
        for conf in structures:
            conf_dirs.extend(glob.glob(conf))
        conf_dirs.sort()

        prop_list = return_prop_list(parameter)
        task_list = []
        for ii in conf_dirs:
            conf_dir_global = os.path.join(work_d, ii)
            for jj in prop_list:
                #task_list.append(os.path.join(conf_dir_global, jj))
                prop = os.path.join(conf_dir_global, jj)
                os.chdir(prop)
                prop_tasks = glob.glob(os.path.join(prop, 'task.*'))
                prop_tasks.sort()
                for kk in prop_tasks:
                    task_list.append(kk)

        all_jobs = task_list
        njobs = len(all_jobs)
        jobs = []
        for job in all_jobs:
            jobs.append(pathlib.Path(job))

        os.chdir(cwd)
        op_out = OPIO({
            "output": op_in["input"],
            "njobs": njobs,
            "task_paths": jobs
        })
        return op_out


class PropsPostLAMMPS(OP):
    """
    class for analyzing calculation results
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
            'output_post': Artifact(Path, sub_path=False)
        })

    @OP.exec_sign_check
    def execute(self, op_in: OPIO) -> OPIO:
        from apex.property.common_prop import post_property

        cwd = os.getcwd()
        os.chdir(str(op_in['input_all'])+op_in['path'])
        shutil.copytree(str(op_in['input_post']) + op_in['path'], './', dirs_exist_ok=True)

        param_argv = op_in["param"]
        post_property(loadfn(param_argv)["structures"], loadfn(param_argv)["interaction"], loadfn(param_argv)["properties"])

        conf_dirs = []
        for conf in loadfn(param_argv)["structures"]:
            conf_dirs.extend(glob.glob(conf))
        conf_dirs.sort()

        prop_list = return_prop_list(loadfn(param_argv)["properties"])
        for ii in conf_dirs:
            for jj in prop_list:
                os.chdir(os.path.join(ii,jj))
                cmd = "for kk in task.*; do cd $kk; rm *.pb; cd ..; done"
                subprocess.call(cmd, shell=True)
                os.chdir('../../../')

        os.chdir(cwd)
        shutil.copytree(str(op_in['input_all']) + op_in['path'] + '/confs', './confs', dirs_exist_ok=True)

        op_out = OPIO({
            'output_post': Path('./confs')
        })
        return op_out


# for future usage of serial running MD
"""
class PropsLAMMPS(OP):
    def __init__(self, infomode=1):
        self.infomode = infomode

    @classmethod
    def get_input_sign(cls):
        return OPIOSign({
            'input_lammps': Artifact(Path),
            'run_command': str
        })

    @classmethod
    def get_output_sign(cls):
        return OPIOSign({
            'output_lammps': Artifact(Path, sub_path=False)
        })

    @OP.exec_sign_check
    def execute(self, op_in: OPIO) -> OPIO:
        cwd = os.getcwd()
        os.chdir(op_in["input_lammps"])
        lmp = op_in["run_command"]
        cmd = "for ii in task.*; do cd $ii; " + lmp + "; cd ..; done"
        subprocess.call(cmd, shell=True)
        os.chdir(cwd)
        op_out = OPIO({
            "output_lammps": op_in["input_lammps"]
        })
        return op_out
"""

