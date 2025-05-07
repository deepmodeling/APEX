import glob
import json
import logging
import os
import re
import numpy as np
from monty.serialization import dumpfn, loadfn

from apex.core.calculator.lib import abacus_utils
from apex.core.calculator.lib import vasp_utils
from apex.core.calculator.lib import abacus_scf
from apex.core.property.Property import Property
from apex.core.reproduce import make_repro, post_repro
from dflow.python import upload_packages
upload_packages.append(__file__)

class Cohesive(Property):
    def __init__(self, parameter, inter_param=None):
        parameter["reproduce"] = parameter.get("reproduce", False)
        self.reprod = parameter["reproduce"]
        if not self.reprod:
            if not ("init_from_suffix" in parameter and "output_suffix" in parameter):
                self.latt_start = parameter["latt_start"]
                self.latt_end = parameter["latt_end"]
                self.latt_step = parameter["latt_step"]
                parameter["latt_abs"] = parameter.get("latt_abs", False)
                self.latt_abs = parameter["latt_abs"]
            parameter["cal_type"] = parameter.get("cal_type", "static")
            self.cal_type = parameter["cal_type"]
            default_cal_setting = {
                "relax_pos": False,
                "relax_shape": False,
                "relax_vol": False
            }
            parameter.setdefault("cal_setting", {})
            #parameter["cal_setting"].update(
            #    {k: v for k, v in default_cal_setting.items() if k not in parameter["cal_setting"]})
            self.cal_setting = parameter["cal_setting"]
        else:
            parameter["cal_type"] = "static"
            self.cal_type = parameter["cal_type"]
            default_cal_setting = {
                "relax_pos": False,
                "relax_shape": False,
                "relax_vol": False
            }
            parameter.setdefault("cal_setting", {})
            self.cal_setting = parameter["cal_setting"]
            parameter["init_from_suffix"] = parameter.get("init_from_suffix", "00")
            self.init_from_suffix = parameter["init_from_suffix"]
        self.parameter = parameter
        self.inter_param = inter_param if inter_param != None else {"type": "vasp"}

    def make_confs(self, path_to_work, path_to_equi, refine=False):
        path_to_work = os.path.abspath(path_to_work)
        if os.path.exists(path_to_work):
            logging.debug("%s already exists" % path_to_work)
        else:
            os.makedirs(path_to_work)
        path_to_equi = os.path.abspath(path_to_equi)

        if "start_confs_path" in self.parameter and os.path.exists(
            self.parameter["start_confs_path"]
        ):
            init_path_list = glob.glob(
                os.path.join(self.parameter["start_confs_path"], "*")
            )
            struct_init_name_list = [os.path.basename(ii) for ii in init_path_list]
            struct_output_name = os.path.basename(os.path.dirname(path_to_work))
            assert struct_output_name in struct_init_name_list
            path_to_equi = os.path.abspath(
                os.path.join(
                    self.parameter["start_confs_path"],
                    struct_output_name,
                    "relaxation",
                    "relax_task"
                )
            )

        cwd = os.getcwd()
        task_list = []
        
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
                self.parameter.get("reprod_last_frame", True)
            )

        else:
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

            if self.inter_param["type"] == "abacus":
                equi_contcar = os.path.join(
                    path_to_equi, abacus_utils.final_stru(path_to_equi)
                )
                stru_data = abacus_scf.get_abacus_STRU(equi_contcar)
            else:
                equi_contcar = os.path.join(path_to_equi, "CONTCAR")

            if not os.path.isfile(equi_contcar):
                raise RuntimeError(
                    "Can not find %s, please do relaxation first" % equi_contcar
                )

            if self.inter_param["type"] == "abacus":
                stru_data = abacus_scf.get_abacus_STRU(equi_contcar)
                latt_a0_to_poscar = np.linalg.norm(stru_data["cells"], axis=1)[0]
            else:
                with open(equi_contcar, 'r') as f:
                    lines = f.readlines()
                scale = float(lines[1].strip())
                cell = np.array([[float(x) for x in line.split()] for line in lines[2:5]])
                cell *= scale
                latt_a0_to_poscar = np.linalg.norm(cell, axis=1)[0]
                
            self.parameter["scale2equi"] = []

            task_num = 0
            while self.latt_start + self.latt_step * task_num <= self.latt_end:
                latt = self.latt_start + task_num * self.latt_step
                output_task = os.path.join(path_to_work, "task.%06d" % task_num)
                os.makedirs(output_task, exist_ok=True)
                os.chdir(output_task)
                if self.inter_param["type"] == "abacus":
                    POSCAR = "STRU"
                    POSCAR_orig = "STRU.orig"
                    scale_func = abacus_utils.stru_scale
                else:
                    POSCAR = "POSCAR"
                    POSCAR_orig = "POSCAR.orig"
                    scale_func = vasp_utils.poscar_scale

                for ii in [
                    "INCAR",
                    "POTCAR",
                    POSCAR_orig,
                    POSCAR,
                    "conf.lmp",
                    "in.lammps",
                ]:
                    if os.path.exists(ii):
                        os.remove(ii)
                task_list.append(output_task)
                os.symlink(os.path.relpath(equi_contcar), POSCAR_orig)

                if self.latt_abs:
                    scale = latt / latt_a0_to_poscar
                    cohesive_params = {"lattice": latt, "scale": scale}
                else:
                    scale = latt
                    cohesive_params = {"lattice": latt * latt_a0_to_poscar, "scale": scale}
                dumpfn(cohesive_params, "cohesive.json", indent=4)
                self.parameter["scale2equi"].append(scale)
                scale_func(POSCAR_orig, POSCAR, scale)
                task_num += 1
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
        res_data = {}
        ptr_data = "conf_dir: " + os.path.dirname(output_file) + "\n"

        if not self.reprod:
            if self.latt_abs:
                ptr_data += " Latt(A)  Etot(eV)  Ecoh(eV/atom)\n"
            else:
                ptr_data += " ScaledLatt  Etot(eV)  Ecoh(eV/atom)\n"
            
            single_atom_energy = loadfn(all_res[-1])["energies"][-1]/sum(loadfn(all_res[-1])["atom_numbs"])
            
            for ii in range(len(all_tasks)):
                task_path = all_tasks[ii]
                latt = loadfn(os.path.join(task_path, "cohesive.json"))["lattice"]
                scale = loadfn(os.path.join(task_path, "cohesive.json"))["scale"]
                task_result = loadfn(all_res[ii])
                
                total_energy = task_result["energies"][-1]
                atom_counts = task_result["atom_numbs"]
                
                total_atoms = sum(atom_counts)

                cohesive_energy = total_energy / total_atoms - single_atom_energy
                                
                if self.latt_abs:
                    ptr_data += "%7.3f  %8.4f  %8.4f\n" % (
                        latt,
                        total_energy / total_atoms,
                        cohesive_energy
                    )
                    res_data[latt] = {
                        "total_energy": total_energy / total_atoms,
                        "cohesive_energy": cohesive_energy
                    }
                else:
                    ptr_data += "%7.3f  %8.4f  %8.4f\n" % (
                        scale,
                        total_energy / total_atoms,
                        cohesive_energy
                    )
                    res_data[scale] = {
                        "total_energy": total_energy / total_atoms,
                        "cohesive_energy": cohesive_energy
                    }
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