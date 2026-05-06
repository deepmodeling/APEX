"""Finite-temperature elastic constants from paired Langevin stress response.

This property implements noise-reduced finite-temperature elastic constants via
paired Langevin stress-strain response. DOI: 10.1103/sd49-wqd6
"""

import logging
import math
import os
import re
from typing import Dict, List, Tuple

import numpy as np
from monty.serialization import dumpfn, loadfn
from pymatgen.analysis.elasticity.elastic import ElasticTensor
from pymatgen.core.structure import Structure

from apex.core.calculator.lib import vasp_utils
from apex.core.property.Property import Property
from apex.core.refine import make_refine
from dflow.python import upload_packages

upload_packages.append(__file__)

PROPERTY_TYPE = "finite_t_elastic"
METADATA_FILE = "FiniteTElastic.json"
STRESS_FILE = "stress_timeseries.txt"
EQUI_RESTART = "finite_t_elastic.equi.restart"

DEFAULT_SUPERCELL = [2, 2, 2]
DEFAULT_CAL_SETTING = {
    "temperature": [300],
    "strain": 0.001,
    "strain_components": [0, 1, 2, 3, 4, 5],
    "equi_step": 16000,
    "response_step": 16000,
    "stress_output_every": 100,
    "timestep": 0.001,
    "tdamp": 0.1,
    "pdamp": 1.0,
    "seed": 12345,
    "n_blocks": 10,
    "method": "paired_langevin",
}

COMPONENT_LABELS = {
    0: "xx",
    1: "yy",
    2: "zz",
    3: "yz",
    4: "xz",
    5: "xy",
}

COMPONENT_ALIASES = {
    "0": 0,
    "xx": 0,
    "11": 0,
    "e11": 0,
    "1": 1,
    "yy": 1,
    "22": 1,
    "e22": 1,
    "2": 2,
    "zz": 2,
    "33": 2,
    "e33": 2,
    "3": 3,
    "yz": 3,
    "zy": 3,
    "23": 3,
    "32": 3,
    "e23": 3,
    "e32": 3,
    "4": 4,
    "xz": 4,
    "zx": 4,
    "13": 4,
    "31": 4,
    "e13": 4,
    "e31": 4,
    "5": 5,
    "xy": 5,
    "yx": 5,
    "12": 5,
    "21": 5,
    "e12": 5,
    "e21": 5,
}


