"""Two-phase-coexistence melting-point workflow for LAMMPS.

The property prepares one solid/liquid coexistence trajectory per target
temperature and velocity-seed replica.  The upper part of the simulation cell
is premelted while the lower crystal is pinned, both halves are conditioned at
the target temperature, and the complete cell is released under zero-pressure
NPT dynamics.  Melting is bracketed from the sign of the q6-derived interface
velocity, rather than from a single-phase energy or MSD discontinuity.
"""

from __future__ import annotations

import csv
import json
import math
import os
import re
import shutil
from collections import defaultdict
from numbers import Integral, Real
from typing import Any, Dict, Iterable, List

import numpy as np
from monty.serialization import dumpfn, loadfn
from pymatgen.core.periodic_table import Element

from apex.core.property.Property import Property
from dflow.python import upload_packages

upload_packages.append(__file__)

PROPERTY_TYPE = "melting_point"
METADATA_FILE = "MeltingPoint.json"
VARIABLE_FILE = "variable_MeltingPoint.in"
DUMP_FILE = "dump.melting"

DEFAULT_CAL_SETTING = {
    "temperature": [1500, 1600, 1700],
    "premelt_temperature": 4500.0,
    "premelt_steps": 5000,
    "conditioning_steps": 5000,
    "production_steps": 100000,
    "timestep": 0.001,
    "tdamp": 0.1,
    "pdamp": 1.0,
    "pressure": 0.0,
    "barostat": "iso",
    "interface_axis": "z",
    "liquid_fraction": 0.5,
    "dump_step": 100,
    "thermo_step": 100,
    "restart_interval": 10000,
    "q6_cutoff": 3.5,
    "q6_neighbors": 12,
    "spatial_bins": 12,
    "analysis_stride": 2,
    "analysis_block_ps": 2.0,
    "minimum_q6_gap": 0.03,
    "minimum_directional_change": 0.02,
    "replicas": 1,
    "velocity_seeds": {
        "premelt": 324159,
        "condition": 271828,
        "release": 161803,
    },
}


