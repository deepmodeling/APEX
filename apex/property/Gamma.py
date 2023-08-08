import glob
import json
import os
import re

import dpdata
import numpy as np
from ase.lattice.cubic import SimpleCubic as sc
from ase.lattice.cubic import BodyCenteredCubic as bcc
from ase.lattice.cubic import FaceCenteredCubic as fcc
from monty.serialization import dumpfn, loadfn
from pymatgen.core.structure import Structure
from pymatgen.core.surface import SlabGenerator
from pymatgen.io.ase import AseAtomsAdaptor

import apex.calculator.lib.abacus as abacus
import apex.calculator.lib.vasp as vasp
from apex.property.Property import Property
from apex.property.refine import make_refine
from apex.property.reproduce import make_repro, post_repro
from apex.property.Structure import StructureInfo
from apex.property.lib.slab_orientation import SlabSlipSystem
from apex.property.lib.trans_tools import trans_mat_basis
from dflow.python import upload_packages
upload_packages.append(__file__)


class Gamma(Property):
    """
    Calculation of common gamma lines for bcc and fcc
    """

    def __init__(self, parameter, inter_param=None):
        parameter["reproduce"] = parameter.get("reproduce", False)
        self.reprod = parameter["reproduce"]
        if not self.reprod:
            if not ("init_from_suffix" in parameter and "output_suffix" in parameter):
                self.parameter = parameter
                parameter["supercell_size"] = parameter.get("supercell_size", (1, 1, 5))
                self.supercell_size = parameter["supercell_size"]
                parameter["min_vacuum_size"] = parameter.get("min_vacuum_size", 0)
                self.min_vacuum_size = parameter["min_vacuum_size"]
                parameter["add_fix"] = parameter.get(
                    "add_fix", ["true", "true", "false"]
                )  # standard method
                self.add_fix = parameter["add_fix"]
                parameter["n_steps"] = parameter.get("n_steps", 10)
                self.n_steps = parameter["n_steps"]
                self.atom_num = None
            parameter["cal_type"] = parameter.get("cal_type", "relaxation")
            self.cal_type = parameter["cal_type"]
            default_cal_setting = {
                "relax_pos": True,
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
        path_to_work = os.path.abspath(path_to_work)
        if os.path.exists(path_to_work):
            print("%s already exists" % path_to_work)
            #dlog.warning("%s already exists" % path_to_work)
        else:
            os.makedirs(path_to_work)
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
            struct_output_name = path_to_work.split("/")[-2]
            assert struct_output_name in struct_init_name_list
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
            print("gamma line reproduce starts")
            if "init_data_path" not in self.parameter:
                raise RuntimeError("please provide the initial data path to reproduce")
            init_data_path = os.path.abspath(self.parameter["init_data_path"])
            task_list = make_repro(
                init_data_path,
                self.init_from_suffix,
                path_to_work,
                self.parameter.get("reprod_last_frame", True),
            )
            os.chdir(cwd)

        else:
            if refine:
                print("gamma line refine starts")
                task_list = make_refine(
                    self.parameter["init_from_suffix"],
                    self.parameter["output_suffix"],
                    path_to_work,
                )
                os.chdir(cwd)
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
                    if os.path.isfile("miller.json"):
                        os.remove("miller.json")
                    if os.path.islink("miller.json"):
                        os.remove("miller.json")
                    os.symlink(
                        os.path.relpath(os.path.join(init_from_task, "miller.json")),
                        "miller.json",
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
                print(
                    "we now only support gamma line calculation for BCC FCC and HCP metals"
                )
                #print(
                #    f"supported slip systems are:\n{SlabSlipSystem.hint_string()}"
                #)

                if self.inter_param["type"] == "abacus":
                    stru = dpdata.System(equi_contcar, fmt="stru")
                    stru.to("contcar", "CONTCAR.tmp")
                    ptypes = vasp.get_poscar_types("CONTCAR.tmp")
                    ss = Structure.from_file("CONTCAR.tmp")
                    os.remove("CONTCAR.tmp")
                else:
                    ptypes = vasp.get_poscar_types(equi_contcar)
                    # read structure from relaxed CONTCAR
                    ss = Structure.from_file(equi_contcar)

                # get structure type
                st = StructureInfo(ss)
                self.structure_type = st.lattice_structure

                # rewrite new CONTCAR with direct coords
                os.chdir(path_to_equi)
                ss.to("CONTCAR.direct", "POSCAR")
                # re-read new CONTCAR
                ss = Structure.from_file("CONTCAR.direct")
                relax_a = ss.lattice.a
                relax_b = ss.lattice.b
                relax_c = ss.lattice.c
                # gen initial slab
                if self.structure_type in ['bcc', 'fcc', 'hcp']:
                    (plane_miller, x_miller_index,
                     xy_miller_index, frac_slip_vec) = self.__return_slip_system_millers()

                    slab = self.__gen_slab_pmg(ss, plane_miller, x_miller_index,
                                               xy_miller_index, is_reorient_slab=True)
                else:
                    raise RuntimeError(f'unsupported crystal structure '
                                       f'for Gamma line function: {self.structure_type}')

                os.chdir(path_to_work)
                if os.path.isfile(POSCAR):
                    os.remove(POSCAR)
                if os.path.islink(POSCAR):
                    os.remove(POSCAR)
                os.symlink(os.path.relpath(equi_contcar), POSCAR)
                #           task_poscar = os.path.join(output, 'POSCAR')
                count = 0
                # define slip vector
                try:
                    frac_slip_vec = np.array([frac_slip_vec[0], frac_slip_vec[1], 0]) * relax_a
                except TypeError or IndexError:
                    raise RuntimeError('A list array with minimal length of 2 should be input as a slip vector')
                # get displaced structure
                for obtained_slab in self.__displace_slab_generator(slab,
                                                                    disp_vector=frac_slip_vec,
                                                                    is_frac=False):
                    output_task = os.path.join(path_to_work, "task.%06d" % count)
                    os.makedirs(output_task, exist_ok=True)
                    os.chdir(output_task)
                    for jj in ["INCAR", "POTCAR", POSCAR, "conf.lmp", "in.lammps"]:
                        if os.path.exists(jj):
                            os.remove(jj)
                    task_list.append(output_task)
                    # print("# %03d generate " % ii, output_task)
                    print(
                        "# %03d generate " % count,
                        output_task,
                        " \t %d atoms" % len(obtained_slab.sites)
                    )
                    # make confs
                    obtained_slab.to("POSCAR.tmp", "POSCAR")
                    vasp.regulate_poscar("POSCAR.tmp", "POSCAR")
                    vasp.sort_poscar("POSCAR", "POSCAR", ptypes)
                    if self.inter_param["type"] == "abacus":
                        abacus.poscar2stru("POSCAR", self.inter_param, "STRU")
                        os.remove("POSCAR")
                    # vasp.perturb_xz('POSCAR', 'POSCAR', self.pert_xz)
                    # record miller
                    dumpfn(self.plane_miller, "miller.json")
                    count += 1
                os.chdir(cwd)

        return task_list

    @staticmethod
    def centralize_slab(slab) -> None:
        z_pos_list = list(set([site.position[2] for site in slab]))
        z_pos_list.sort()
        central_atoms = (z_pos_list[-1] - z_pos_list[0]) / 2
        central_cell = slab.cell[2][2] / 2
        disp_length = central_cell - central_atoms
        for site in slab:
            site.position[2] += disp_length

    def __return_slip_system_millers(self):
        dict_directions = SlabSlipSystem.atomic_system_dict()
        # check if support relaxed structure and get specific pre-defined direction dict
        try:
            system = dict_directions[self.structure_type]
        except KeyError:
            raise KeyError(
                f"{self.structure_type} structure is not supported for stacking fault (Gamma) calculation\n"
                f"Currently support: {' '.join(dict_directions.keys())}"
            )

        # get user input slip parameter for specific structure
        self.plane_miller = self.parameter.get("plane_miller", None)
        self.primary_direction = self.parameter.get("primary_direction", None)
        self.frac_slip_vector = self.parameter.get("plane_slip_vector", None)
        type_param = self.parameter.get(self.structure_type, None)
        if type_param:
            self.plane_miller = type_param.get("plane_miller", None)
            self.primary_direction = type_param.get("primary_direction", None)
            self.frac_slip_vector = type_param.get("plane_slip_vector", None)
        if not [self.plane_miller or self.primary_direction]:
            raise RuntimeError(f'fail to get slip plane and direction of '
                               f'{self.structure_type} structure from input json file')

        # get search key string
        plane_str = ''.join([str(i) for i in self.plane_miller])
        slip_str = ''.join([str(i) for i in self.primary_direction])
        combined_key = 'x'.join([plane_str, slip_str])

        # check and get slip miller index info
        try:
            plane_miller, x_miller, xy_miller, frac_slip_vector = system[combined_key].values()
        except KeyError:
            raise KeyError(
                f"Unsupported input combination of miller index and displacement direction:"
                f"{plane_str}:{slip_str}"
                f"Currently support following slip plane and direction:\n" + SlabSlipSystem.hint_string()
            )

        if self.frac_slip_vector:
            frac_slip_vector = self.frac_slip_vector

        return plane_miller, x_miller, xy_miller, frac_slip_vector

    def __gen_slab_pmg(self, structure, plane_miller,
                       x_miller_index, xy_miller_index,
                       is_reorient_slab=False):
       slabGen = SlabGenerator(structure, miller_index=plane_miller,
                               min_slab_size=self.supercell_size[2],
                               min_vacuum_size=self.min_vacuum_size,
                               center_slab=True, in_unit_planes=True,
                               lll_reduce=True, reorient_lattice=False,
                               primitive=False)
       slabs_pmg = slabGen.get_slabs(ftol=0.001)
       slab = [s for s in slabs_pmg if s.miller_index == tuple(plane_miller)][0]
       # If is_reorient_slab is True, reorient the slab such that x direction
       # is along x_miller_index and z direction is along the normal to the slab
       if is_reorient_slab:
           # Express x_miller_index and xy_miller_index
           # in the conventional standard cartesian coordinate system
           l2_normalize_1d_np_vec = lambda v: v / np.linalg.norm(v, 2)
           x_cartesian = np.zeros(3)
           xy_cartesian = np.zeros(3)
           for idx in range(0, 3):
               x_cartesian = x_cartesian + x_miller_index[idx] * structure.lattice.matrix[idx]
               xy_cartesian = xy_cartesian + xy_miller_index[idx] * structure.lattice.matrix[idx]

           x_cartesian_unit_vector = l2_normalize_1d_np_vec(x_cartesian)
           z_cartesian_unit_vector = l2_normalize_1d_np_vec(np.cross(x_cartesian,
                                                                     xy_cartesian))
           y_cartesian_unit_vector = l2_normalize_1d_np_vec(np.cross(z_cartesian_unit_vector,
                                                                     x_cartesian_unit_vector))
           reoriented_basis = np.array([x_cartesian_unit_vector,
                                        y_cartesian_unit_vector,
                                        z_cartesian_unit_vector])
           # Transform the lattice vectors of the slab
           Q = trans_mat_basis(reoriented_basis)
           reoriented_lattice_vectors = [Q.dot(v) for v in slab.lattice.matrix]
           slab = Structure(lattice=np.matrix(reoriented_lattice_vectors),
                            coords=slab.frac_coords, species=slab.species)

       # Order the atoms in the lattice in the increasing order of the third lattice direction
       #n_atoms_slab = len(slab.frac_coords)
       order = zip(slab.frac_coords, slab.species)
       c_order = sorted(order, key=lambda x: x[0][2])
       sorted_frac_coords = []
       sorted_species = []
       for (frac_coord, species) in c_order:
           sorted_frac_coords.append(frac_coord)
           sorted_species.append(species)
       slab_lattice = slab.lattice
       slab = Structure(lattice=slab_lattice, coords=sorted_frac_coords, species=sorted_species)

       # Slab area
       #slab_area = np.linalg.norm(np.cross(slab.lattice.matrix[0], slab.lattice.matrix[1]))

       slab.make_supercell(scaling_matrix=[self.supercell_size[0], self.supercell_size[1], 1])
       return slab

    def __displace_slab_generator(self, slab, disp_vector,
                                  is_frac=True, to_unit_cell=True):
        # generator of displaced slab structures
        yield slab.copy()
        # return list of atoms number to be displaced which above 0.5 z
        disp_atoms_list = np.where(slab.frac_coords[:, 2] > 0.5)[0]
        for _ in list(range(self.n_steps)):
            frac_disp = 1 / self.n_steps
            unit_vector = frac_disp * np.array(disp_vector)
            slab.translate_sites(
                indices=disp_atoms_list,
                vector=unit_vector,
                frac_coords=is_frac,
                to_unit_cell=to_unit_cell,
            )
            yield slab.copy()

    def __poscar_fix(self, poscar) -> None:
        # add position fix condition of x and y in POSCAR
        insert_pos = -self.atom_num
        fix_dict = {"true": "F", "false": "T"}
        add_fix_str = (
            " "
            + fix_dict[self.add_fix[0]]
            + " "
            + fix_dict[self.add_fix[1]]
            + " "
            + fix_dict[self.add_fix[2]]
            + "\n"
        )
        with open(poscar, "r") as fin1:
            contents = fin1.readlines()
            contents.insert(insert_pos - 1, "Selective dynamics\n")
            for ii in range(insert_pos, 0, 1):
                contents[ii] = contents[ii].replace("\n", "")
                content_split = contents[ii].split(' ')
                if len(content_split[-1]) < 3:
                    content_split.pop()
                contents[ii] = ' '.join(content_split)
                contents[ii] += add_fix_str
        with open(poscar, "w") as fin2:
            for ii in range(len(contents)):
                fin2.write(contents[ii])

    def __stru_fix(self, stru) -> None:
        fix_dict = {"true": True, "false": False}
        fix_xyz = [fix_dict[i] for i in self.addfix]
        abacus.stru_fix_atom(stru, fix_atom=fix_xyz)

    def __inLammpes_fix(self, inLammps) -> None:
        # add position fix condition of x and y of in.lammps
        fix_dict = {"true": "0", "false": "NULL"}
        add_fix_str = (
            "fix             1 all setforce"
            + " "
            + fix_dict[self.add_fix[0]]
            + " "
            + fix_dict[self.add_fix[1]]
            + " "
            + fix_dict[self.add_fix[2]]
            + "\n"
        )
        with open(inLammps, "r") as fin1:
            contents = fin1.readlines()
            for ii in range(len(contents)):
                upper = re.search("variable        N equal count\(all\)", contents[ii])
                lower = re.search("min_style       cg", contents[ii])
                if lower:
                    lower_id = ii
                    # print(lower_id)
                elif upper:
                    upper_id = ii
                    # print(upper_id)
            del contents[lower_id + 1 : upper_id - 1]
            contents.insert(lower_id + 1, add_fix_str)
        with open(inLammps, "w") as fin2:
            for ii in range(len(contents)):
                fin2.write(contents[ii])

    def post_process(self, task_list):
        if self.add_fix:
            count = 0
            for ii in task_list:
                count += 1
                inter = os.path.join(ii, "inter.json")
                poscar = os.path.join(ii, "POSCAR")
                calc_type = loadfn(inter)["type"]
                if calc_type == "vasp":
                    self.__poscar_fix(poscar)
                elif calc_type == "abacus":
                    self.__stru_fix(os.path.join(ii, "STRU"))
                else:
                    inLammps = os.path.join(ii, "in.lammps")
                    if count == 1:
                        self.__inLammpes_fix(inLammps)

    def task_type(self):
        return self.parameter["type"]

    def task_param(self):
        return self.parameter

    def _compute_lower(self, output_file, all_tasks, all_res):
        output_file = os.path.abspath(output_file)
        res_data = {}
        ptr_data = os.path.dirname(output_file) + "\n"

        if not self.reprod:
            """
            ptr_data += (
                str(tuple(self.miller_index))
                + " plane along "
                + str(self.displace_direction)
            )
            """
            ptr_data += "No_task: \tDisplacement \tStacking_Fault_E(J/m^2) EpA(eV) slab_equi_EpA(eV)\n"
            all_tasks.sort()
            task_result_slab_equi = loadfn(
                os.path.join(all_tasks[0], "result_task.json")
            )
            for ii in all_tasks:
                task_result = loadfn(os.path.join(ii, "result_task.json"))
                natoms = np.sum(task_result["atom_numbs"])
                epa = task_result["energies"][-1] / natoms
                equi_epa_slab = task_result_slab_equi["energies"][-1] / natoms
                AA = np.linalg.norm(
                    np.cross(task_result["cells"][0][0], task_result["cells"][0][1])
                )

                equi_path = os.path.abspath(
                    os.path.join(
                        os.path.dirname(output_file), "../relaxation/relax_task"
                    )
                )
                equi_result = loadfn(os.path.join(equi_path, "result.json"))
                equi_epa = equi_result["energies"][-1] / np.sum(
                    equi_result["atom_numbs"]
                )
                structure_dir = os.path.basename(ii)

                Cf = 1.60217657e-16 / 1e-20 * 0.001
                sfe = (
                    (
                        task_result["energies"][-1]
                        - task_result_slab_equi["energies"][-1]
                    )
                    / AA
                    * Cf
                )

                miller_index = loadfn(os.path.join(ii, "miller.json"))
                ptr_data += "%-25s     %7.2f   %7.3f    %8.3f %8.3f\n" % (
                    str(miller_index) + "-" + structure_dir + ":",
                    int(ii[-4:]) / self.n_steps,
                    sfe,
                    epa,
                    equi_epa_slab,
                )
                res_data[int(ii[-4:]) / self.n_steps] = [sfe, epa, equi_epa]

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