class FiniteTElastic(Property):
    """
    LAMMPS-only finite-temperature elastic constants from paired stress response.

    Each strained production trajectory is paired with an unstrained reference
    trajectory using the same equilibrated restart, velocity seed, Langevin seed,
    timestep, tdamp, run length, and stress output cadence. This reduces noise in
    stress differences but is not described as mathematically exact cancellation.
    """

    def __init__(self, parameter: Dict, inter_param: Dict | None = None):
        if inter_param is not None and inter_param.get("type") in ["vasp", "abacus"]:
            raise TypeError("FiniteTElastic supports only LAMMPS interactions.")

        parameter.setdefault("type", PROPERTY_TYPE)
        parameter.setdefault("cal_setting", {})
        for key, val in DEFAULT_CAL_SETTING.items():
            parameter["cal_setting"].setdefault(key, val)

        if parameter["cal_setting"].get("method") != "paired_langevin":
            raise ValueError("FiniteTElastic currently supports only method='paired_langevin'.")

        parameter["cal_setting"]["strain_components"] = normalize_strain_components(
            parameter["cal_setting"]["strain_components"]
        )
        parameter.setdefault("supercell_size", DEFAULT_SUPERCELL)
        parameter["cal_type"] = "finite_t_elastic"

        self.parameter = parameter
        self.cal_setting = parameter["cal_setting"]
        self.supercell_size = parameter["supercell_size"]
        self.inter_param = inter_param or {"type": "lammps"}

    def make_confs(self, path_to_work: str, path_to_equi: str, refine: bool = False):
        path_to_work = os.path.abspath(path_to_work)
        path_to_equi = os.path.abspath(path_to_equi)
        os.makedirs(path_to_work, exist_ok=True)

        if self.inter_param["type"] in ["vasp", "abacus"]:
            raise TypeError("FiniteTElastic only supports LAMMPS calculation.")

        if refine:
            return self._make_refine(path_to_work)

        equi_contcar = os.path.join(path_to_equi, "CONTCAR")
        if not os.path.exists(equi_contcar):
            raise RuntimeError(f"missing CONTCAR in equilibrium path: {path_to_equi}")

        ptypes = vasp_utils.get_poscar_types(equi_contcar)
        structure = Structure.from_file(equi_contcar)

        task_list: List[str] = []
        task_index = 0
        for temperature in self._temperatures():
            equi_task = os.path.join(path_to_work, f"task.{task_index:06d}")
            os.makedirs(equi_task, exist_ok=True)
            self._write_task(
                equi_task,
                structure,
                ptypes,
                temperature=temperature,
                role="equi",
                pair_id=None,
                strain_component=None,
                strain_value=0.0,
                restart_source=None,
            )
            task_list.append(equi_task)
            task_index += 1

            equi_restart = os.path.join(equi_task, EQUI_RESTART)
            for component in self.cal_setting["strain_components"]:
                for sign in [-1, 1]:
                    signed_strain = sign * float(self.cal_setting["strain"])
                    sign_label = "m" if sign < 0 else "p"
                    pair_id = f"T{_format_temperature(temperature)}_c{component}_{sign_label}"
                    for role, value in [("reference", 0.0), ("strained", signed_strain)]:
                        task_dir = os.path.join(path_to_work, f"task.{task_index:06d}")
                        os.makedirs(task_dir, exist_ok=True)
                        restart_source = os.path.relpath(equi_restart, task_dir)
                        self._write_task(
                            task_dir,
                            structure,
                            ptypes,
                            temperature=temperature,
                            role=role,
                            pair_id=pair_id,
                            strain_component=component,
                            strain_value=value,
                            restart_source=restart_source,
                        )
                        task_list.append(task_dir)
                        task_index += 1
        return task_list

    def post_process(self, task_list):
        pass

    def task_type(self):
        return self.parameter["type"]

    def task_param(self):
        return self.parameter

    def _compute_lower(self, output_file, all_tasks, all_res):
        output_file = os.path.abspath(output_file)
        records = self._load_task_records(all_tasks)
        warnings: List[str] = []
        res_data = {
            "property": PROPERTY_TYPE,
            "description": "noise-reduced finite-temperature elastic constants via paired Langevin stress-strain response",
            "units": {"elastic_tensor": "GPa", "stress_timeseries": "bar"},
            "temperatures": {},
        }
        ptr_data = os.path.dirname(output_file) + "\n"
        ptr_data += "Finite-temperature elastic constants from paired Langevin stress-strain response\n"
        ptr_data += "Method: noise-reduced paired Langevin\n"
        ptr_data += "Stress convention: LAMMPS pressure sign-flipped to Cauchy stress\n"
        ptr_data += "Units: elastic tensor in GPa\n"

        by_temperature: Dict[float, List[Dict]] = {}
        for record in records:
            by_temperature.setdefault(float(record["metadata"]["temperature"]), []).append(record)

        for temperature in sorted(by_temperature):
            temp_records = by_temperature[temperature]
            pairs = self._paired_records(temp_records)
            temp_warnings: List[str] = []
            pair_results = []
            strain_rows = []
            stress_rows = []

            for pair_id, pair in sorted(pairs.items()):
                ref = pair["reference"]
                strained = pair["strained"]
                mean_delta, stderr_delta, block_means, n_samples, pair_warnings = _compute_pair_delta(
                    ref["task_dir"],
                    strained["task_dir"],
                    int(strained["metadata"]["n_blocks"]),
                )
                temp_warnings.extend([f"{pair_id}: {msg}" for msg in pair_warnings])
                component = int(strained["metadata"]["strain_component"])
                strain_value = float(strained["metadata"]["strain_value"])
                strain_voigt = _voigt_strain(component, strain_value)
                strain_rows.append(strain_voigt)
                stress_rows.append(_stress_tensor_to_voigt(mean_delta))
                pair_results.append(
                    {
                        "pair_id": pair_id,
                        "strain_component": component,
                        "strain_label": COMPONENT_LABELS[component],
                        "strain_value": strain_value,
                        "mean_delta_stress_bar": mean_delta.tolist(),
                        "stderr_delta_stress_bar": stderr_delta.tolist(),
                        "block_mean_delta_stress_bar": block_means.tolist(),
                        "n_samples": n_samples,
                        "n_blocks": len(block_means),
                    }
                )

            c_raw_bar, c_sym_bar, rank = _fit_elastic_tensor_bar(
                np.asarray(strain_rows, dtype=float),
                np.asarray(stress_rows, dtype=float),
            )
            c_gpa = _bar_to_gpa(c_sym_bar)
            et = ElasticTensor(c_gpa)
            bulk = float(et.k_voigt)
            shear = float(et.g_voigt)
            young = float(9.0 * bulk * shear / (3.0 * bulk + shear))
            poisson = float(0.5 * (3.0 * bulk - 2.0 * shear) / (3.0 * bulk + shear))

            temp_key = _format_temperature(temperature)
            res_data["temperatures"][temp_key] = {
                "method": "noise-reduced paired Langevin",
                "strain_magnitude": float(self.cal_setting["strain"]),
                "number_of_paired_responses": len(pair_results),
                "rank": rank,
                "elastic_tensor_raw_bar": c_raw_bar.tolist(),
                "elastic_tensor": c_gpa.tolist(),
                "elastic_tensor_GPa": c_gpa.tolist(),
                "equilibrium_stress_bar": None,
                "B": bulk,
                "G": shear,
                "E": young,
                "u": poisson,
                "poisson_ratio": poisson,
                "pairs": pair_results,
                "warnings": temp_warnings,
            }
            warnings.extend(temp_warnings)

            ptr_data += f"\nT = {temperature:g} K\n"
            ptr_data += f"paired responses = {len(pair_results)}\n"
            for row in c_gpa:
                ptr_data += " ".join(f"{val:10.4f}" for val in row) + "\n"
            ptr_data += f"# Bulk   Modulus B = {bulk:.4f} GPa\n"
            ptr_data += f"# Shear  Modulus G = {shear:.4f} GPa\n"
            ptr_data += f"# Youngs Modulus E = {young:.4f} GPa\n"
            ptr_data += f"# Poisson Ratio u = {poisson:.6f}\n"

        if warnings:
            res_data["warnings"] = warnings
            ptr_data += "\nWarnings:\n"
            for warning in warnings:
                ptr_data += f"- {warning}\n"

        dumpfn(res_data, output_file, indent=4)
        return res_data, ptr_data

    def _temperatures(self) -> List[float]:
        temperatures = self.cal_setting["temperature"]
        if isinstance(temperatures, (int, float)):
            temperatures = [temperatures]
        return [float(temp) for temp in temperatures]

    def _make_refine(self, path_to_work: str) -> List[str]:
        if "init_from_suffix" not in self.parameter or "output_suffix" not in self.parameter:
            raise NotImplementedError(
                "FiniteTElastic refine requires init_from_suffix and output_suffix."
            )
        logging.info("FiniteTElastic refine starts")
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
        for task_name in map(os.path.basename, task_list):
            init_task = os.path.join(init_from_path, task_name)
            out_task = os.path.join(path_to_work, task_name)
            for fname in [
                METADATA_FILE,
                "variable_FiniteTElastic.in",
                "deform_FiniteTElastic.in",
                "output_FiniteTElastic.in",
            ]:
                src = os.path.join(init_task, fname)
                dst = os.path.join(out_task, fname)
                if os.path.exists(dst) or os.path.islink(dst):
                    os.remove(dst)
                if os.path.exists(src):
                    os.symlink(os.path.relpath(src, out_task), dst)
                else:
                    raise RuntimeError(f"missing refine source file: {src}")
        return task_list

    def _write_task(
        self,
        task_dir: str,
        structure: Structure,
        ptypes,
        temperature: float,
        role: str,
        pair_id: str | None,
        strain_component: int | None,
        strain_value: float,
        restart_source: str | None,
    ):
        for fname in [
            "INCAR",
            "POTCAR",
            "POSCAR",
            "POSCAR.tmp",
            "conf.lmp",
            "in.lammps",
            "STRU",
            METADATA_FILE,
            "variable_FiniteTElastic.in",
            "deform_FiniteTElastic.in",
            "output_FiniteTElastic.in",
        ]:
            path = os.path.join(task_dir, fname)
            if os.path.exists(path) or os.path.islink(path):
                os.remove(path)

        structure.to(os.path.join(task_dir, "POSCAR.tmp"), "POSCAR")
        vasp_utils.regulate_poscar(os.path.join(task_dir, "POSCAR.tmp"), os.path.join(task_dir, "POSCAR"))
        vasp_utils.sort_poscar(os.path.join(task_dir, "POSCAR"), os.path.join(task_dir, "POSCAR"), ptypes)

        metadata = self._metadata(
            temperature=temperature,
            role=role,
            pair_id=pair_id,
            strain_component=strain_component,
            strain_value=strain_value,
            restart_source=restart_source,
        )
        dumpfn(metadata, os.path.join(task_dir, METADATA_FILE), indent=4)
        with open(os.path.join(task_dir, "variable_FiniteTElastic.in"), "w") as fp:
            fp.write(self._variable(metadata))
        with open(os.path.join(task_dir, "deform_FiniteTElastic.in"), "w") as fp:
            fp.write(_deform_include(strain_component, strain_value))
        with open(os.path.join(task_dir, "output_FiniteTElastic.in"), "w") as fp:
            fp.write(_output_include())

    def _metadata(
        self,
        temperature: float,
        role: str,
        pair_id: str | None,
        strain_component: int | None,
        strain_value: float,
        restart_source: str | None,
    ) -> Dict:
        component = None if strain_component is None else int(strain_component)
        return {
            "property": PROPERTY_TYPE,
            "method": "paired_langevin",
            "temperature": float(temperature),
            "supercell_size": list(self.supercell_size),
            "role": role,
            "pair_id": pair_id,
            "strain_component": component,
            "strain_label": "none" if component is None else COMPONENT_LABELS[component],
            "strain_value": float(strain_value),
            "is_reference": role == "reference",
            "restart_source": restart_source,
            "langevin_seed": int(self.cal_setting["seed"]),
            "timestep": float(self.cal_setting["timestep"]),
            "tdamp": float(self.cal_setting["tdamp"]),
            "pdamp": float(self.cal_setting["pdamp"]),
            "equi_step": int(self.cal_setting["equi_step"]),
            "response_step": int(self.cal_setting["response_step"]),
            "stress_output_every": int(self.cal_setting["stress_output_every"]),
            "n_blocks": int(self.cal_setting["n_blocks"]),
        }

    def _variable(self, metadata: Dict) -> str:
        restart_source = metadata["restart_source"] or EQUI_RESTART
        return (
            "# variable_FiniteTElastic.in\n"
            f"variable role string {metadata['role']}\n"
            f"variable temperature equal {metadata['temperature']:.8g}\n"
            f"variable nx equal {self.supercell_size[0]}\n"
            f"variable ny equal {self.supercell_size[1]}\n"
            f"variable nz equal {self.supercell_size[2]}\n"
            f"variable timestep equal {metadata['timestep']:.8g}\n"
            f"variable tdamp equal {metadata['tdamp']:.8g}\n"
            f"variable pdamp equal {metadata['pdamp']:.8g}\n"
            f"variable seed equal {metadata['langevin_seed']}\n"
            f"variable equi_step equal {metadata['equi_step']}\n"
            f"variable response_step equal {metadata['response_step']}\n"
            f"variable stress_output_every equal {metadata['stress_output_every']}\n"
            f"variable strain equal {metadata['strain_value']:.16g}\n"
            f"variable restart_source string {restart_source}\n"
            f"variable equi_restart string {EQUI_RESTART}\n"
        )

    def _load_task_records(self, all_tasks) -> List[Dict]:
        records = []
        for task_dir in all_tasks:
            metadata_path = os.path.join(task_dir, METADATA_FILE)
            if not os.path.exists(metadata_path):
                raise RuntimeError(f"missing {METADATA_FILE} in {task_dir}")
            metadata = loadfn(metadata_path)
            if metadata.get("property") != PROPERTY_TYPE:
                raise RuntimeError(f"unexpected property metadata in {metadata_path}")
            records.append({"task_dir": task_dir, "metadata": metadata})
        return records

    def _paired_records(self, records: List[Dict]) -> Dict[str, Dict[str, Dict]]:
        pairs: Dict[str, Dict[str, Dict]] = {}
        for record in records:
            role = record["metadata"]["role"]
            if role == "equi":
                continue
            pair_id = record["metadata"].get("pair_id")
            if not pair_id:
                raise RuntimeError(f"missing pair_id in {record['task_dir']}")
            pairs.setdefault(pair_id, {})
            if role not in ["reference", "strained"]:
                raise RuntimeError(f"invalid FiniteTElastic role '{role}' in {record['task_dir']}")
            if role in pairs[pair_id]:
                raise RuntimeError(f"duplicate {role} task for pair_id {pair_id}")
            pairs[pair_id][role] = record

        for pair_id, pair in pairs.items():
            if "reference" not in pair:
                raise RuntimeError(f"missing reference task for pair_id {pair_id}")
            if "strained" not in pair:
                raise RuntimeError(f"missing strained task for pair_id {pair_id}")
        return pairs


