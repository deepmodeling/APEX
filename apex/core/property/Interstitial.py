import glob
import json
import logging
import os
import re
import shutil

import numpy as np
from monty.serialization import dumpfn, loadfn
from pymatgen.analysis.defects.generators import VoronoiInterstitialGenerator
from pymatgen.analysis.defects.core import Interstitial as pmgInterstitial
from pymatgen.core.structure import Structure, Lattice
from pymatgen.core.sites import PeriodicSite
from pymatgen.core.tensors import Tensor
from pymatgen.core.operations import SymmOp

from apex.core.calculator.lib import abacus_utils
from apex.core.calculator.lib import lammps_utils
from apex.core.property.Property import Property
from apex.core.refine import make_refine
from apex.core.reproduce import make_repro, post_repro
from apex.core.structure import StructureInfo
from dflow.python import upload_packages

upload_packages.append(__file__)

TOL = 1e-5

class Interstitial(Property):
    def __init__(self, parameter, inter_param=None):
        parameter["reproduce"] = parameter.get("reproduce", False)
        self.reprod = parameter["reproduce"]
        if not self.reprod:
            if not ("init_from_suffix" in parameter and "output_suffix" in parameter):
                default_supercell = [1, 1, 1]
                parameter["supercell"] = parameter.get("supercell", default_supercell)
                self.supercell = parameter["supercell"]
                self.insert_ele = parameter.get("insert_ele", None)
                parameter["lattice_type"] = parameter.get("lattice_type", None)
                self.lattice_type = parameter["lattice_type"]
                parameter["voronoi_param"] = parameter.get("voronoi_param", {})
                self.voronoi_param = parameter["voronoi_param"]
                parameter["special_list"] = parameter.get("special_list", ['bcc', 'fcc', 'hcp'])
                self.special_list = parameter["special_list"]

            parameter["cal_type"] = parameter.get("cal_type", "relaxation")
            default_cal_setting = {
                "relax_pos": True,
                "relax_shape": True,
                "relax_vol": True,
            }
        else:
            parameter["cal_type"] = "static"
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
        self.path_to_work = os.path.abspath(path_to_work)
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
            assert struct_output_name in struct_init_name_list, f"{struct_output_name} not in initial configurations"
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
                if os.path.exists("element.out"):
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
                    if os.path.exists("supercell.json"):
                        os.remove("supercell.json")
                    os.symlink(
                        os.path.relpath(os.path.join(init_from_task, "supercell.json")),
                        "supercell.json",
                    )

            else:
                if self.inter_param["type"] == "abacus":
                    CONTCAR = abacus_utils.final_stru(path_to_equi)
                    POSCAR = "STRU"
                else:
                    CONTCAR = "CONTCAR"
                    POSCAR = "POSCAR"

                equi_contcar = os.path.join(path_to_equi, CONTCAR)
                orig_poscar = os.path.join(path_to_equi, POSCAR)
                if not os.path.exists(equi_contcar):
                    raise RuntimeError("please do relaxation first")

                if self.inter_param["type"] == "abacus":
                    ss = abacus_utils.stru2Structure(equi_contcar)
                else:
                    ss = Structure.from_file(equi_contcar)
                rot = Tensor.get_ieee_rotation(ss)
                op = SymmOp.from_rotation_and_translation(rot)
                ss.apply_operation(op)
                # get structure type
                os.chdir(self.path_to_work)
                # convert site element into same type for a pseudo-structure just for simple lattice type judgment
                same_type_ss = ss.copy()
                species_mapping = {str(specie): "Ni" for specie in same_type_ss.composition.elements}
                same_type_ss.replace_species(species_mapping)
                st = StructureInfo(same_type_ss, symprec=0.1, angle_tolerance=5)
                # indication of structure type
                self.structure_type = st.lattice_structure
                # get conventional standard structure to ss
                orig_st = StructureInfo(ss, symprec=0.1, angle_tolerance=5)
                conv_ss = orig_st.conventional_structure
                conv_ss.to("POSCAR", "POSCAR")
                conv_ss.to("POSCAR_conv", "POSCAR")
                ss = conv_ss
                if self.lattice_type:
                    print(f'Adopt user indicated lattice type: {self.lattice_type}')
                    self.structure_type = self.lattice_type
                os.chdir(cwd)

                # gen defects
                dss = []
                self.insert_element_task = os.path.join(self.path_to_work, "element.out")
                if os.path.isfile(self.insert_element_task):
                    os.remove(self.insert_element_task)
                if not self.insert_ele:
                    self.insert_ele = [str(ii) for ii in set(ss.composition.elements)]
                for ii in self.insert_ele:
                    if self.structure_type in self.special_list:
                        # rotate and translate hcp structure to specific orientation for interstitial generation
                        if self.structure_type == 'hcp':
                            theta = -2 * np.pi / 3
                            rot_m = np.array([
                                [np.cos(theta), -np.sin(theta), 0],
                                [np.sin(theta), np.cos(theta), 0],
                                [0, 0, 1]
                            ])
                            op = SymmOp.from_rotation_and_translation(rotation_matrix=rot_m)
                            ss.apply_operation(op)
                            new_lattice = Lattice([
                                ss.lattice.matrix[0] * -1, ss.lattice.matrix[1] * -1, ss.lattice.matrix[2]
                            ])
                            new_frac_coords = ss.frac_coords.copy()
                            if not ((new_frac_coords[0][0] < 0.5 and new_frac_coords[0][2] < 0.5)\
                                    or (new_frac_coords[0][0] > 0.5 and new_frac_coords[0][2] > 0.5)):
                                new_frac_coords[0][2] = ss.frac_coords[1][2]
                                new_frac_coords[1][2] = ss.frac_coords[0][2]
                            new_ss = Structure(new_lattice, ss.species, new_frac_coords, coords_are_cartesian=False)
                            ss = new_ss
                            ss.to(os.path.join(self.path_to_work, 'POSCAR_conv'), 'POSCAR')
                        # produce a pseudo interstitial structure for later modification
                        vds = [pmgInterstitial(ss, PeriodicSite(ii, [0.12, 0.13, 0.14], ss.lattice))]
                    else:
                        pre_vds = VoronoiInterstitialGenerator(**self.voronoi_param)
                        vds = pre_vds.generate(ss, [ii])
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
                if os.path.exists(POSCAR):
                    os.remove(POSCAR)
                os.symlink(os.path.relpath(equi_contcar), POSCAR)
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
                    dumpfn(f'VoronoiType_{ii}', 'interstitial_type.json')
                os.chdir(cwd)

                super_size = (
                        self.supercell[0] * self.supercell[1] * self.supercell[2]
                )
                num_atom = super_size * 2
                # chl = -num_atom - 2
                os.chdir(self.path_to_work)

                # create pre-defined special SIA structure for bcc fcc and hcp
                if self.structure_type in self.special_list:
                    self.task_list = []
                    if not os.path.isfile("task.000000/POSCAR"):
                        raise RuntimeError("need task.000000 structure as reference")

                    with open('POSCAR_conv', "r") as fin:
                        fin.readline()
                        scale = float(fin.readline().split()[0])
                        self.latt_param = float(fin.readline().split()[0])
                        self.latt_param *= scale

                    with open("task.000000/POSCAR", "r") as fin:
                        self.pos_line = fin.read().split("\n")

                    for idx, ii in enumerate(self.pos_line):
                        ss = ii.split()
                        if len(ss) > 3:
                            if (
                                    abs(0.12 / self.supercell[0] - float(ss[0])) < TOL
                                    and abs(0.13 / self.supercell[1] - float(ss[1])) < TOL
                                    and abs(0.14 / self.supercell[2] - float(ss[2])) < TOL
                            ):
                                chl = idx
                    shutil.rmtree("task.000000")

                    os.chdir(cwd)
                    # specify interstitial structures
                    if self.structure_type == 'bcc':
                        for idx, ii in enumerate(self.pos_line):
                            ss = ii.split()
                            if len(ss) > 3:
                                if (
                                        abs(0.5 / self.supercell[0] - float(ss[0])) < TOL
                                        and abs(0.5 / self.supercell[1] - float(ss[1])) < TOL
                                        and abs(0.5 / self.supercell[2] - float(ss[2])) < TOL
                                ):
                                    center = idx
                        bcc_interstital_dict = {
                            'tetrahedral': {chl: [0.25, 0.5, 0]},
                            'octahedral': {chl: [0.5, 0.5, 0]},
                            'crowdion': {chl: [0.25, 0.25, 0.25]},
                            '<111>dumbbell': {chl: [1 / 3, 1 / 3, 1 / 3],
                                              center: [2 / 3, 2 / 3, 2 / 3]},
                            '<110>dumbbell': {chl: [1 / 4, 3 / 4, 1 / 2],
                                              center: [3 / 4, 1 / 4, 1 / 2]},
                            '<100>dumbbell': {chl: [1 / 2, 1 / 2, 1 / 6],
                                              center: [1 / 2, 1 / 2, 5 / 6]}
                        }
                        total_task = self.__gen_tasks(bcc_interstital_dict)

                    elif self.structure_type == 'fcc':
                        for idx, ii in enumerate(self.pos_line):
                            ss = ii.split()
                            if len(ss) > 3:
                                if (
                                        abs(1 / self.supercell[0] - float(ss[0])) < TOL
                                        and abs(0.5 / self.supercell[1] - float(ss[1])) < TOL
                                        and abs(0.5 / self.supercell[2] - float(ss[2])) < TOL
                                ):
                                    face = idx

                                if (
                                        abs(1 / self.supercell[0] - float(ss[0])) < TOL
                                        and abs(1 / self.supercell[1] - float(ss[1])) < TOL
                                        and abs(1 / self.supercell[2] - float(ss[2])) < TOL
                                ):
                                    corner = idx

                        fcc_interstital_dict = {
                            'tetrahedral': {chl: [0.75, 0.25, 0.25]},
                            'octahedral': {chl: [1, 0, 0.5]},
                            'crowdion': {chl: [1, 0.25, 0.25]},
                            '<111>dumbbell': {
                                chl: [1 - 0.3 / np.sqrt(3),
                                      1 - 0.3 / np.sqrt(3),
                                      1 - 0.3 / np.sqrt(3)],
                                corner: [0.3 / np.sqrt(3),
                                         0.3 / np.sqrt(3),
                                         0.3 / np.sqrt(3)]
                            },
                            '<110>dumbbell': {
                                chl: [1,
                                      0.5 + (0.3 / np.sqrt(2)),
                                      0.5 + (0.3 / np.sqrt(2))],
                                face: [1,
                                       0.5 - (0.3 / np.sqrt(2)),
                                       0.5 - (0.3 / np.sqrt(2))]
                            },
                            '<100>dumbbell': {
                                chl: [1, 0.2, 0.5],
                                face: [1, 0.8, 0.5]
                            },
                        }
                        total_task = self.__gen_tasks(fcc_interstital_dict)

                    elif self.structure_type == 'hcp':
                        for idx, ii in enumerate(self.pos_line):
                            ss = ii.split()
                            if len(ss) > 3:
                                if (
                                        abs(1/3 / self.supercell[0] - float(ss[0])) < TOL
                                        and abs(2/3 / self.supercell[1] - float(ss[1])) < TOL
                                        and abs(0.25 / self.supercell[2] - float(ss[2])) < TOL
                                ):
                                    center = idx
                        hcp_interstital_dict = {
                            'O': {chl: [0, 0, 0.5]},
                            'BO': {chl: [0, 0, 0.25]},
                            'C': {chl: [0.5, 0.5, 0.5]},
                            'BC': {chl: [5/6, 2/3, 0.25]},
                            'S': {
                                chl: [1/3, 2/3, 0.475],
                                center: [1/3, 2/3, 0.025]
                            },
                            'BS': {
                                chl: [2/3, 2/3, 0.25],
                                center: [0, 2/3, 0.25]
                            },
                            'T': {chl: [2/3, 1/3, 0.375]},
                            'BT': {chl: [2/3, 1/3, 0.25]},
                        }
                        total_task = self.__gen_tasks(hcp_interstital_dict)
                else:
                    total_task = len(dss)

                if self.inter_param["type"] == "abacus":
                    for ii in range(total_task):
                        output_task = os.path.join(self.path_to_work, "task.%06d" % ii)
                        os.chdir(output_task)
                        abacus_utils.poscar2stru("POSCAR", self.inter_param, "STRU")
                        #os.remove("POSCAR")
        os.chdir(cwd)
        return self.task_list

    def __gen_tasks(self, interstitial_dict):
        cwd = os.getcwd()
        for ii, (type_str, adjust_dict) in enumerate(interstitial_dict.items()):
            output_task = os.path.join(
                self.path_to_work, "task.%06d" % (len(self.dss) + ii - 1)
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
                        "%.6f" % float(pos[0] / self.supercell[0])
                        + " "
                        + "%.6f" % float(pos[1] / self.supercell[1])
                        + " "
                        + "%.6f" % float(pos[2] / self.supercell[2])
                        + " "
                        + self.insert_ele[0]
                )
            with open("POSCAR", "w+") as fout:
                for ii in new_pos_line:
                    print(ii, file=fout)
            print(f"gen {type_str}")
            os.chdir(cwd)

        total_task = len(self.dss) + len(interstitial_dict) - 1

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
                    type_map_list = lammps_utils.element_list(type_map)
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
            ptr_data += "Insert_ele-Struct:          \tInter_E(eV)    \tE(eV)     \tequi_E(eV)\n"

            equi_path = os.path.abspath(
                os.path.join(
                    os.path.dirname(output_file), "../relaxation/relax_task"
                )
            )
            equi_result = loadfn(os.path.join(equi_path, "result.json"))
            equi_epa = equi_result["energies"][-1] / sum(equi_result["atom_numbs"])

            for idid, ii in enumerate(all_tasks, start=0): # skip task.000000
                structure_dir = os.path.basename(ii)
                task_result = loadfn(all_res[idid])
                interstitial_type = loadfn(os.path.join(ii, 'interstitial_type.json'))
                natoms = sum(task_result["atom_numbs"])
                evac = task_result["energies"][-1] - equi_epa * natoms
                supercell_index = loadfn(os.path.join(ii, "supercell.json"))
                # insert_ele = loadfn(os.path.join(ii, 'task.json'))['insert_ele'][0]
                insert_ele = fc[idid]
                ptr_data += "%s: \t%7.3f  \t%7.3f \t%7.3f \n" % (
                    insert_ele + "_" + str(interstitial_type) + "_" + structure_dir,
                    evac,
                    task_result["energies"][-1],
                    equi_epa * natoms,
                )
                res_data[
                    insert_ele + "_" + str(interstitial_type) + "_" + structure_dir
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
