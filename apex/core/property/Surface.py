import glob
import json
import logging
import os
import re
import dpdata
import numpy as np
from monty.serialization import dumpfn, loadfn
from pymatgen.core.structure import Structure
from pymatgen.core.surface import generate_all_slabs

from apex.core.calculator.lib import abacus_utils
from apex.core.calculator.lib import vasp_utils
from apex.core.property.Property import Property
from apex.core.refine import make_refine
from apex.core.reproduce import make_repro, post_repro
from dflow.python import upload_packages
upload_packages.append(__file__)


class Surface(Property):
    def __init__(self, parameter, inter_param=None):
        parameter["reproduce"] = parameter.get("reproduce", False)
        self.reprod = parameter["reproduce"]
        if not self.reprod:
            if not ("init_from_suffix" in parameter and "output_suffix" in parameter):
                self.min_slab_size = parameter["min_slab_size"]
                self.min_vacuum_size = parameter["min_vacuum_size"]
                parameter["pert_xz"] = parameter.get("pert_xz", 0.01)
                self.pert_xz = parameter["pert_xz"]
                default_max_miller = 2
                parameter["max_miller"] = parameter.get(
                    "max_miller", default_max_miller
                )
                self.miller = parameter["max_miller"]
            parameter["cal_type"] = parameter.get("cal_type", "relaxation")
            default_cal_setting = {
                "relax_pos": True,
                "relax_shape": True,
                "relax_vol": False,
            }
        else:
            parameter["cal_type"] = "static"
            self.cal_type = parameter["cal_type"]
            default_cal_setting = {
                "relax_pos": False,
                "relax_shape": False,
                "relax_vol": False,
            }            
            parameter["init_from_suffix"] = parameter.get("init_from_suffix", "00")
            self.init_from_suffix = parameter["init_from_suffix"]
        self.cal_type = parameter["cal_type"]
        parameter["cal_setting"] = parameter.get("cal_setting", default_cal_setting)
        for key in default_cal_setting:
            parameter["cal_setting"].setdefault(key, default_cal_setting[key])
        self.cal_setting = parameter["cal_setting"]
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
            assert struct_output_name in struct_init_name_list, f"{struct_output_name} not in initial configuration names"
            path_to_equi = os.path.abspath(
                os.path.join(
                    self.parameter["start_confs_path"],
                    struct_output_name,
                    "relaxation",
                    "relax_task",
                )
            )

        task_list = []
        cwd = os.getcwd()

        if self.reprod:
            print("surface reproduce starts")
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

        else:
            if refine:
                print("surface refine starts")
                task_list = make_refine(
                    self.parameter["init_from_suffix"],
                    self.parameter["output_suffix"],
                    path_to_work,
                )
                # record miller
                init_from_path = re.sub(
                    self.parameter["output_suffix"][::-1],
                    self.parameter["init_from_suffix"][::-1],
                    path_to_work[::-1],
                    count=1,
                )[::-1]
                task_list_basename = list(map(os.path.basename, task_list))

                for ii in task_list_basename:
                    init_from_task = os.path.join(init_from_path, ii)
                    output_task = os.path.join(path_to_work, ii)
                    os.chdir(output_task)
                    if os.path.exists("miller.json"):
                        os.remove("miller.json")
                    os.symlink(
                        os.path.relpath(os.path.join(init_from_task, "miller.json")),
                        "miller.json",
                    )

            else:
                if self.inter_param["type"] == "abacus":
                    CONTCAR = abacus_utils.final_stru(path_to_equi)
                    POSCAR = "STRU"
                else:
                    CONTCAR = "CONTCAR"
                    POSCAR = "POSCAR"

                equi_contcar = os.path.join(path_to_equi, CONTCAR)
                if not os.path.exists(equi_contcar):
                    raise RuntimeError("please do relaxation first")

                if self.inter_param["type"] == "abacus":
                    stru = dpdata.System(equi_contcar, fmt="stru")
                    stru.to("contcar", "CONTCAR.tmp")
                    ptypes = vasp_utils.get_poscar_types("CONTCAR.tmp")
                    ss = Structure.from_file("CONTCAR.tmp")
                    os.remove("CONTCAR.tmp")
                else:
                    ptypes = vasp_utils.get_poscar_types(equi_contcar)
                    # gen structure
                    ss = Structure.from_file(equi_contcar)

                # gen slabs
                all_slabs = generate_all_slabs(
                    ss, self.miller, self.min_slab_size, self.min_vacuum_size
                )

                os.chdir(path_to_work)
                if os.path.exists(POSCAR):
                    os.remove(POSCAR)
                os.symlink(os.path.relpath(equi_contcar), POSCAR)
                for ii in range(len(all_slabs)):
                    output_task = os.path.join(path_to_work, "task.%06d" % ii)
                    os.makedirs(output_task, exist_ok=True)
                    os.chdir(output_task)
                    for jj in [
                        "INCAR",
                        "POTCAR",
                        "POSCAR",
                        "conf.lmp",
                        "in.lammps",
                        "STRU",
                    ]:
                        if os.path.exists(jj):
                            os.remove(jj)
                    task_list.append(output_task)

                    logging.info(f"{ii} generate {output_task} {len(all_slabs[ii].sites)} atoms")

                    # make confs
                    all_slabs[ii].to("POSCAR.tmp", "POSCAR")
                    vasp_utils.regulate_poscar("POSCAR.tmp", "POSCAR")
                    vasp_utils.sort_poscar("POSCAR", "POSCAR", ptypes)
                    vasp_utils.perturb_xz("POSCAR", "POSCAR", self.pert_xz)
                    if self.inter_param["type"] == "abacus":
                        abacus_utils.poscar2stru("POSCAR", self.inter_param, "STRU")
                        #os.remove("POSCAR")
                    # record miller
                    dumpfn(all_slabs[ii].miller_index, "miller.json")
        
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
        ptr_data = os.path.dirname(output_file) + "\n"

        if not self.reprod:
            ptr_data += "Miller_Indices: \tSurf_E(J/m^2) EpA(eV) equi_EpA(eV)\n"

            equi_path = os.path.abspath(
                os.path.join(
                    os.path.dirname(output_file), "../relaxation/relax_task"
                )
            )
            equi_result = loadfn(os.path.join(equi_path, "result.json"))
            equi_epa = equi_result["energies"][-1] / np.sum(
                equi_result["atom_numbs"]
            )

            for ii in all_tasks:
                task_result = loadfn(os.path.join(ii, "result_task.json"))
                natoms = np.sum(task_result["atom_numbs"])
                epa = task_result["energies"][-1] / natoms
                AA = np.linalg.norm(
                    np.cross(task_result["cells"][0][0], task_result["cells"][0][1])
                )

                structure_dir = os.path.basename(ii)
                Cf = 1.60217657e-16 / (1e-20 * 2) * 0.001
                evac = (task_result["energies"][-1] - equi_epa * natoms) / AA * Cf
                miller_index = loadfn(os.path.join(ii, "miller.json"))
                
                ptr_data += "%-25s     %7.3f    %8.3f %8.3f\n" % (
                    str(miller_index) + "-" + structure_dir + ":",
                    evac,
                    epa,
                    equi_epa,
                )
                res_data[str(miller_index) + "_" + structure_dir] = [
                    evac,
                    epa,
                    equi_epa,
                ]

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