def normalize_strain_components(components) -> List[int]:
    normalized = []
    for component in components:
        key = str(component).strip().lower()
        if key not in COMPONENT_ALIASES:
            raise ValueError(f"unsupported strain component '{component}'")
        normalized.append(COMPONENT_ALIASES[key])
    return normalized


def _pressure_bar_to_stress_bar(pressure_tensor):
    return -np.asarray(pressure_tensor, dtype=float)


def _bar_to_gpa(value):
    return np.asarray(value, dtype=float) / 10000.0


def _read_stress_timeseries(task_dir: str) -> Dict[int, np.ndarray]:
    path = os.path.join(task_dir, STRESS_FILE)
    if not os.path.exists(path):
        raise RuntimeError(f"missing {STRESS_FILE} in {task_dir}")

    data: Dict[int, np.ndarray] = {}
    with open(path, "r") as fp:
        for line in fp:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parts = stripped.split()
            if len(parts) != 7:
                continue
            try:
                step = int(float(parts[0]))
                pxx, pyy, pzz, pxy, pxz, pyz = [float(val) for val in parts[1:]]
            except ValueError:
                continue
            pressure = np.array(
                [
                    [pxx, pxy, pxz],
                    [pxy, pyy, pyz],
                    [pxz, pyz, pzz],
                ],
                dtype=float,
            )
            data[step] = pressure
    if not data:
        raise RuntimeError(f"empty or unparsable {STRESS_FILE} in {task_dir}")
    return data