class MeltingPoint(Property):
    """LAMMPS-only direct two-phase melting-point bracket."""

    def __init__(self, parameter: Dict[str, Any], inter_param=None):
        inter_param = inter_param or {"type": "deepmd"}
        if inter_param.get("type") in {"vasp", "abacus"}:
            raise NotImplementedError(
                "melting_point method='two_phase' supports only LAMMPS"
            )
        parameter = dict(parameter)
        method = str(parameter.get("method", "two_phase")).lower().replace("-", "_")
        if method in {"coexistence", "two_phase_coexistence", "direct_coexistence"}:
            method = "two_phase"
        if method != "two_phase":
            raise ValueError("melting_point currently supports method='two_phase'")

        parameter["type"] = PROPERTY_TYPE
        parameter["method"] = method
        parameter["cal_type"] = PROPERTY_TYPE
        parameter.setdefault("supercell_size", [1, 1, 1])
        cal = dict(DEFAULT_CAL_SETTING)
        cal.update(parameter.get("cal_setting", {}))
        cal["velocity_seeds"] = _normalize_velocity_seeds(
            cal.get("velocity_seeds"), int(cal.get("replicas", 1))
        )
        _validate_settings(parameter["supercell_size"], cal)
        parameter["cal_setting"] = cal

        self.parameter = parameter
        self.inter_param = inter_param
        self.supercell_size = [int(v) for v in parameter["supercell_size"]]
        self.cal_setting = cal

    def task_type(self):
        return PROPERTY_TYPE

    def task_param(self):
        return self.parameter

    def make_confs(self, path_to_work: str, path_to_equi: str, refine=False):
        if refine:
            raise NotImplementedError(
                "melting_point refinement is expressed as a new temperature list/suffix"
            )
        path_to_work = os.path.abspath(path_to_work)
        path_to_equi = os.path.abspath(path_to_equi)
        os.makedirs(path_to_work, exist_ok=True)
        contcar = os.path.join(path_to_equi, "CONTCAR")
        if not os.path.isfile(contcar):
            raise RuntimeError("please finish relaxation before melting_point")

        restart_files = self.cal_setting.get("restart_files")
        if restart_files is not None:
            if len(restart_files) != len(self.cal_setting["temperature"]):
                raise ValueError(
                    "melting_point cal_setting.restart_files must have one "
                    "entry per temperature"
                )
            restart_files = [os.path.abspath(path) for path in restart_files]
            missing = [path for path in restart_files if not os.path.isfile(path)]
            if missing:
                raise FileNotFoundError(
                    "melting_point restart file(s) not found: " + ", ".join(missing)
                )

        tasks = []
        replicas = int(self.cal_setting["replicas"])
        for temperature_index, temperature in enumerate(self.cal_setting["temperature"]):
            for replica in range(replicas):
                task_dir = os.path.join(path_to_work, f"task.{len(tasks):06d}")
                os.makedirs(task_dir, exist_ok=True)
                shutil.copyfile(contcar, os.path.join(task_dir, "POSCAR"))
                if restart_files is not None:
                    shutil.copyfile(
                        restart_files[temperature_index],
                        os.path.join(task_dir, "restart.coexistence.start"),
                    )
                metadata = self._metadata(float(temperature), replica)
                dumpfn(metadata, os.path.join(task_dir, METADATA_FILE), indent=4)
                with open(os.path.join(task_dir, VARIABLE_FILE), "w") as fp:
                    fp.write(_variable_file(metadata))
                tasks.append(task_dir)
        return tasks

    def post_process(self, task_list):
        if task_list:
            dumpfn(
                [os.path.basename(path) for path in task_list],
                os.path.join(os.path.dirname(task_list[0]), "task_list.json"),
                indent=4,
            )

    def compute(self, output_file, print_file, path_to_work):
        tasks = sorted(
            path for path in (
                os.path.join(path_to_work, name) for name in os.listdir(path_to_work)
            )
            if os.path.isdir(path) and re.match(r"task\.[0-9]+$", os.path.basename(path))
        )
        result, report = self._compute_lower(output_file, tasks, [])
        with open(print_file, "w") as fp:
            fp.write(report)
        _write_tidy_csv(os.path.join(path_to_work, "melting_point_tidy.csv"), result)
        _write_plots(path_to_work, result)

    def _compute_lower(self, output_file, all_tasks, all_res):
        points = []
        failures = []
        for task in all_tasks:
            try:
                points.append(_analyse_task(task))
            except Exception as exc:
                failures.append({"task": os.path.basename(task), "error": str(exc)})
        points.sort(key=lambda row: (row["temperature_K"], row["replica"]))
        temperatures = _aggregate_temperatures(
            points,
            expected_replicas=int(self.cal_setting["replicas"]),
            expected_temperatures=self.cal_setting["temperature"],
        )
        bracket = _infer_bracket(temperatures)
        result = {
            "schema": "apex.melting_point.two_phase/v1",
            "property": PROPERTY_TYPE,
            "method": "two_phase",
            "criterion": (
                "q6-derived solid-fraction Theil-Sen slope; 95% interval must "
                "exclude zero and projected release change must exceed threshold"
            ),
            "points": points,
            "temperatures": temperatures,
            "bracket": bracket,
            "failed_tasks": failures,
            "replica_warning": (
                "Replica-to-replica variation is unavailable with one replica per temperature"
                if int(self.cal_setting["replicas"]) == 1 else None
            ),
            "settings": self.parameter,
        }
        dumpfn(result, output_file, indent=4)
        report = _format_report(os.path.dirname(output_file), result)
        return result, report

    def _metadata(self, temperature: float, replica: int):
        cal = self.cal_setting
        seeds = cal["velocity_seeds"][replica]
        premelt_steps = int(cal["premelt_steps"])
        conditioning_steps = int(cal["conditioning_steps"])
        restart_mode = cal.get("restart_files") is not None
        return {
            "schema": "apex.melting_point.task/v1",
            "property": PROPERTY_TYPE,
            "method": "two_phase",
            "temperature_K": temperature,
            "replica": replica + 1,
            "supercell_size": self.supercell_size,
            "interface_axis": cal["interface_axis"],
            "liquid_fraction": float(cal["liquid_fraction"]),
            "premelt_temperature_K": float(cal["premelt_temperature"]),
            "premelt_steps": premelt_steps,
            "conditioning_steps": conditioning_steps,
            # A restart is an already prepared coexistence state. Reset its
            # timestep to zero and use that initial frame as the analysis
            # reference instead of repeating premelting/conditioning.
            "restart_mode": restart_mode,
            "reference_step": 0 if restart_mode else premelt_steps,
            "release_step": 0 if restart_mode else premelt_steps + conditioning_steps,
            "production_steps": int(cal["production_steps"]),
            "timestep_ps": float(cal["timestep"]),
            "tdamp_ps": float(cal["tdamp"]),
            "pdamp_ps": float(cal["pdamp"]),
            "pressure_bar": float(cal["pressure"]),
            "barostat": cal["barostat"],
            "dump_step": int(cal["dump_step"]),
            "thermo_step": int(cal["thermo_step"]),
            "restart_interval": int(cal["restart_interval"]),
            "q6_cutoff_A": float(cal["q6_cutoff"]),
            "q6_neighbors": int(cal["q6_neighbors"]),
            "spatial_bins": int(cal["spatial_bins"]),
            "analysis_stride": int(cal["analysis_stride"]),
            "analysis_block_ps": float(cal["analysis_block_ps"]),
            "minimum_q6_gap": float(cal["minimum_q6_gap"]),
            "minimum_directional_change": float(cal["minimum_directional_change"]),
            "temperature_tolerance_K": float(
                cal.get("temperature_tolerance_K", max(50.0, 0.05 * temperature))
            ),
            "velocity_seeds": seeds,
        }


