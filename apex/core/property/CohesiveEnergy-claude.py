import glob
import json
import logging
import os
import re
import numpy as np
from monty.serialization import dumpfn, loadfn

# 直接导入模块，避免通过apex包导入
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from apex.core.calculator.lib import abacus_utils
from apex.core.calculator.lib import vasp_utils
from apex.core.calculator.lib import abacus_scf
from apex.core.property.Property import Property
from apex.core.refine import make_refine
from apex.core.reproduce import make_repro, post_repro
# 移除对dflow的依赖
# from dflow.python import upload_packages
# upload_packages.append(__file__)

def poscar_elem(poscar_file):
    """
    从POSCAR文件中提取元素列表
    """
    with open(poscar_file, 'r') as f:
        lines = f.readlines()
    # POSCAR格式：第6行是元素类型
    elements = lines[5].strip().split()
    return elements

# 然后修改导入部分，添加这个函数到vasp_utils模块
vasp_utils.poscar_elem = poscar_elem

class CohesiveEnergy(Property):
    def __init__(self, parameter, inter_param=None):
        parameter["reproduce"] = parameter.get("reproduce", False)
        self.reprod = parameter["reproduce"]
        if not self.reprod:
            if not ("init_from_suffix" in parameter and "output_suffix" in parameter):
                self.lattice_param_start = parameter["lattice_param_start"]
                self.lattice_param_end = parameter["lattice_param_end"]
                self.lattice_param_step = parameter["lattice_param_step"]
                parameter["lattice_abs"] = parameter.get("lattice_abs", False)
                self.latt_abs = parameter["latt_abs"]
                # 添加别名以兼容代码中的引用
                self.latt_start = self.lattice_param_start
                self.latt_end = self.lattice_param_end
                self.latt_step = self.lattice_param_step
                # 单原子能量计算参数
                parameter["single_atom"] = parameter.get("single_atom", True)
                self.single_atom = parameter["single_atom"]
                parameter["single_atom_path"] = parameter.get("single_atom_path", None)
                self.single_atom_path = parameter["single_atom_path"]
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

        else:
            if refine:
                print("cohesive energy refine starts")
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
                    if os.path.isfile("cohesive.json"):
                        os.remove("cohesive.json")
                    if os.path.islink("cohesive.json"):
                        os.remove("cohesive.json")
                    os.symlink(
                        os.path.relpath(os.path.join(init_from_task, "cohesive.json")),
                        "cohesive.json",
                    )

            else:
                print(
                    "gen cohesive energy from "
                    + str(self.lattice_param_start)
                    + " to "
                    + str(self.lattice_param_end)
                    + " by every "
                    + str(self.lattice_param_step)
                )
                if self.latt_abs:
                    print("treat latt_start and latt_end as absolute lattice constant")
                else:
                    print("treat latt_start and latt_end as relative lattice constant")

                # 创建单原子计算任务
                if self.single_atom and self.single_atom_path is None:
                    single_atom_task = os.path.join(path_to_work, "single_atom")
                    os.makedirs(single_atom_task, exist_ok=True)
                    os.chdir(single_atom_task)
                    task_list.append(single_atom_task)
                    
                    # 为每种元素创建单原子计算
                    if self.inter_param["type"] == "abacus":
                        equi_contcar = os.path.join(
                            path_to_equi, abacus_utils.final_stru(path_to_equi)
                        )
                        stru_data = abacus_scf.get_abacus_STRU(equi_contcar)
                        elements = stru_data["atom_names"]
                    else:
                        equi_contcar = os.path.join(path_to_equi, "CONTCAR")
                        elements = vasp_utils.poscar_elem(equi_contcar)
                    
                    # 保存单原子计算信息
                    single_atom_info = {"elements": elements}
                    dumpfn(single_atom_info, "single_atom.json", indent=4)
                    
                    # 这里需要实现为每个元素创建单原子计算的具体逻辑
                    # 由于具体实现依赖于计算引擎，这里只是占位
                    # 实际应用中需要根据VASP或ABACUS的要求创建相应的输入文件

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

                # 获取晶格常数
                if self.inter_param["type"] == "abacus":
                    stru_data = abacus_scf.get_abacus_STRU(equi_contcar)
                    latt_to_poscar = np.mean(np.linalg.norm(stru_data["cells"], axis=1))
                else:
                    # 对于VASP，我们使用晶胞的平均晶格常数作为参考
                    with open(equi_contcar, 'r') as f:
                        lines = f.readlines()
                    scale = float(lines[1].strip())
                    cell = np.array([[float(x) for x in line.split()] for line in lines[2:5]])
                    cell *= scale
                    latt_to_poscar = np.mean(np.linalg.norm(cell, axis=1))
                
                self.parameter["scale2equi"] = []

                task_num = 0
                while self.lattice_param_start + self.lattice_param_step * task_num < self.lattice_param_end:
                    latt = self.lattice_param_start + task_num * self.lattice_param_step
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
                        scale = latt / latt_to_poscar
                        cohesive_params = {"lattice": latt, "scale": scale}
                    else:
                        scale = latt
                        cohesive_params = {"lattice": latt * latt_to_poscar, "scale": scale}
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
            # 检查是否有单原子计算结果
            single_atom_energies = {}
            single_atom_task = None
            
            for task in all_tasks:
                if os.path.basename(task) == "single_atom":
                    single_atom_task = task
                    break
            
            if single_atom_task and os.path.exists(os.path.join(single_atom_task, "single_atom.json")):
                # 读取单原子能量
                with open(os.path.join(single_atom_task, "result_task.json")) as fp:
                    single_atom_result = json.load(fp)
                # 这里需要根据具体的输出格式解析单原子能量
                # 假设结果中包含了每个元素的单原子能量
                if "single_atom_energies" in single_atom_result:
                    single_atom_energies = single_atom_result["single_atom_energies"]
            elif self.single_atom_path and os.path.exists(self.single_atom_path):
                # 从指定路径读取单原子能量
                with open(self.single_atom_path) as fp:
                    single_atom_energies = json.load(fp)
            
            # 计算内聚能
            ptr_data += " Latt(Å)  Etot(eV)  Ecoh(eV/atom)\n"
            bulk_tasks = [t for t in all_tasks if os.path.basename(t) != "single_atom"]
            
            for ii in range(len(bulk_tasks)):
                task_path = bulk_tasks[ii]
                latt = loadfn(os.path.join(task_path, "cohesive.json"))["lattice"]
                task_result = loadfn(all_res[ii])
                
                total_energy = task_result["energies"][-1]
                atom_counts = task_result["atom_numbs"]
                atom_types = task_result["atom_names"]
                
                # 计算体系总原子数
                total_atoms = sum(atom_counts)
                
                # 计算单原子能量总和
                sum_single_atom_energy = 0
                if single_atom_energies:
                    for i, elem in enumerate(atom_types):
                        if elem in single_atom_energies:
                            sum_single_atom_energy += single_atom_energies[elem] * atom_counts[i]
                
                # 计算内聚能 (eV/atom)
                if sum_single_atom_energy != 0:
                    # 深势能计算支持
                    cohesive_energy = (total_energy - sum_single_atom_energy) / total_atoms
                    if 'deep_potential' in self.parameter:
                        cohesive_energy *= self.parameter['deep_potential'].get('energy_scale_factor', 1.0)
                else:
                    # 如果没有单原子能量，则只输出总能量
                    cohesive_energy = 0
                
                # 存储结果
                res_data[latt] = {
                    "total_energy": total_energy / total_atoms,
                    "cohesive_energy": cohesive_energy
                }
                
                ptr_data += "%7.3f  %8.4f  %8.4f\n" % (
                    latt,
                    total_energy / total_atoms,
                    cohesive_energy
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