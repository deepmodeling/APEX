import glob
import json
import logging
import os
import shutil
import re
import subprocess
from typing import List, Dict, Any

import dpdata
import seekpath
from pathlib import Path
from monty.serialization import dumpfn, loadfn
from pymatgen.core.structure import Structure

from apex.core.structure import StructureInfo
from apex.core.calculator.calculator import LAMMPS_INTER_TYPE
from apex.core.calculator.lib import abacus_utils
from apex.core.calculator.lib import vasp_utils
from apex.core.property.Property import Property
from apex.core.refine import make_refine
from apex.core.reproduce import make_repro, post_repro
from dflow.python import upload_packages
upload_packages.append(__file__)


class Phonon(Property):
    def __init__(self, parameter, inter_param=None):
        parameter["reproduce"] = parameter.get("reproduce", False)
        self.reprod = parameter["reproduce"]
        if not self.reprod:
            if not ("init_from_suffix" in parameter and "output_suffix" in parameter):
                parameter["primitive"] = parameter.get('primitive', False)
                self.primitive = parameter["primitive"]
                parameter["approach"] = parameter.get('approach', 'linear')
                self.approach = parameter["approach"]
                parameter["supercell_size"] = parameter.get('supercell_size', [2, 2, 2])
                self.supercell_size = parameter["supercell_size"]
                parameter["seekpath_from_original"] = parameter.get('seekpath_from_original', False)
                self.seekpath_from_original = parameter["seekpath_from_original"]
                parameter["seekpath_param"] = parameter.get('seekpath_param', {})
                self.seekpath_param = parameter["seekpath_param"]
                parameter["MESH"] = parameter.get('MESH', None)
                self.MESH = parameter["MESH"]
                parameter["PRIMITIVE_AXES"] = parameter.get('PRIMITIVE_AXES', None)
                self.PRIMITIVE_AXES = parameter["PRIMITIVE_AXES"]
                parameter["BAND"] = parameter.get('BAND', None)
                self.BAND = parameter["BAND"]
                parameter["BAND_LABELS"] = parameter.get('BAND_LABELS', None)
                if self.BAND:
                    self.BAND_LABELS = parameter["BAND_LABELS"]
                else:
                    self.BAND_LABELS = None
                parameter["BAND_POINTS"] = parameter.get('BAND_POINTS', None)
                self.BAND_POINTS = parameter["BAND_POINTS"]
                parameter["BAND_CONNECTION"] = parameter.get('BAND_CONNECTION', True)
                self.BAND_CONNECTION = parameter["BAND_CONNECTION"]
            parameter["cal_type"] = parameter.get("cal_type", "static")
            default_cal_setting = {
                "relax_pos": True,
                "relax_shape": False,
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
        self.inter_param = inter_param if inter_param is not None else {"type": "vasp"}

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
            print("phonon reproduce starts")
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
            os.chdir(cwd)

        else:
            if refine:
                print("phonon refine starts")
                task_list = make_refine(
                    self.parameter["init_from_suffix"],
                    self.parameter["output_suffix"],
                    path_to_work,
                )
                os.chdir(cwd)

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
                    ss = Structure.from_file(equi_contcar)
                    # gen structure

                # get user input parameter for specific structure
                st = StructureInfo(ss)
                self.structure_type = st.lattice_structure
                type_param = self.parameter.get(self.structure_type, None)
                if type_param:
                    self.primitive = type_param.get("primitive", self.primitive)
                    self.approach = type_param.get("approach", self.approach)
                    self.supercell_size = type_param.get("supercell_size", self.supercell_size)
                    self.MESH = type_param.get("MESH", self.MESH)
                    self.PRIMITIVE_AXES = type_param.get("PRIMITIVE_AXES", self.PRIMITIVE_AXES)
                    self.BAND = type_param.get("BAND", self.BAND)
                    self.BAND_POINTS = type_param.get("BAND_POINTS", self.BAND_POINTS)
                    self.BAND_CONNECTION = type_param.get("BAND_CONNECTION", self.BAND_CONNECTION)

                os.chdir(path_to_work)
                if os.path.exists(POSCAR):
                    os.remove(POSCAR)
                os.symlink(os.path.relpath(equi_contcar), POSCAR)

                # get band path
                if not self.BAND:
                    # use seekpath to get band path
                    if self.seekpath_from_original:
                        print('Band path (BAND) not indicated, using seekpath from original cell')
                        sp = seekpath.get_path_orig_cell(
                            self.get_seekpath_structure(ss),
                            **self.seekpath_param
                        )
                    else:
                        print('Band path (BAND) not indicated, using seekpath for it')
                        sp = seekpath.get_path(
                            self.get_seekpath_structure(ss),
                            **self.seekpath_param
                        )
                    band_list = self.extract_seekpath_band(sp)
                    self.BAND, self.BAND_LABELS = self.band_list_2_phonopy_band_string(band_list)
                else:
                    # use user input band path
                    print(f'Band path (BAND) indicated, using: {self.BAND}')
                    band_list = self.phonopy_band_string_2_band_list(self.BAND, self.BAND_LABELS)

                dumpfn(band_list, os.path.join(path_to_work, "band_path.json"), indent=4)

                # prepare phonopy input head
                ret = ""
                ret += "ATOM_NAME ="
                for ii in ptypes:
                    ret += " %s" % ii
                ret += "\n"
                ret += "DIM = %s %s %s\n" % (
                    self.supercell_size[0],
                    self.supercell_size[1],
                    self.supercell_size[2]
                )
                if self.MESH:
                    ret += "MESH = %s %s %s\n" % (
                        self.MESH[0], self.MESH[1], self.MESH[2]
                    )
                if self.PRIMITIVE_AXES:
                    ret += "PRIMITIVE_AXES = %s\n" % self.PRIMITIVE_AXES
                ret += "BAND = %s\n" % self.BAND
                if self.BAND_LABELS:
                    ret += "BAND_LABELS = %s\n" % self.BAND_LABELS
                if self.BAND_POINTS:
                    ret += "BAND_POINTS = %s\n" % self.BAND_POINTS
                if self.BAND_CONNECTION:
                    ret += "BAND_CONNECTION = %s\n" % self.BAND_CONNECTION

                ret_force_read = ret + "FORCE_CONSTANTS=READ\n"

                task_list = []
                # ------------make for abacus---------------
                if self.inter_param["type"] == "abacus":
                    # make setting.conf
                    ret_sc = ""
                    ret_sc += "DIM=%s %s %s\n" % (
                        self.supercell_size[0],
                        self.supercell_size[1],
                        self.supercell_size[2]
                    )
                    ret_sc += "ATOM_NAME ="
                    for atom in ptypes:
                        ret_sc += " %s" % (atom)
                    ret_sc += "\n"
                    with open("setting.conf", "a") as fp:
                        fp.write(ret_sc)
                    # append NUMERICAL_ORBITAL to STRU after relaxation
                    orb_file = self.inter_param.get("orb_files", None)
                    abacus_utils.append_orb_file_to_stru("STRU", orb_file, prefix='pp_orb')
                    ## generate STRU-00x
                    cmd = "phonopy setting.conf --abacus -d"
                    subprocess.call(cmd, shell=True)

                    with open("band.conf", "a") as fp:
                        fp.write(ret)
                    # generate task.000*
                    stru_list = glob.glob("STRU-0*")
                    for ii in range(len(stru_list)):
                        task_path = os.path.join(path_to_work, 'task.%06d' % ii)
                        os.makedirs(task_path, exist_ok=True)
                        os.chdir(task_path)
                        task_list.append(task_path)
                        os.symlink(os.path.join(path_to_work, stru_list[ii]), 'STRU')
                        os.symlink(os.path.join(path_to_work, 'STRU'), 'STRU.ori')
                        os.symlink(os.path.join(path_to_work, 'band.conf'), 'band.conf')
                        os.symlink(os.path.join(path_to_work, 'phonopy_disp.yaml'), 'phonopy_disp.yaml')
                        try:
                            os.symlink(os.path.join(path_to_work, 'KPT'), 'KPT')
                        except:
                            pass
                    os.chdir(cwd)
                    return task_list

                # ------------make for vasp and lammps------------
                if self.primitive:
                    subprocess.call('phonopy --symmetry', shell=True)
                    subprocess.call('cp PPOSCAR POSCAR', shell=True)
                    shutil.copyfile("PPOSCAR", "POSCAR-unitcell")
                else:
                    shutil.copyfile("POSCAR", "POSCAR-unitcell")

                # make tasks
                if self.inter_param["type"] == 'vasp':
                    cmd = "phonopy -d --dim='%d %d %d' -c POSCAR" % (
                        int(self.supercell_size[0]),
                        int(self.supercell_size[1]),
                        int(self.supercell_size[2])
                    )
                    subprocess.call(cmd, shell=True)
                    # linear response method
                    if self.approach == 'linear':
                        task_path = os.path.join(path_to_work, 'task.000000')
                        os.makedirs(task_path, exist_ok=True)
                        os.chdir(task_path)
                        task_list.append(task_path)
                        os.symlink(os.path.join(path_to_work, "SPOSCAR"), "POSCAR")
                        os.symlink(os.path.join(path_to_work, "POSCAR-unitcell"), "POSCAR-unitcell")
                        with open("band.conf", "a") as fp:
                            fp.write(ret_force_read)
                    # finite displacement method
                    elif self.approach == 'displacement':
                        poscar_list = glob.glob("POSCAR-0*")
                        for ii in range(len(poscar_list)):
                            task_path = os.path.join(path_to_work, 'task.%06d' % ii)
                            os.makedirs(task_path, exist_ok=True)
                            os.chdir(task_path)
                            task_list.append(task_path)
                            os.symlink(os.path.join(path_to_work, poscar_list[ii]), 'POSCAR')
                            os.symlink(os.path.join(path_to_work, "POSCAR-unitcell"), "POSCAR-unitcell")

                        os.chdir(path_to_work)
                        with open("band.conf", "a") as fp:
                            fp.write(ret)
                        shutil.copyfile("band.conf", "task.000000/band.conf")
                        shutil.copyfile("phonopy_disp.yaml", "task.000000/phonopy_disp.yaml")

                    else:
                        raise RuntimeError(
                            f'Unsupported phonon approach input: {self.approach}. '
                            f'Please choose from "linear" and "displacement".'
                        )
                    os.chdir(cwd)
                    return task_list
                # ----------make for lammps-------------
                elif self.inter_param["type"] in LAMMPS_INTER_TYPE:
                    task_path = os.path.join(path_to_work, 'task.000000')
                    os.makedirs(task_path, exist_ok=True)
                    os.chdir(task_path)
                    task_list.append(task_path)
                    if os.path.isfile(POSCAR) or os.path.islink(POSCAR):
                        os.remove(POSCAR)
                    os.symlink(os.path.join(path_to_work, "POSCAR-unitcell"), POSCAR)

                    with open("band.conf", "a") as fp:
                        fp.write(ret_force_read)
                    os.chdir(cwd)
                    return task_list
                else:
                    raise RuntimeError(
                        f'Unsupported interaction type input: {self.inter_param["type"]}'
                    )

    def post_process(self, task_list):
        cwd = os.getcwd()
        inter_type = self.inter_param["type"]
        if inter_type in LAMMPS_INTER_TYPE:
            # prepare in.lammps
            for ii in task_list:
                os.chdir(ii)
                with open("in.lammps", 'r') as f1:
                    contents = f1.readlines()
                    for jj in range(len(contents)):
                        is_pair_coeff = re.search("pair_coeff", contents[jj])
                        if is_pair_coeff:
                            pair_line_id = jj
                            break
                    del contents[pair_line_id + 1:]

                with open("in.lammps", 'w') as f2:
                    for jj in range(len(contents)):
                        f2.write(contents[jj])
                # dump phonolammps command
                phonolammps_cmd = "phonolammps in.lammps -c POSCAR --dim %s %s %s " %(
                    self.supercell_size[0], self.supercell_size[1], self.supercell_size[2]
                )
                with open("run_command", 'w') as f3:
                    f3.write(phonolammps_cmd)
        elif inter_type == "vasp":
            pass
        elif inter_type == "abacus":
            pass
        os.chdir(cwd)

    def task_type(self):
        return self.parameter["type"]

    def task_param(self):
        return self.parameter

    @staticmethod
    def unpack_band(band_out: str) -> list:
        branch_list = band_out.split('\n\n\n')
        branch_list.pop()
        unpacked_branch_list = []
        for ii in branch_list:
            segment_list = ii.split('\n\n')
            unpacked_segment_list = []
            for jj in segment_list:
                point_list = jj.split('\n')
                segment_dict = {float(kk.split()[0]): float(kk.split()[1]) for kk in point_list}
                unpacked_segment_list.append(segment_dict)
            unpacked_branch_list.append(unpacked_segment_list)
        return unpacked_branch_list

    @staticmethod
    def get_seekpath_structure(ss: Structure) -> list:
        """
        Convert pymatgen structure to seekpath structure
        """
        seekpath_structure = [
            ss.lattice.matrix,
            ss.frac_coords,
            ss.atomic_numbers
        ]
        return seekpath_structure

    @staticmethod
    def extract_seekpath_band(seekpath_data: dict):
        point_coords = seekpath_data['point_coords']
        band_path = seekpath_data['path']
        extracted_path = []
        phonopy_band = []
        pre_seg_end = None
        for segment in band_path:
            seg0 = segment[0]
            seg1 = segment[1]
            coord0 = point_coords[segment[0]]
            coord1 = point_coords[segment[1]]
            if not pre_seg_end:
                long_branch = [{seg0: coord0}, {seg1: coord1}]
            elif pre_seg_end == seg0:
                long_branch.append({seg1: coord1})
            else:
                extracted_path.append(long_branch)
                long_branch = [{seg0: coord0}, {seg1: coord1}]
            pre_seg_end = seg1
        extracted_path.append(long_branch)
        # return type: list[list[dict[Any, Any]]]
        return extracted_path

    @staticmethod
    def band_list_2_phonopy_band_string(band_list) -> [str, str]:
        band_string = ""
        band_label = ""
        # type of band_list: list[list[dict[Any, Any]]]
        for branch in band_list:
            for point in branch:
                name = list(point.keys())[0]
                coord = list(point.values())[0]
                coord_str = " ".join([str(ii) for ii in coord])
                band_string += f"{coord_str}  "
                band_label += f"{name}  "
            band_string = band_string[:-2]
            band_label = band_label[:-2]
            band_string += ", "
            band_label += ", "
        band_string = band_string[:-2]
        band_label = band_label[:-2]

        return band_string, band_label

    @staticmethod
    def phonopy_band_string_2_band_list(band_str: str, band_label: str = None):
        band_list = []
        branch_list = band_str.split(',')
        point_num = 0
        do_label = False

        for branch in branch_list:
            unit_list = branch.split()
            unit_num = len(unit_list)
            if unit_num % 3 != 0:
                raise ValueError("Input BAND List length is not a multiple of 3.")
            else:
                point_num += unit_num // 3

        if band_label:
            label_branch_list = band_label.split(',')
            label_num = 0
            all_labels = []
            for branch in label_branch_list:
                label_point_list = branch.split()
                all_labels.extend(label_point_list)
                label_num += len(label_point_list)
            if point_num == label_num:
                do_label = True
            else:
                logging.warning("band string and label string have different length, skip labelling the band")

        for branch in branch_list:
            unit_list = branch.split()
            if do_label:
                label_iter = iter(all_labels)
                seg_list = [{f'{next(label_iter)}': unit_list[i:i+3]} for i in range(0, len(unit_list), 3)]
            else:
                seg_list = [{f'{i}': unit_list[ii:ii+3]} for i, ii in enumerate(range(0, len(unit_list), 3))]
            band_list.append(seg_list)
        # return type -> list[list[dict[Any, Any]]]
        return band_list

    @staticmethod
    def check_same_copy(src, dst):
        if os.path.samefile(src, dst):
            return
        shutil.copyfile(src, dst)

    def _compute_lower(self, output_file, all_tasks, all_res):
        cwd = Path.cwd()
        work_path = Path(output_file).parent.absolute()
        output_file = os.path.abspath(output_file)
        res_data = {}
        ptr_data = os.path.dirname(output_file) + "\n"

        band_path = loadfn(os.path.join(work_path, "band_path.json"))

        if not self.reprod:
            os.chdir(work_path)
            if self.inter_param["type"] == 'abacus':
                self.check_same_copy("task.000000/band.conf", "band.conf")
                self.check_same_copy("task.000000/STRU.ori", "STRU")
                self.check_same_copy("task.000000/phonopy_disp.yaml", "phonopy_disp.yaml")
                os.system('phonopy -f task.0*/OUT.ABACUS/running_scf.log')
                if os.path.exists("FORCE_SETS"):
                    print('FORCE_SETS is created')
                else:
                    logging.warning('FORCE_SETS can not be created')
                os.system('phonopy band.conf --abacus')
                os.system('phonopy-bandplot --gnuplot band.yaml > band.dat')

            elif self.inter_param["type"] == 'vasp':
                self.check_same_copy("task.000000/band.conf", "band.conf")
                self.check_same_copy("task.000000/POSCAR-unitcell", "POSCAR-unitcell")

                if self.approach == "linear":
                    os.chdir(all_tasks[0])
                    assert os.path.isfile('vasprun.xml'), "vasprun.xml not found"
                    os.system('phonopy --fc vasprun.xml')
                    assert os.path.isfile('FORCE_CONSTANTS'), "FORCE_CONSTANTS not created"
                    os.system('phonopy --dim="%s %s %s" -c POSCAR-unitcell band.conf' % (
                            self.supercell_size[0],
                            self.supercell_size[1],
                            self.supercell_size[2]))
                    os.system('phonopy-bandplot --gnuplot band.yaml > band.dat')
                    print('band.dat is created')
                    shutil.copyfile("band.dat", work_path/"band.dat")

                elif self.approach == "displacement":
                    self.check_same_copy("task.000000/band.conf", "band.conf")
                    self.check_same_copy("task.000000/phonopy_disp.yaml", "phonopy_disp.yaml")
                    os.system('phonopy -f task.0*/vasprun.xml')
                    if os.path.exists("FORCE_SETS"):
                        print('FORCE_SETS is created')
                    else:
                        logging.warning('FORCE_SETS can not be created')
                    os.system('phonopy --dim="%s %s %s" -c POSCAR-unitcell band.conf' % (
                        self.supercell_size[0],
                        self.supercell_size[1],
                        self.supercell_size[2]))
                    os.system('phonopy-bandplot --gnuplot band.yaml > band.dat')

            elif self.inter_param["type"] in LAMMPS_INTER_TYPE:
                os.chdir(all_tasks[0])
                assert os.path.isfile('FORCE_CONSTANTS'), "FORCE_CONSTANTS not created"
                os.system('phonopy --dim="%s %s %s" -c POSCAR band.conf' % (
                    self.supercell_size[0], self.supercell_size[1], self.supercell_size[2])
                    )
                os.system('phonopy-bandplot --gnuplot band.yaml > band.dat')
                shutil.copyfile("band.dat", work_path/"band.dat")

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

        os.chdir(work_path)
        with open('band.dat', 'r') as f:
            ptr_data = f.read()

        result_points = ptr_data.split('\n')[1][4:].split()
        result_lines = ptr_data.split('\n')[2:]
        unpacked_lines = self.unpack_band('\n'.join(result_lines))
        res_data['segment'] = result_points
        res_data['band_path'] = band_path
        res_data['band'] = unpacked_lines

        with open(output_file, "w") as fp:
            json.dump(res_data, fp, indent=4)

        os.chdir(cwd)
        return res_data, ptr_data
