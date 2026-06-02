"""Utilities to build and post-process decohesive surface calculations."""

import glob
import json
import os
from typing import Dict, List

import dpdata
import numpy as np
from monty.serialization import dumpfn, loadfn
from pymatgen.core.structure import Structure
from pymatgen.core.surface import SlabGenerator

from apex.core.calculator.lib import abacus_utils
from apex.core.calculator.lib import vasp_utils
from apex.core.property.Property import Property
from apex.core.reproduce import make_repro, post_repro
from dflow.python import upload_packages

upload_packages.append(__file__)

CAL_SETTING_DEFAULT: Dict[str, bool] = {
    "relax_pos": False,
    "relax_shape": False,
    "relax_vol": False,
}

class Decohesive(Property):
    def __init__(self, parameter: Dict, inter_param: Dict | None = None):
        parameter["reproduce"] = parameter.get("reproduce", False)
        self.reprod = parameter["reproduce"]

        if not self.reprod and not (
            "init_from_suffix" in parameter and "output_suffix" in parameter
        ):
            self.min_slab_size = parameter["min_slab_size"]
            self.pert_xz = parameter.get("pert_xz", 0.01)
            self.max_vacuum_size = parameter.get("max_vacuum_size", 15)
            self.vacuum_size_step = parameter.get("vacuum_size_step", 1)
            self.miller_index = tuple(parameter["miller_index"])

        parameter["cal_type"] = "static" if self.reprod else parameter.get(
            "cal_type", "static"
        )
        parameter["cal_setting"] = parameter.get("cal_setting", {}).copy()
        for key, val in CAL_SETTING_DEFAULT.items():
            parameter["cal_setting"].setdefault(key, val)

        if self.reprod:
            parameter["init_from_suffix"] = parameter.get("init_from_suffix", "00")
            self.init_from_suffix = parameter["init_from_suffix"]

        self.cal_type = parameter["cal_type"]
        self.cal_setting = parameter["cal_setting"]
        self.parameter = parameter
        self.inter_param = inter_param or {"type": "vasp"}

    def make_confs(self, path_to_work: str, path_to_equi: str, refine: bool = False):
        """Generate slab tasks with different vacuum sizes or reproduce prior runs."""
        path_to_work = os.path.abspath(path_to_work)
        path_to_equi = os.path.abspath(path_to_equi)
        os.makedirs(path_to_work, exist_ok=True)

        path_to_equi = self._maybe_override_equi(path_to_work, path_to_equi)

        if self.reprod:
            return self._make_repro_tasks(path_to_work)

        ptypes, ss, equi_contcar, poscar_name = self._load_equilibrium(path_to_equi)
        slabs, vacuums = self._build_slab_series(ss)

        cwd = os.getcwd()
        try:
            os.chdir(path_to_work)
            if os.path.exists(poscar_name):
                os.remove(poscar_name)
            os.symlink(os.path.relpath(equi_contcar), poscar_name)
            task_list: List[str] = []
            for idx, slab in enumerate(slabs):
                task_dir = os.path.join(path_to_work, f"task.{idx:06d}")
                self._write_task(task_dir, slab, ptypes, vacuums[idx])
                task_list.append(task_dir)
            return task_list
        finally:
            os.chdir(cwd)

    def _maybe_override_equi(self, path_to_work: str, path_to_equi: str) -> str:
        """Use provided start_confs_path if present; otherwise keep original path."""
        start_confs = self.parameter.get("start_confs_path")
        if start_confs and os.path.exists(start_confs):
            init_path_list = glob.glob(os.path.join(start_confs, "*"))
            struct_init_name_list = [os.path.basename(ii) for ii in init_path_list]
            struct_output_name = os.path.basename(os.path.dirname(path_to_work))
            assert (
                struct_output_name in struct_init_name_list
            ), f"{struct_output_name} not in initial configuration names"
            return os.path.abspath(
                os.path.join(start_confs, struct_output_name, "relaxation", "relax_task")
            )
        return path_to_equi

    def _make_repro_tasks(self, path_to_work: str) -> List[str]:
        """Create reproduce tasks."""
        print("surface reproduce starts")
        if "init_data_path" not in self.parameter:
            raise RuntimeError("please provide the initial data path to reproduce")
        init_data_path = os.path.abspath(self.parameter["init_data_path"])
        return make_repro(
            self.inter_param,
            init_data_path,
            self.init_from_suffix,
            path_to_work,
            self.parameter.get("reprod_last_frame", True),
        )

    def _load_equilibrium(self, path_to_equi: str):
        """Load equilibrium structure and POSCAR types."""
        if self.inter_param["type"] == "abacus":
            contcar_name = abacus_utils.final_stru(path_to_equi)
            poscar_name = "STRU"
        else:
            contcar_name = "CONTCAR"
            poscar_name = "POSCAR"

        equi_contcar = os.path.join(path_to_equi, contcar_name)
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

        return ptypes, ss, equi_contcar, poscar_name

    def _build_slab_series(self, ss: Structure):
        """Generate slabs with incremental vacuum sizes."""
        all_slabs: List[Structure] = []
        vacuums: List[float] = []
        num = 0
        while self.vacuum_size_step * num <= self.max_vacuum_size:
            vacuum_size = self.vacuum_size_step * num
            slab = self.__gen_slab_pmg(ss, self.miller_index, self.min_slab_size, vacuum_size)
            all_slabs.append(slab)
            vacuums.append(vacuum_size)
            num += 1
        return all_slabs, vacuums

    def _write_task(self, task_dir: str, slab: Structure, ptypes, vacuum_size: float):
        """Write one task directory."""
        os.makedirs(task_dir, exist_ok=True)
        cwd = os.getcwd()
        try:
            os.chdir(task_dir)
            for fname in ["INCAR", "POTCAR", "POSCAR", "conf.lmp", "in.lammps", "STRU"]:
                if os.path.exists(fname):
                    os.remove(fname)

            slab.to("POSCAR.tmp", "POSCAR")
            vasp_utils.regulate_poscar("POSCAR.tmp", "POSCAR")
            vasp_utils.sort_poscar("POSCAR", "POSCAR", ptypes)
            vasp_utils.perturb_xz("POSCAR", "POSCAR", self.pert_xz)

            if self.inter_param["type"] == "abacus":
                abacus_utils.poscar2stru("POSCAR", self.inter_param, "STRU")
                os.remove("POSCAR")

            decohesive = {"miller_index": self.miller_index, "vacuum_size": vacuum_size}
            dumpfn(decohesive, "decohesive.json", indent=4)
        finally:
            os.chdir(cwd)

    def post_process(self, task_list):
        pass

    def task_type(self):
        return self.parameter["type"]

    def task_param(self):
        return self.parameter

    def _compute_lower(self, output_file, all_tasks, all_res):
        output_file = os.path.abspath(output_file)
        res_data: Dict[str, list] = {}
        ptr_data = os.path.dirname(output_file) + "\n"

        if self.reprod:
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
        else:
            param_path = os.path.join(os.path.dirname(output_file), "param.json")
            param_content = loadfn(param_path)
            vacuum_size_step = param_content["vacuum_size_step"]
            ptr_data += f"Miller Index: {param_content['miller_index']}\n"
            ptr_data += "Vacuum_size(A) \tDecohesion_E(J/m^2) \tDecohesion_S(Pa)\n"

            first_result = loadfn(os.path.join(all_tasks[0], "result_task.json"))
            equi_evac = first_result["energies"][-1]
            pre_evac = 0.0
            CF_EV_TO_J_PER_M2 = 1.60217657e-16 / 1e-20 * 0.001

            for task_dir in all_tasks:
                task_result = loadfn(os.path.join(task_dir, "result_task.json"))
                area = np.linalg.norm(
                    np.cross(task_result["cells"][0][0], task_result["cells"][0][1])
                )
                evac = (task_result["energies"][-1] - equi_evac) / area * CF_EV_TO_J_PER_M2
                vacuum_size = loadfn(os.path.join(task_dir, "decohesive.json"))["vacuum_size"]
                stress = (evac - pre_evac) / vacuum_size_step * 1e10

                ptr_data += f"{vacuum_size:7.3f}   {evac:7.3f}     {stress:10.3e} \n"
                res_data[f"{vacuum_size}_{os.path.basename(task_dir)}"] = [
                    vacuum_size,
                    evac,
                    stress,
                ]
                pre_evac = evac

        with open(output_file, "w") as fp:
            json.dump(res_data, fp, indent=4)

        return res_data, ptr_data

    def __gen_slab_pmg(
        self, structure: Structure, plane_miller, slab_size, vacuum_size
    ) -> Structure:
        """Create a slab and stretch c-axis to add vacuum."""
        slab_generator = SlabGenerator(
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
        slabs_pmg = slab_generator.get_slabs(ftol=0.001)
        slab = next((s for s in slabs_pmg if s.miller_index == plane_miller), None)
        if slab is None:
            raise RuntimeError(f"No slab found for Miller index {plane_miller}")

        ordered = sorted(zip(slab.frac_coords, slab.species), key=lambda x: x[0][2])
        sorted_frac_coords, sorted_species = zip(*ordered)

        a_vec, b_vec, c_vec = slab.lattice.matrix
        slab_height = abs(c_vec[2])
        self.is_flip = c_vec[2] < 0
        elong_scale = 1 + (abs(vacuum_size) / slab_height)

        new_lattice = [a_vec, b_vec, elong_scale * c_vec]
        new_frac_coords = []
        for frac in sorted_frac_coords:
            coord = frac.copy()
            coord[2] = coord[2] / elong_scale
            new_frac_coords.append(coord)

        return Structure(
            lattice=np.array(new_lattice), coords=new_frac_coords, species=sorted_species
        )
