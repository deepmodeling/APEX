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
from apex.core.refine import make_refine
from apex.core.reproduce import make_repro, post_repro
from dflow.python import upload_packages
upload_packages.append(__file__)


class EOS(Property):
    def __init__(self, parameter, inter_param=None):
        parameter["reproduce"] = parameter.get("reproduce", False)
        self.reprod = parameter["reproduce"]
        if not self.reprod:
            if not ("init_from_suffix" in parameter and "output_suffix" in parameter):
                self.vol_start = parameter["vol_start"]
                self.vol_end = parameter["vol_end"]
                self.vol_step = parameter["vol_step"]
                parameter["vol_abs"] = parameter.get("vol_abs", False)
                self.vol_abs = parameter["vol_abs"]
            parameter["cal_type"] = parameter.get("cal_type", "relaxation")
            self.cal_type = parameter["cal_type"]
            default_cal_setting = {
                "relax_pos": True,
                "relax_shape": True,
                "relax_vol": False,
            }
            parameter.setdefault("cal_setting", {})
            parameter["cal_setting"].update(
                {k: v for k, v in default_cal_setting.items() if k not in parameter["cal_setting"]})
            self.cal_setting = parameter["cal_setting"]
        else:
            parameter["cal_type"] = "static"
            self.cal_type = parameter["cal_type"]
            default_cal_setting = {
                "relax_pos": False,
                "relax_shape": False,
                "relax_vol": False,
            }
            parameter.setdefault("cal_setting", {})
            parameter["cal_setting"].update(
                {k: v for k, v in default_cal_setting.items() if k not in parameter["cal_setting"]})
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
                    "relax_task",
                )
            )

        cwd = os.getcwd()
        task_list = []
        if self.reprod:
            print("eos reproduce starts")
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
                print("eos refine starts")
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
                task_list_basename = list(map(os.path.basename, task_list))

                for ii in task_list_basename:
                    init_from_task = os.path.join(init_from_path, ii)
                    output_task = os.path.join(path_to_work, ii)
                    os.chdir(output_task)
                    if os.path.isfile("eos.json"):
                        os.remove("eos.json")
                    if os.path.islink("eos.json"):
                        os.remove("eos.json")
                    os.symlink(
                        os.path.relpath(os.path.join(init_from_task, "eos.json")),
                        "eos.json",
                    )

            else:
                print(
                    "gen eos from "
                    + str(self.vol_start)
                    + " to "
                    + str(self.vol_end)
                    + " by every "
                    + str(self.vol_step)
                )
                if self.vol_abs:
                    print("treat vol_start and vol_end as absolute volume")
                else:
                    print("treat vol_start and vol_end as relative volume")

                if self.inter_param["type"] == "abacus":
                    equi_contcar = os.path.join(
                        path_to_equi, abacus_utils.final_stru(path_to_equi)
                    )
                else:
                    equi_contcar = os.path.join(path_to_equi, "CONTCAR")

                if not os.path.isfile(equi_contcar):
                    raise RuntimeError(
                        "Can not find %s, please do relaxation first" % equi_contcar
                    )

                if self.inter_param["type"] == "abacus":
                    stru_data = abacus_scf.get_abacus_STRU(equi_contcar)
                    vol_to_poscar = (
                        abs(np.linalg.det(stru_data["cells"]))
                        / np.array(stru_data["atom_numbs"]).sum()
                    )
                else:
                    vol_to_poscar = vasp_utils.poscar_vol(equi_contcar) / vasp_utils.poscar_natoms(
                        equi_contcar
                    )
                self.parameter["scale2equi"] = []

                task_num = 0
                while self.vol_start + self.vol_step * task_num < self.vol_end:
                    vol = self.vol_start + task_num * self.vol_step
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
                    # scale = (vol / vol_to_poscar) ** (1. / 3.)

                    if self.vol_abs:
                        scale = (vol / vol_to_poscar) ** (1.0 / 3.0)
                        eos_params = {"volume": vol, "scale": scale}
                    else:
                        scale = vol ** (1.0 / 3.0)
                        eos_params = {"volume": vol * vol_to_poscar, "scale": scale}
                    dumpfn(eos_params, "eos.json", indent=4)
                    self.parameter["scale2equi"].append(scale)  # 06/22
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
            ptr_data += " VpA(A^3)  EpA(eV)\n"
            for ii in range(len(all_tasks)):
                # vol = self.vol_start + ii * self.vol_step
                vol = loadfn(os.path.join(all_tasks[ii], "eos.json"))["volume"]
                task_result = loadfn(all_res[ii])
                res_data[vol] = task_result["energies"][-1] / sum(
                    task_result["atom_numbs"]
                )
                ptr_data += "%7.3f  %8.4f \n" % (
                    vol,
                    task_result["energies"][-1] / sum(task_result["atom_numbs"]),
                )

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
