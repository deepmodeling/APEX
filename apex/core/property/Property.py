import glob
import json
import os
from abc import ABC, abstractmethod

from monty.serialization import dumpfn, loadfn

from apex.core.calculator.calculator import make_calculator
from dflow.python import upload_packages
upload_packages.append(__file__)


def is_failed_task_result(result) -> bool:
    """Return True when a task result cannot be used for property aggregation.

    Accepts plain dicts and mapping-like objects such as dpdata LabeledSystem
    (fixture ``result_task.json`` files often load as the latter).
    """
    if result is None:
        return True
    if isinstance(result, dict) and result.get("failed") is True:
        return True
    try:
        energies = result["energies"]
    except Exception:
        return True
    return energies is None


def _task_marked_failed(task_dir: str) -> bool:
    status_path = os.path.join(task_dir, "apex_task_status.json")
    if not os.path.isfile(status_path):
        return False
    try:
        status = loadfn(status_path)
    except Exception:
        return True
    if not isinstance(status, dict):
        return True
    return status.get("state") != "succeeded" or status.get("exit_code", 0) != 0


class Property(ABC):
    @abstractmethod
    def __init__(self, parameter):
        """
        Constructor

        Parameters
        ----------
        parameter : dict
            A dict that defines the apex.
        """
        pass

    @abstractmethod
    def make_confs(self, path_to_work, path_to_equi, refine=False):
        """
        Make configurations needed to compute the apex.
        The tasks directory will be named as path_to_work/task.xxxxxx
        IMPORTANT: handel the case when the directory exists.

        Parameters
        ----------
        path_to_work : str
            The path where the tasks for the apex are located
        path_to_equi : str
            -refine == False: The path to the directory that equilibrated the configuration.
            -refine == True: The path to the directory that has apex confs.
        refine : str
            To refine existing apex confs or generate apex confs from a equilibrated conf

        Returns
        -------
        task_list: list of str
            The list of task directories.
        """
        pass

    @abstractmethod
    def post_process(self, task_list):
        """
        post_process the KPOINTS file in elastic.
        """
        pass

    @property
    @abstractmethod
    def task_type(self):
        """
        Return the type of each computational task, for example, 'relaxation', 'static'....
        """
        pass

    @property
    @abstractmethod
    def task_param(self):
        """
        Return the parameter of each computational task, for example, {'ediffg': 1e-4}
        """
        pass

    def compute(self, output_file, print_file, path_to_work):
        """
        Postprocess the finished tasks to compute the apex.
        Output the result to a json database

        Parameters
        ----------
        output_file:
            The file to output the apex in json format
        print_file:
            The file to output the apex in txt format
        path_to_work:
            The working directory where the computational tasks locate.
        """
        path_to_work = os.path.abspath(path_to_work)
        task_dirs = glob.glob(os.path.join(path_to_work, "task.[0-9]*[0-9]"))
        task_dirs.sort()
        all_res = []
        for ii in task_dirs:
            with open(os.path.join(ii, "inter.json")) as fp:
                idata = json.load(fp)
            poscar = os.path.join(ii, "POSCAR")
            task = make_calculator(idata, poscar)
            res = None if _task_marked_failed(ii) else task.compute(ii)
            if is_failed_task_result(res):
                res = {"failed": True}
            dumpfn(res, os.path.join(ii, "result_task.json"), indent=4)
            # all_res.append(res)
            all_res.append(os.path.join(ii, "result_task.json"))

        # cwd = os.getcwd()
        # os.chdir(path_to_work)
        res, ptr = self._compute_lower(output_file, task_dirs, all_res)
        #        with open(output_file, 'w') as fp:
        #            json.dump(fp, res, indent=4)
        with open(print_file, "w") as fp:
            fp.write(ptr)
        # os.chdir(cwd)

    @abstractmethod
    def _compute_lower(self, output_file, all_tasks, all_res) -> [dict, str]:
        """
        Compute the post.

        Parameters
        ----------
        output_file:
            The file to output the props
        all_tasks : list of str
            The list of directories to the tasks
        all_res : list of str
            The list of results
        Returns:
        -------
        res_data : dict
            The dict storing the result of the props
        ptr_data : str
            The result printed in string format
        """
        pass
