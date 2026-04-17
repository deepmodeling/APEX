import glob
import json
import logging
import math
import os
import shutil
import subprocess
from typing import Dict, List

from monty.serialization import dumpfn, loadfn
from pymatgen.core.structure import Structure
import seekpath
import yaml

from apex.core.calculator.Lammps import Lammps
from apex.core.calculator.lib import lammps_utils
from apex.core.calculator.lib import vasp_utils
from apex.core.calculator.calculator import LAMMPS_INTER_TYPE
from apex.core.property.Property import Property
from apex.core.property.Phonon import Phonon
from dflow.python import upload_packages

upload_packages.append(__file__)


DEFAULT_CAL_SETTING = {
    "relax_pos": True,
    "relax_shape": False,
    "relax_vol": False,
}

DEFAULT_SUPERCELL = [2, 2, 2]
THZ_TO_K = 47.99243073366221
KB_EV_PER_K = 8.617333262145e-5


class Gruneisen(Property):
    def __init__(self, parameter, inter_param=None):
        parameter["reproduce"] = parameter.get("reproduce", False)
        self.reprod = parameter["reproduce"]

        parameter["primitive"] = parameter.get("primitive", False)
        self.primitive = parameter["primitive"]
        parameter["supercell_size"] = parameter.get("supercell_size", DEFAULT_SUPERCELL)
        self.supercell_size = parameter["supercell_size"]
        parameter["seekpath_from_original"] = parameter.get("seekpath_from_original", False)
        self.seekpath_from_original = parameter["seekpath_from_original"]
        parameter["seekpath_param"] = parameter.get("seekpath_param", {})
        self.seekpath_param = parameter["seekpath_param"]
        parameter["MESH"] = parameter.get("MESH", None)
        self.MESH = parameter["MESH"]
        parameter["PRIMITIVE_AXES"] = parameter.get("PRIMITIVE_AXES", None)
        self.PRIMITIVE_AXES = parameter["PRIMITIVE_AXES"]
        parameter["BAND"] = parameter.get("BAND", None)
        self.BAND = parameter["BAND"]
        parameter["BAND_LABELS"] = parameter.get("BAND_LABELS", None)
        self.BAND_LABELS = parameter["BAND_LABELS"]
        parameter["BAND_POINTS"] = parameter.get("BAND_POINTS", 51)
        self.BAND_POINTS = parameter["BAND_POINTS"]
        parameter["BAND_CONNECTION"] = parameter.get("BAND_CONNECTION", True)
        self.BAND_CONNECTION = parameter["BAND_CONNECTION"]
        parameter["alpha_mode"] = parameter.get("alpha_mode", "sign_only")
        self.alpha_mode = parameter["alpha_mode"]
        parameter["bulk_modulus_source"] = parameter.get("bulk_modulus_source", "eos_fit")
        self.bulk_modulus_source = parameter["bulk_modulus_source"]
        parameter["eos_model"] = parameter.get("eos_model", "birch_murnaghan")
        self.eos_model = parameter["eos_model"]
        parameter["phonolammps_run_command"] = parameter.get("phonolammps_run_command", None)
        self.phonolammps_run_command = parameter["phonolammps_run_command"]
        parameter["lammps_run_command"] = parameter.get("lammps_run_command", None)
        self.lammps_run_command = parameter["lammps_run_command"]
        parameter["approach"] = parameter.get("approach", "linear")
        self.approach = parameter["approach"]
        parameter["cal_type"] = parameter.get("cal_type", "static")
        self.cal_type = parameter["cal_type"]
        parameter["cal_setting"] = parameter.get("cal_setting", {})
        for key, value in DEFAULT_CAL_SETTING.items():
            parameter["cal_setting"].setdefault(key, value)
        self.cal_setting = parameter["cal_setting"]

        self.volume_strains = parameter["volume_strains"]
        self.temperatures = parameter["temperatures"]
        self.parameter = parameter
        self.inter_param = inter_param if inter_param is not None else {"type": "vasp"}

        self._validate()

    def _validate(self):
        if len(self.volume_strains) < 3:
            raise ValueError("volume_strains must contain at least 3 points")
        if len(self.temperatures) < 1:
            raise ValueError("temperatures must contain at least 1 point")
        if not any(math.isclose(float(value), 0.0, abs_tol=1e-10) for value in self.volume_strains):
            raise ValueError("volume_strains must include 0.0")
        if self.alpha_mode not in {"sign_only", "full"}:
            raise ValueError("alpha_mode must be either 'sign_only' or 'full'")
        if self.bulk_modulus_source != "eos_fit":
            raise ValueError("first-version gruneisen only supports bulk_modulus_source='eos_fit'")
        if self.eos_model != "birch_murnaghan":
            raise ValueError("first-version gruneisen only supports eos_model='birch_murnaghan'")
        if self.approach not in {"linear", "displacement"}:
            raise ValueError("gruneisen approach must be either 'linear' or 'displacement'")
        if self.cal_setting["relax_pos"] is not True:
            raise ValueError("gruneisen requires cal_setting.relax_pos = true")
        if self.cal_setting["relax_shape"] is not False:
            raise ValueError("gruneisen requires cal_setting.relax_shape = false")
        if self.cal_setting["relax_vol"] is not False:
            raise ValueError("gruneisen requires cal_setting.relax_vol = false")

        volume_strains = [float(value) for value in self.volume_strains]
        if volume_strains != sorted(volume_strains):
            raise ValueError("volume_strains must be strictly increasing")
        if len(set(volume_strains)) != len(volume_strains):
            raise ValueError("volume_strains must not contain duplicates")
        if any(float(value) <= 0.0 for value in self.temperatures):
            raise ValueError("temperatures must be positive")
        temperatures = [float(value) for value in self.temperatures]
        if temperatures != sorted(temperatures):
            raise ValueError("temperatures must be strictly increasing")

        strain_set = {round(value, 10) for value in volume_strains}
        for value in volume_strains:
            if not math.isclose(value, 0.0, abs_tol=1e-10):
                if round(-value, 10) not in strain_set:
                    raise ValueError("volume_strains must be symmetric around 0.0")

    def make_confs(self, path_to_work, path_to_equi, refine=False):
        if refine:
            raise NotImplementedError("gruneisen refine mode is not implemented yet")
        if self.reprod:
            raise NotImplementedError("gruneisen reproduce mode is not implemented yet")

        path_to_work = os.path.abspath(path_to_work)
        if os.path.exists(path_to_work):
            logging.debug("%s already exists" % path_to_work)
        else:
            os.makedirs(path_to_work)
        path_to_equi = os.path.abspath(path_to_equi)

        equi_contcar = os.path.join(path_to_equi, "CONTCAR")
        if not os.path.isfile(equi_contcar):
            raise RuntimeError("please do relaxation first")

        task_list = []
        volume_path = os.path.join(path_to_work, "volume_points.json")
        volume_points = []
        natoms = vasp_utils.poscar_natoms(equi_contcar)
        base_volume = vasp_utils.poscar_vol(equi_contcar)
        cwd = os.getcwd()
        band_payload = self._build_band_payload(equi_contcar)

        try:
            dumpfn(band_payload["band_path"], os.path.join(path_to_work, "band_path.json"), indent=4)
            for task_id, strain in enumerate(self.volume_strains):
                output_task = os.path.join(path_to_work, f"task.{task_id:06d}")
                os.makedirs(output_task, exist_ok=True)
                os.chdir(output_task)
                for file_name in [
                    "POSCAR.orig",
                    "POSCAR",
                    "POSCAR-unitcell",
                    "SPOSCAR",
                    "phonopy_disp.yaml",
                    "volume.json",
                    "band.conf",
                ]:
                    if os.path.exists(file_name):
                        os.remove(file_name)

                os.symlink(os.path.relpath(equi_contcar), "POSCAR.orig")
                scale = (1.0 + float(strain)) ** (1.0 / 3.0)
                vasp_utils.poscar_scale("POSCAR.orig", "POSCAR", scale)
                scaled_volume = vasp_utils.poscar_vol("POSCAR")
                volume_data = {
                    "strain": float(strain),
                    "scale": scale,
                    "volume": scaled_volume,
                    "volume_per_atom": scaled_volume / natoms,
                    "reference_volume": base_volume,
                    "reference_volume_per_atom": base_volume / natoms,
                }
                dumpfn(volume_data, "volume.json", indent=4)
                with open("band.conf", "w") as fp:
                    fp.write(band_payload["band_conf"])
                if self.inter_param["type"] == "vasp":
                    self._prepare_vasp_phonon_task()
                volume_points.append(volume_data)
                task_list.append(output_task)

            os.chdir(path_to_work)
            dumpfn(volume_points, volume_path, indent=4)
        finally:
            os.chdir(cwd)
        return task_list

    def post_process(self, task_list):
        cwd = os.getcwd()
        if self.inter_param["type"] in LAMMPS_INTER_TYPE:
            helper = Phonon(dict(self.parameter), inter_param=self.inter_param)
            for task_dir in task_list:
                os.chdir(task_dir)
                if os.path.isfile("in.lammps"):
                    with open("in.lammps", "r") as f1:
                        contents = f1.readlines()
                    pair_line_id = None
                    for idx, line in enumerate(contents):
                        if "pair_coeff" in line:
                            pair_line_id = idx
                            break
                    if pair_line_id is not None:
                        contents = contents[: pair_line_id + 1]
                    with open("in.lammps", "w") as f2:
                        f2.write(helper._ensure_deepmd_plugin_loaded("".join(contents)))
                self._write_fixed_volume_relax_inputs(task_dir)
                with open("run_command", "w") as f3:
                    f3.write("bash run_gruneisen_task.sh")
        os.chdir(cwd)

    def _prepare_vasp_phonon_task(self) -> None:
        if self.approach != "linear":
            raise NotImplementedError("VASP gruneisen currently supports approach='linear' only")

        if self.primitive:
            subprocess.check_call("phonopy --symmetry", shell=True)
            if not os.path.isfile("PPOSCAR"):
                raise FileNotFoundError("PPOSCAR was not created by phonopy --symmetry")
            shutil.copyfile("PPOSCAR", "POSCAR-unitcell")
            shutil.copyfile("PPOSCAR", "POSCAR")
        else:
            shutil.copyfile("POSCAR", "POSCAR-unitcell")

        subprocess.check_call(
            'phonopy -d --dim="%s %s %s" -c POSCAR'
            % (self.supercell_size[0], self.supercell_size[1], self.supercell_size[2]),
            shell=True,
        )
        if not os.path.isfile("SPOSCAR"):
            raise FileNotFoundError("SPOSCAR was not created by phonopy")
        os.remove("POSCAR")
        os.symlink("SPOSCAR", "POSCAR")

    def task_type(self):
        return self.parameter["type"]

    def task_param(self):
        return self.parameter

    def _compute_lower(self, output_file, all_tasks, all_res):
        output_file = os.path.abspath(output_file)
        work_path = os.path.dirname(output_file)
        ptr_lines = [work_path]
        cwd = os.getcwd()

        try:
            for task_dir in all_tasks:
                self._ensure_mesh_yaml(task_dir)

            task_infos = [self._load_task_info(task_dir) for task_dir in all_tasks]
            if self.alpha_mode == "full":
                self._attach_task_energies(task_infos, all_res)
            ref_info, minus_info, plus_info = self._select_reference_triplet(task_infos)
            sign_only = self._compute_sign_only(ref_info, minus_info, plus_info)
            bulk_modulus = None
            thermal_expansion = {
                "alpha_mode": self.alpha_mode,
                "temperatures": self.temperatures,
                "sum_gamma_cv": sign_only["sum_gamma_cv"],
                "sign": sign_only["sign"],
            }
            if self.alpha_mode == "full":
                bulk_modulus = self._fit_bulk_modulus(task_infos)
                thermal_expansion.update(
                    self._compute_full_thermal_expansion(ref_info, sign_only, bulk_modulus)
                )

            result = {
                "volume_points": [
                    {
                        "strain": info["strain"],
                        "volume": info["volume"],
                        "volume_per_atom": info["volume_per_atom"],
                        "phonon_cell_volume": info["phonon_cell_volume"],
                        "fit_volume_per_atom": info["fit_volume_per_atom"],
                        **(
                            {"energy_per_atom": info["energy_per_atom"]}
                            if "energy_per_atom" in info
                            else {}
                        ),
                    }
                    for info in task_infos
                ],
                "gruneisen": {
                    "reference_volume": ref_info["volume"],
                    "reference_volume_per_atom": ref_info["volume_per_atom"],
                    "qpoint_count": sign_only["qpoint_count"],
                    "mode_count": sign_only["mode_count"],
                    "skipped_mode_count": sign_only["skipped_mode_count"],
                    "difference_pair": {
                        "minus_strain": minus_info["strain"],
                        "reference_strain": ref_info["strain"],
                        "plus_strain": plus_info["strain"],
                    },
                },
                "thermal_expansion": thermal_expansion,
                "mode_gruneisen": sign_only["mode_gruneisen"],
                "mode_heat_capacity": sign_only["mode_heat_capacity"],
                "mode_contributions": sign_only["mode_contributions"],
                "contribution_summary": sign_only["contribution_summary"],
                "bulk_modulus": bulk_modulus,
            }

            if bulk_modulus is not None:
                ptr_lines.append("VolumePoint  Strain  FitVolumePerAtom(A^3)  EnergyPerAtom(eV)")
                for idx, info in enumerate(task_infos):
                    ptr_lines.append(
                        f"{idx:6d}  {info['strain']: .8f}  "
                        f"{info['fit_volume_per_atom']: .10e}  {info['energy_per_atom']: .10e}"
                    )
                ptr_lines.append(
                    "# bulk modulus: "
                    f"K_T={bulk_modulus['K_T_GPa']:.10e} GPa, "
                    f"K_T={bulk_modulus['K_T_eV_per_A3']:.10e} eV/A^3, "
                    f"fit_variant={bulk_modulus['fit_variant']}"
                )

            header = "Temperature(K)  SumGammaCv  Sign"
            if self.alpha_mode == "full":
                header = "Temperature(K)  SumGammaCvRaw  SumGammaCvPerCell  Alpha(K^-1)  Sign"
            ptr_lines.append(header)
            for temp_idx, (temperature, sum_gamma_cv, sign) in enumerate(zip(
                self.temperatures,
                sign_only["sum_gamma_cv"],
                sign_only["sign"],
            )):
                if self.alpha_mode == "full":
                    ptr_lines.append(
                        f"{float(temperature):10.4f}  {sum_gamma_cv: .10e}  "
                        f"{thermal_expansion['sum_gamma_cv_per_cell'][temp_idx]: .10e}  "
                        f"{thermal_expansion['alpha'][temp_idx]: .10e}  {sign}"
                    )
                else:
                    ptr_lines.append(f"{float(temperature):10.4f}  {sum_gamma_cv: .10e}  {sign}")
            ptr_lines.append("# contribution summary")
            for summary in sign_only["contribution_summary"]:
                ptr_lines.append(
                    f"# T={summary['temperature']:.4f}  "
                    f"positive={summary['positive_sum']:.10e}  "
                    f"negative={summary['negative_sum']:.10e}  "
                    f"net={summary['net_sum']:.10e}"
                )
            ptr_lines.append(
                f"# difference pair: {minus_info['strain']} / {ref_info['strain']} / {plus_info['strain']}"
            )
            ptr_lines.append(f"# skipped modes: {sign_only['skipped_mode_count']}")

            with open(output_file, "w") as fp:
                json.dump(result, fp, indent=4)

            return result, "\n".join(ptr_lines) + "\n"
        finally:
            os.chdir(cwd)

    def _build_band_payload(self, poscar_path: str) -> dict:
        structure = Structure.from_file(poscar_path)
        if self.BAND:
            band_path = Phonon.phonopy_band_string_2_band_list(self.BAND, self.BAND_LABELS)
            band_string = self.BAND
            band_labels = self.BAND_LABELS
        else:
            if self.seekpath_from_original:
                sp = seekpath.get_path_orig_cell(
                    Phonon.get_seekpath_structure(structure),
                    **self.seekpath_param,
                )
            else:
                sp = seekpath.get_path(
                    Phonon.get_seekpath_structure(structure),
                    **self.seekpath_param,
                )
            band_path = Phonon.extract_seekpath_band(sp)
            band_string, band_labels = Phonon.band_list_2_phonopy_band_string(band_path)

        lines = ["ATOM_NAME =" + "".join(f" {name}" for name in vasp_utils.get_poscar_types(poscar_path))]
        lines.append(
            "DIM = %s %s %s" % (
                self.supercell_size[0],
                self.supercell_size[1],
                self.supercell_size[2],
            )
        )
        if self.MESH:
            lines.append("MESH = %s %s %s" % (self.MESH[0], self.MESH[1], self.MESH[2]))
        if self.PRIMITIVE_AXES:
            lines.append(f"PRIMITIVE_AXES = {self.PRIMITIVE_AXES}")
        lines.append(f"BAND = {band_string}")
        if band_labels:
            lines.append(f"BAND_LABELS = {band_labels}")
        if self.BAND_POINTS:
            lines.append(f"BAND_POINTS = {self.BAND_POINTS}")
        if self.BAND_CONNECTION:
            lines.append(f"BAND_CONNECTION = {self.BAND_CONNECTION}")
        lines.append("FORCE_CONSTANTS=READ")
        return {"band_path": band_path, "band_conf": "\n".join(lines) + "\n"}

    def _write_fixed_volume_relax_inputs(self, task_dir: str) -> None:
        lammps_task = Lammps(self.inter_param, os.path.join(task_dir, "POSCAR"))
        lammps_task.set_model_param()
        relax_input = lammps_utils.make_lammps_equi(
            "conf.lmp",
            lammps_task.type_map,
            lammps_task.inter_func,
            lammps_task.model_param,
            self.cal_setting.get("etol", 0),
            self.cal_setting.get("ftol", 1e-10),
            self.cal_setting.get("maxiter", 5000),
            self.cal_setting.get("maxeval", 500000),
            False,
            prop_type="relaxation",
        )
        with open("in.relax.lammps", "w") as fp:
            fp.write(relax_input)
        type_map = lammps_utils.element_list(lammps_task.type_map)
        with open("type_map.json", "w") as fp:
            json.dump(type_map, fp, indent=4)
        with open("convert_relax_dump_to_poscar.py", "w") as fp:
            fp.write(self._relax_dump_converter_script())
        with open("run_gruneisen_task.sh", "w") as fp:
            fp.write(self._build_two_stage_run_script())

    def _build_lammps_run_command(self, input_file: str) -> str:
        command_template = self.lammps_run_command
        if not command_template:
            return f"lmp -in {input_file}"
        if "{input_file}" in command_template:
            return command_template.format(input_file=input_file)
        if "in.lammps" in command_template:
            return command_template.replace("in.lammps", input_file, 1)
        return f"{command_template} -in {input_file}"

    def _build_two_stage_run_script(self) -> str:
        relax_cmd = self._build_lammps_run_command("in.relax.lammps")
        phonon_cmd = Phonon(dict(self.parameter), inter_param=self.inter_param)._build_phonolammps_run_command()
        return (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n\n"
            "cp POSCAR POSCAR.pre_relax\n"
            f"{relax_cmd}\n"
            "python3 convert_relax_dump_to_poscar.py dump.relax POSCAR.relaxed type_map.json\n"
            "cp POSCAR.relaxed POSCAR\n"
            f"{phonon_cmd}\n"
        )

    @staticmethod
    def _relax_dump_converter_script() -> str:
        return """#!/usr/bin/env python3
import json
import sys

import dpdata


def main():
    if len(sys.argv) != 4:
        raise SystemExit("usage: convert_relax_dump_to_poscar.py DUMP POSCAR_OUT TYPE_MAP_JSON")
    dump_path, poscar_out, type_map_json = sys.argv[1:]
    with open(type_map_json, "r") as fp:
        type_map = json.load(fp)
    system = dpdata.System(dump_path, fmt="lammps/dump", type_map=type_map)
    system.to_vasp_poscar(poscar_out, frame_idx=-1)
    with open(poscar_out, "r") as fp:
        lines = fp.read().splitlines()
    labels = []
    for token in lines[5].split():
        if "_" in token:
            labels.append(type_map[int(token.split("_")[1])])
        else:
            labels.append(token)
    lines[5] = " ".join(labels)
    with open(poscar_out, "w") as fp:
        fp.write("\\n".join(lines) + "\\n")


if __name__ == "__main__":
    main()
"""

    def _ensure_mesh_yaml(self, task_dir: str) -> None:
        mesh_path = os.path.join(task_dir, "mesh.yaml")
        if os.path.isfile(mesh_path):
            return
        force_constants = os.path.join(task_dir, "FORCE_CONSTANTS")
        band_conf = os.path.join(task_dir, "band.conf")
        if self.inter_param["type"] == "vasp":
            vasprun = os.path.join(task_dir, "vasprun.xml")
            if not os.path.isfile(force_constants):
                if not os.path.isfile(vasprun):
                    raise FileNotFoundError(f"vasprun.xml not found in {task_dir}")
                os.chdir(task_dir)
                subprocess.check_call("phonopy --fc vasprun.xml", shell=True)
            poscar = os.path.join(task_dir, "POSCAR-unitcell")
        else:
            poscar = os.path.join(task_dir, "POSCAR")
        if not os.path.isfile(force_constants):
            raise FileNotFoundError(f"FORCE_CONSTANTS not found in {task_dir}")
        if not os.path.isfile(band_conf):
            raise FileNotFoundError(f"band.conf not found in {task_dir}")
        if not os.path.isfile(poscar):
            raise FileNotFoundError(f"POSCAR not found in {task_dir}")
        os.chdir(task_dir)
        cell_file = "POSCAR-unitcell" if self.inter_param["type"] == "vasp" else "POSCAR"
        command = (
            'phonopy --nomeshsym --dim="%s %s %s" -c %s band.conf'
            % (self.supercell_size[0], self.supercell_size[1], self.supercell_size[2], cell_file)
        )
        subprocess.check_call(command, shell=True)
        if not os.path.isfile(mesh_path):
            raise FileNotFoundError(f"mesh.yaml was not created in {task_dir}")

    def _load_task_info(self, task_dir: str) -> dict:
        with open(os.path.join(task_dir, "volume.json"), "r") as fp:
            volume_data = json.load(fp)
        with open(os.path.join(task_dir, "mesh.yaml"), "r") as fp:
            mesh_data = yaml.safe_load(fp)
        phonon = mesh_data["phonon"]
        weights = [int(point["weight"]) for point in phonon]
        frequencies = [
            [float(band["frequency"]) for band in point["band"]]
            for point in phonon
        ]
        phonon_cell_path = (
            os.path.join(task_dir, "POSCAR-unitcell")
            if self.inter_param["type"] == "vasp"
            else os.path.join(task_dir, "POSCAR")
        )
        phonon_cell_volume = float(volume_data["volume"])
        phonon_cell_natoms = None
        if os.path.isfile(phonon_cell_path):
            phonon_cell_volume = float(vasp_utils.poscar_vol(phonon_cell_path))
            phonon_cell_natoms = int(vasp_utils.poscar_natoms(phonon_cell_path))
        elif self.primitive:
            raise FileNotFoundError(
                "POSCAR-unitcell is required for primitive gruneisen alpha calculation "
                f"but was not found at {phonon_cell_path}"
            )
        return {
            "task_dir": task_dir,
            "strain": float(volume_data["strain"]),
            "volume": float(volume_data["volume"]),
            "volume_per_atom": float(volume_data["volume_per_atom"]),
            "phonon_cell_volume": phonon_cell_volume,
            "phonon_cell_natoms": phonon_cell_natoms,
            "fit_volume_per_atom": float(volume_data["volume_per_atom"]),
            "weights": weights,
            "frequencies": frequencies,
        }

    def _attach_task_energies(self, task_infos: List[dict], all_res: List[str]) -> None:
        if len(all_res) != len(task_infos):
            raise ValueError("full gruneisen requires one result_task.json for each volume task")
        for info, result_path in zip(task_infos, all_res):
            if not os.path.isfile(result_path):
                raise FileNotFoundError(f"result_task.json not found for {info['task_dir']}")
            task_result = loadfn(result_path)
            energy = self._last_result_value(task_result, "energies")
            atom_count = self._result_atom_count(task_result)
            if atom_count <= 0:
                raise ValueError(f"invalid atom count in {result_path}")
            info["energy"] = energy
            info["atom_count"] = atom_count
            info["energy_per_atom"] = energy / atom_count
            if info["phonon_cell_natoms"]:
                info["fit_volume_per_atom"] = info["phonon_cell_volume"] / info["phonon_cell_natoms"]

    @staticmethod
    def _last_result_value(task_result, key: str) -> float:
        try:
            values = task_result[key]
        except (KeyError, TypeError):
            values = task_result["data"][key]
        if isinstance(values, dict) and "data" in values:
            values = values["data"]
        if len(values) == 0:
            raise ValueError(f"result field {key} is empty")
        return float(values[-1])

    @staticmethod
    def _result_atom_count(task_result) -> int:
        try:
            atom_numbs = task_result["atom_numbs"]
        except (KeyError, TypeError):
            atom_numbs = task_result["data"]["atom_numbs"]
        if isinstance(atom_numbs, dict) and "data" in atom_numbs:
            atom_numbs = atom_numbs["data"]
        return int(sum(atom_numbs))

    def _fit_bulk_modulus(self, task_infos: List[dict]) -> dict:
        from apex.core.lib.mfp_eosfit import fit_birch_murnaghan

        volumes = [info["fit_volume_per_atom"] for info in task_infos]
        energies = [info["energy_per_atom"] for info in task_infos]
        fixed_bp = 4.0 if len(task_infos) == 3 else None
        fit = fit_birch_murnaghan(volumes, energies, fixed_bp=fixed_bp)
        if not math.isfinite(fit["K_T_eV_per_A3"]) or fit["K_T_eV_per_A3"] <= 0.0:
            raise ValueError("full gruneisen requires a positive fitted bulk modulus")
        if not math.isfinite(fit["K_T_GPa"]) or fit["K_T_GPa"] <= 0.0:
            raise ValueError("full gruneisen requires a positive fitted bulk modulus")
        return {
            "source": self.bulk_modulus_source,
            "model": self.eos_model,
            "K_T_GPa": fit["K_T_GPa"],
            "K_T_eV_per_A3": fit["K_T_eV_per_A3"],
            "B0_prime": fit["B0_prime"],
            "V0_per_atom_A3": fit["V0_A3"],
            "E0_per_atom_eV": fit["E0_eV"],
            "fit_variant": fit["fit_variant"],
            "used_point_count": fit["used_point_count"],
            "residual_sum_squares": fit["residual_sum_squares"],
        }

    def _compute_full_thermal_expansion(
        self,
        ref_info: dict,
        sign_only: dict,
        bulk_modulus: dict,
    ) -> dict:
        qpoint_weight_sum = sum(ref_info["weights"])
        if qpoint_weight_sum <= 0:
            raise ValueError("full gruneisen requires a positive q-point weight sum")
        reference_volume = ref_info["phonon_cell_volume"]
        if not math.isfinite(reference_volume) or reference_volume <= 0.0:
            raise ValueError("full gruneisen requires a positive reference volume")
        k_t = bulk_modulus["K_T_eV_per_A3"]
        sum_gamma_cv_per_cell = [
            value / qpoint_weight_sum for value in sign_only["sum_gamma_cv"]
        ]
        alpha = [
            value / (reference_volume * k_t)
            for value in sum_gamma_cv_per_cell
        ]
        return {
            "sum_gamma_cv_per_cell": sum_gamma_cv_per_cell,
            "alpha": alpha,
            "alpha_unit": "K^-1",
            "qpoint_weight_sum": qpoint_weight_sum,
            "reference_volume_for_alpha": reference_volume,
        }

    def _select_reference_triplet(self, task_infos: List[dict]) -> tuple[dict, dict, dict]:
        ref_candidates = [info for info in task_infos if math.isclose(info["strain"], 0.0, abs_tol=1e-10)]
        if len(ref_candidates) != 1:
            raise ValueError("gruneisen sign_only requires exactly one reference task with strain 0.0")
        ref_info = ref_candidates[0]

        negative_infos = sorted(
            [info for info in task_infos if info["strain"] < 0.0],
            key=lambda info: abs(info["strain"]),
        )
        positive_infos = sorted(
            [info for info in task_infos if info["strain"] > 0.0],
            key=lambda info: abs(info["strain"]),
        )
        if not negative_infos or not positive_infos:
            raise ValueError("gruneisen sign_only requires symmetric negative and positive strain tasks")

        minus_info = negative_infos[0]
        plus_info = positive_infos[0]
        if not math.isclose(abs(minus_info["strain"]), abs(plus_info["strain"]), rel_tol=1e-10, abs_tol=1e-10):
            raise ValueError("nearest positive and negative strains must be symmetric for sign_only mode")
        return ref_info, minus_info, plus_info

    def _compute_sign_only(self, ref_info: dict, minus_info: dict, plus_info: dict) -> dict:
        if ref_info["weights"] != minus_info["weights"] or ref_info["weights"] != plus_info["weights"]:
            raise ValueError("mesh q-point weights must be identical across volume points")
        if len(ref_info["frequencies"]) != len(minus_info["frequencies"]) or len(ref_info["frequencies"]) != len(plus_info["frequencies"]):
            raise ValueError("mesh q-point count must be identical across volume points")

        sum_gamma_cv = [0.0 for _ in self.temperatures]
        positive_sum = [0.0 for _ in self.temperatures]
        negative_sum = [0.0 for _ in self.temperatures]
        skipped_mode_count = 0

        log_v_minus = math.log(minus_info["volume"])
        log_v_plus = math.log(plus_info["volume"])
        denom = log_v_plus - log_v_minus
        if math.isclose(denom, 0.0, abs_tol=1e-20):
            raise ValueError("volume difference for gruneisen central difference is zero")

        qpoint_count = len(ref_info["frequencies"])
        mode_count = len(ref_info["frequencies"][0]) if qpoint_count else 0
        mode_gruneisen = []
        mode_heat_capacity = []
        mode_contributions = []

        for q_idx in range(qpoint_count):
            ref_modes = ref_info["frequencies"][q_idx]
            minus_modes = minus_info["frequencies"][q_idx]
            plus_modes = plus_info["frequencies"][q_idx]
            if len(ref_modes) != len(minus_modes) or len(ref_modes) != len(plus_modes):
                raise ValueError("mesh band count must be identical across volume points")
            weight = ref_info["weights"][q_idx]
            gamma_row = []
            cv_row = {self._temperature_key(temp): [] for temp in self.temperatures}
            contribution_row = {self._temperature_key(temp): [] for temp in self.temperatures}
            for mode_idx in range(len(ref_modes)):
                omega_ref = ref_modes[mode_idx]
                omega_minus = minus_modes[mode_idx]
                omega_plus = plus_modes[mode_idx]
                if omega_ref <= 0.0 or omega_minus <= 0.0 or omega_plus <= 0.0:
                    skipped_mode_count += 1
                    gamma_row.append(None)
                    for temp_idx, temperature in enumerate(self.temperatures):
                        temp_key = self._temperature_key(temperature)
                        cv_row[temp_key].append(0.0)
                        contribution_row[temp_key].append(0.0)
                    continue
                gamma = -(math.log(omega_plus) - math.log(omega_minus)) / denom
                gamma_row.append(gamma)
                for temp_idx, temperature in enumerate(self.temperatures):
                    cv = self._mode_heat_capacity(omega_ref, float(temperature))
                    contribution = weight * gamma * cv
                    sum_gamma_cv[temp_idx] += contribution
                    if contribution > 0.0:
                        positive_sum[temp_idx] += contribution
                    elif contribution < 0.0:
                        negative_sum[temp_idx] += contribution
                    temp_key = self._temperature_key(temperature)
                    cv_row[temp_key].append(cv)
                    contribution_row[temp_key].append(contribution)

            mode_gruneisen.append(
                {
                    "q_index": q_idx,
                    "weight": weight,
                    "omega_ref": ref_modes,
                    "gamma": gamma_row,
                }
            )
            mode_heat_capacity.append(
                {
                    "q_index": q_idx,
                    "weight": weight,
                    "cv": cv_row,
                }
            )
            mode_contributions.append(
                {
                    "q_index": q_idx,
                    "weight": weight,
                    "gamma_cv": contribution_row,
                }
            )

        contribution_summary = []
        for temp_idx, temperature in enumerate(self.temperatures):
            contribution_summary.append(
                {
                    "temperature": float(temperature),
                    "positive_sum": positive_sum[temp_idx],
                    "negative_sum": negative_sum[temp_idx],
                    "net_sum": sum_gamma_cv[temp_idx],
                }
            )

        return {
            "qpoint_count": qpoint_count,
            "mode_count": mode_count,
            "skipped_mode_count": skipped_mode_count,
            "sum_gamma_cv": sum_gamma_cv,
            "sign": [self._classify_sign(value) for value in sum_gamma_cv],
            "mode_gruneisen": mode_gruneisen,
            "mode_heat_capacity": mode_heat_capacity,
            "mode_contributions": mode_contributions,
            "contribution_summary": contribution_summary,
        }

    @staticmethod
    def _mode_heat_capacity(frequency_thz: float, temperature: float) -> float:
        if frequency_thz <= 0.0 or temperature <= 0.0:
            return 0.0
        x = THZ_TO_K * frequency_thz / temperature
        exp_neg_x = math.exp(-x)
        if exp_neg_x == 0.0:
            return 0.0
        return KB_EV_PER_K * (x * x * exp_neg_x / ((1.0 - exp_neg_x) ** 2))

    @staticmethod
    def _classify_sign(value: float, tol: float = 1e-14) -> str:
        if value > tol:
            return "positive"
        if value < -tol:
            return "negative"
        return "zero"

    @staticmethod
    def _temperature_key(temperature: float) -> str:
        return str(float(temperature))