def _normalize_velocity_seeds(value, replicas: int):
    if replicas < 1:
        raise ValueError("cal_setting.replicas must be >= 1")
    if isinstance(value, list):
        if len(value) != replicas:
            raise ValueError("velocity_seeds list length must equal replicas")
        source = value
    elif isinstance(value, dict):
        for key, seed in value.items():
            if isinstance(seed, (bool, np.bool_)) or not isinstance(seed, Integral):
                raise ValueError("velocity_seeds must contain positive integers")
        source = [
            {key: seed + 104729 * index for key, seed in value.items()}
            for index in range(replicas)
        ]
    else:
        raise ValueError("velocity_seeds must be a mapping or list of mappings")
    normalized = []
    for seeds in source:
        missing = {"premelt", "condition", "release"} - set(seeds)
        if missing:
            raise ValueError(f"velocity_seeds missing keys: {sorted(missing)}")
        normalized_seeds = {}
        for key in ("premelt", "condition", "release"):
            seed = seeds[key]
            if isinstance(seed, (bool, np.bool_)) or not isinstance(seed, Integral):
                raise ValueError("velocity_seeds must contain positive integers")
            normalized_seeds[key] = int(seed)
        invalid = [key for key, seed in normalized_seeds.items() if seed <= 0]
        if invalid:
            raise ValueError(
                "velocity_seeds must contain positive integers; invalid keys: "
                f"{invalid}"
            )
        normalized.append(normalized_seeds)
    return normalized


def _validate_settings(supercell_size, cal):
    if (
        not isinstance(supercell_size, (list, tuple))
        or len(supercell_size) != 3
        or any(
            isinstance(value, (bool, np.bool_))
            or not isinstance(value, Integral)
            or value < 1
            for value in supercell_size
        )
    ):
        raise ValueError("supercell_size must contain three positive integers")
    temperatures = cal.get("temperature")
    if not isinstance(temperatures, list) or not temperatures:
        raise ValueError("cal_setting.temperature must be a non-empty list")
    if any(
        isinstance(temp, (bool, np.bool_))
        or not isinstance(temp, Real)
        or not np.isfinite(temp)
        or temp <= 0
        for temp in temperatures
    ):
        raise ValueError("all melting-point temperatures must be positive")
    if cal["interface_axis"] not in {"x", "y", "z"}:
        raise ValueError("interface_axis must be x, y, or z")
    if not 0.1 <= float(cal["liquid_fraction"]) <= 0.9:
        raise ValueError("liquid_fraction must be between 0.1 and 0.9")
    if cal["barostat"] not in {"iso", "aniso", "x", "y", "z"}:
        raise ValueError("barostat must be iso, aniso, x, y, or z")
    for key in (
        "premelt_steps",
        "conditioning_steps",
        "production_steps",
        "dump_step",
        "thermo_step",
        "restart_interval",
    ):
        value = cal[key]
        if (
            isinstance(value, (bool, np.bool_))
            or not isinstance(value, Integral)
            or value <= 0
        ):
            raise ValueError(f"{key} must be positive")
    if cal["production_steps"] < 10 * cal["dump_step"]:
        raise ValueError("production_steps must contain at least ten dump intervals")


def _variable_file(meta):
    axis_index = {"x": 0, "y": 1, "z": 2}[meta["interface_axis"]]
    seeds = meta["velocity_seeds"]
    lines = [
        "# variable_MeltingPoint.in",
        f"variable temperature equal {meta['temperature_K']:.10g}",
        f"variable premelt_temperature equal {meta['premelt_temperature_K']:.10g}",
        f"variable nx equal {meta['supercell_size'][0]}",
        f"variable ny equal {meta['supercell_size'][1]}",
        f"variable nz equal {meta['supercell_size'][2]}",
        f"variable interface_axis string {meta['interface_axis']}",
        f"variable axis_index equal {axis_index}",
        f"variable liquid_fraction equal {meta['liquid_fraction']:.10g}",
        f"variable premelt_steps equal {meta['premelt_steps']}",
        f"variable conditioning_steps equal {meta['conditioning_steps']}",
        f"variable production_steps equal {meta['production_steps']}",
        f"variable timestep equal {meta['timestep_ps']:.10g}",
        f"variable tdamp equal {meta['tdamp_ps']:.10g}",
        f"variable pdamp equal {meta['pdamp_ps']:.10g}",
        f"variable target_pressure equal {meta['pressure_bar']:.10g}",
        f"variable dump_step equal {meta['dump_step']}",
        f"variable thermo_step equal {meta['thermo_step']}",
        f"variable restart_interval equal {meta['restart_interval']}",
        f"variable q6_cutoff equal {meta['q6_cutoff_A']:.10g}",
        f"variable q6_neighbors equal {meta['q6_neighbors']}",
        f"variable premelt_seed equal {seeds['premelt']}",
        f"variable condition_seed equal {seeds['condition']}",
        f"variable release_seed equal {seeds['release']}",
    ]
    return "\n".join(lines) + "\n"


