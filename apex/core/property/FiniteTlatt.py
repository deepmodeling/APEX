"""Lattice parameter vs temperature (LAMMPS npt + averaging). Only LAMMPS supported."""

import json
import logging
import os
import re
from typing import Dict, List, Tuple

from monty.serialization import dumpfn
from pymatgen.core.structure import Structure

from apex.core.calculator.lib import vasp_utils
from apex.core.property.Property import Property
from apex.core.refine import make_refine
from apex.core.reproduce import make_repro, post_repro
from dflow.python import upload_packages

upload_packages.append(__file__)

DEFAULT_SUPERCELL = [2, 2, 2]
DEFAULT_CAL_SETTING: Dict[str, int | List[int]] = {
    "temperature": [200, 400, 600, 800],
    "equi_step": 80000,
    "N_every": 100,
    "N_repeat": 10,
    "N_freq": 2000,
    "ave_step": 40000,
    "timestep": 0.001,
    "tdamp": 0.1,
    "pdamp": 1.0}


class FiniteTlatt(Property):
    """
    Generate LAMMPS tasks to measure lattice parameters at finite temperatures
    using NPT runs plus time-averaging.
    """

    def __init__(self, parameter: Dict, inter_param: Dict | None = None):
        parameter["reproduce"] = parameter.get("reproduce", False)
        self.reprod = parameter["reproduce"]

        # Enforce LAMMPS-only workflow
        if inter_param is not None and inter_param.get("type") in ["vasp", "abacus"]:
            raise TypeError("FiniteTlatt supports only LAMMPS calculations.")

        parameter.setdefault("cal_setting", {})
        for key, val in DEFAULT_CAL_SETTING.items():
            parameter["cal_setting"].setdefault(key, val)

        if not self.reprod and not (
            "init_from_suffix" in parameter and "output_suffix" in parameter
        ):
            parameter["supercell_size"] = parameter.get("supercell_size", DEFAULT_SUPERCELL)
            self.supercell_size = parameter["supercell_size"]
        else:
            parameter["init_from_suffix"] = parameter.get("init_from_suffix", "00")
            self.init_from_suffix = parameter["init_from_suffix"]
            self.supercell_size = parameter.get("supercell_size", DEFAULT_SUPERCELL)

        parameter["cal_type"] = "npt+ave/time"
        self.cal_setting = parameter["cal_setting"]
        self.parameter = parameter
        # only supports LAMMPS now
        self.inter_param = inter_param or {"type": "lammps"}

    def make_confs(self, path_to_work: str, path_to_equi: str, refine: bool = False):
        path_to_work = os.path.abspath(path_to_work)
        os.makedirs(path_to_work, exist_ok=True)
        path_to_equi = os.path.abspath(path_to_equi)

        cwd = os.getcwd()
        if self.reprod:
            task_list = self._make_repro(path_to_work)
        elif refine:
            task_list = self._make_refine(path_to_work)
        else:
            task_list = self._make_fresh_tasks(path_to_work, path_to_equi)
        os.chdir(cwd)
        return task_list

    def post_process(self, task_list):
        pass

    def task_type(self):
        return self.parameter["type"]

    def task_param(self):
        return self.parameter

    def _compute_lower(self, output_file, all_tasks, all_res):
        output_file = os.path.abspath(output_file)
        res_data: Dict[str, List[float]] = {}
        ptr_data = os.path.dirname(output_file) + "\n"

        if self.reprod:
            if "init_data_path" not in self.parameter:
                raise RuntimeError("please provide the initial data path to reproduce")
            init_data_path = os.path.abspath(self.parameter["init_data_path"])
            res_data, ptr_data = post_repro(
                init_data_path,
                self.parameter["init_from_suffix"],
                all_tasks,
                ptr_data,
                self.parameter.get("reprod_last_frame", True),
            )
        else:
            ptr_data += " Temperature(K)  a(A)  b(A)  c(A)\n"
            for idx, task_dir in enumerate(all_tasks):
                temp = self.cal_setting["temperature"][idx]
                a, b, c = self._average_box(task_dir, self.supercell_size)
                ptr_data += f"{temp:>10}:  {a:7.6f}  {b:7.6f}  {c:7.6f}\n"
                res_data[str(temp)] = [a, b, c, temp]

        with open(output_file, "w") as fp:
            json.dump(res_data, fp, indent=4)

        return res_data, ptr_data

    # ---- helpers -----------------------------------------------------
    def _make_repro(self, path_to_work: str) -> List[str]:
        if "init_data_path" not in self.parameter:
            raise RuntimeError("please provide the initial data path to reproduce")
        init_data_path = os.path.abspath(self.parameter["init_data_path"])
        return make_repro(
            self.inter_param,
            init_data_path,
            self.init_from_suffix,
            path_to_work,
            self.parameter.get("reprod_last_frame", True),
        )

    def _make_refine(self, path_to_work: str) -> List[str]:
        logging.info("FiniteTlatt refine starts")
        task_list = make_refine(
            self.parameter["init_from_suffix"],
            self.parameter["output_suffix"],
            path_to_work,
        )
        init_from_path = re.sub(
            self.parameter["output_suffix"][::-1],
            self.parameter["init_from_suffix"][::-1],
            path_to_work[::-1],
            count=1,
        )[::-1]
        for task_name in map(os.path.basename, task_list):
            init_task = os.path.join(init_from_path, task_name)
            out_task = os.path.join(path_to_work, task_name)
            self._symlink_variable(init_task, out_task)
        return task_list

    def _make_fresh_tasks(self, path_to_work: str, path_to_equi: str) -> List[str]:
        if self.inter_param["type"] in ["vasp", "abacus"]:
            raise TypeError("FiniteTlatt only supports LAMMPS calculation")

        equi_contcar = os.path.join(path_to_equi, "CONTCAR")
        if not os.path.exists(equi_contcar):
            raise RuntimeError("please do relaxation first")

        ptypes = vasp_utils.get_poscar_types(equi_contcar)
        structure = Structure.from_file(equi_contcar)

        task_list: List[str] = []
        for idx, temp in enumerate(self.cal_setting["temperature"]):
            task_dir = os.path.join(path_to_work, f"task.{idx:06d}")
            os.makedirs(task_dir, exist_ok=True)
            self._write_task(task_dir, structure, ptypes, temp)
            task_list.append(task_dir)
        return task_list

    def _symlink_variable(self, init_task: str, out_task: str):
        os.makedirs(out_task, exist_ok=True)
        dst = os.path.join(out_task, "variable_FiniteTlatt.json")
        if os.path.exists(dst):
            os.remove(dst)
        os.symlink(
            os.path.relpath(os.path.join(init_task, "variable_FiniteTlatt.json"), out_task),
            dst,
        )

    def _write_task(self, task_dir: str, structure: Structure, ptypes, temp: float):
        os.chdir(task_dir)
        for fname in ["INCAR", "POTCAR", "POSCAR", "conf.lmp", "in.lammps", "STRU"]:
            if os.path.exists(fname):
                os.remove(fname)
        structure.to("POSCAR.tmp", "POSCAR")
        vasp_utils.regulate_poscar("POSCAR.tmp", "POSCAR")
        vasp_utils.sort_poscar("POSCAR", "POSCAR", ptypes)

        FiniteTlatt_task = {"temperature": temp, "supercell_size": self.supercell_size}
        dumpfn(FiniteTlatt_task, "FiniteTlatt.json", indent=4)

        with open("variable_FiniteTlatt.in", "w") as fp:
            fp.write(self._variable(temp))

    def _average_box(self, task_dir: str, supercell_size: List[int]) -> Tuple[float, float, float]:
        a_sum = b_sum = c_sum = count = 0
        box_file = os.path.join(task_dir, "average_box.txt")
        with open(box_file, "r") as fh:
            for line in fh:
                if line.startswith("#") or not line.strip():
                    continue
                parts = line.split()
                if len(parts) == 4:
                    _, v_lx, v_ly, v_lz = parts
                    a_sum += float(v_lx)
                    b_sum += float(v_ly)
                    c_sum += float(v_lz)
                    count += 1
        if count == 0:
            return 0.0, 0.0, 0.0
        a = a_sum / count / supercell_size[0]
        b = b_sum / count / supercell_size[1]
        c = c_sum / count / supercell_size[2]
        return a, b, c

    def _variable(self, temp: float) -> str:
        return (
            " # variable_FiniteTlatt.in \n"
            f"variable temperature equal {temp:.2f}\n"
            f"variable nx equal {self.supercell_size[0]}\n"
            f"variable ny equal {self.supercell_size[1]}\n"
            f"variable nz equal {self.supercell_size[2]}\n"
            f"variable equi_step equal {self.cal_setting['equi_step']}\n"
            f"variable N_every equal {self.cal_setting['N_every']}\n"
            f"variable N_repeat equal {self.cal_setting['N_repeat']}\n"
            f"variable N_freq equal {self.cal_setting['N_freq']}\n"
            f"variable ave_step equal {self.cal_setting['ave_step']}\n"
            f"variable timestep equal {self.cal_setting['timestep']}\n"
            f"variable tdamp equal {self.cal_setting['tdamp']}\n"
            f"variable pdamp equal {self.cal_setting['pdamp']}\n"
        )
