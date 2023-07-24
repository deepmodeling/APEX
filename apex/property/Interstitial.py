import glob
import json
import os
import re
import copy

import numpy as np
from monty.serialization import dumpfn, loadfn
from pymatgen.analysis.defects.generators import InterstitialGenerator
from pymatgen.core.structure import Structure

import apex.calculator.lib.abacus as abacus
import apex.calculator.lib.lammps as lammps
from apex.property.Property import Property
from apex.property.refine import make_refine
from apex.property.reproduce import make_repro, post_repro
from apex.property.Structure import StructureType
from dflow.python import upload_packages
upload_packages.append(__file__)


class Interstitial(Property):
    def __init__(self, parameter, inter_param=None):
        parameter["reproduce"] = parameter.get("reproduce", False)
        self.reprod = parameter["reproduce"]
        if not self.reprod:
            if not ("init_from_suffix" in parameter and "output_suffix" in parameter):
                default_supercell = [1, 1, 1]
                parameter["supercell"] = parameter.get("supercell", default_supercell)
                self.supercell = parameter["supercell"]
                self.insert_ele = parameter["insert_ele"]
            parameter["cal_type"] = parameter.get("cal_type", "relaxation")
            self.cal_type = parameter["cal_type"]
            default_cal_setting = {
                "relax_pos": True,
                "relax_shape": True,
                "relax_vol": True,
            }
            if "cal_setting" not in parameter:
                parameter["cal_setting"] = default_cal_setting
            else:
                if "relax_pos" not in parameter["cal_setting"]:
                    parameter["cal_setting"]["relax_pos"] = default_cal_setting[
                        "relax_pos"
                    ]
                if "relax_shape" not in parameter["cal_setting"]:
                    parameter["cal_setting"]["relax_shape"] = default_cal_setting[
                        "relax_shape"
                    ]
                if "relax_vol" not in parameter["cal_setting"]:
                    parameter["cal_setting"]["relax_vol"] = default_cal_setting[
                        "relax_vol"
                    ]
            self.cal_setting = parameter["cal_setting"]
        else:
            parameter["cal_type"] = "static"
            self.cal_type = parameter["cal_type"]
            default_cal_setting = {
                "relax_pos": False,
                "relax_shape": False,
                "relax_vol": False,
            }
            if "cal_setting" not in parameter:
                parameter["cal_setting"] = default_cal_setting
            else:
                if "relax_pos" not in parameter["cal_setting"]:
                    parameter["cal_setting"]["relax_pos"] = default_cal_setting[
                        "relax_pos"
                    ]
                if "relax_shape" not in parameter["cal_setting"]:
                    parameter["cal_setting"]["relax_shape"] = default_cal_setting[
                        "relax_shape"
                    ]
                if "relax_vol" not in parameter["cal_setting"]:
                    parameter["cal_setting"]["relax_vol"] = default_cal_setting[
                        "relax_vol"
                    ]
            self.cal_setting = parameter["cal_setting"]
            parameter["init_from_suffix"] = parameter.get("init_from_suffix", "00")
            self.init_from_suffix = parameter["init_from_suffix"]
        self.parameter = parameter
        self.inter_param = inter_param if inter_param != None else {"type": "vasp"}

    def make_confs(self, path_to_work, path_to_equi, refine=False):
        self.path_to_work = os.path.abspath(path_to_work)
        path_to_equi = os.path.abspath(path_to_equi)

        if "start_confs_path" in self.parameter and os.path.exists(
            self.parameter["start_confs_path"]
        ):
            init_path_list = glob.glob(
                os.path.join(self.parameter["start_confs_path"], "*")
            )
            struct_init_name_list = []
            for ii in init_path_list:
                struct_init_name_list.append(ii.split("/")[-1])
            struct_output_name = self.path_to_work.split("/")[-2]
            assert struct_output_name in struct_init_name_list
            path_to_equi = os.path.abspath(
                os.path.join(
                    self.parameter["start_confs_path"],
                    struct_output_name,
                    "relaxation",
                    "relax_task",
                )
            )

        self.task_list = []
        cwd = os.getcwd()

        if self.reprod:
            print("interstitial reproduce starts")
            if "init_data_path" not in self.parameter:
                raise RuntimeError("please provide the initial data path to reproduce")
            init_data_path = os.path.abspath(self.parameter["init_data_path"])
            self.task_list = make_repro(
                self.inter_param,
                init_data_path,
                self.init_from_suffix,
                self.path_to_work,
                self.parameter.get("reprod_last_frame", False),
            )
            os.chdir(cwd)

        else:
            if refine:
                print("interstitial refine starts")
                self.task_list = make_refine(
                    self.parameter["init_from_suffix"],
                    self.parameter["output_suffix"],
                    self.path_to_work,
                )

                init_from_path = re.sub(
                    self.parameter["output_suffix"][::-1],
                    self.parameter["init_from_suffix"][::-1],
                    self.path_to_work[::-1],
                    count=1,
                )[::-1]
                task_list_basename = list(map(os.path.basename, self.task_list))

                os.chdir(self.path_to_work)
                if os.path.isfile("element.out"):
                    os.remove("element.out")
                if os.path.islink("element.out"):
                    os.remove("element.out")
                os.symlink(
                    os.path.relpath(os.path.join(init_from_path, "element.out")),
                    "element.out",
                )
                os.chdir(cwd)

                for ii in task_list_basename:
                    init_from_task = os.path.join(init_from_path, ii)
                    output_task = os.path.join(self.path_to_work, ii)
                    os.chdir(output_task)
                    if os.path.isfile("supercell.json"):
                        os.remove("supercell.json")
                    if os.path.islink("supercell.json"):
                        os.remove("supercell.json")
                    os.symlink(
                        os.path.relpath(os.path.join(init_from_task, "supercell.json")),
                        "supercell.json",
                    )
                os.chdir(cwd)

            else:
                if self.inter_param["type"] == "abacus":
                    CONTCAR = abacus.final_stru(path_to_equi)
                    POSCAR = "STRU"
                else:
                    CONTCAR = "CONTCAR"
                    POSCAR = "POSCAR"

                equi_contcar = os.path.join(path_to_equi, CONTCAR)
                if not os.path.exists(equi_contcar):
                    raise RuntimeError("please do relaxation first")

                if self.inter_param["type"] == "abacus":
                    ss = abacus.stru2Structure(equi_contcar)
                else:
                    ss = Structure.from_file(equi_contcar)

                # get structure type
                os.chdir(self.path_to_work)
                ss.to("POSCAR", "POSCAR")
                st = StructureType(ss)
                self.structure_type = st.get_structure_type()
                os.chdir(cwd)

                # gen defects
                dss = []
                self.insert_element_task = os.path.join(self.path_to_work, "element.out")
                if os.path.isfile(self.insert_element_task):
                    os.remove(self.insert_element_task)

                for ii in self.insert_ele:
                    pre_vds = InterstitialGenerator()
                    vds = pre_vds.generate(ss, {ii: [[0.1, 0.1, 0.1]]})
                    for jj in vds:
                        temp = jj.get_supercell_structure(
                            sc_mat=np.diag(self.supercell, k=0)
                        )
                        smallest_distance = list(set(temp.distance_matrix.ravel()))[1]
                        if (
                            "conf_filters" in self.parameter
                            and "min_dist" in self.parameter["conf_filters"]
                        ):
                            min_dist = self.parameter["conf_filters"]["min_dist"]
                            if smallest_distance >= min_dist:
                                dss.append(temp)
                                with open(self.insert_element_task, "a+") as fout:
                                    print(ii, file=fout)
                        else:
                            dss.append(temp)
                            with open(self.insert_element_task, "a+") as fout:
                                print(ii, file=fout)
                #            dss.append(jj.generate_defect_structure(self.supercell))
                        self.dss = dss

                print(
                    "gen interstitial with supercell "
                    + str(self.supercell)
                    + " with element "
                    + str(self.insert_ele)
                )
                os.chdir(self.path_to_work)
                if os.path.isfile(POSCAR):
                    os.remove(POSCAR)
                if os.path.islink(POSCAR):
                    os.remove(POSCAR)
                os.symlink(os.path.relpath(equi_contcar), POSCAR)
                #           task_poscar = os.path.join(output, 'POSCAR')

                for ii in range(len(dss)):
                    output_task = os.path.join(self.path_to_work, "task.%06d" % ii)
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
                    self.task_list.append(output_task)
                    dss[ii].to("POSCAR", "POSCAR")
                    # np.savetxt('supercell.out', self.supercell, fmt='%d')
                    dumpfn(self.supercell, "supercell.json")
                os.chdir(cwd)

                super_size = (
                        self.supercell[0] * self.supercell[1] * self.supercell[2]
                )
                num_atom = super_size * 2
                #chl = -num_atom - 2
                os.chdir(self.path_to_work)

                if not os.path.isfile("task.000000/POSCAR"):
                    raise RuntimeError("need task.000000 structure as reference")

                with open('POSCAR', "r") as fin:
                    fin.readline()
                    scale = float(fin.readline().split()[0])
                    self.latt_param = float(fin.readline().split()[0])
                    self.latt_param *= scale

                with open("task.000000/POSCAR", "r") as fin:
                    self.pos_line = fin.read().split("\n")


                self.super_latt_param = float(self.pos_line[2].split()[0])
                self.unit_frac = self.latt_param / self.super_latt_param

                for idx, ii in enumerate(self.pos_line):
                    ss = ii.split()
                    if len(ss) > 3:
                        if (
                                abs(self.unit_frac * 0.1 - float(ss[0])) < 1e-5
                                and abs(self.unit_frac * 0.1 - float(ss[1])) < 1e-5
                                and abs(self.unit_frac * 0.1 - float(ss[2])) < 1e-5
                        ):
                            chl = idx

                os.chdir(cwd)

                # specify interstitial structures
                if self.structure_type == 'bcc':
                    for idx, ii in enumerate(self.pos_line):
                        ss = ii.split()
                        if len(ss) > 3:
                            if (
                                    abs(self.unit_frac * 0.5 - float(ss[0])) < 1e-5
                                    and abs(self.unit_frac * 0.5 - float(ss[1])) < 1e-5
                                    and abs(self.unit_frac * 0.5 - float(ss[2])) < 1e-5
                            ):
                                center = idx
                    bcc_interstital_dict = {
                        'tetrahedral':   {chl: [0.25, 0.5, 0]},
                        'octahedral':    {chl: [0.5, 0.5, 0]},
                        'crowdion':      {chl: [0.25, 0.25, 0]},
                        '<111>dumbbell': {chl: [1/3, 1/3, 1/3],
                                          center: [2/3, 2/3, 2/3]},
                        '<110>dumbbell': {chl: [1/4, 3/4, 1/2],
                                          center: [3/4, 1/4, 1/2]},
                        '<100>dumbbell': {chl: [1/2, 1/2, 1/6],
                                          center: [1/2, 1/2, 5/6]}
                    }
                    total_task = self.__gen_tasks(bcc_interstital_dict)

                elif self.structure_type == 'fcc':
                    for idx, ii in enumerate(self.pos_line):
                        ss = ii.split()
                        if len(ss) > 3:
                            if (
                                    abs(self.unit_frac * 1 - float(ss[0])) < 1e-5
                                    and abs(self.unit_frac * 0.5 - float(ss[1])) < 1e-5
                                    and abs(self.unit_frac * 0.5 - float(ss[2])) < 1e-5
                            ):
                                face = idx

                            if (
                                    abs(self.unit_frac * 1 - float(ss[0])) < 1e-5
                                    and abs(self.unit_frac * 1 - float(ss[1])) < 1e-5
                                    and abs(self.unit_frac * 1 - float(ss[2])) < 1e-5
                            ):
                                corner = idx

                    fcc_interstital_dict = {
                        'tetrahedral':      {chl: [0.75, 0.25, 0.25]},
                        'octahedral':       {chl: [1, 0, 0.5]},
                        'crowdion':         {chl: [1, 0.25, 0.25]},
                        '<111>dumbbell':    {
                            chl: [1-0.3/np.sqrt(3),
                                    1-0.3/np.sqrt(3),
                                    1-0.3/np.sqrt(3)],
                            corner: [0.3/np.sqrt(3),
                                    0.3/np.sqrt(3),
                                    0.3/np.sqrt(3)]
                        },
                        '<110>dumbbell': {
                            chl: [1,
                                    0.5+(0.3/np.sqrt(2)),
                                    0.5+(0.3/np.sqrt(2))],
                            face: [1,
                                    0.5-(0.3/np.sqrt(2)),
                                    0.5-(0.3/np.sqrt(2))]
                        },
                        '<100>dumbbell': {
                            chl: [1, 0.2, 0.5],
                            face: [1, 0.8, 0.5]
                        },
                    }
                    total_task = self.__gen_tasks(fcc_interstital_dict)

                elif self.structure_type == 'hcp':
                    pass
                else:
                    total_task = len(dss)

                if self.inter_param["type"] == "abacus":
                    for ii in range(total_task):
                        output_task = os.path.join(self.path_to_work, "task.%06d" % ii)
                        os.chdir(output_task)
                        abacus.poscar2stru("POSCAR", self.inter_param, "STRU")
                        os.remove("POSCAR")
                    os.chdir(cwd)

        return self.task_list


    def __gen_tasks(self, interstitial_dict):
        cwd = os.getcwd()
        for ii, (type_str, adjust_dict) in enumerate(interstitial_dict.items()):
            output_task = os.path.join(
                self.path_to_work, "task.%06d" % (len(self.dss) + ii)
            )
            os.makedirs(output_task, exist_ok=True)
            os.chdir(output_task)
            self.task_list.append(output_task)
            # adjust atom positions in POSCAR
            with open(self.insert_element_task, "a+") as fout:
                print(self.insert_ele[0], file=fout)
            dumpfn(self.supercell, "supercell.json")
            dumpfn(type_str, 'interstitial_type.json')
            new_pos_line = self.pos_line.copy()
            for line, pos in adjust_dict.items():
                new_pos_line[line] = (
                        "%.6f" % float(self.unit_frac * pos[0])
                        + " "
                        + "%.6f" % float(self.unit_frac * pos[1])
                        + " "
                        + "%.6f" % float(self.unit_frac * pos[2])
                        + " "
                        + self.insert_ele[0]
                )
            with open("POSCAR", "w+") as fout:
                for ii in new_pos_line:
                    print(ii, file=fout)
            print(f"gen {type_str}")
            os.chdir(cwd)

        total_task = len(self.dss) + len(interstitial_dict)

        return total_task

    def post_process(self, task_list):
        if True:
            fin1 = open(os.path.join(task_list[0], "..", "element.out"), "r")
            for ii in task_list:
                conf = os.path.join(ii, "conf.lmp")
                inter = os.path.join(ii, "inter.json")
                insert_ele = fin1.readline().split()[0]
                if os.path.isfile(conf):
                    with open(conf, "r") as fin2:
                        conf_line = fin2.read().split("\n")
                        insert_line = conf_line[-2]
                    type_map = loadfn(inter)["type_map"]
                    type_map_list = lammps.element_list(type_map)
                    if int(insert_line.split()[1]) > len(type_map_list):
                        type_num = type_map[insert_ele] + 1
                        conf_line[2] = str(len(type_map_list)) + " atom types"
                        conf_line[-2] = (
                            "%6.d" % int(insert_line.split()[0])
                            + "%7.d" % type_num
                            + "%16.10f" % float(insert_line.split()[2])
                            + "%16.10f" % float(insert_line.split()[3])
                            + "%16.10f" % float(insert_line.split()[4])
                        )
                        with open(conf, "w+") as fout:
                            for jj in conf_line:
                                print(jj, file=fout)
            fin1.close()

    def task_type(self):
        return self.parameter["type"]

    def task_param(self):
        return self.parameter

    def _compute_lower(self, output_file, all_tasks, all_res):
        output_file = os.path.abspath(output_file)
        res_data = {}
        ptr_data = os.path.dirname(output_file) + "\n"

        if not self.reprod:
            with open(
                os.path.join(os.path.dirname(output_file), "element.out"), "r"
            ) as fin:
                fc = fin.read().split("\n")
            ptr_data += "Insert_ele-Struct: Inter_E(eV)  E(eV) equi_E(eV)\n"
            idid = -1
            for ii in all_tasks:
                idid += 1
                structure_dir = os.path.basename(ii)
                task_result = loadfn(all_res[idid])
                natoms = task_result["atom_numbs"][0]
                equi_path = os.path.abspath(
                    os.path.join(
                        os.path.dirname(output_file), "../relaxation/relax_task"
                    )
                )
                equi_result = loadfn(os.path.join(equi_path, "result.json"))
                equi_epa = equi_result["energies"][-1] / equi_result["atom_numbs"][0]
                evac = task_result["energies"][-1] - equi_epa * natoms

                supercell_index = loadfn(os.path.join(ii, "supercell.json"))
                # insert_ele = loadfn(os.path.join(ii, 'task.json'))['insert_ele'][0]
                insert_ele = fc[idid]
                ptr_data += "%s: %7.3f  %7.3f %7.3f \n" % (
                    insert_ele + "-" + str(supercell_index) + "-" + structure_dir,
                    evac,
                    task_result["energies"][-1],
                    equi_epa * natoms,
                )
                res_data[
                    insert_ele + "-" + str(supercell_index) + "-" + structure_dir
                ] = [evac, task_result["energies"][-1], equi_epa * natoms]

        else:
            if "init_data_path" not in self.parameter:
                raise RuntimeError("please provide the initial data path to reproduce")
            init_data_path = os.path.abspath(self.parameter["init_data_path"])
            res_data, ptr_data = post_repro(
                init_data_path,
                self.parameter["init_from_suffix"],
                all_tasks,
                ptr_data,
                self.parameter.get("reprod_last_frame", False),
            )

        with open(output_file, "w") as fp:
            json.dump(res_data, fp, indent=4)

        return res_data, ptr_data
