import glob
import json
import logging
import os
import re

import dpdata
import numpy as np
from monty.serialization import dumpfn, loadfn
from pymatgen.analysis.diffraction.tem import TEMCalculator
from pymatgen.core.structure import Structure
from pymatgen.core.surface import SlabGenerator

from apex.core.calculator.lib import abacus_utils
from apex.core.calculator.lib import vasp_utils
from apex.core.lib.slab_orientation import SlabSlipSystem
from apex.core.lib.trans_tools import direction_miller_bravais_to_miller
from apex.core.lib.trans_tools import plane_miller_bravais_to_miller
from apex.core.lib.trans_tools import trans_mat_basis
from apex.core.property.base import Property
from apex.core.refine import make_refine
from apex.core.reproduce import make_repro
from apex.core.reproduce import post_repro
from apex.core.structure import StructureInfo
from dflow.python import upload_packages

upload_packages.append(__file__)


class GammaSurface(Property):
    """Calculation of generalized stacking fault energy surface."""

    def __init__(self, parameter, inter_param=None):
        parameter["reproduce"] = parameter.get("reproduce", False)
        self.reprod = parameter["reproduce"]
        if not self.reprod:
            if not ("init_from_suffix" in parameter and "output_suffix" in parameter):
                parameter["plane_miller"] = parameter.get("plane_miller", None)
                self.plane_miller = parameter["plane_miller"]
                parameter["slip_direction"] = parameter.get("slip_direction", None)
                self.slip_direction = parameter["slip_direction"]
                parameter["slip_length"] = parameter.get("slip_length", None)
                self.slip_length = parameter["slip_length"]
                parameter["slip_length_y"] = parameter.get("slip_length_y", None)
                self.slip_length_y = parameter["slip_length_y"]
                parameter["plane_shift"] = parameter.get("plane_shift", 0)
                self.plane_shift = parameter["plane_shift"]
                parameter["supercell_size"] = parameter.get("supercell_size", (1, 1, 5))
                self.supercell_size = parameter["supercell_size"]
                parameter["vacuum_size"] = parameter.get("vacuum_size", 0)
                self.vacuum_size = parameter["vacuum_size"]
                parameter["add_fix"] = parameter.get(
                    "add_fix", ["true", "true", "false"]
                )
                self.add_fix = parameter["add_fix"]
                parameter["n_steps_x"] = parameter.get(
                    "n_steps_x", parameter.get("n_steps", 10)
                )
                parameter["n_steps"] = parameter["n_steps_x"]
                self.n_steps_x = parameter["n_steps_x"]
                self.n_steps = self.n_steps_x
                parameter["n_steps_y"] = parameter.get("n_steps_y", self.n_steps_x)
                self.n_steps_y = parameter["n_steps_y"]
                self.atom_num = None
            parameter["cal_type"] = parameter.get("cal_type", "relaxation")
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

    def _resolve_equilibrium_structure(self, path_to_equi):
        return os.path.join(path_to_equi, "CONTCAR"), "POSCAR"

    def _load_equilibrium_structure(self, equi_contcar):
        ptypes = vasp_utils.get_poscar_types(equi_contcar)
        ss = Structure.from_file(equi_contcar)
        return ptypes, ss

    def _finalize_task_structure(self):
        pass

    def _fix_task_output(self, task_dir, first_task):
        self.__poscar_fix(os.path.join(task_dir, "POSCAR"))

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
            init_path_list = glob.glob(os.path.join(self.parameter["start_confs_path"], "*"))
            struct_init_name_list = [os.path.basename(ii) for ii in init_path_list]
            struct_output_name = os.path.basename(os.path.dirname(path_to_work))
            assert (
                struct_output_name in struct_init_name_list
            ), f"{struct_output_name} not in initial configuration names"
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
            print("gamma surface reproduce starts")
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
                print("gamma surface refine starts")
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
                    if os.path.exists("miller.json"):
                        os.remove("miller.json")
                    os.symlink(
                        os.path.relpath(os.path.join(init_from_task, "miller.json")),
                        "miller.json",
                    )

            else:
                equi_contcar, POSCAR = self._resolve_equilibrium_structure(path_to_equi)
                if not os.path.exists(equi_contcar):
                    raise RuntimeError("please do relaxation first")

                ptypes, ss = self._load_equilibrium_structure(equi_contcar)

                os.chdir(path_to_equi)
                ss.to("CONTCAR.direct", "POSCAR")
                ss = Structure.from_file("CONTCAR.direct")
                st = StructureInfo(ss)
                self.structure_type = st.lattice_structure
                self.conv_std_structure = st.conventional_structure
                relax_a = self.conv_std_structure.lattice.a
                relax_b = self.conv_std_structure.lattice.b
                relax_c = self.conv_std_structure.lattice.c

                type_param = self.parameter.get(self.structure_type, None)
                if type_param:
                    self.plane_miller = type_param.get("plane_miller", self.plane_miller)
                    self.slip_direction = type_param.get("slip_direction", self.slip_direction)
                    self.slip_length = type_param.get("slip_length", self.slip_length)
                    self.slip_length_y = type_param.get("slip_length_y", self.slip_length_y)
                    self.plane_shift = type_param.get("plane_shift", self.plane_shift)
                    self.supercell_size = type_param.get("supercell_size", self.supercell_size)
                    self.vacuum_size = type_param.get("vacuum_size", self.vacuum_size)
                    self.add_fix = type_param.get("add_fix", self.add_fix)
                    self.n_steps_x = type_param.get(
                        "n_steps_x", type_param.get("n_steps", self.n_steps_x)
                    )
                    self.n_steps = self.n_steps_x
                    self.n_steps_y = type_param.get("n_steps_y", self.n_steps_y)

                if not (self.plane_miller and self.slip_direction):
                    raise RuntimeError(
                        "fail to obtain both slip plane and slip direction info from input json file!"
                    )

                if self.structure_type not in ["bcc", "fcc", "hcp"]:
                    logging.warning(
                        "Gamma surface function for %s is not fully supported so far. "
                        "Please double check generated slab structures.",
                        self.structure_type,
                    )

                plane_miller, _, slip_length_x, Q = self.__convert_input_miller(
                    self.conv_std_structure
                )
                slab = self.__gen_slab_pmg(
                    self.conv_std_structure, plane_miller, trans_matrix=Q
                )
                self.atom_num = len(slab.sites)

                os.chdir(path_to_work)
                if os.path.exists(POSCAR):
                    os.remove(POSCAR)
                os.symlink(os.path.relpath(equi_contcar), POSCAR)

                slip_length_x = self.__resolve_slip_length(
                    slip_length_x, relax_a, relax_b, relax_c
                )
                if self.slip_length_y is None:
                    self.slip_length_y = slip_length_x
                slip_length_y = self.__resolve_slip_length(
                    self.slip_length_y, relax_a, relax_b, relax_c
                )
                self.slip_length = slip_length_x
                self.slip_length_y = slip_length_y

                top_atoms = np.where(slab.frac_coords[:, 2] > 0.5)[0]
                n_steps_x = int(self.n_steps_x)
                n_steps_y = int(self.n_steps_y)
                n_steps_x_denom = max(n_steps_x, 1)
                n_steps_y_denom = max(n_steps_y, 1)

                count = 0
                for idx_x in range(n_steps_x + 1):
                    frac_x = idx_x / n_steps_x_denom
                    for idx_y in range(n_steps_y + 1):
                        frac_y = idx_y / n_steps_y_denom
                        output_task = os.path.join(path_to_work, "task.%06d" % count)
                        os.makedirs(output_task, exist_ok=True)
                        os.chdir(output_task)
                        for jj in ["INCAR", "POTCAR", POSCAR, "conf.lmp", "in.lammps"]:
                            if os.path.exists(jj):
                                os.remove(jj)
                        task_list.append(output_task)

                        disp_cart = np.array(
                            [slip_length_x * frac_x, slip_length_y * frac_y, 0.0]
                        )
                        slab_task = slab.copy()
                        if np.linalg.norm(disp_cart) > 0:
                            slab_task.translate_sites(
                                indices=top_atoms,
                                vector=disp_cart,
                                frac_coords=False,
                                to_unit_cell=True,
                            )

                        slab_task.to("POSCAR.tmp", "POSCAR")
                        vasp_utils.regulate_poscar("POSCAR.tmp", "POSCAR")
                        vasp_utils.sort_poscar("POSCAR", "POSCAR", ptypes)
                        self._finalize_task_structure()
                        dumpfn(self.plane_miller, "miller.json")
                        dumpfn(slip_length_x, "slip_length_x.json")
                        dumpfn(slip_length_y, "slip_length_y.json")
                        dumpfn(
                            {
                                "frac_x": frac_x,
                                "frac_y": frac_y,
                                "idx_x": idx_x,
                                "idx_y": idx_y,
                            },
                            "displacement.json",
                        )
                        count += 1

        os.chdir(cwd)
        return task_list

    def __resolve_slip_length(self, slip_length, relax_a, relax_b, relax_c):
        if isinstance(slip_length, (int, float)):
            return float(slip_length) * relax_a
        try:
            slip_vector_cartesian = np.multiply(
                np.array(slip_length), np.array([relax_a, relax_b, relax_c])
            )
            return float(np.linalg.norm(slip_vector_cartesian, 2))
        except Exception:
            raise RuntimeError(
                "Only int | float or Sequence[int | float, int | float, int | float] "
                "is allowed for slip_length/slip_length_y"
            )

    def __convert_input_miller(self, structure: Structure):
        plane_miller = tuple(self.plane_miller)
        slip_direction = tuple(self.slip_direction)
        slip_length = self.slip_length

        plane_str = "".join([str(i) for i in plane_miller])
        slip_str = "".join([str(i) for i in slip_direction])
        combined_key = "x".join([plane_str, slip_str])
        l2_normalize_1d = lambda v: v / np.linalg.norm(v, 2)

        dir_dict = SlabSlipSystem.atomic_system_dict()
        try:
            system = dir_dict[self.structure_type]
            plane_miller, x_miller, xy_miller, stored_slip_length = system[combined_key].values()
        except KeyError:
            logging.warning(
                "Input slip system is not pre-defined in GammaSurface. "
                "Please double check generated slab structure."
            )
            x_miller = slip_direction
            if not slip_length:
                slip_length = 1
            if self.structure_type == "hcp" and (
                len(self.plane_miller) == 4 or len(self.slip_direction) == 4
            ):
                if len(plane_miller) == 4:
                    plane_miller = plane_miller_bravais_to_miller(self.plane_miller)
                if len(x_miller) == 4:
                    x_miller = direction_miller_bravais_to_miller(self.slip_direction)
            dir_dot = np.array(plane_miller).dot(np.array(x_miller))
            if dir_dot != 0:
                raise RuntimeError(
                    f"slip direction {self.slip_direction} is not on plane given {self.plane_miller}"
                )
            x_cartesian = np.dot(np.array(x_miller), structure.lattice.matrix)
            z_cartesian = np.dot(np.array(plane_miller), structure.lattice.matrix)
            x_cartesian_unit_vector = l2_normalize_1d(x_cartesian)
            y_cartesian_unit_vector = l2_normalize_1d(np.cross(z_cartesian, x_cartesian))
            z_cartesian_unit_vector = l2_normalize_1d(
                np.cross(x_cartesian_unit_vector, y_cartesian_unit_vector)
            )
        else:
            if not slip_length:
                slip_length = stored_slip_length
            x_cartesian = np.dot(np.array(x_miller), structure.lattice.matrix)
            xy_cartesian = np.dot(np.array(xy_miller), structure.lattice.matrix)
            x_cartesian_unit_vector = l2_normalize_1d(x_cartesian)
            z_cartesian_unit_vector = l2_normalize_1d(np.cross(x_cartesian, xy_cartesian))
            y_cartesian_unit_vector = l2_normalize_1d(
                np.cross(z_cartesian_unit_vector, x_cartesian_unit_vector)
            )
        finally:
            reoriented_basis = np.array(
                [x_cartesian_unit_vector, y_cartesian_unit_vector, z_cartesian_unit_vector]
            )
            Q = trans_mat_basis(reoriented_basis)

        return plane_miller, x_miller, slip_length, Q

    def __gen_slab_pmg(self, structure: Structure, plane_miller, trans_matrix=None) -> Structure:
        tem_calc_obj = TEMCalculator()
        spacing_dict = tem_calc_obj.get_interplanar_spacings(self.conv_std_structure, [plane_miller])
        slab_size = spacing_dict[plane_miller] * self.supercell_size[2]
        slab_gen = SlabGenerator(
            structure,
            miller_index=plane_miller,
            min_slab_size=slab_size,
            min_vacuum_size=0,
            center_slab=True,
            in_unit_planes=False,
            lll_reduce=True,
            reorient_lattice=False,
            primitive=False,
        )
        slabs_pmg = slab_gen.get_slabs(ftol=0.001)
        slab = [s for s in slabs_pmg if s.miller_index == plane_miller][0]
        if trans_matrix.any():
            reoriented_lattice_vectors = [trans_matrix.dot(v) for v in slab.lattice.matrix]
            slab = Structure(
                lattice=np.matrix(reoriented_lattice_vectors),
                coords=slab.frac_coords,
                species=slab.species,
            )

        order = zip(slab.frac_coords, slab.species)
        c_order = sorted(order, key=lambda x: x[0][2])
        sorted_frac_coords = []
        sorted_species = []
        for frac_coord, species in c_order:
            sorted_frac_coords.append(frac_coord)
            sorted_species.append(species)

        a, b, c = slab.lattice.matrix
        slab_height = slab.lattice.matrix[2][2]
        if slab_height >= 0:
            self.is_flip = False
            elong_scale = 1 + (self.vacuum_size / slab_height)
        else:
            self.is_flip = True
            elong_scale = 1 + (-self.vacuum_size / slab_height)
        new_lattice = [a, b, elong_scale * c]
        new_frac_coords = []
        for ii in range(len(sorted_frac_coords)):
            coord = sorted_frac_coords[ii].copy()
            coord[2] = coord[2] / elong_scale
            new_frac_coords.append(coord)
        slab = Structure(
            lattice=np.matrix(new_lattice), coords=new_frac_coords, species=sorted_species
        )

        plane_shift_frac = self.plane_shift * structure.lattice.c / slab.lattice.matrix[2][2]
        avg_c = np.average([coord[2] for coord in slab.frac_coords])
        slab.translate_sites(list(range(len(slab))), [0, 0, 0.5 - avg_c - plane_shift_frac])
        slab.make_supercell(
            scaling_matrix=[self.supercell_size[0], self.supercell_size[1], 1]
        )
        return slab

    def __poscar_fix(self, poscar) -> None:
        insert_pos = -self.atom_num
        fix_dict = {"true": "F", "false": "T"}
        add_fix_str = (
            " " + fix_dict[self.add_fix[0]] + " " + fix_dict[self.add_fix[1]] + " " + fix_dict[self.add_fix[2]] + "\n"
        )
        with open(poscar, "r") as fin1:
            contents = fin1.readlines()
            contents.insert(insert_pos - 1, "Selective dynamics\n")
            for ii in range(insert_pos, 0, 1):
                contents[ii] = contents[ii].replace("\n", "")
                content_split = contents[ii].split(" ")
                if len(content_split[-1]) < 3:
                    content_split.pop()
                contents[ii] = " ".join(content_split)
                contents[ii] += add_fix_str
        with open(poscar, "w") as fin2:
            for ii in range(len(contents)):
                fin2.write(contents[ii])

    def __stru_fix(self, stru) -> None:
        fix_dict = {"true": True, "false": False}
        fix_xyz = [fix_dict[i] for i in self.add_fix]
        abacus_utils.stru_fix_atom(stru, fix_atom=fix_xyz)

    def post_process(self, task_list):
        try:
            add_fix = self.add_fix
        except AttributeError:
            add_fix = None

        if add_fix:
            count = 0
            for ii in task_list:
                count += 1
                self._fix_task_output(ii, count == 1)

    def task_type(self):
        return self.parameter["type"]

    def task_param(self):
        return self.parameter

    def _compute_lower(self, output_file, all_tasks, all_res):
        output_file = os.path.abspath(output_file)
        res_data = {}
        ptr_data = os.path.dirname(output_file) + "\n"

        if not self.reprod:
            ptr_data += (
                "No_task: \tFrac_X\tFrac_Y\tDisp_X(\\AA)\tDisp_Y(\\AA)\t"
                "Stacking_Fault_E(J/m^2)\tEpA(eV)\tslab_equi_EpA(eV)\n"
            )
            all_tasks.sort()
            task_result_slab_equi = loadfn(os.path.join(all_tasks[0], "result_task.json"))
            slip_length_x = loadfn(os.path.join(all_tasks[0], "slip_length_x.json"))
            slip_length_y = loadfn(os.path.join(all_tasks[0], "slip_length_y.json"))
            equi_path = os.path.abspath(
                os.path.join(os.path.dirname(output_file), "../relaxation/relax_task")
            )
            equi_result = loadfn(os.path.join(equi_path, "result.json"))
            equi_epa = equi_result["energies"][-1] / np.sum(equi_result["atom_numbs"])

            for ii in all_tasks:
                task_result = loadfn(os.path.join(ii, "result_task.json"))
                natoms = np.sum(task_result["atom_numbs"])
                epa = task_result["energies"][-1] / natoms
                equi_epa_slab = task_result_slab_equi["energies"][-1] / natoms
                area = np.linalg.norm(
                    np.cross(task_result["cells"][0][0], task_result["cells"][0][1])
                )

                structure_dir = os.path.basename(ii)
                disp_info = loadfn(os.path.join(ii, "displacement.json"))
                frac_x = float(disp_info["frac_x"])
                frac_y = float(disp_info["frac_y"])
                cf = 1.60217657e-16 / 1e-20 * 0.001
                sfe = (
                    (task_result["energies"][-1] - task_result_slab_equi["energies"][-1])
                    / area
                    * cf
                )
                miller_index = loadfn(os.path.join(ii, "miller.json"))
                disp_x = slip_length_x * frac_x
                disp_y = slip_length_y * frac_y
                ptr_data += (
                    "%-25s  %7.3f  %7.3f  %7.3f  %7.3f  %7.3f  %8.3f %8.3f\n"
                    % (
                        str(miller_index) + "-" + structure_dir + ":",
                        frac_x,
                        frac_y,
                        disp_x,
                        disp_y,
                        sfe,
                        epa,
                        equi_epa_slab,
                    )
                )
                res_data[f"{frac_x:.6f},{frac_y:.6f}"] = [
                    disp_x,
                    disp_y,
                    sfe,
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