def render_melting_point_lammps_input(conf, type_map, interaction, model_param, task_param=None):
    """Render the validated solid/liquid preparation and NPT release."""
    cal = (task_param or {}).get("cal_setting", {})
    axis = cal.get("interface_axis", "z")
    barostat = cal.get("barostat", "iso")
    restart_mode = cal.get("restart_files") is not None
    axis_lo, axis_hi = f"{axis}lo", f"{axis}hi"
    lo = "v_split" if float(cal.get("liquid_fraction", 0.5)) < 1.0 else "INF"
    region_args = {
        "x": f"${{{lo[2:]}}} INF INF INF INF INF" if lo.startswith("v_") else "INF INF INF INF INF INF",
        "y": f"INF INF ${{{lo[2:]}}} INF INF INF" if lo.startswith("v_") else "INF INF INF INF INF INF",
        "z": f"INF INF INF INF ${{{lo[2:]}}} INF" if lo.startswith("v_") else "INF INF INF INF INF INF",
    }[axis]
    type_map_list = [
        element for element, _type_id in sorted(type_map.items(), key=lambda item: item[1])
    ]
    ret = [
        f"include {VARIABLE_FILE}",
        "clear",
        "units metal",
        "dimension 3",
        "boundary p p p",
        "atom_style atomic",
        "atom_modify map array",
        *( ["newton on"] if model_param.get("type") == "mace" else [] ),
        "box tilt large",
    ]
    if restart_mode:
        ret.extend([
            "read_restart restart.coexistence.start",
            "reset_timestep 0",
        ])
    else:
        ret.extend([f"read_data {conf}", "replicate ${nx} ${ny} ${nz}"])
        for index, element in enumerate(type_map_list, 1):
            ret.append(f"mass {index} {float(Element(element).atomic_mass):.8f}")
    ret.extend([
        "neighbor 2.0 bin",
        "neigh_modify every 1 delay 0 check yes",
        interaction(model_param).rstrip(),
        "timestep ${timestep}",
        "restart ${restart_interval} restart.melting.1 restart.melting.2",
        "compute mype all pe",
        "compute q6 all orientorder/atom degrees 1 6 nnn ${q6_neighbors} cutoff ${q6_cutoff}",
        "thermo ${thermo_step}",
        "thermo_style custom step temp press pe ke etotal enthalpy density pxx pyy pzz pxy pxz pyz lx ly lz vol",
        "thermo_modify flush yes",
        f"dump melting all custom ${{dump_step}} {DUMP_FILE} id type xs ys zs c_q6[1]",
        "dump_modify melting sort id",
        f"variable split equal {axis_lo}+(1.0-${{liquid_fraction}})*({axis_hi}-{axis_lo})",
        f"region liquid_region block {region_args} units box",
        "group liquid_seed region liquid_region",
        "group solid_seed subtract all liquid_seed",
    ])
    if not restart_mode:
        ret.extend([
            "velocity all set 0.0 0.0 0.0",
            "velocity liquid_seed create ${premelt_temperature} ${premelt_seed} mom yes rot yes dist gaussian",
            "fix pin_solid solid_seed setforce 0.0 0.0 0.0",
            "fix melt_liquid liquid_seed nvt temp ${premelt_temperature} ${premelt_temperature} ${tdamp}",
            'print "APEX_MELTING_STAGE premelt_liquid"',
            "run ${premelt_steps}",
            "unfix melt_liquid",
            "unfix pin_solid",
            "velocity all create ${temperature} ${condition_seed} mom yes rot yes dist gaussian",
            "fix condition_all all nvt temp ${temperature} ${temperature} ${tdamp}",
            'print "APEX_MELTING_STAGE condition_all"',
            "run ${conditioning_steps}",
            "unfix condition_all",
            "velocity all create ${temperature} ${release_seed} mom yes rot yes dist gaussian",
        ])
    else:
        # Emit the restored state as the u=0 analysis reference before
        # continuing the coexistence trajectory with preserved velocities.
        ret.extend([
            'print "APEX_MELTING_STAGE restart_coexistence"',
            "run 0",
        ])
    pressure_clause = (
        f"{barostat} ${{target_pressure}} ${{target_pressure}} ${{pdamp}}"
        if barostat in {"iso", "aniso"}
        else f"{barostat} ${{target_pressure}} ${{target_pressure}} ${{pdamp}}"
    )
    ret.extend([
        f"fix coexistence all npt temp ${{temperature}} ${{temperature}} ${{tdamp}} {pressure_clause}",
        "fix remove_drift all momentum 100 linear 1 1 1",
        "compute msd_solid solid_seed msd com yes",
        "compute msd_liquid liquid_seed msd com yes",
        "thermo_style custom step temp press pe ke etotal enthalpy density pxx pyy pzz pxy pxz pyz lx ly lz vol c_msd_solid[4] c_msd_liquid[4]",
        'print "APEX_MELTING_STAGE coexistence_release"',
        "run ${production_steps}",
        "write_restart restart.melting.final",
        'print "All done"',
    ])
    return "\n".join(ret) + "\n"


