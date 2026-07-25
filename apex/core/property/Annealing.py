import glob
import math
import os
import logging
import re
from typing import List, Dict, Any

import numpy as np
from monty.serialization import dumpfn, loadfn
from pymatgen.core.lattice import Lattice
from pymatgen.core.structure import Structure
from dflow.python import upload_packages

from apex.core.property.Property import Property
from apex.core.calculator.lib import vasp_utils
from apex.core.lib.vasp_trajectory import (
    parse_outcar_stage_geometry,
    split_apex_stage_sections,
    tail_align_stage_geometry,
)

upload_packages.append(__file__)


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


class Annealing(Property):

    def __init__(self, parameter: Dict[str, Any], inter_param=None):
        self.inter_param = inter_param if inter_param is not None else {"type": "lammps"}
        if self.inter_param.get("type") == "abacus":
            raise NotImplementedError(
                "annealing does not support the ABACUS backend; "
                "use LAMMPS or VASP"
            )
        parameter["cal_type"] = "static"
        self.parameter = parameter

        # geometry
        self.supercell_size = parameter.get("supercell_size", [2, 2, 2])
        self.supercell_length = parameter.get("supercell_length", None)

        # MD controls (independent knobs only)
        cal = parameter.setdefault("cal_setting", {})
        self.protocol = parameter.get("protocol", cal.get("protocol", "ramp_cool"))
        if self.protocol not in {"ramp_cool", "coexistence"}:
            raise ValueError(
                "annealing protocol must be 'ramp_cool' or 'coexistence'"
            )
        if self.protocol == "coexistence" and self.inter_param.get("type") != "vasp":
            raise ValueError(
                "annealing protocol='coexistence' currently supports only VASP"
            )
        dft_backend = self.inter_param.get("type") == "vasp"
        equi_default = (
            5000 if self.protocol == "coexistence" else (100 if dft_backend else 20000)
        )
        ramp_default = 200 if dft_backend else 0
        final_equi_default = 100 if dft_backend else 20000
        cool_default = 200 if dft_backend else 0
        # Schedule defaults mirror annealing/spec.
        self.start_temp = float(cal.get("start_temp", 4))
        _tgt = cal.get("target_temp", cal.get("temp", 300))
        self.target_temp = float(_tgt if not isinstance(_tgt, list) else _tgt[0])
        self.end_temp = float(cal.get("end_temp", 4))
        self._has_ramp_rate = "temp_ramp_rate" in cal or "ramp_rate" in cal
        self._has_cool_rate = "cool_rate" in cal
        self.temp_ramp_rate = cal.get("temp_ramp_rate", cal.get("ramp_rate", 1000))
        self.cool_rate = cal.get("cool_rate", self.temp_ramp_rate)
        self.equi_step = int(
            cal.get("equi_step", cal.get("init_thermo_equil_step", equi_default))
        )
        self.init_lgv_thermo_equil_step = int(
            cal.get("init_lgv_thermo_equil_step", equi_default)
        )
        self.init_thermo_equil_step = int(cal.get("init_thermo_equil_step", self.equi_step))
        self.final_thermo_equil_step = int(
            cal.get(
                "final_thermo_equil_step",
                cal.get("hold_step", final_equi_default),
            )
        )
        # Explicit step counts override rate-derived counts when provided.
        self.ramp_step = int(
            cal.get("ramp_step", cal.get("temp_ramp_step", ramp_default))
        )
        self.cool_step = int(
            cal.get("cool_step", cal.get("temp_decline_step", cool_default))
        )
        self.hold_step = int(cal.get("hold_step", self.final_thermo_equil_step))
        self.production_step = int(cal.get("production_step", 10000))
        # options
        self.thermostat = cal.get("thermostat", "nose_hoover")
        self.ensemble = cal.get("ensemble", "npt")
        if "timestep_fs" in cal:
            self.timestep_fs = float(cal["timestep_fs"])
            self.timestep = self.timestep_fs / 1000.0
        else:
            self.timestep = float(cal.get("timestep", 0.001))
            self.timestep_fs = 1000.0 * self.timestep
        self.tdamp_factor = cal.get("tdamp_factor", 100)
        self.pdamp_factor = cal.get("pdamp_factor", 1000)
        self.tdamp = cal.get("tdamp")
        self.pdamp = cal.get("pdamp")
        self.velocity_seed = cal.get("velocity_seed", cal.get("init_v_seed", 123457))
        self.lgv_seed = cal.get("lgv_seed", self.velocity_seed)
        self.req_lgv_damping = _as_bool(cal.get("req_lgv_damping", False))
        self.req_opti_init_structure = _as_bool(cal.get("req_opti_init_structure", True))
        self.req_write_restart = _as_bool(cal.get("req_write_restart", True))
        self.req_dump_init_atom = _as_bool(cal.get("req_dump_init_atom", True))
        self.req_dump_ave_atom = _as_bool(cal.get("req_dump_ave_atom", False))
        self.dump_step = int(cal.get("dump_step", cal.get("dump_interval", 2000)))
        self.dump_interval = int(cal.get("dump_interval", self.dump_step))
        self.thermo_interval = int(cal.get("thermo_interval", 2000))
        self.restart_interval = int(cal.get("restart_interval", 20000))
        self.ave_atom_sample_feq = int(cal.get("ave_atom_sample_feq", 1))
        self.ave_atom_num_sample = int(cal.get("ave_atom_num_sample", self.dump_interval))
        self.ave_atom_sample_length = int(cal.get(
            "ave_atom_sample_length",
            self.ave_atom_sample_feq * self.ave_atom_num_sample,
        ))
        self.init_opt_loop_size = int(cal.get("init_opt_loop_size", 10))
        self.init_fmax_tol = cal.get("init_fmax_tol", 1.0e-8)
        self.init_stress_tol = cal.get("init_stress_tol", 1.0e-2)
        # RDF settings
        self.req_compute_rdf = _as_bool(cal.get("req_compute_rdf", True))
        self.rdf_bins = int(cal.get("rdf_bins", 100))
        self.rdf_cutoff = float(cal.get("rdf_cutoff", 6.0))
        self.rdf_nevery = int(cal.get("rdf_nevery", cal.get("rdf_interval", 100)))
        self.rdf_nrepeat = int(cal.get("rdf_nrepeat", 1))
        self.rdf_nfreq = int(cal.get("rdf_nfreq", cal.get("rdf_interval", 200)))
        self.rdf_interval = int(cal.get("rdf_interval", self.rdf_nfreq))
        # MSD settings
        self.req_compute_msd = _as_bool(cal.get("req_compute_msd", True))
        self.msd_nevery = int(cal.get("msd_nevery", 100))
        self.msd_nrepeat = int(cal.get("msd_nrepeat", 1))
        self.msd_nfreq = int(cal.get("msd_nfreq", 200))

    def task_type(self):
        return self.parameter["type"]

    def task_param(self):
        # make cal_setting explicit and in-sync
        cal = self.parameter.setdefault("cal_setting", {})
        cal.update(
            {
                "start_temp": self.start_temp,
                "protocol": self.protocol,
                "target_temp": self.target_temp,
                "temp": self.target_temp,
                "end_temp": self.end_temp,
                "temp_ramp_rate": self.temp_ramp_rate,
                "equi_step": self.equi_step,
                "init_lgv_thermo_equil_step": self.init_lgv_thermo_equil_step,
                "init_thermo_equil_step": self.init_thermo_equil_step,
                "final_thermo_equil_step": self.final_thermo_equil_step,
                "ramp_step": self.ramp_step,
                "temp_ramp_step": self.ramp_step,
                "hold_step": self.hold_step,
                "production_step": self.production_step,
                "cool_step": self.cool_step,
                "temp_decline_step": self.cool_step,
                "thermostat": self.thermostat,
                "ensemble": self.ensemble,
                "tdamp_factor": self.tdamp_factor,
                "pdamp_factor": self.pdamp_factor,
                "velocity_seed": self.velocity_seed,
                "lgv_seed": self.lgv_seed,
                "req_lgv_damping": self.req_lgv_damping,
                "req_opti_init_structure": self.req_opti_init_structure,
                "req_write_restart": self.req_write_restart,
                "req_dump_init_atom": self.req_dump_init_atom,
                "req_dump_ave_atom": self.req_dump_ave_atom,
                "dump_step": self.dump_step,
                "dump_interval": self.dump_interval,
                "thermo_interval": self.thermo_interval,
                "restart_interval": self.restart_interval,
                "ave_atom_sample_feq": self.ave_atom_sample_feq,
                "ave_atom_num_sample": self.ave_atom_num_sample,
                "ave_atom_sample_length": self.ave_atom_sample_length,
                "init_opt_loop_size": self.init_opt_loop_size,
                "init_fmax_tol": self.init_fmax_tol,
                "init_stress_tol": self.init_stress_tol,
                "timestep": self.timestep,
                "timestep_fs": self.timestep_fs,
                "req_compute_rdf": self.req_compute_rdf,
                "rdf_bins": self.rdf_bins,
                "rdf_cutoff": self.rdf_cutoff,
                "rdf_interval": self.rdf_interval,
                "rdf_nevery": self.rdf_nevery,
                "rdf_nrepeat": self.rdf_nrepeat,
                "rdf_nfreq": self.rdf_nfreq,
                "req_compute_msd": self.req_compute_msd,
                "msd_nevery": self.msd_nevery,
                "msd_nrepeat": self.msd_nrepeat,
                "msd_nfreq": self.msd_nfreq,
            }
        )
        if self.tdamp is not None:
            cal["tdamp"] = self.tdamp
        if self.pdamp is not None:
            cal["pdamp"] = self.pdamp
        self.parameter["supercell_size"] = self.supercell_size
        if self.supercell_length is not None:
            self.parameter["supercell_length"] = self.supercell_length
        return self.parameter

    def make_confs(self, path_to_work: str, path_to_equi: str, refine=False) -> List[str]:
        path_to_work = os.path.abspath(path_to_work)
        if not os.path.exists(path_to_work):
            os.makedirs(path_to_work)
        else:
            logging.warning("%s already exists" % path_to_work)

        equi_structure = os.path.join(path_to_equi, "CONTCAR")
        if not os.path.isdir(path_to_equi) or not os.path.isfile(equi_structure):
            raise RuntimeError("please finish relaxation before annealing")

        task_list: List[str] = []
        # One task per target_temp (allow list), else single
        targets = self.parameter.get("cal_setting", {}).get(
            "target_temp",
            self.parameter.get("cal_setting", {}).get("temp", self.target_temp),
        )
        if not isinstance(targets, list):
            targets = [targets]

        for idx, tgt in enumerate(targets):
            task_dir = os.path.join(path_to_work, f"task.{idx:06d}")
            os.makedirs(task_dir, exist_ok=True)

            import shutil
            shutil.copy(equi_structure, os.path.join(task_dir, "POSCAR"))
            s_sorted = Structure.from_file(os.path.join(task_dir, "POSCAR"))
            lattice_lengths = list(s_sorted.lattice.abc)

            # Derive integer replication from physical length if requested
            if self.supercell_length is not None:
                try:
                    a, b, c = lattice_lengths
                    import math
                    sx, sy, sz = self.supercell_length
                    nx = max(1, int(math.ceil(sx / a)))
                    ny = max(1, int(math.ceil(sy / b)))
                    nz = max(1, int(math.ceil(sz / c)))
                    self.supercell_size = [nx, ny, nz]
                except Exception as e:
                    logging.warning(f"Failed to derive supercell_size from supercell_length: {e}")
            if self.inter_param.get("type") == "vasp":
                s_sorted.make_supercell(self.supercell_size)
                s_sorted.to(filename=os.path.join(task_dir, "POSCAR"))

            # Persist params per task
            anneal_task = {
                "protocol": self.protocol,
                "start_temp": self.start_temp,
                "target_temp": float(tgt),
                "temp": float(tgt),
                "end_temp": self.end_temp,
                "temp_ramp_rate": self.temp_ramp_rate,
                "supercell_size": self.supercell_size,
            }
            dumpfn(anneal_task, os.path.join(task_dir, "Annealing.json"), indent=4)

            # variable_Annealing.in for LAMMPS
            var = []
            var.append("# variable_Annealing.in")
            var.append(f"variable nx equal {self.supercell_size[0]}")
            var.append(f"variable ny equal {self.supercell_size[1]}")
            var.append(f"variable nz equal {self.supercell_size[2]}")
            var.append(f"variable start_temp equal {self.start_temp:.2f}")
            var.append(f"variable target_temp equal {float(tgt):.2f}")
            var.append(f"variable temp equal {float(tgt):.2f}")
            var.append(f"variable end_temp equal {self.end_temp:.2f}")
            var.append(f"variable temp_ramp_rate equal {self.temp_ramp_rate}")
            var.append(f"variable equi_step equal {self.equi_step}")
            # derive ramp/cool steps if rates are provided (K/step); else use defaults
            import math
            if self._has_ramp_rate and self.temp_ramp_rate is not None:
                try:
                    # convert K/ns -> steps using timestep (ps): dt_ns = dt_ps/1000
                    rstep = max(1, int(math.ceil(abs(float(tgt) - self.start_temp) * 1000.0 / (float(self.temp_ramp_rate) * self.timestep))))
                except Exception:
                    rstep = self.ramp_step
            elif self.ramp_step > 0:
                rstep = self.ramp_step
            elif self.temp_ramp_rate is not None:
                try:
                    rstep = max(1, int(math.ceil(abs(float(tgt) - self.start_temp) * 1000.0 / (float(self.temp_ramp_rate) * self.timestep))))
                except Exception:
                    rstep = self.ramp_step
            else:
                rstep = self.ramp_step
            if (self._has_cool_rate or self._has_ramp_rate) and self.cool_rate is not None:
                try:
                    cr = self.cool_rate[idx] if isinstance(self.cool_rate, (list, tuple)) else float(self.cool_rate)
                    cstep = max(1, int(math.ceil(abs(float(tgt) - self.end_temp) * 1000.0 / (float(cr) * self.timestep))))
                except Exception:
                    cstep = self.cool_step
            elif self.cool_step > 0:
                cstep = self.cool_step
            elif self.cool_rate is not None:
                try:
                    cstep = max(1, int(math.ceil(abs(float(tgt) - self.end_temp) * 1000.0 / (float(self.cool_rate) * self.timestep))))
                except Exception:
                    cstep = self.cool_step
            else:
                cstep = self.cool_step
            var.append(f"variable ramp_step equal {rstep}")
            var.append(f"variable temp_ramp_step equal {rstep}")
            var.append(f"variable temp_ramp_remain_step equal {rstep}")
            var.append(f"variable hold_step equal {self.hold_step}")
            var.append(f"variable cool_step equal {cstep}")
            var.append(f"variable temp_decline_step equal {cstep}")
            var.append(f"variable temp_decline_remain_step equal {cstep}")
            var.append(f"variable init_lgv_thermo_equil_step equal {self.init_lgv_thermo_equil_step}")
            var.append(f"variable init_thermo_equil_step equal {self.init_thermo_equil_step}")
            var.append(f"variable final_thermo_equil_step equal {self.final_thermo_equil_step}")
            var.append(f"variable final_thermo_equil_remain_step equal {self.final_thermo_equil_step}")
            var.append(f"variable timestep equal {self.timestep}")
            var.append(f"variable thermo_interval equal {self.thermo_interval}")
            var.append(f"variable dump_interval equal {self.dump_interval}")
            var.append(f"variable restart_interval equal {self.restart_interval}")
            var.append(f"variable req_lgv_damping equal {str(self.req_lgv_damping).lower()}")
            var.append(f"variable req_opti_init_structure equal {str(self.req_opti_init_structure).lower()}")
            var.append(f"variable req_write_restart equal {str(self.req_write_restart).lower()}")
            var.append(f"variable req_dump_init_atom equal {str(self.req_dump_init_atom).lower()}")
            var.append(f"variable req_dump_ave_atom equal {str(self.req_dump_ave_atom).lower()}")
            var.append(f"variable ave_atom_sample_feq equal {self.ave_atom_sample_feq}")
            var.append(f"variable ave_atom_num_sample equal {self.ave_atom_num_sample}")
            var.append(f"variable ave_atom_sample_length equal {self.ave_atom_sample_length}")
            var.append(f"variable init_opt_loop_size equal {self.init_opt_loop_size}")
            var.append(f"variable init_fmax_tol equal {self.init_fmax_tol}")
            var.append(f"variable init_stress_tol equal {self.init_stress_tol}")
            var.append(f"variable req_compute_rdf equal {str(self.req_compute_rdf).lower()}")
            var.append(f"variable rdf_bins equal {self.rdf_bins}")
            var.append(f"variable rdf_cutoff equal {self.rdf_cutoff}")
            var.append(f"variable rdf_interval equal {self.rdf_interval}")
            var.append(f"variable rdf_nevery equal {self.rdf_nevery}")
            var.append(f"variable rdf_nrepeat equal {self.rdf_nrepeat}")
            var.append(f"variable rdf_nfreq equal {self.rdf_nfreq}")
            var.append("variable rdf_file_eq string rdf.eq_${start_temp}K.txt")
            var.append("variable rdf_file_ramp string rdf.T_ramp_${start_temp}K_${temp}K.txt")
            var.append("variable rdf_file_decline string rdf.T_decline_${temp}K_${end_temp}K.txt")
            var.append("variable rdf_file_final_eq string rdf.final_eq_${end_temp}K.txt")
            var.append(f"variable req_compute_msd equal {str(self.req_compute_msd).lower()}")
            var.append(f"variable msd_nevery equal {self.msd_nevery}")
            var.append(f"variable msd_nrepeat equal {self.msd_nrepeat}")
            var.append(f"variable msd_nfreq equal {self.msd_nfreq}")
            var.append("variable msd_file_eq string msd.eq_${start_temp}K.txt")
            var.append("variable msd_file_ramp string msd.T_ramp_${start_temp}K_${temp}K.txt")
            var.append("variable msd_file_decline string msd.T_decline_${temp}K_${end_temp}K.txt")
            var.append("variable msd_file_final_eq string msd.final_eq_${end_temp}K.txt")
            var.append(f"variable tdamp_factor equal {self.tdamp_factor}")
            var.append(f"variable pdamp_factor equal {self.pdamp_factor}")
            if self.tdamp is not None:
                var.append(f"variable tdamp equal {self.tdamp}")
            else:
                var.append("variable tdamp equal v_tdamp_factor*${timestep}")
            if self.pdamp is not None:
                var.append(f"variable pdamp equal {self.pdamp}")
            else:
                var.append("variable pdamp equal v_pdamp_factor*${timestep}")
            var.append(f"variable velocity_seed equal {int(self.velocity_seed)}")
            var.append(f"variable init_v_seed equal {int(self.velocity_seed)}")
            var.append(f"variable lgv_seed equal {int(self.lgv_seed)}")
            var.append(f"variable dump_step equal {self.dump_step}")
            var.append(f"variable thermostat string {self.thermostat}")
            var.append(f"variable ensemble string {self.ensemble}")
            if self.inter_param.get("type") != "vasp":
                with open(os.path.join(task_dir, "variable_Annealing.in"), "w") as fp:
                    fp.write("\n".join(var) + "\n")

            anneal_task.update(
                {
                    "equi_step": self.init_thermo_equil_step,
                    "production_step": self.production_step,
                    "ramp_step": rstep,
                    "cool_step": cstep,
                    "final_equi_step": self.final_thermo_equil_step,
                    "timestep_fs": self.timestep_fs,
                    "req_compute_rdf": self.req_compute_rdf,
                    "req_compute_msd": self.req_compute_msd,
                    "rdf_bins": self.rdf_bins,
                    "rdf_cutoff": self.rdf_cutoff,
                }
            )
            dumpfn(anneal_task, os.path.join(task_dir, "Annealing.json"), indent=4)

            task_list.append(task_dir)

        return task_list

    def post_process(self, task_list: List[str]):
        # No post aggregation for annealing in this minimal port
        pass

    def _compute_lower(self, output_file, all_tasks, all_res):
        res_data = {
            "property": "annealing",
            "tasks": {},
        }
        ptr_lines = [os.path.dirname(output_file)]
        for task_dir in all_tasks:
            name = os.path.basename(task_dir)
            task_result = self._collect_task_result(task_dir)
            res_data["tasks"][name] = task_result
            ptr_lines.append(self._task_summary_line(name, task_result))
        ptr_data = "\n".join(ptr_lines) + "\n"
        dumpfn(res_data, output_file, indent=4)
        return res_data, ptr_data

    @classmethod
    def _collect_task_result(cls, task_dir: str) -> Dict[str, Any]:
        task_path = os.path.abspath(task_dir)
        metadata_path = os.path.join(task_path, "Annealing.json")
        status_path = os.path.join(task_path, "apex_task_status.json")
        metadata = cls._safe_load_json(metadata_path)
        dft_result = cls._collect_dft_result(task_path, metadata)
        result = {
            "task": os.path.basename(task_path),
            "path": task_path,
            "metadata": metadata,
            "status": cls._safe_load_json(status_path),
            "rdf": dft_result.get("rdf", cls._collect_rdf(task_path)),
            "msd": dft_result.get("msd", cls._collect_msd(task_path)),
            "volume_temperature": dft_result.get(
                "volume_temperature", cls._collect_volume_temperature(task_path)
            ),
        }
        result["summary"] = cls._build_summary(result)
        return result

    @classmethod
    def _collect_dft_result(cls, task_path, metadata):
        xdatcar = os.path.join(task_path, "XDATCAR")
        if os.path.isfile(xdatcar):
            frames = cls._parse_vasp_xdatcar(xdatcar)
            cls._attach_vasp_thermo(
                frames, os.path.join(task_path, "OUTCAR")
            )
        else:
            return {}
        if not frames:
            return {}

        stage_names = {
            "eq": f"eq_{metadata.get('start_temp', 0):g}K",
            "equi": f"equi_{metadata.get('target_temp', 0):g}K",
            "production": f"production_{metadata.get('target_temp', 0):g}K",
            "ramp": (
                f"T_ramp_{metadata.get('start_temp', 0):g}K_"
                f"{metadata.get('target_temp', 0):g}K"
            ),
            "decline": (
                f"T_decline_{metadata.get('target_temp', 0):g}K_"
                f"{metadata.get('end_temp', 0):g}K"
            ),
            "final_eq": f"final_eq_{metadata.get('end_temp', 0):g}K",
        }
        rdf = {}
        msd = {}
        volume_temperature = {}
        bins = int(metadata.get("rdf_bins", 100))
        cutoff = float(metadata.get("rdf_cutoff", 6.0))
        timestep_fs = float(metadata.get("timestep_fs", 1.0))
        compute_rdf = _as_bool(metadata.get("req_compute_rdf", True))
        compute_msd = _as_bool(metadata.get("req_compute_msd", True))
        for stage, stage_frames in frames.items():
            if not stage_frames:
                continue
            label = stage_names.get(stage, stage)
            first = stage_frames[0]
            natoms = len(first["frac"])
            if compute_rdf:
                hist = np.zeros(bins, dtype=float)
                edges = np.linspace(0.0, cutoff, bins + 1)
                pair_indices = np.triu_indices(natoms, 1)
                for frame in stage_frames:
                    structure = Structure(
                        Lattice(frame["cell"]),
                        frame["labels"],
                        frame["frac"],
                    )
                    distances = structure.distance_matrix[pair_indices]
                    hist += np.histogram(distances, bins=edges)[0]
                radius = 0.5 * (edges[:-1] + edges[1:])
                volume = float(
                    np.mean([abs(np.linalg.det(f["cell"])) for f in stage_frames])
                )
                shell = (
                    4.0
                    * math.pi
                    / 3.0
                    * (edges[1:] ** 3 - edges[:-1] ** 3)
                )
                expected = (
                    0.5
                    * natoms
                    * max(natoms - 1, 0)
                    / volume
                    * shell
                    * len(stage_frames)
                )
                g_r = np.divide(
                    hist,
                    expected,
                    out=np.zeros_like(hist),
                    where=expected > 0,
                )
                coordination = (
                    2.0 * np.cumsum(hist) / (natoms * len(stage_frames))
                )
                rdf[label] = {
                    "source": os.path.basename(
                        xdatcar
                    ),
                    "timestep": stage_frames[-1]["step"],
                    "nblocks": len(stage_frames),
                    "columns": {},
                    "radius": radius.tolist(),
                    "g_r": g_r.tolist(),
                    "coordination": coordination.tolist(),
                }

            if compute_msd:
                reference = first["frac"].copy()
                previous = reference.copy()
                unwrapped = reference.copy()
                reference_cart = np.dot(reference, first["cell"])
                msd_x, msd_y, msd_z, msd_total, timesteps = [], [], [], [], []
                for index, frame in enumerate(stage_frames):
                    if index:
                        delta = frame["frac"] - previous
                        delta -= np.rint(delta)
                        unwrapped += delta
                        previous = frame["frac"].copy()
                    displacement = np.dot(unwrapped, frame["cell"]) - reference_cart
                    components = np.mean(displacement ** 2, axis=0)
                    timesteps.append(float(frame["step"]) * timestep_fs)
                    msd_x.append(float(components[0]))
                    msd_y.append(float(components[1]))
                    msd_z.append(float(components[2]))
                    msd_total.append(float(np.sum(components)))
                msd[label] = {
                    "source": os.path.basename(
                        xdatcar
                    ),
                    "timestep": timesteps,
                    "msd_x": msd_x,
                    "msd_y": msd_y,
                    "msd_z": msd_z,
                    "msd_total": msd_total,
                }

            if stage in {"ramp", "decline", "production"}:
                vt_stage = {
                    "ramp": "heating",
                    "decline": "cooling",
                    "production": "production",
                }[stage]
                volume_temperature[vt_stage] = {
                    "source": os.path.basename(
                        os.path.join(task_path, "OUTCAR")
                    ),
                    "timestep": [frame["step"] for frame in stage_frames],
                    "temperature": [
                        frame.get("temperature") for frame in stage_frames
                    ],
                    "volume_per_atom": [
                        frame.get(
                            "outcar_volume", abs(np.linalg.det(frame["cell"]))
                        )
                        / natoms
                        for frame in stage_frames
                    ],
                    "total_volume": [
                        frame.get(
                            "outcar_volume", abs(np.linalg.det(frame["cell"]))
                        )
                        for frame in stage_frames
                    ],
                    "potential_energy": [
                        frame.get("potential_energy") for frame in stage_frames
                    ],
                    "total_energy": [
                        frame.get("total_energy") for frame in stage_frames
                    ],
                    "pressure": [frame.get("pressure") for frame in stage_frames],
                }
        return {
            "rdf": rdf,
            "msd": msd,
            "volume_temperature": volume_temperature,
        }

    @staticmethod
    def _stage_sections(path):
        with open(path, encoding="utf-8", errors="ignore") as fp:
            return split_apex_stage_sections(fp.read())

    @classmethod
    def _parse_vasp_xdatcar(cls, path):
        result = {}
        for stage, text in cls._stage_sections(path).items():
            lines = text.splitlines()
            frames = []
            idx = 0
            cell = None
            labels = []
            natoms = 0
            while idx < len(lines):
                if not lines[idx].strip():
                    idx += 1
                    continue
                if "Direct configuration=" not in lines[idx]:
                    try:
                        scale = float(lines[idx + 1].split()[0])
                        cell = np.asarray(
                            [
                                [float(value) for value in lines[idx + offset].split()[:3]]
                                for offset in (2, 3, 4)
                            ]
                        ) * scale
                        species = lines[idx + 5].split()
                        counts = [int(value) for value in lines[idx + 6].split()]
                        labels = [
                            symbol
                            for symbol, count in zip(species, counts)
                            for _ in range(count)
                        ]
                        natoms = sum(counts)
                        idx += 7
                    except (IndexError, ValueError):
                        idx += 1
                        continue
                if idx >= len(lines) or "Direct configuration=" not in lines[idx]:
                    continue
                match = re.search(r"=\s*(\d+)", lines[idx])
                step = int(match.group(1)) if match else len(frames)
                try:
                    frac = np.asarray(
                        [
                            [float(value) for value in lines[idx + offset].split()[:3]]
                            for offset in range(1, natoms + 1)
                        ]
                    )
                except (IndexError, ValueError):
                    break
                frames.append(
                    {
                        "step": step,
                        "cell": cell.copy(),
                        "frac": frac,
                        "labels": labels,
                    }
                )
                idx += natoms + 1
            result[stage] = frames
        return result

    @classmethod
    def _attach_vasp_thermo(cls, frames, outcar):
        if not os.path.isfile(outcar):
            return
        with open(outcar, encoding="utf-8", errors="ignore") as fp:
            outcar_text = fp.read()
        sections = split_apex_stage_sections(outcar_text)
        tail_align_stage_geometry(
            frames, parse_outcar_stage_geometry(outcar_text)
        )
        for stage, text in sections.items():
            stage_frames = frames.get(stage, [])
            if not stage_frames:
                continue
            values = {
                "temperature": [
                    float(value)
                    for value in re.findall(
                        r"(?:temperature\s*=|\bT=)\s*([-+0-9.eE]+)", text
                    )
                ],
                "pressure": [
                    float(value)
                    for value in re.findall(
                        r"external pressure\s*=\s*([-+0-9.eE]+)", text
                    )
                ],
                "potential_energy": [
                    float(value)
                    for value in re.findall(
                        r"free\s+energy\s+TOTEN\s*=\s*([-+0-9.eE]+)", text
                    )
                ],
                "total_energy": [
                    float(value)
                    for value in re.findall(
                        r"total energy\s+ETOTAL\s*=\s*([-+0-9.eE]+)", text
                    )
                ],
            }
            for key, series in values.items():
                if not series:
                    continue
                series = series[-len(stage_frames):]
                for frame, value in zip(stage_frames[-len(series):], series):
                    frame[key] = value

    @staticmethod
    def _safe_load_json(path: str):
        try:
            if os.path.isfile(path):
                return loadfn(path)
        except Exception as exc:
            return {"error": f"failed to read {os.path.basename(path)}: {exc}"}
        return {}

    @classmethod
    def _collect_rdf(cls, task_path: str) -> Dict[str, Any]:
        stages = {}
        for path in sorted(glob.glob(os.path.join(task_path, "rdf.*.txt"))):
            stage = cls._stage_from_analysis_file(path, prefix="rdf", suffix=".txt")
            parsed = cls._parse_rdf_file(path)
            if parsed:
                stages[stage] = parsed
        return stages

    @classmethod
    def _collect_msd(cls, task_path: str) -> Dict[str, Any]:
        stages = {}
        for path in sorted(glob.glob(os.path.join(task_path, "msd.*.txt"))):
            stage = cls._stage_from_analysis_file(path, prefix="msd", suffix=".txt")
            parsed = cls._parse_msd_file(path)
            if parsed:
                stages[stage] = parsed
        return stages

    @classmethod
    def _collect_volume_temperature(cls, task_path: str) -> Dict[str, Any]:
        stages = {}
        for path in sorted(glob.glob(os.path.join(task_path, "heating_interval_*.dat"))):
            parsed = cls._parse_thermo_interval_file(path)
            if parsed:
                stages["heating"] = parsed
        for path in sorted(glob.glob(os.path.join(task_path, "cooling_interval_*.dat"))):
            parsed = cls._parse_thermo_interval_file(path)
            if parsed:
                stages["cooling"] = parsed
        return stages

    @staticmethod
    def _stage_from_analysis_file(path: str, prefix: str, suffix: str) -> str:
        name = os.path.basename(path)
        if name.startswith(prefix + "."):
            name = name[len(prefix) + 1:]
        if suffix and name.endswith(suffix):
            name = name[:-len(suffix)]
        return name

    @staticmethod
    def _numeric_tokens(line: str) -> List[float]:
        values = []
        for token in line.split():
            try:
                values.append(float(token))
            except ValueError:
                return []
        return values

    @classmethod
    def _parse_ave_time_blocks(cls, path: str):
        column_names = []
        blocks = []
        try:
            with open(path, "r", errors="replace") as fp:
                lines = fp.readlines()
        except OSError:
            return column_names, blocks

        idx = 0
        while idx < len(lines):
            line = lines[idx].strip()
            if not line:
                idx += 1
                continue
            if line.startswith("#"):
                if line.startswith("# Row "):
                    column_names = line[2:].split()[1:]
                idx += 1
                continue

            header = line.split()
            if len(header) != 2:
                idx += 1
                continue
            try:
                timestep = int(float(header[0]))
                nrows = int(float(header[1]))
            except ValueError:
                idx += 1
                continue

            rows = []
            for raw_row in lines[idx + 1: idx + 1 + nrows]:
                values = cls._numeric_tokens(raw_row.strip())
                if values:
                    rows.append(values)
            if rows:
                blocks.append({"timestep": timestep, "rows": rows})
            idx += 1 + nrows
        return column_names, blocks

    @classmethod
    def _parse_rdf_file(cls, path: str) -> Dict[str, Any]:
        column_names, blocks = cls._parse_ave_time_blocks(path)
        if not blocks:
            return {}
        last_block = blocks[-1]
        rows = [
            row[1:] if column_names and len(row) == len(column_names) + 1 else row
            for row in last_block["rows"]
        ]
        columns = {}
        ncols = max(len(row) for row in rows)
        if len(column_names) < ncols:
            column_names = column_names + [f"column_{idx}" for idx in range(len(column_names), ncols)]
        for idx in range(ncols):
            columns[column_names[idx]] = [row[idx] for row in rows if len(row) > idx]

        radius = columns.get("c_myRDF[1]", columns.get("column_1", []))
        g_r = columns.get("c_myRDF[2]", columns.get("column_2", []))
        coordination = columns.get("c_myRDF[3]", columns.get("column_3", []))
        return {
            "source": os.path.basename(path),
            "timestep": last_block["timestep"],
            "nblocks": len(blocks),
            "columns": columns,
            "radius": radius,
            "g_r": g_r,
            "coordination": coordination,
        }

    @classmethod
    def _parse_msd_file(cls, path: str) -> Dict[str, Any]:
        _column_names, blocks = cls._parse_ave_time_blocks(path)
        if not blocks:
            return {}
        timesteps = []
        msd_x = []
        msd_y = []
        msd_z = []
        msd_total = []
        for block in blocks:
            values = [row[1] if len(row) > 1 else row[0] for row in block["rows"]]
            if len(values) < 4:
                continue
            timesteps.append(block["timestep"])
            msd_x.append(values[0])
            msd_y.append(values[1])
            msd_z.append(values[2])
            msd_total.append(values[3])
        return {
            "source": os.path.basename(path),
            "timestep": timesteps,
            "msd_x": msd_x,
            "msd_y": msd_y,
            "msd_z": msd_z,
            "msd_total": msd_total,
        }

    @classmethod
    def _parse_thermo_interval_file(cls, path: str) -> Dict[str, Any]:
        header = []
        rows = []
        try:
            with open(path, "r", errors="replace") as fp:
                for raw_line in fp:
                    line = raw_line.strip()
                    if not line:
                        continue
                    if line.startswith("#"):
                        if line.startswith("# TimeStep"):
                            header = line[2:].split()
                        continue
                    values = cls._numeric_tokens(line)
                    if values:
                        rows.append(values)
        except OSError:
            return {}
        if not header or not rows:
            return {}

        columns = {}
        for idx, name in enumerate(header):
            columns[name] = [row[idx] for row in rows if len(row) > idx]

        atom_count = columns.get("v_N", [])
        volume_per_atom = columns.get("v_Vatom", [])
        total_volume = [
            n * v
            for n, v in zip(atom_count, volume_per_atom)
        ]
        return {
            "source": os.path.basename(path),
            "timestep": columns.get("TimeStep", []),
            "temperature": columns.get("v_Temp", []),
            "volume_per_atom": volume_per_atom,
            "total_volume": total_volume,
            "potential_energy": columns.get("v_pote", []),
            "total_energy": columns.get("v_Etotal", []),
            "pressure": columns.get("v_Press", []),
        }

    @staticmethod
    def _build_summary(task_result: Dict[str, Any]) -> Dict[str, Any]:
        rdf_points = {
            stage: len(data.get("radius", []))
            for stage, data in task_result.get("rdf", {}).items()
        }
        msd_points = {
            stage: len(data.get("timestep", []))
            for stage, data in task_result.get("msd", {}).items()
        }
        volume_points = {
            stage: len(data.get("temperature", []))
            for stage, data in task_result.get("volume_temperature", {}).items()
        }
        return {
            "rdf_stages": sorted(task_result.get("rdf", {}).keys()),
            "msd_stages": sorted(task_result.get("msd", {}).keys()),
            "volume_temperature_stages": sorted(task_result.get("volume_temperature", {}).keys()),
            "rdf_points": rdf_points,
            "msd_points": msd_points,
            "volume_temperature_points": volume_points,
        }

    @staticmethod
    def _task_summary_line(name: str, task_result: Dict[str, Any]) -> str:
        summary = task_result.get("summary", {})
        return (
            f"{name}: "
            f"rdf={summary.get('rdf_stages', [])}, "
            f"msd={summary.get('msd_stages', [])}, "
            f"volume_temperature={summary.get('volume_temperature_stages', [])}"
        )