def _compute_pair_delta(ref_task: str, strained_task: str, n_blocks: int):
    ref_pressure = _read_stress_timeseries(ref_task)
    strained_pressure = _read_stress_timeseries(strained_task)
    common_steps = sorted(set(ref_pressure).intersection(strained_pressure))
    if not common_steps:
        raise RuntimeError(f"no common time steps between {ref_task} and {strained_task}")

    warnings = []
    dropped = len(ref_pressure) + len(strained_pressure) - 2 * len(common_steps)
    if dropped:
        warnings.append(f"dropped {dropped} unpaired stress samples during step alignment")

    deltas = []
    for step in common_steps:
        ref_stress = _pressure_bar_to_stress_bar(ref_pressure[step])
        strained_stress = _pressure_bar_to_stress_bar(strained_pressure[step])
        deltas.append(strained_stress - ref_stress)
    deltas = np.asarray(deltas, dtype=float)
    mean = np.mean(deltas, axis=0)
    block_means, stderr = _block_average(deltas, n_blocks)
    return mean, stderr, block_means, len(common_steps), warnings


def _block_average(values, n_blocks: int):
    arr = np.asarray(values, dtype=float)
    if arr.shape[0] == 0:
        raise RuntimeError("cannot block-average an empty array")
    n_blocks = max(1, min(int(n_blocks), arr.shape[0]))
    chunks = np.array_split(arr, n_blocks, axis=0)
    block_means = np.asarray([np.mean(chunk, axis=0) for chunk in chunks])
    if len(block_means) == 1:
        stderr = np.zeros_like(block_means[0], dtype=float)
    else:
        stderr = np.std(block_means, axis=0, ddof=1) / math.sqrt(len(block_means))
    return block_means, stderr


