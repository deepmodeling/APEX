"""Cohesive energy property generation and post-processing."""

import glob
import json
import logging
import os
from contextlib import contextmanager
from typing import Dict, List, Optional, Tuple

import numpy as np
from monty.serialization import dumpfn, loadfn

from apex.core.calculator.lib import abacus_scf
from apex.core.calculator.lib import abacus_utils
from apex.core.calculator.lib import vasp_utils
from apex.core.property.Property import Property
from apex.core.reproduce import make_repro, post_repro
from dflow.python import upload_packages

upload_packages.append(__file__)


@contextmanager
def _chdir(path: str):
    """Temporarily change working directory."""
    prev = os.getcwd()
    os.makedirs(path, exist_ok=True)
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(prev)


class Cohesive(Property):
    """Generate scaled-lattice tasks and compute cohesive energy."""

    def __init__(self, parameter: Dict, inter_param: Optional[Dict] = None):
        # Normalize commonly used flags/sections.
        parameter["reproduce"] = parameter.get("reproduce", False)
        self.reprod = parameter["reproduce"]

        if not self.reprod:
            # Lattice scan settings (may be absolute or relative to a0).
            if not ("init_from_suffix" in parameter and "output_suffix" in parameter):
                self.latt_start = parameter["latt_start"]
                self.latt_end = parameter["latt_end"]
                self.latt_step = parameter["latt_step"]
                parameter["latt_abs"] = parameter.get("latt_abs", False)
                self.latt_abs = parameter["latt_abs"]

            parameter["cal_type"] = parameter.get("cal_type", "static")
            self.cal_type = parameter["cal_type"]
            parameter.setdefault("cal_setting", {})
            self.cal_setting = parameter["cal_setting"]
        else:
            # Reproduction mode always uses static calcs.
            parameter["cal_type"] = "static"
            self.cal_type = parameter["cal_type"]
            parameter.setdefault("cal_setting", {})
            self.cal_setting = parameter["cal_setting"]
            parameter["init_from_suffix"] = parameter.get("init_from_suffix", "00")
            self.init_from_suffix = parameter["init_from_suffix"]

        self.parameter = parameter
        self.inter_param = inter_param if inter_param is not None else {"type": "vasp"}

    def make_confs(self, path_to_work: str, path_to_equi: str, refine: bool = False) -> List[str]:
        """Create task directories with scaled structures.

        When reproduce-mode is on, delegate to the reproduction utilities.
        Otherwise, generate a lattice scan by scaling the relaxed structure.

        Returns a list of created task directories.
        """
        path_to_work = os.path.abspath(path_to_work)
        if os.path.exists(path_to_work):
            logging.debug("%s already exists", path_to_work)
        else:
            os.makedirs(path_to_work, exist_ok=True)
        path_to_equi = os.path.abspath(path_to_equi)

        # Optionally override equilibrium path from a provided pool of starts.
        if "start_confs_path" in self.parameter and os.path.exists(self.parameter["start_confs_path"]):
            init_path_list = glob.glob(os.path.join(self.parameter["start_confs_path"], "*"))
            struct_init_name_list = [os.path.basename(ii) for ii in init_path_list]
            struct_output_name = os.path.basename(os.path.dirname(path_to_work))
            if struct_output_name not in struct_init_name_list:
                raise RuntimeError(f"{struct_output_name} not found in start_confs_path.")
            path_to_equi = os.path.abspath(
                os.path.join(self.parameter["start_confs_path"], struct_output_name, "relaxation", "relax_task")
            )

        task_list: List[str] = []

        if self.reprod:
            print("cohesive energy reproduce starts")
            if "init_data_path" not in self.parameter:
                raise RuntimeError("please provide the initial data path to reproduce")
            init_data_path = os.path.abspath(self.parameter["init_data_path"])
            task_list = make_repro(
                self.inter_param,
                init_data_path,
                self.init_from_suffix,
                path_to_work,
                self.parameter.get("reprod_last_frame", True),
            )
            return task_list

        # Normal generation mode
        print(
            "gen cohesive energy from "
            + str(self.latt_start)
            + " to "
            + str(self.latt_end)
            + " by every "
            + str(self.latt_step)
        )
        if self.latt_abs:
            print("treat latt_start and latt_end as absolute lattice constant")
        else:
            print("treat latt_start and latt_end as relative lattice constant")

        # Determine equilibrium structure file and a0 (|a| of the first lattice vector).
        if self.inter_param["type"] == "abacus":
            equi_stru = os.path.join(path_to_equi, abacus_utils.final_stru(path_to_equi))
        else:
            equi_stru = os.path.join(path_to_equi, "CONTCAR")

        if not os.path.isfile(equi_stru):
            raise RuntimeError(f"Can not find {equi_stru}, please do relaxation first")

        if self.inter_param["type"] == "abacus":
            stru_data = abacus_scf.get_abacus_STRU(equi_stru)
            a0 = np.linalg.norm(stru_data["cells"], axis=1)[0]
        else:
            with open(equi_stru, "r") as f:
                lines = f.readlines()
            scale = float(lines[1].strip())
            cell = np.array([[float(x) for x in line.split()] for line in lines[2:5]])
            cell *= scale
            a0 = np.linalg.norm(cell, axis=1)[0]

        self.parameter["scale2equi"] = []

        # Build tasks along the scan.
        task_num = 0
        while self.latt_start + self.latt_step * task_num <= self.latt_end + 1e-12:
            latt = self.latt_start + task_num * self.latt_step
            output_task = os.path.join(path_to_work, f"task.{task_num:06d}")
            task_list.append(output_task)

            # Decide file names and scaling function based on calculator type.
            if self.inter_param["type"] == "abacus":
                poscar_name = "STRU"
                poscar_orig = "STRU.orig"
                scale_func = abacus_utils.stru_scale
            else:
                poscar_name = "POSCAR"
                poscar_orig = "POSCAR.orig"
                scale_func = vasp_utils.poscar_scale

            with _chdir(output_task):
                # Clean any leftovers from previous runs.
                for fname in ("INCAR", "POTCAR", poscar_orig, poscar_name, "conf.lmp", "in.lammps"):
                    if os.path.exists(fname):
                        os.remove(fname)

                # Symlink to the equilibrium structure as the "orig".
                rel_src = os.path.relpath(equi_stru, output_task)
                os.symlink(rel_src, poscar_orig)

                # Determine scale and lattice value to record.
                if self.latt_abs:
                    scale_val = latt / a0
                    lattice_val = latt
                else:
                    scale_val = latt
                    lattice_val = latt * a0

                dumpfn({"lattice": lattice_val, "scale": scale_val}, "cohesive.json", indent=4)
                self.parameter["scale2equi"].append(scale_val)

                # Create scaled structure.
                scale_func(poscar_orig, poscar_name, scale_val)

            task_num += 1

        return task_list

    def post_process(self, task_list: List[str]) -> None:
        """Placeholder for future post-processing hooks."""
        pass

    def task_type(self) -> str:
        return self.parameter["type"]

    def task_param(self) -> Dict:
        return self.parameter

    def _compute_lower(
        self, output_file: str, all_tasks: List[str], all_res: List[str]
    ) -> Tuple[Dict, str]:
        """Compute cohesive energies from finished tasks and write a JSON result."""
        output_file = os.path.abspath(output_file)
        res_data: Dict = {}
        ptr_data = "conf_dir: " + os.path.dirname(output_file) + "\n"

        if not self.reprod:
            if getattr(self, "latt_abs", False):
                ptr_data += " Latt(A)  Etot(eV)  Ecoh(eV/atom)\n"
            else:
                ptr_data += " ScaledLatt  Etot(eV)  Ecoh(eV/atom)\n"

            # Use the last result as the single-atom reference.
            last_res = loadfn(all_res[-1])
            single_atom_energy = last_res["energies"][-1] / sum(last_res["atom_numbs"])

            for ii, task_path in enumerate(all_tasks):
                conf = loadfn(os.path.join(task_path, "cohesive.json"))
                latt = conf["lattice"]
                scale = conf["scale"]
                task_result = loadfn(all_res[ii])

                total_energy = task_result["energies"][-1]
                total_atoms = sum(task_result["atom_numbs"])

                e_per_atom = total_energy / total_atoms
                cohesive_energy = e_per_atom - single_atom_energy

                if getattr(self, "latt_abs", False):
                    ptr_data += "%7.3f  %8.4f  %8.4f\n" % (latt, e_per_atom, cohesive_energy)
                    res_data[latt] = {"total_energy": e_per_atom, "cohesive_energy": cohesive_energy}
                else:
                    ptr_data += "%7.3f  %8.4f  %8.4f\n" % (scale, e_per_atom, cohesive_energy)
                    res_data[scale] = {"total_energy": e_per_atom, "cohesive_energy": cohesive_energy}
        else:
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

        with open(output_file, "w") as fp:
            json.dump(res_data, fp, indent=4)

        return res_data, ptr_data