def _iter_dump_frames(path, axis):
    axis_column = {"x": "xs", "y": "ys", "z": "zs"}[axis]
    with open(path, errors="replace") as fp:
        while True:
            line = fp.readline()
            if not line:
                return
            if line.strip() != "ITEM: TIMESTEP":
                continue
            step = int(fp.readline())
            if fp.readline().strip() != "ITEM: NUMBER OF ATOMS":
                raise RuntimeError("malformed LAMMPS dump atom-count header")
            natoms = int(fp.readline())
            bounds_header = fp.readline().strip()
            if not bounds_header.startswith("ITEM: BOX BOUNDS"):
                raise RuntimeError("malformed LAMMPS dump box header")
            bounds = [list(map(float, fp.readline().split()[:2])) for _ in range(3)]
            atom_header = fp.readline().split()[2:]
            try:
                id_col = atom_header.index("id")
                axis_col = atom_header.index(axis_column)
                q6_col = next(i for i, name in enumerate(atom_header) if name.startswith("c_q6"))
                x_col = atom_header.index("xs")
                y_col = atom_header.index("ys")
                z_col = atom_header.index("zs")
            except (ValueError, StopIteration) as exc:
                raise RuntimeError("dump must contain id, xs/ys/zs and c_q6") from exc
            ids = np.empty(natoms, dtype=int)
            scaled = np.empty(natoms, dtype=float)
            coords = np.empty((natoms, 3), dtype=float)
            q6 = np.empty(natoms, dtype=float)
            for row in range(natoms):
                fields = fp.readline().split()
                ids[row] = int(fields[id_col])
                scaled[row] = float(fields[axis_col]) % 1.0
                coords[row] = [float(fields[x_col]) % 1.0, float(fields[y_col]) % 1.0, float(fields[z_col]) % 1.0]
                q6[row] = float(fields[q6_col])
            order = np.argsort(ids)
            length = bounds[{"x": 0, "y": 1, "z": 2}[axis]][1] - bounds[{"x": 0, "y": 1, "z": 2}[axis]][0]
            yield {"step": step, "scaled": scaled[order], "coords": coords[order], "q6": q6[order], "axis_length_A": length}