def _voigt_strain_tensor(component: int, strain_value: float):
    tensor = np.zeros((3, 3), dtype=float)
    if component in [0, 1, 2]:
        tensor[component, component] = strain_value
    elif component == 3:
        tensor[1, 2] = tensor[2, 1] = strain_value / 2.0
    elif component == 4:
        tensor[0, 2] = tensor[2, 0] = strain_value / 2.0
    elif component == 5:
        tensor[0, 1] = tensor[1, 0] = strain_value / 2.0
    else:
        raise ValueError(f"unsupported strain component {component}")
    return tensor


def _voigt_strain(component: int, strain_value: float):
    """Return engineering Voigt strain used by the 6x6 elastic matrix fit.

    LAMMPS tilt changes use engineering shear gamma. The tensor strain has
    epsilon_ij = gamma / 2, while the Voigt strain vector for C44/C55/C66 uses
    gamma so stress_ij = C_ij * gamma.
    """
    voigt = np.zeros(6, dtype=float)
    voigt[component] = strain_value
    return voigt


def _stress_tensor_to_voigt(stress_tensor):
    stress = np.asarray(stress_tensor, dtype=float)
    return np.array(
        [stress[0, 0], stress[1, 1], stress[2, 2], stress[1, 2], stress[0, 2], stress[0, 1]],
        dtype=float,
    )


