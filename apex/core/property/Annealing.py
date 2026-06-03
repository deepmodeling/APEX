import os
import logging
from typing import List, Dict, Any

from monty.serialization import dumpfn
from pymatgen.core.structure import Structure

from apex.core.property.Property import Property
from apex.core.calculator.lib import vasp_utils


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


class Annealing(Property):

    def __init__(self, parameter: Dict[str, Any], inter_param=None):
        parameter["cal_type"] = "annealing"
        self.parameter = parameter
        self.inter_param = inter_param if inter_param is not None else {"type": "lammps"}

        # geometry
        self.supercell_size = parameter.get("supercell_size", [2, 2, 2])
        self.supercell_length = parameter.get("supercell_length", None)

        # MD controls (independent knobs only)
        cal = parameter.setdefault("cal_setting", {})
        # Schedule defaults mirror annealing/spec.
        self.start_temp = float(cal.get("start_temp", 4))
        _tgt = cal.get("target_temp", cal.get("temp", 300))
        self.target_temp = float(_tgt if not isinstance(_tgt, list) else _tgt[0])
        self.end_temp = float(cal.get("end_temp", 4))
        self._has_ramp_rate = "temp_ramp_rate" in cal or "ramp_rate" in cal
        self._has_cool_rate = "cool_rate" in cal
        self.temp_ramp_rate = cal.get("temp_ramp_rate", cal.get("ramp_rate", 1000))
        self.cool_rate = cal.get("cool_rate", self.temp_ramp_rate)
        self.equi_step = int(cal.get("equi_step", cal.get("init_thermo_equil_step", 20000)))
        self.init_lgv_thermo_equil_step = int(cal.get("init_lgv_thermo_equil_step", 20000))
        self.init_thermo_equil_step = int(cal.get("init_thermo_equil_step", self.equi_step))
        self.final_thermo_equil_step = int(cal.get("final_thermo_equil_step", cal.get("hold_step", 20000)))
        # Explicit step counts override rate-derived counts when provided.
        self.ramp_step = int(cal.get("ramp_step", cal.get("temp_ramp_step", 0)))
        self.cool_step = int(cal.get("cool_step", cal.get("temp_decline_step", 0)))
        self.hold_step = int(cal.get("hold_step", self.final_thermo_equil_step))
        # options
        self.thermostat = cal.get("thermostat", "nose_hoover")
        self.ensemble = cal.get("ensemble", "npt")
        self.timestep = float(cal.get("timestep", 0.001))
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

        # Calculator selection happens downstream via make_calculator; do not hard-code here.
        # Annealing is implemented for LAMMPS; other calculators should provide their own impls.
        if not os.path.isdir(path_to_equi) or not os.path.isfile(os.path.join(path_to_equi, "CONTCAR")):
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

            # Build POSCAR from relaxation
            import shutil
            equi_contcar = os.path.join(path_to_equi, "CONTCAR")
            shutil.copy(equi_contcar, os.path.join(task_dir, "POSCAR"))
            # Load back to fetch lattice metrics if needed later
            s_sorted = Structure.from_file(os.path.join(task_dir, "POSCAR"))

            # Derive integer replication from physical length if requested
            if self.supercell_length is not None:
                try:
                    a, b, c = s_sorted.lattice.abc
                    import math
                    sx, sy, sz = self.supercell_length
                    nx = max(1, int(math.ceil(sx / a)))
                    ny = max(1, int(math.ceil(sy / b)))
                    nz = max(1, int(math.ceil(sz / c)))
                    self.supercell_size = [nx, ny, nz]
                except Exception as e:
                    logging.warning(f"Failed to derive supercell_size from supercell_length: {e}")

            # Persist params per task
            anneal_task = {
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
            with open(os.path.join(task_dir, "variable_Annealing.in"), "w") as fp:
                fp.write("\n".join(var) + "\n")

            task_list.append(task_dir)

        return task_list

    def post_process(self, task_list: List[str]):
        # No post aggregation for annealing in this minimal port
        pass

    def _compute_lower(self, output_file, all_tasks, all_res):
        # Minimal aggregator: return basic info per task; users inspect dumps/logs
        res_data = {}
        ptr_data = os.path.dirname(output_file) + "\n"
        for t in all_tasks:
            name = os.path.basename(t)
            res_data[name] = {
                "task": name,
                "note": "annealing run; inspect log.lammps and dump files",
            }
        return res_data, ptr_data