def _analyse_task(task_dir):
    meta = loadfn(os.path.join(task_dir, METADATA_FILE))
    dump_path = os.path.join(task_dir, DUMP_FILE)
    if not os.path.isfile(dump_path):
        raise RuntimeError(f"missing {DUMP_FILE}")
    release_step = int(meta["release_step"])
    reference_step = int(meta.get("reference_step", meta["premelt_steps"]))
    reference = None
    reference_matches = 0
    for frame in _iter_dump_frames(dump_path, meta["interface_axis"]):
        if frame["step"] == reference_step:
            reference = frame
            reference_matches += 1
    if reference_matches != 1 or reference is None:
        raise RuntimeError(f"expected one reference frame at step {reference_step}")
    solid_edge = 1.0 - float(meta["liquid_fraction"])
    solid_bulk = (reference["scaled"] >= 0.08 * solid_edge) & (reference["scaled"] <= 0.84 * solid_edge)
    liquid_width = 1.0 - solid_edge
    liquid_bulk = (
        (reference["scaled"] >= solid_edge + 0.16 * liquid_width)
        & (reference["scaled"] <= solid_edge + 0.92 * liquid_width)
    )
    if not np.any(solid_bulk) or not np.any(liquid_bulk):
        raise RuntimeError("reference frame has no atoms in a solid/liquid bulk core")
    q_solid = float(np.mean(reference["q6"][solid_bulk]))
    q_liquid = float(np.mean(reference["q6"][liquid_bulk]))
    q_gap = q_solid - q_liquid

    stride = int(meta["analysis_stride"])
    bins = int(meta["spatial_bins"])
    times, fractions, profiles, crossings, lengths = [], [], [], [], []
    if q_gap <= 0:
        q_gap = float(q_gap)

    final_frame = None
    last_sampled_step = None
    release_frame_count = 0

    def append_frame(frame):
        nonlocal final_frame, last_sampled_step
        times.append((frame["step"] - release_step) * float(meta["timestep_ps"]))
        normalized = np.clip((frame["q6"] - q_liquid) / q_gap, 0.0, 1.0) if q_gap > 0 else np.full_like(frame["q6"], np.nan)
        fractions.append(float(np.nanmean(normalized)))
        bin_index = np.minimum((frame["scaled"] * bins).astype(int), bins - 1)
        profile = [float(np.nanmean(normalized[bin_index == index])) if np.any(bin_index == index) else None for index in range(bins)]
        profiles.append(profile)
        crossings.append(_interface_crossings(profile))
        lengths.append(frame["axis_length_A"])
        final_frame = frame
        last_sampled_step = frame["step"]

    last_release_frame = None
    for frame in _iter_dump_frames(dump_path, meta["interface_axis"]):
        if frame["step"] < release_step:
            continue
        if release_frame_count % stride == 0:
            append_frame(frame)
        release_frame_count += 1
        last_release_frame = frame
    if last_release_frame is not None and last_sampled_step != last_release_frame["step"]:
        append_frame(last_release_frame)
    if len(times) < 6 or final_frame is None:
        raise RuntimeError("released trajectory contains too few sampled frames")

    times_array = np.asarray(times)
    fractions_array = np.asarray(fractions)
    block_times, block_values = _block_means(times_array, fractions_array, float(meta["analysis_block_ps"]))
    slope, low, high = _theil_sen(block_times, block_values)
    duration = float(times_array[-1] - times_array[0])
    projected = float(slope * duration)
    minimum_change = float(meta["minimum_directional_change"])
    thermo = _parse_thermo(os.path.join(task_dir, "log.lammps"), release_step)
    tail_temperature = thermo.get("tail_temperature_mean_K")
    thermodynamics_valid = bool(
        thermo.get("sample_count", 0) >= 10
        and tail_temperature is not None
        and abs(tail_temperature - float(meta["temperature_K"]))
        <= float(
            meta.get(
                "temperature_tolerance_K",
                max(50.0, 0.05 * float(meta["temperature_K"])),
            )
        )
    )
    if not thermodynamics_valid:
        outcome = "invalid_thermodynamics"
    elif q_gap < float(meta["minimum_q6_gap"]):
        outcome = "invalid_preparation"
    elif low > 0.0 and projected >= minimum_change:
        outcome = "solid_growth"
    elif high < 0.0 and projected <= -minimum_change:
        outcome = "liquid_growth"
    else:
        outcome = "inconclusive"
    mean_length = float(np.mean(lengths))
    return {
        "task": os.path.basename(task_dir),
        "temperature_K": float(meta["temperature_K"]),
        "replica": int(meta["replica"]),
        "atom_count": int(len(reference["q6"])),
        "release_frame_count": release_frame_count,
        "reference_solid_q6": q_solid,
        "reference_liquid_q6": q_liquid,
        "reference_q6_gap": q_gap,
        "release_duration_ps": duration,
        "time_ps": times,
        "solid_fraction": fractions,
        "liquid_fraction": [float(1.0 - value) for value in fractions],
        "spatial_solid_fraction": profiles,
        "interface_positions_fractional": crossings,
        "interface_motion": {
            "block_time_ps": block_times.tolist(),
            "block_solid_fraction": block_values.tolist(),
            "solid_fraction_slope_per_ps": slope,
            "slope_95pct_low_per_ps": low,
            "slope_95pct_high_per_ps": high,
            "projected_solid_fraction_change": projected,
            "interface_velocity_A_per_ps": slope * mean_length / 2.0,
            "interface_velocity_95pct_low_A_per_ps": low * mean_length / 2.0,
            "interface_velocity_95pct_high_A_per_ps": high * mean_length / 2.0,
            "outcome": outcome,
        },
        "thermodynamics": thermo,
        "thermodynamics_valid": thermodynamics_valid,
        "snapshot": {
            "reference_scaled_coordinates": reference["coords"].tolist(),
            "reference_q6": reference["q6"].tolist(),
            "final_scaled_coordinates": final_frame["coords"].tolist(),
            "final_q6": final_frame["q6"].tolist(),
        },
        "metadata": meta,
    }


def _interface_crossings(profile):
    values = np.asarray([np.nan if value is None else value for value in profile], dtype=float)
    result = []
    for index in range(len(values)):
        left, right = values[index], values[(index + 1) % len(values)]
        if not np.isfinite(left) or not np.isfinite(right) or (left - 0.5) * (right - 0.5) > 0:
            continue
        fraction = 0.0 if right == left else (0.5 - left) / (right - left)
        result.append(float(((index + 0.5 + fraction) / len(values)) % 1.0))
    return result


def _block_means(times, values, width):
    indices = np.floor((times - times[0]) / width).astype(int)
    unique = np.unique(indices)
    return (
        np.asarray([np.mean(times[indices == index]) for index in unique]),
        np.asarray([np.mean(values[indices == index]) for index in unique]),
    )


