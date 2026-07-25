"""Lattice parameter versus temperature from NpT molecular dynamics."""

import json
import logging
import os
import re
from shutil import copyfile
from typing import Dict, List, Tuple

import numpy as np
from monty.serialization import dumpfn
from pymatgen.core.lattice import Lattice
from pymatgen.core.structure import Structure

from apex.core.property.Property import Property
from apex.core.refine import make_refine
from apex.core.reproduce import make_repro, post_repro
from dflow.python import upload_packages

upload_packages.append(__file__)

DEFAULT_SUPERCELL = [2, 2, 2]
DEFAULT_CAL_SETTING: Dict[str, float | int | List[int]] = {
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
    Generate LAMMPS or VASP tasks to measure finite-temperature lattice
    parameters using NpT runs plus production-trajectory statistics.
    """

    def __init__(self, parameter: Dict, inter_param: Dict | None = None):
        self.inter_param = inter_param or {"type": "lammps"}
        if self.inter_param.get("type") == "abacus":
            raise NotImplementedError(
                "finite_t_latt does not support the ABACUS backend; "
                "use LAMMPS or VASP"
            )
        parameter["reproduce"] = parameter.get("reproduce", False)
        self.reprod = parameter["reproduce"]

        parameter.setdefault("cal_setting", {})
        default_cal_setting = dict(DEFAULT_CAL_SETTING)
        if self.inter_param["type"] == "vasp":
            default_cal_setting.update(
                {
                    "temperature": [300, 500, 700, 900, 1100, 1300, 1500],
                    "equi_step": 5000,
                    "ave_step": 10000,
                    "timestep_fs": 1.0,
                }
            )
        for key, val in default_cal_setting.items():
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

        # MD calculators dispatch on the property type.  "static" remains a
        # valid fallback for calculators that inspect cal_type first and,
        # unlike "relaxation", does not require relaxation-only settings.
        parameter["cal_type"] = "static"
        self.cal_setting = parameter["cal_setting"]
        self.parameter = parameter

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
            statistics = {}
            for idx, task_dir in enumerate(all_tasks):
                temp = self.cal_setting["temperature"][idx]
                stats = self._cell_statistics(task_dir, self.supercell_size)
                a, b, c = stats["lengths"]["mean"]
                ptr_data += f"{temp:>10}:  {a:7.6f}  {b:7.6f}  {c:7.6f}\n"
                res_data[str(temp)] = [a, b, c, temp]
                statistics[str(temp)] = stats
            # Preserve the historic temperature -> [a, b, c, T] mapping.
            # Rich tensor/statistical data lives under a reserved companion key.
            res_data["_metadata"] = {
                "schema": "apex.finite_t_latt.statistics/v1",
                "temperatures": statistics,
            }

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
        equi_structure = os.path.join(path_to_equi, "CONTCAR")
        if not os.path.exists(equi_structure):
            raise RuntimeError("please do relaxation first")

        task_list: List[str] = []
        for idx, temp in enumerate(self.cal_setting["temperature"]):
            task_dir = os.path.join(path_to_work, f"task.{idx:06d}")
            os.makedirs(task_dir, exist_ok=True)
            self._write_task(task_dir, equi_structure, temp)
            task_list.append(task_dir)
        return task_list

    def _symlink_variable(self, init_task: str, out_task: str):
        os.makedirs(out_task, exist_ok=True)
        if self.inter_param["type"] == "vasp":
            return
        src = os.path.join(init_task, "variable_FiniteTlatt.in")
        dst = os.path.join(out_task, "variable_FiniteTlatt.in")
        if os.path.lexists(dst):
            os.remove(dst)
        os.symlink(os.path.relpath(src, out_task), dst)

    def _write_task(self, task_dir: str, equi_structure: str, temp: float):
        os.chdir(task_dir)
        for fname in ["INCAR", "POTCAR", "POSCAR", "conf.lmp", "in.lammps", "STRU"]:
            if os.path.exists(fname):
                os.remove(fname)
        if self.inter_param["type"] == "vasp":
            structure = Structure.from_file(equi_structure)
            structure.make_supercell(self.supercell_size)
            structure.to(filename="POSCAR")
        else:
            copyfile(equi_structure, "POSCAR")

        FiniteTlatt_task = {"temperature": temp, "supercell_size": self.supercell_size}
        dumpfn(FiniteTlatt_task, "FiniteTlatt.json", indent=4)

        if self.inter_param["type"] != "vasp":
            with open("variable_FiniteTlatt.in", "w") as fp:
                fp.write(self._variable(temp))

    def _average_box(self, task_dir: str, supercell_size: List[int]) -> Tuple[float, float, float]:
        stats = self._cell_statistics(task_dir, supercell_size)
        return tuple(stats["lengths"]["mean"])

    def _cell_statistics(self, task_dir: str, supercell_size: List[int]) -> Dict:
        if self.inter_param["type"] == "vasp":
            cells = self._vasp_cells(os.path.join(task_dir, "OUTCAR"))
        else:
            cells = []
            box_file = os.path.join(task_dir, "average_box.txt")
            with open(box_file, "r") as fh:
                for line in fh:
                    if line.startswith("#") or not line.strip():
                        continue
                    parts = line.split()
                    if len(parts) == 4:
                        cells.append(np.diag([float(value) for value in parts[1:]]))
        return self._summarize_cells(cells, supercell_size)

    @staticmethod
    def _series_statistics(values):
        array = np.asarray(values, dtype=float)
        count = int(array.shape[0])
        if count == 0:
            shape = list(array.shape[1:])
            zero = np.zeros(shape, dtype=float).tolist()
            return {
                "mean": zero,
                "std": zero,
                "block_standard_error": zero,
                "sample_count": 0,
                "block_count": 0,
            }
        mean = np.mean(array, axis=0)
        std = np.std(array, axis=0, ddof=1) if count > 1 else np.zeros_like(mean)
        block_size = max(1, int(np.sqrt(count)))
        block_count = count // block_size
        if block_count > 1:
            trimmed = array[: block_count * block_size]
            block_means = trimmed.reshape(
                (block_count, block_size) + array.shape[1:]
            ).mean(axis=1)
            block_error = np.std(block_means, axis=0, ddof=1) / np.sqrt(block_count)
        else:
            block_error = np.zeros_like(mean)
        return {
            "mean": np.asarray(mean).tolist(),
            "std": np.asarray(std).tolist(),
            "block_standard_error": np.asarray(block_error).tolist(),
            "sample_count": count,
            "block_count": block_count,
        }

    @classmethod
    def _summarize_cells(cls, cells, supercell_size):
        if not cells:
            empty = cls._series_statistics(np.empty((0, 3)))
            return {
                "cell": cls._series_statistics(np.empty((0, 3, 3))),
                "lengths": empty,
                "angles": empty,
                "volume": cls._series_statistics(np.empty((0,))),
                "sample_count": 0,
            }
        scale = np.asarray(supercell_size, dtype=float)[:, None]
        normalized = np.asarray(cells, dtype=float) / scale
        lengths = np.linalg.norm(normalized, axis=2)
        angles = np.asarray(
            [Lattice(cell).angles for cell in normalized], dtype=float
        )
        volumes = np.abs(np.linalg.det(normalized))
        return {
            "cell": cls._series_statistics(normalized),
            "lengths": cls._series_statistics(lengths),
            "angles": cls._series_statistics(angles),
            "volume": cls._series_statistics(volumes),
            "sample_count": int(len(normalized)),
        }

    @staticmethod
    def _mean_cell_lengths(cells, supercell_size):
        if not cells:
            return 0.0, 0.0, 0.0
        lengths = np.asarray(
            [[np.linalg.norm(vector) for vector in cell] for cell in cells],
            dtype=float,
        )
        return tuple(
            float(np.mean(lengths[:, axis]) / supercell_size[axis])
            for axis in range(3)
        )

    @staticmethod
    def _vasp_cells(outcar):
        cells = []
        with open(outcar, encoding="utf-8", errors="ignore") as fp:
            lines = fp.readlines()
        for idx, line in enumerate(lines):
            if "direct lattice vectors" not in line.lower():
                continue
            try:
                cell = [
                    [float(value) for value in lines[idx + offset].split()[:3]]
                    for offset in (1, 2, 3)
                ]
            except (IndexError, ValueError):
                continue
            cells.append(cell)
        return cells

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
