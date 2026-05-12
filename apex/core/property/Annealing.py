import os
import logging
from typing import List, Dict, Any

from monty.serialization import dumpfn
from pymatgen.core.structure import Structure

from apex.core.property.Property import Property
from apex.core.calculator.lib import vasp_utils


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
        # required temps
        self.start_temp = float(cal.get("start_temp", 300))
        _tgt = cal.get("target_temp", 800)
        self.target_temp = float(_tgt if not isinstance(_tgt, list) else _tgt[0])
        self.end_temp = float(cal.get("end_temp", 300))
        # required steps
        self.equi_step = int(cal.get("equi_step", 10000))
        # Allow specifying ramp/cool by rate (K/step); derive steps per task if provided
        self.ramp_rate = cal.pop("ramp_rate", None)
        self.cool_rate = cal.pop("cool_rate", None)
        self.ramp_step = int(cal.get("ramp_step", 20000))
        self.hold_step = int(cal.get("hold_step", 0))
        self.cool_step = int(cal.get("cool_step", 20000))
        # options
        self.thermostat = cal.get("thermostat", "nose_hoover")
        self.ensemble = cal.get("ensemble", "npt")
        self.tdamp = cal.get("tdamp", 100)
        self.pdamp = cal.get("pdamp", 1000)
        self.velocity_seed = cal.get("velocity_seed", 12345)
        self.dump_step = int(cal.get("dump_step", 1000))
        # timestep (ps, units metal); default 0.002 ps (2 fs)
        self.timestep = float(cal.get("timestep", 0.002))
        # RDF settings
        self.rdf_bins = int(cal.get("rdf_bins", 200))
        self.rdf_cutoff = float(cal.get("rdf_cutoff", 10.0))
        self.rdf_interval = int(cal.get("rdf_interval", 100))

    def task_type(self):
        return self.parameter["type"]

    def task_param(self):
        # make cal_setting explicit and in-sync
        cal = self.parameter.setdefault("cal_setting", {})
        cal.update(
            {
                "start_temp": self.start_temp,
                "target_temp": self.target_temp,
                "end_temp": self.end_temp,
                "equi_step": self.equi_step,
                "ramp_step": self.ramp_step,
                "hold_step": self.hold_step,
                "cool_step": self.cool_step,
                "thermostat": self.thermostat,
                "ensemble": self.ensemble,
                "tdamp": self.tdamp,
                "pdamp": self.pdamp,
                "velocity_seed": self.velocity_seed,
                "dump_step": self.dump_step,
                "timestep": self.timestep,
                "rdf_bins": self.rdf_bins,
                "rdf_cutoff": self.rdf_cutoff,
                "rdf_interval": self.rdf_interval,
            }
        )
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
        targets = self.parameter.get("cal_setting", {}).get("target_temp", self.target_temp)
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
                "end_temp": self.end_temp,
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
            var.append(f"variable end_temp equal {self.end_temp:.2f}")
            var.append(f"variable equi_step equal {self.equi_step}")
            # derive ramp/cool steps if rates are provided (K/step); else use defaults
            import math
            if self.ramp_rate is not None:
                try:
                    # convert K/ns -> steps using timestep (ps): dt_ns = dt_ps/1000
                    rstep = max(1, int(math.ceil(abs(float(tgt) - self.start_temp) * 1000.0 / (float(self.ramp_rate) * self.timestep))))
                except Exception:
                    rstep = self.ramp_step
            else:
                rstep = self.ramp_step
            if self.cool_rate is not None:
                try:
                    cr = self.cool_rate[idx] if isinstance(self.cool_rate, (list, tuple)) else float(self.cool_rate)
                    cstep = max(1, int(math.ceil(abs(float(tgt) - self.end_temp) * 1000.0 / (float(cr) * self.timestep))))
                except Exception:
                    cstep = self.cool_step
            else:
                cstep = self.cool_step
            var.append(f"variable ramp_step equal {rstep}")
            var.append(f"variable hold_step equal {self.hold_step}")
            var.append(f"variable cool_step equal {cstep}")
            var.append(f"variable timestep equal {self.timestep}")
            var.append(f"variable rdf_bins equal {self.rdf_bins}")
            var.append(f"variable rdf_cutoff equal {self.rdf_cutoff}")
            var.append(f"variable rdf_interval equal {self.rdf_interval}")
            var.append(f"variable tdamp equal {self.tdamp}")
            var.append(f"variable pdamp equal {self.pdamp}")
            var.append(f"variable velocity_seed equal {int(self.velocity_seed)}")
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
