import glob
import hashlib
import json
import os
import re
import logging

import dpdata
import numpy as np
from monty.serialization import dumpfn, loadfn
from pymatgen.core.structure import Structure

from apex.core.calculator.lib import abacus_utils
from apex.core.calculator.lib import vasp_utils
from apex.core.property.Property import Property, is_failed_task_result
from apex.core.property.gamma_slab import get_first_gamma_slab
from apex.core.property.gamma_slab import make_gamma_slab_generator
from apex.core.property.gamma_slab import validate_gamma_slab_settings
from apex.core.property.gamma_slab import validate_generated_gamma_slab
from apex.core.refine import make_refine
from apex.core.reproduce import make_repro, post_repro
from apex.core.structure import (
    StructureInfo,
    normalize_parent_lattice_hint,
    resolve_parent_lattice_hint,
)
from apex.core.lib.slab_orientation import SlabSlipSystem
from apex.core.lib.parent_lattice_mapping import (
    resolve_parent_slip_geometry,
    resolve_parent_supercell,
)
from apex.core.property.gamma_geometry import (
    build_parent_gamma_slab,
    validate_gamma_cell_geometry,
    validate_vacuum_size,
)
from apex.core.lib.trans_tools import trans_mat_basis
from apex.core.lib.trans_tools import (plane_miller_bravais_to_miller,
                                       direction_miller_bravais_to_miller)
from dflow.python import upload_packages

upload_packages.append(__file__)


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_displacement_points(values):
    if values is None:
        return None
    if not isinstance(values, (list, tuple)):
        raise ValueError("gamma displacement_points must be a list")
    points = [float(value) for value in values]
    if (
        not points
        or any(not np.isfinite(value) or value < 0.0 or value > 1.0 for value in points)
        or len(set(points)) != len(points)
        or 0.0 not in points
    ):
        raise ValueError(
            "gamma displacement_points must include 0 and contain unique values in [0, 1]"
        )
    return sorted(points)


def _validate_n_steps(value):
    if (
        isinstance(value, (bool, np.bool_))
        or not isinstance(value, (int, np.integer))
        or value <= 0
    ):
        raise ValueError("gamma n_steps must be a positive integer")
    return int(value)


def _resolve_require_orthogonal_cell(parameter, default=False):
    explicit = parameter.get("require_orthogonal_cell", None)
    legacy_alias = parameter.get("orthogonalize_cell", None)
    for key, candidate in (
        ("require_orthogonal_cell", explicit),
        ("orthogonalize_cell", legacy_alias),
    ):
        if candidate is not None and not isinstance(candidate, (bool, np.bool_)):
            raise ValueError(f"gamma {key} must be a boolean")
    if explicit is not None and legacy_alias is not None and explicit != legacy_alias:
        raise ValueError(
            "gamma require_orthogonal_cell and orthogonalize_cell disagree"
        )
    value = explicit if explicit is not None else legacy_alias
    if value is None:
        value = default
    if legacy_alias:
        logging.info(
            "gamma orthogonalize_cell=true is a strict geometry gate; APEX "
            "does not Gram-Schmidt periodic cells"
        )
    parameter["require_orthogonal_cell"] = bool(value)
    return bool(value)