def _fit_elastic_tensor_bar(strain_rows, stress_rows):
    strain = np.asarray(strain_rows, dtype=float)
    stress = np.asarray(stress_rows, dtype=float)
    if strain.ndim != 2 or strain.shape[1] != 6:
        raise RuntimeError("strain matrix must have shape (n_pairs, 6)")
    if stress.ndim != 2 or stress.shape != (strain.shape[0], 6):
        raise RuntimeError("stress matrix must have shape (n_pairs, 6)")

    rank = int(np.linalg.matrix_rank(strain))
    if rank < 6:
        raise RuntimeError(
            f"insufficient independent strain responses to fit full elastic tensor: rank {rank} < 6"
        )
    solution, _, _, _ = np.linalg.lstsq(strain, stress, rcond=None)
    c_raw = solution.T
    c_sym = 0.5 * (c_raw + c_raw.T)
    return c_raw, c_sym, rank


def _deform_include(strain_component: int | None, strain_value: float) -> str:
    ret = "# deform_FiniteTElastic.in\n"
    ret += "# Shear tilt uses engineering shear gamma; fitting uses Voigt shear gamma.\n"
    if strain_component is None or abs(float(strain_value)) == 0.0:
        ret += "# reference/equilibration task: no strain deformation\n"
        return ret
    if strain_component == 0:
        ret += "variable scale_x equal 1.0+${strain}\n"
        ret += "change_box all x scale ${scale_x} remap units box\n"
    elif strain_component == 1:
        ret += "variable scale_y equal 1.0+${strain}\n"
        ret += "change_box all y scale ${scale_y} remap units box\n"
    elif strain_component == 2:
        ret += "variable scale_z equal 1.0+${strain}\n"
        ret += "change_box all z scale ${scale_z} remap units box\n"
    elif strain_component == 3:
        ret += "variable d_yz equal ${strain}*lz\n"
        ret += "change_box all yz delta ${d_yz} remap units box\n"
    elif strain_component == 4:
        ret += "variable d_xz equal ${strain}*lz\n"
        ret += "change_box all xz delta ${d_xz} remap units box\n"
    elif strain_component == 5:
        ret += "variable d_xy equal ${strain}*ly\n"
        ret += "change_box all xy delta ${d_xy} remap units box\n"
    else:
        raise ValueError(f"unsupported strain component {strain_component}")
    return ret


def _output_include() -> str:
    return (
        "# output_FiniteTElastic.in\n"
        "variable out_pxx equal pxx\n"
        "variable out_pyy equal pyy\n"
        "variable out_pzz equal pzz\n"
        "variable out_pxy equal pxy\n"
        "variable out_pxz equal pxz\n"
        "variable out_pyz equal pyz\n"
        "fix stress_out all ave/time ${stress_output_every} 1 ${stress_output_every} "
        "v_out_pxx v_out_pyy v_out_pzz v_out_pxy v_out_pxz v_out_pyz "
        "file stress_timeseries.txt title1 '# step pxx pyy pzz pxy pxz pyz'\n"
    )


def _format_temperature(temperature: float) -> str:
    if float(temperature).is_integer():
        return str(int(temperature))
    return ("%g" % temperature).replace(".", "p")