def _theil_sen(x, y):
    if len(x) < 3:
        return 0.0, -math.inf, math.inf
    try:
        from scipy.stats import theilslopes
        slope, _intercept, low, high = theilslopes(y, x, alpha=0.95)
        return float(slope), float(low), float(high)
    except Exception:
        slopes = [(y[j] - y[i]) / (x[j] - x[i]) for i in range(len(x)) for j in range(i + 1, len(x)) if x[j] != x[i]]
        return float(np.median(slopes)), float(np.percentile(slopes, 2.5)), float(np.percentile(slopes, 97.5))


def _parse_thermo(path, release_step):
    header = None
    rows = []
    if not os.path.isfile(path):
        return {"sample_count": 0}
    with open(path, errors="replace") as fp:
        for line in fp:
            fields = line.split()
            if fields and fields[0] == "Step" and "Temp" in fields and "Press" in fields:
                header = fields
                continue
            if header is None or len(fields) != len(header):
                continue
            try:
                row = dict(zip(header, map(float, fields)))
            except ValueError:
                continue
            if row["Step"] >= release_step:
                rows.append(row)
    if not rows:
        return {"sample_count": 0}
    tail = rows[len(rows) // 2:]
    def stats(keys):
        key = next((candidate for candidate in keys if candidate in tail[0]), None)
        if key is None:
            return None, None
        values = np.asarray([row[key] for row in tail])
        return float(values.mean()), float(values.std(ddof=1)) if len(values) > 1 else 0.0
    temp_mean, temp_std = stats(["Temp"])
    pressure_mean, pressure_std = stats(["Press"])
    energy_mean, energy_std = stats(["TotEng", "Etotal", "Etot"])
    return {
        "sample_count": len(rows),
        "tail_temperature_mean_K": temp_mean,
        "tail_temperature_std_K": temp_std,
        "tail_pressure_mean_bar": pressure_mean,
        "tail_pressure_std_bar": pressure_std,
        "tail_total_energy_mean_eV": energy_mean,
        "tail_total_energy_std_eV": energy_std,
    }


def _aggregate_temperatures(
    points,
    expected_replicas=None,
    expected_temperatures=None,
):
    if expected_replicas is not None and expected_replicas < 1:
        raise ValueError("expected_replicas must be >= 1")
    grouped = defaultdict(list)
    for point in points:
        grouped[float(point["temperature_K"])].append(point)
    temperatures = set(grouped)
    if expected_temperatures is not None:
        temperatures.update(float(value) for value in expected_temperatures)
    rows = []
    for temperature in sorted(temperatures):
        replicas = grouped.get(temperature, [])
        velocities = np.asarray([
            row["interface_motion"]["interface_velocity_A_per_ps"]
            for row in replicas
        ])
        outcomes = [row["interface_motion"]["outcome"] for row in replicas]
        replica_ids = [row.get("replica") for row in replicas]
        replicas_complete = (
            expected_replicas is None
            or (
                len(replicas) == expected_replicas
                and len(set(replica_ids)) == expected_replicas
            )
        )
        consensus = (
            outcomes[0]
            if replicas_complete
            and outcomes
            and outcomes[0] in {"solid_growth", "liquid_growth"}
            and len(set(outcomes)) == 1
            else "inconclusive"
        )
        rows.append({
            "temperature_K": temperature,
            "replica_count": len(replicas),
            "expected_replica_count": expected_replicas,
            "replicas_complete": replicas_complete,
            "replica_outcomes": outcomes,
            "consensus_outcome": consensus,
            "interface_velocity_mean_A_per_ps": (
                float(np.mean(velocities)) if len(velocities) else None
            ),
            "interface_velocity_std_A_per_ps": float(np.std(velocities, ddof=1)) if len(velocities) > 1 else None,
            "interface_velocity_standard_error_A_per_ps": float(np.std(velocities, ddof=1) / math.sqrt(len(velocities))) if len(velocities) > 1 else None,
        })
    return rows


def _infer_bracket(rows):
    solid = [row["temperature_K"] for row in rows if row["consensus_outcome"] == "solid_growth"]
    liquid = [row["temperature_K"] for row in rows if row["consensus_outcome"] == "liquid_growth"]
    if solid and liquid and max(solid) < min(liquid):
        low, high = max(solid), min(liquid)
        return {
            "status": "bracketed",
            "lower_solid_growth_K": low,
            "upper_liquid_growth_K": high,
            "width_K": high - low,
            "estimated_melting_temperature_K": 0.5 * (low + high),
            "uncertainty_half_width_K": 0.5 * (high - low),
            "recommended_refinement_temperature_K": 0.5 * (low + high),
        }
    if rows and all(row["consensus_outcome"] == "solid_growth" for row in rows):
        return {"status": "expand_upper_temperature"}
    if rows and all(row["consensus_outcome"] == "liquid_growth" for row in rows):
        return {"status": "expand_lower_temperature"}
    return {"status": "inconclusive_or_unbracketed"}


def _write_tidy_csv(path, result):
    with open(path, "w", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=["temperature_K", "replica", "outcome", "interface_velocity_A_per_ps", "q6_gap", "temperature_tail_K", "pressure_tail_bar", "energy_tail_eV"])
        writer.writeheader()
        for point in result["points"]:
            thermo = point["thermodynamics"]
            writer.writerow({
                "temperature_K": point["temperature_K"],
                "replica": point["replica"],
                "outcome": point["interface_motion"]["outcome"],
                "interface_velocity_A_per_ps": point["interface_motion"]["interface_velocity_A_per_ps"],
                "q6_gap": point["reference_q6_gap"],
                "temperature_tail_K": thermo.get("tail_temperature_mean_K"),
                "pressure_tail_bar": thermo.get("tail_pressure_mean_bar"),
                "energy_tail_eV": thermo.get("tail_total_energy_mean_eV"),
            })


def _write_plots(work_dir, result):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return
    points = result["points"]
    if not points:
        return
    fig, ax = plt.subplots(figsize=(6.2, 4.2), dpi=180)
    for point in points:
        ax.plot(point["time_ps"], point["solid_fraction"], label=f"{point['temperature_K']:g} K r{point['replica']}")
    ax.set(xlabel="Released time (ps)", ylabel="q6-normalized solid fraction")
    ax.legend(fontsize=7, ncol=2)
    fig.tight_layout(); fig.savefig(os.path.join(work_dir, "solid_fraction_vs_time.png")); plt.close(fig)

    fig, ax = plt.subplots(figsize=(5.6, 4.2), dpi=180)
    for point in points:
        motion = point["interface_motion"]
        y = motion["interface_velocity_A_per_ps"]
        lo = motion["interface_velocity_95pct_low_A_per_ps"]
        hi = motion["interface_velocity_95pct_high_A_per_ps"]
        ax.errorbar(point["temperature_K"], y, yerr=[[y - lo], [hi - y]], fmt="o", color="tab:blue", alpha=0.8)
    ax.axhline(0.0, color="black", linewidth=1)
    ax.set(xlabel="Temperature (K)", ylabel="Interface velocity (A/ps)")
    fig.tight_layout(); fig.savefig(os.path.join(work_dir, "interface_velocity_vs_temperature.png")); plt.close(fig)

    chosen = min(points, key=lambda row: abs(row["temperature_K"] - np.mean([p["temperature_K"] for p in points])))
    snap = chosen["snapshot"]
    interface_axis = chosen.get("metadata", {}).get("interface_axis", "z")
    transverse_index, axis_index, xlabel, ylabel = _snapshot_projection(
        interface_axis
    )
    fig, axes = plt.subplots(1, 2, figsize=(9.0, 4.0), dpi=180, sharex=True, sharey=True)
    for ax, coords_key, q6_key, title in [
        (axes[0], "reference_scaled_coordinates", "reference_q6", "Prepared two-phase state"),
        (axes[1], "final_scaled_coordinates", "final_q6", "End of release"),
    ]:
        coords = np.asarray(snap[coords_key]); q6 = np.asarray(snap[q6_key])
        scatter = ax.scatter(
            coords[:, transverse_index], coords[:, axis_index],
            c=q6, s=3, cmap="viridis", rasterized=True,
        )
        ax.set(
            title=title,
            xlabel=xlabel,
            ylabel=ylabel,
        )
    fig.colorbar(scatter, ax=axes, label="local q6", shrink=0.8)
    fig.savefig(os.path.join(work_dir, "solid_liquid_interface_snapshots.png"), bbox_inches="tight"); plt.close(fig)


def _snapshot_projection(interface_axis):
    """Return a projection that always contains the interface normal."""
    axis_index = {"x": 0, "y": 1, "z": 2}[interface_axis]
    transverse_index = 0 if axis_index != 0 else 1
    axis_names = ("x", "y", "z")
    return (
        transverse_index,
        axis_index,
        f"fractional {axis_names[transverse_index]}",
        f"fractional {interface_axis}",
    )


def _format_report(path, result):
    bracket = result["bracket"]
    lines = [path, "Two-phase coexistence melting point", f"Status: {bracket['status']}"]
    if bracket["status"] == "bracketed":
        lines.append(
            f"Tm = {bracket['estimated_melting_temperature_K']:.6g} +/- "
            f"{bracket['uncertainty_half_width_K']:.6g} K "
            f"({bracket['lower_solid_growth_K']:.6g}-{bracket['upper_liquid_growth_K']:.6g} K)"
        )
    for row in result["temperatures"]:
        lines.append(f"{row['temperature_K']:.6g} K: {row['consensus_outcome']}, replicas={row['replica_count']}")
    if result.get("replica_warning"):
        lines.append("WARNING: " + result["replica_warning"])
    return "\n".join(lines) + "\n"