class Gamma(Property):
    """
    Calculation of gamma lines
    """

    def __init__(self, parameter, inter_param=None):
        self.parent_lattice = normalize_parent_lattice_hint(
            parameter.get("parent_lattice")
        )
        if self.parent_lattice is not None:
            parameter["parent_lattice"] = self.parent_lattice
        self.parent_max_site_distance = float(
            parameter.get("parent_max_site_distance", 0.65)
        )
        if not np.isfinite(self.parent_max_site_distance) or self.parent_max_site_distance <= 0:
            raise ValueError("gamma parent_max_site_distance must be finite and positive")
        parameter["parent_max_site_distance"] = self.parent_max_site_distance
        parameter["reproduce"] = parameter.get("reproduce", False)
        self.reprod = parameter["reproduce"]
        if not self.reprod:
            if not ("init_from_suffix" in parameter and "output_suffix" in parameter):
                parameter["cal_type"] = parameter.get("cal_type", "relaxation")
                parameter["plane_miller"] = parameter.get("plane_miller", None)
                self.plane_miller = parameter["plane_miller"]
                parameter["slip_direction"] = parameter.get("slip_direction", None)
                self.slip_direction = parameter["slip_direction"]
                parameter["slip_length"] = parameter.get("slip_length", None)
                self.slip_length = parameter["slip_length"]
                parameter["plane_shift"] = parameter.get("plane_shift", 0)
                self.plane_shift = parameter["plane_shift"]
                parameter["supercell_size"] = parameter.get("supercell_size", (1, 1, 5))
                self.supercell_size = parameter["supercell_size"]
                parameter["min_slab_height"] = parameter.get(
                    "min_slab_height", None
                )
                self.min_slab_height = parameter["min_slab_height"]
                parameter["max_atoms"] = parameter.get("max_atoms", None)
                self.max_atoms = parameter["max_atoms"]
                parameter["min_distance"] = parameter.get("min_distance", 0.2)
                self.min_distance = parameter["min_distance"]
                parameter["vacuum_size"] = parameter.get("vacuum_size", 20)
                self.vacuum_size = validate_vacuum_size(
                    parameter["vacuum_size"], "Gamma"
                )
                parameter["vacuum_size"] = self.vacuum_size
                self.require_orthogonal_cell = _resolve_require_orthogonal_cell(
                    parameter
                )
                if parameter["cal_type"] == "static" and "add_fix" not in parameter:
                    parameter["add_fix"] = None
                else:
                    parameter["add_fix"] = parameter.get(
                        "add_fix", ["true", "true", "false"]
                    )  # standard method
                self.add_fix = parameter["add_fix"]
                parameter["n_steps"] = parameter.get("n_steps", 10)
                self.n_steps = _validate_n_steps(parameter["n_steps"])
                parameter["n_steps"] = self.n_steps
                parameter["displacement_points"] = parameter.get(
                    "displacement_points", None
                )
                self.displacement_points = _validate_displacement_points(
                    parameter["displacement_points"]
                )
                parameter["displacement_points"] = self.displacement_points
                self.atom_num = None
            else:
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

        else:
            if refine:
                print("gamma line refine starts")
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
                    raise RuntimeError(
                        "Gamma requires a baseline relaxation before PropsMake. "
                        "For static gamma scans, run a static baseline relaxation "
                        "with relax_pos=false, relax_shape=false, and relax_vol=false."
                    )
                equi_result = os.path.join(path_to_equi, "result.json")
                if not os.path.exists(equi_result):
                    raise RuntimeError(
                        "Gamma post-processing requires relaxation/relax_task/result.json. "
                        "Please provide a static baseline relaxation result before "
                        "running gamma PropsMake."
                    )
                # print("we now only support gamma line calculation for BCC FCC and HCP metals")
                # print(
                #    f"supported slip systems are:\n{SlabSlipSystem.hint_string()}"
                # )

                if self.inter_param["type"] == "abacus":
                    stru = dpdata.System(equi_contcar, fmt="stru")
                    stru.to("contcar", "CONTCAR.tmp")
                    ptypes = vasp_utils.get_poscar_types("CONTCAR.tmp")
                    ss = Structure.from_file("CONTCAR.tmp")
                    os.remove("CONTCAR.tmp")
                else:
                    ptypes = vasp_utils.get_poscar_types(equi_contcar)
                    # read structure from relaxed CONTCAR
                    ss = Structure.from_file(equi_contcar)

                # rewrite new CONTCAR with direct coords
                os.chdir(path_to_equi)
                ss.to("CONTCAR.direct", "POSCAR")
                # re-read new CONTCAR
                ss = Structure.from_file("CONTCAR.direct")
                # get structure type
                st = StructureInfo(ss)
                self.detected_structure_type = st.lattice_structure
                self.structure_type, self.structure_type_source = (
                    resolve_parent_lattice_hint(
                        self.detected_structure_type, self.parent_lattice
                    )
                )
                if self.structure_type_source == "user_override":
                    logging.info(
                        "Gamma parent_lattice=%s overrides automatic structure "
                        "classification %s without changing the input geometry.",
                        self.structure_type,
                        self.detected_structure_type,
                    )
                # A parent_lattice hint changes the crystallographic index
                # basis, not merely the parameter block. Preserve the exact
                # relaxed RSS row basis so the inferred integer supercell
                # mapping remains traceable from POSCAR to CONTCAR.
                self.conv_std_structure = (
                    ss if self.parent_lattice is not None else st.conventional_structure
                )
                relax_a = self.conv_std_structure.lattice.a
                relax_b = self.conv_std_structure.lattice.b
                relax_c = self.conv_std_structure.lattice.c
                # get user input slip parameter for specific structure
                type_param = self.parameter.get(self.structure_type, None)
                if type_param:
                    self.plane_miller = type_param.get("plane_miller", self.plane_miller)
                    self.slip_direction = type_param.get("slip_direction", self.slip_direction)
                    self.slip_length = type_param.get("slip_length", self.slip_length)
                    self.plane_shift = type_param.get("plane_shift", self.plane_shift)
                    self.supercell_size = type_param.get("supercell_size", self.supercell_size)
                    self.min_slab_height = type_param.get(
                        "min_slab_height", self.min_slab_height
                    )
                    self.max_atoms = type_param.get("max_atoms", self.max_atoms)
                    self.min_distance = type_param.get(
                        "min_distance", self.min_distance
                    )
                    self.vacuum_size = type_param.get("vacuum_size", self.vacuum_size)
                    self.require_orthogonal_cell = _resolve_require_orthogonal_cell(
                        type_param, self.require_orthogonal_cell
                    )
                    self.add_fix = type_param.get("add_fix", self.add_fix)
                    self.n_steps = type_param.get("n_steps", self.n_steps)
                    self.displacement_points = type_param.get(
                        "displacement_points", self.displacement_points
                    )
                self.displacement_points = _validate_displacement_points(
                    self.displacement_points
                )
                self.vacuum_size = validate_vacuum_size(
                    self.vacuum_size, "Gamma"
                )
                self.n_steps = _validate_n_steps(self.n_steps)
                if not (self.plane_miller and self.slip_direction):
                    raise RuntimeError(f'fail to obtain both slip plane '
                                       f'and slip direction info from input json file!')

                if not self.structure_type in ['bcc', 'fcc', 'hcp']:
                    logging.warning(
                        f'Gamma line function for {self.structure_type} '
                        f'is not fully supported so far.\n'
                        f'You may need to double check the generated slab structures'
                    )
                (
                    self.supercell_size,
                    self.min_slab_height,
                    self.max_atoms,
                    self.min_distance,
                ) = validate_gamma_slab_settings(
                    self.supercell_size,
                    self.min_slab_height,
                    self.max_atoms,
                    self.min_distance,
                )
                # Generate the initial slab. Disordered parent-mapped cells use
                # the user-facing parent indices and an automatically inferred
                # parent-supercell mapping. Legacy perfect-crystal inputs retain
                # the established path.
                self.parent_mapping = None
                self.parent_slip_geometry = None
                self.gamma_geometry = None
                if self.parent_lattice is not None:
                    self.parent_mapping = resolve_parent_supercell(
                        self.conv_std_structure,
                        self.parent_lattice,
                        max_site_distance=self.parent_max_site_distance,
                    )
                    self.parent_slip_geometry = resolve_parent_slip_geometry(
                        self.conv_std_structure,
                        self.parent_mapping,
                        self.plane_miller,
                        self.slip_direction,
                        self.slip_length,
                    )
                    built = build_parent_gamma_slab(
                        self.conv_std_structure,
                        self.parent_slip_geometry,
                        self.supercell_size,
                        self.supercell_size[2],
                        self.min_slab_height,
                        self.vacuum_size,
                        self.plane_shift,
                        self.require_orthogonal_cell,
                    )
                    slab = built.slab
                    self._gamma_upper_indices = built.upper_indices
                    self._gamma_lower_indices = built.lower_indices
                    self._slab_generation_metadata = built.generation_metadata
                    self.gamma_geometry = {
                        "parent_mapping": self.parent_mapping.as_dict(),
                        "slip_geometry": self.parent_slip_geometry.as_dict(),
                        "slab_geometry": built.metadata,
                        "source_structure": {
                            "path": os.path.abspath(equi_contcar),
                            "sha256": _sha256_file(equi_contcar),
                            "atom_count": len(self.conv_std_structure),
                        },
                    }
                    self.slab_generation = validate_generated_gamma_slab(
                        slab,
                        built.generation_metadata,
                        self.supercell_size[:2],
                        self.max_atoms,
                        self.min_distance,
                        "Gamma",
                    )
                    self.slab_generation.update(built.metadata)
                    slip_length = float(
                        np.linalg.norm(
                            self.parent_slip_geometry.burgers_vector_cart
                        )
                    )
                else:
                    (plane_miller, slip_direction,
                     slip_length, Q) = self.__convert_input_miller(self.conv_std_structure)
                    slab = self.__gen_slab_pmg(
                        self.conv_std_structure, plane_miller, trans_matrix=Q
                    )
                    cell_geometry = validate_gamma_cell_geometry(
                        slab,
                        require_orthogonal=self.require_orthogonal_cell,
                        property_name="Gamma",
                    )
                    self.slab_generation.update(
                        {
                            "interface_count": 1 if self.vacuum_size > 0 else 2,
                            "cell_geometry": cell_geometry,
                            "require_orthogonal_cell": self.require_orthogonal_cell,
                        }
                    )
                    self.gamma_geometry = {
                        "parent_mapping": None,
                        "slip_geometry": None,
                        "slab_geometry": {
                            "interface_count": self.slab_generation["interface_count"],
                            "added_vacuum_angstrom": self.vacuum_size,
                            "cell_geometry": cell_geometry,
                            "require_orthogonal_cell": self.require_orthogonal_cell,
                        },
                        "source_structure": {
                            "path": os.path.abspath(equi_contcar),
                            "sha256": _sha256_file(equi_contcar),
                            "atom_count": len(self.conv_std_structure),
                        },
                    }
                self.atom_num = len(slab.sites)
                self.slab_generation.update(
                    {
                        "detected_structure_type": self.detected_structure_type,
                        "effective_parent_lattice": self.structure_type,
                        "structure_type_source": self.structure_type_source,
                    }
                )
                dumpfn(
                    self.slab_generation,
                    os.path.join(path_to_work, "slab_generation.json"),
                )
                dumpfn(
                    self.gamma_geometry,
                    os.path.join(path_to_work, "gamma_geometry.json"),
                )

                os.chdir(path_to_work)
                if os.path.exists(POSCAR):
                    os.remove(POSCAR)
                os.symlink(os.path.relpath(equi_contcar), POSCAR)
                # task_poscar = os.path.join(output, 'POSCAR')
                count = 0
                # define slip vector
                if self.parent_slip_geometry is not None:
                    frac_slip_vec = (
                        self.parent_slip_geometry.local_frame
                        @ self.parent_slip_geometry.burgers_vector_cart
                    )
                elif type(slip_length) == int or type(slip_length) == float:
                    frac_slip_vec = np.array([slip_length, 0, 0]) * relax_a
                else:
                    # for Sequence[int|float, int|float, int|float] type
                    try:
                        slip_vector_cartesian = np.multiply(np.array(slip_length),
                                                            np.array([relax_a, relax_b, relax_c]))
                        norm_length = np.linalg.norm(slip_vector_cartesian, 2)
                        frac_slip_vec = np.array([norm_length, 0, 0])
                    except Exception:
                        raise RuntimeError(
                            'Only int | float or '
                            'Sequence[int | float, int | float, int | float] is allowed for the input_length'
                        )
                self.slip_length = frac_slip_vec[0]
                # get displaced structure
                for frac, obtained_slab in self.__displace_slab_generator(
                    slab, disp_vector=frac_slip_vec, is_frac=False
                ):
                    validate_generated_gamma_slab(
                        obtained_slab,
                        self._slab_generation_metadata,
                        self.supercell_size[:2],
                        self.max_atoms,
                        self.min_distance,
                        "Gamma task",
                    )
                    output_task = os.path.join(path_to_work, "task.%06d" % count)
                    os.makedirs(output_task, exist_ok=True)
                    os.chdir(output_task)
                    for jj in ["INCAR", "POTCAR", POSCAR, "conf.lmp", "in.lammps"]:
                        if os.path.exists(jj):
                            os.remove(jj)
                    task_list.append(output_task)
                    # print("# %03d generate " % ii, output_task)

                    logging.info(f"# {count} generate {output_task}, with {len(obtained_slab.sites)} atoms")

                    # make confs
                    obtained_slab.to("POSCAR.tmp", "POSCAR")
                    vasp_utils.regulate_poscar("POSCAR.tmp", "POSCAR")
                    vasp_utils.sort_poscar("POSCAR", "POSCAR", ptypes)
                    if self.inter_param["type"] == "abacus":
                        abacus_utils.poscar2stru("POSCAR", self.inter_param, "STRU")
                        #os.remove("POSCAR")
                    # vasp.perturb_xz('POSCAR', 'POSCAR', self.pert_xz)
                    # record miller
                    dumpfn(self.plane_miller, "miller.json")
                    dumpfn(self.slip_length, 'slip_length.json')
                    dumpfn(frac, "normalized_displacement.json")
                    if self.gamma_geometry is not None:
                        dumpfn(self.gamma_geometry, "gamma_geometry.json")
                    count += 1
        
        os.chdir(cwd)
        return task_list

    def __convert_input_miller(self, structure: Structure):
        plane_miller = tuple(self.plane_miller)
        slip_direction = tuple(self.slip_direction)
        slip_length = self.slip_length
        # get search key string
        plane_str = ''.join([str(i) for i in plane_miller])
        slip_str = ''.join([str(i) for i in slip_direction])
        combined_key = 'x'.join([plane_str, slip_str])
        l2_normalize_1d = lambda v: v / np.linalg.norm(v, 2)
        # Use only physically recommended slip systems for crystallographic
        # defaults. Other orthogonal systems remain available through the
        # geometry-only fallback below.
        dir_dict = SlabSlipSystem.recommended_system_dict()
        try:
            system = dir_dict[self.structure_type]
            (plane_miller, x_miller,
             xy_miller, stored_slip_length) = system[combined_key].values()
        except KeyError:
            logging.warning(
                'Warning:\n'
                'The input slip system is not one of the physically recommended '
                'FCC/BCC/HCP systems in README section 4.10.\n'
                'Gamma is falling back to a geometric construction and will only '
                'check that slip_direction lies on plane_miller. Double-check the '
                'generated slab, especially for HCP or structure type "other".'
            )
            x_miller = slip_direction
            if not slip_length:
                slip_length = 1
            if self.structure_type == 'hcp' and (len(self.plane_miller) == 4 or len(self.slip_direction) == 4):
                if len(plane_miller) == 4:
                    plane_miller = plane_miller_bravais_to_miller(self.plane_miller)
                if len(x_miller) == 4:
                    x_miller = direction_miller_bravais_to_miller(self.slip_direction)
            # check user input miller index
            dir_dot = np.array(plane_miller).dot(np.array(x_miller))
            if not dir_dot == 0:
                raise RuntimeError(f'slip direction {self.slip_direction} is not '
                                   f'on plane given {self.plane_miller}')
            # Express miller_index in the conventional standard cartesian coordinate system
            x_cartesian = np.dot(np.array(x_miller), structure.lattice.matrix)
            z_cartesian = np.dot(np.array(plane_miller), structure.lattice.matrix)
            x_cartesian_unit_vector = l2_normalize_1d(x_cartesian)
            y_cartesian_unit_vector = l2_normalize_1d(np.cross(z_cartesian, x_cartesian))
            z_cartesian_unit_vector = l2_normalize_1d(np.cross(x_cartesian_unit_vector,
                                                               y_cartesian_unit_vector))
        else:
            if not slip_length:
                slip_length = stored_slip_length
            x_cartesian = np.dot(np.array(x_miller), structure.lattice.matrix)
            xy_cartesian = np.dot(np.array(xy_miller), structure.lattice.matrix)
            x_cartesian_unit_vector = l2_normalize_1d(x_cartesian)
            z_cartesian_unit_vector = l2_normalize_1d(np.cross(x_cartesian, xy_cartesian))
            y_cartesian_unit_vector = l2_normalize_1d(np.cross(z_cartesian_unit_vector,
                                                                x_cartesian_unit_vector))
        reoriented_basis = np.array([x_cartesian_unit_vector,
                                    y_cartesian_unit_vector,
                                    z_cartesian_unit_vector])
        # Transform the lattice vectors of the slab
        Q = trans_mat_basis(reoriented_basis)

        return plane_miller, x_miller, slip_length, Q

    def __gen_slab_pmg(self, structure: Structure,
                       plane_miller, trans_matrix=None) -> Structure:
        slabGen, generation_metadata = make_gamma_slab_generator(
            structure,
            plane_miller,
            self.supercell_size[2],
            self.min_slab_height,
        )
        slab = get_first_gamma_slab(slabGen, ftol=0.001)
        # If a transform matrix is passed, reorient the slab
        if trans_matrix is not None and np.asarray(trans_matrix).any():
            reoriented_lattice_vectors = [trans_matrix.dot(v) for v in slab.lattice.matrix]
            slab = Structure(lattice=np.matrix(reoriented_lattice_vectors),
                             coords=slab.frac_coords, species=slab.species)
        # Order the atoms in the lattice in the increasing order of the third lattice direction
        # n_atoms_slab = len(slab.frac_coords)
        order = zip(slab.frac_coords, slab.species)
        c_order = sorted(order, key=lambda x: x[0][2])
        sorted_frac_coords = []
        sorted_species = []
        for (frac_coord, species) in c_order:
            sorted_frac_coords.append(frac_coord)
            sorted_species.append(species)
        # add vacuum layer to the slab with height unit of angstrom
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
        slab = Structure(lattice=np.matrix(new_lattice),
                         coords=new_frac_coords, species=sorted_species)
        # Slab area
        # slab_area = np.linalg.norm(np.cross(slab.lattice.matrix[0], slab.lattice.matrix[1]))
        # center the slab layer around the vacuum & add plane shift along z
        plane_shift_frac = self.plane_shift * structure.lattice.c / slab.lattice.matrix[2][2]
        avg_c = np.average([c[2] for c in slab.frac_coords])
        slab.translate_sites(list(range(len(slab))),
                             [0, 0, 0.5 - avg_c - plane_shift_frac])
        # replicate slab to make specific supercell
        slab.make_supercell(scaling_matrix=[self.supercell_size[0],
                                            self.supercell_size[1],
                                            1])
        self._slab_generation_metadata = generation_metadata
        self.slab_generation = validate_generated_gamma_slab(
            slab,
            generation_metadata,
            self.supercell_size[:2],
            self.max_atoms,
            self.min_distance,
            "Gamma",
        )
        self.slab_generation.update(
            {
                "min_slab_height": self.min_slab_height,
                "max_atoms": self.max_atoms,
                "min_distance_threshold": self.min_distance,
                "vacuum_size": self.vacuum_size,
            }
        )
        logging.info("Gamma slab generation: %s", self.slab_generation)
        return slab

    def __displace_slab_generator(self, slab: Structure,
                                  disp_vector, is_frac=True,
                                  to_unit_cell=True):
        """Yield normalized-displacement and slab pairs."""
        # return list of atoms number to be displaced which above 0.5 z
        disp_atoms_list = getattr(
            self,
            "_gamma_upper_indices",
            np.where(slab.frac_coords[:, 2] > 0.5)[0],
        )
        if self.displacement_points is not None:
            fractions = self.displacement_points
        else:
            fractions = [step / self.n_steps for step in range(self.n_steps + 1)]
        for frac in fractions:
            displaced = slab.copy()
            displaced.translate_sites(
                indices=disp_atoms_list,
                vector=frac * np.array(disp_vector),
                frac_coords=is_frac,
                to_unit_cell=to_unit_cell,
            )
            yield frac, displaced

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
        fix_xyz = [fix_dict[i] for i in self.add_fix]
        abacus_utils.stru_fix_atom(stru, fix_atom=fix_xyz)

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
            lower_id = None
            upper_id = None
            for ii in range(len(contents)):
                upper = contents[ii].split()[:3] == ["variable", "N", "equal"]
                lower = re.search("min_style       cg", contents[ii])
                if lower:
                    lower_id = ii
                    # print(lower_id)
                elif upper:
                    upper_id = ii
                    # print(upper_id)
            if lower_id is None or upper_id is None or lower_id >= upper_id:
                raise RuntimeError(
                    "Gamma add_fix was requested, but in.lammps does not contain "
                    "a compatible minimization block to patch. For static gamma "
                    "calculations set add_fix to null, or use a relaxation-style "
                    "LAMMPS input with min_style cg and variable N equal markers."
                )
            del contents[lower_id + 1:upper_id - 1]
            contents.insert(lower_id + 1, add_fix_str)
        with open(inLammps, "w") as fin2:
            for ii in range(len(contents)):
                fin2.write(contents[ii])

    def post_process(self, task_list):
        # for no exist of self.add_fix in refine mode, skip post_process
        try:
            add_fix = self.add_fix
        except AttributeError:
            add_fix = None

        if add_fix:
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
            ptr_data += "No_task: \tDisplacement \tDisplace_Length(\AA) \tStacking_Fault_E(J/m^2) EpA(eV) slab_equi_EpA(eV)\n"
            all_tasks.sort()
            n_steps = len(all_tasks) - 1
            task_result_slab_equi = loadfn(os.path.join(all_tasks[0], "result_task.json"))
            if is_failed_task_result(task_result_slab_equi):
                raise RuntimeError(
                    "gamma reference task "
                    f"{os.path.basename(all_tasks[0])} failed; cannot compute SFE"
                )
            slip_length = loadfn(os.path.join(all_tasks[0], "slip_length.json"))
            geometry_file = os.path.join(all_tasks[0], "gamma_geometry.json")
            interface_count = 1 if self.vacuum_size > 0 else 2
            if os.path.isfile(geometry_file):
                geometry = loadfn(geometry_file)
                interface_count = int(
                    geometry.get("slab_geometry", {}).get("interface_count", 1)
                )
                if interface_count not in (1, 2):
                    raise RuntimeError(
                        f"Invalid Gamma interface_count={interface_count}"
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
            ref_energy = task_result_slab_equi["energies"][-1]
            ref_natoms = np.sum(task_result_slab_equi["atom_numbs"])
            equi_epa_slab = ref_energy / ref_natoms
            reference_cell = np.asarray(task_result_slab_equi["cells"][0], dtype=float)
            reference_area = float(
                np.linalg.norm(np.cross(reference_cell[0], reference_cell[1]))
            )
            if not np.isfinite(reference_area) or reference_area <= 0.0:
                raise RuntimeError("Gamma reference task has an invalid in-plane area")
            for ii in all_tasks:
                structure_dir = os.path.basename(ii)
                frac_file = os.path.join(ii, "normalized_displacement.json")
                frac = loadfn(frac_file) if os.path.isfile(frac_file) else int(ii[-4:]) / n_steps
                miller_index = loadfn(os.path.join(ii, "miller.json"))
                task_result = loadfn(os.path.join(ii, "result_task.json"))
                if is_failed_task_result(task_result):
                    sfe = float("nan")
                    epa = float("nan")
                else:
                    natoms = np.sum(task_result["atom_numbs"])
                    epa = task_result["energies"][-1] / natoms
                    AA = np.linalg.norm(
                        np.cross(task_result["cells"][0][0], task_result["cells"][0][1])
                    )
                    if not np.isclose(
                        AA, reference_area, rtol=1.0e-8, atol=1.0e-8
                    ):
                        raise RuntimeError(
                            "Gamma task in-plane area changed relative to the "
                            f"u=0 reference: {AA:.12g} vs {reference_area:.12g} A^2"
                        )
                    Cf = 1.60217657e-16 / 1e-20 * 0.001
                    sfe = (
                        (task_result["energies"][-1] - ref_energy)
                        / (AA * interface_count)
                        * Cf
                    )
                ptr_data += "%-25s    %7.2f   %7.3f  %7.3f    %8.3f %8.3f\n" % (
                    str(miller_index) + "-" + structure_dir + ":",
                    frac,
                    (slip_length * frac),
                    sfe,
                    epa,
                    equi_epa_slab,
                )
                res_data[frac] = [(slip_length * frac), sfe, epa, equi_epa]

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
