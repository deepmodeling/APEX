#!/usr/bin/env python3
"""Generate deterministic, dependency-free Ti, V, and B2 TiV benchmark cells."""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from benchmark_lib import (
    BENCHMARK_ID,
    SCHEMA_VERSION,
    describe_file,
    utc_now,
    write_json_new,
    write_text_if_identical,
)


@dataclass(frozen=True)
class Case:
    name: str
    formula: str
    elements: tuple[str, ...]
    masses: tuple[float, ...]
    lattice: tuple[tuple[float, float, float], ...]
    atoms: tuple[tuple[int, float, float, float], ...]
    fractional: tuple[tuple[float, float, float], ...]
    poscar_counts: tuple[int, ...]
    crystal: str


def _cases() -> tuple[Case, ...]:
    ti_a = 2.95
    ti_c = 4.68
    ti_y = math.sqrt(3.0) * ti_a / 2.0
    return (
        Case(
            name="Ti_hcp",
            formula="Ti2",
            elements=("Ti",),
            masses=(47.867,),
            lattice=((ti_a, 0.0, 0.0), (ti_a / 2.0, ti_y, 0.0), (0.0, 0.0, ti_c)),
            atoms=((1, 0.0, 0.0, 0.0), (1, ti_a / 2.0, ti_y / 3.0, ti_c / 2.0)),
            fractional=((0.0, 0.0, 0.0), (1.0 / 3.0, 1.0 / 3.0, 0.5)),
            poscar_counts=(2,),
            crystal="hcp primitive",
        ),
        Case(
            name="V_bcc",
            formula="V2",
            elements=("V",),
            masses=(50.9415,),
            lattice=((3.03, 0.0, 0.0), (0.0, 3.03, 0.0), (0.0, 0.0, 3.03)),
            atoms=((1, 0.0, 0.0, 0.0), (1, 1.515, 1.515, 1.515)),
            fractional=((0.0, 0.0, 0.0), (0.5, 0.5, 0.5)),
            poscar_counts=(2,),
            crystal="bcc conventional",
        ),
        Case(
            name="TiV_B2",
            formula="TiV",
            elements=("Ti", "V"),
            masses=(47.867, 50.9415),
            lattice=((3.18, 0.0, 0.0), (0.0, 3.18, 0.0), (0.0, 0.0, 3.18)),
            atoms=((1, 0.0, 0.0, 0.0), (2, 1.59, 1.59, 1.59)),
            fractional=((0.0, 0.0, 0.0), (0.5, 0.5, 0.5)),
            poscar_counts=(1, 1),
            crystal="B2 (CsCl prototype)",
        ),
    )


def _poscar(case: Case) -> str:
    lines = [
        f"{case.name} deterministic DPA4 benchmark cell",
        "1.0",
    ]
    lines.extend("  %.12f  %.12f  %.12f" % vector for vector in case.lattice)
    lines.append("  " + "  ".join(case.elements))
    lines.append("  " + "  ".join(str(value) for value in case.poscar_counts))
    lines.append("Direct")
    lines.extend("  %.12f  %.12f  %.12f" % position for position in case.fractional)
    return "\n".join(lines) + "\n"


def _lammps_data(case: Case) -> str:
    a, b, c = case.lattice
    if any(abs(value) > 1.0e-12 for value in (a[1], a[2], b[2], c[0], c[1])):
        raise ValueError(f"{case.name}: lattice is not LAMMPS restricted triclinic")
    lines = [
        f"LAMMPS data: {case.name} deterministic DPA4 benchmark cell",
        "",
        f"{len(case.atoms)} atoms",
        f"{len(case.elements)} atom types",
        "",
        f"0.0 {a[0]:.12f} xlo xhi",
        f"0.0 {b[1]:.12f} ylo yhi",
        f"0.0 {c[2]:.12f} zlo zhi",
        f"{b[0]:.12f} {c[0]:.12f} {c[1]:.12f} xy xz yz",
        "",
        "Masses",
        "",
    ]
    lines.extend(
        f"{index} {mass:.8f} # {element}"
        for index, (mass, element) in enumerate(zip(case.masses, case.elements), start=1)
    )
    lines.extend(["", "Atoms # atomic", ""])
    lines.extend(
        f"{index} {atom_type} {x:.12f} {y:.12f} {z:.12f}"
        for index, (atom_type, x, y, z) in enumerate(case.atoms, start=1)
    )
    return "\n".join(lines) + "\n"


def _common_input(case: Case) -> list[str]:
    element_map = " ".join(case.elements)
    lines = [
        "clear",
        "units metal",
        "dimension 3",
        "boundary p p p",
        "atom_style atomic",
        "atom_modify map yes",
        "box tilt large",
        "read_data structure.data",
    ]
    lines.extend(
        f"mass {index} {mass:.8f} # {element}"
        for index, (mass, element) in enumerate(
            zip(case.masses, case.elements), start=1
        )
    )
    lines.extend(
        [
        "neigh_modify every 1 delay 0 check no",
        "pair_style deepmd ${runtime_model}",
        f"pair_coeff * * {element_map}",
        "thermo 1",
        "thermo_style custom step atoms pe ke etotal temp press pxx pyy pzz pxy pxz pyz",
        "thermo_modify flush yes",
        ]
    )
    return lines


def _result_tail() -> list[str]:
    return [
        "variable bench_n equal count(all)",
        "variable bench_pe equal pe",
        "variable bench_pxx equal pxx",
        "variable bench_pyy equal pyy",
        "variable bench_pzz equal pzz",
        "variable bench_pxy equal pxy",
        "variable bench_pxz equal pxz",
        "variable bench_pyz equal pyz",
        'print "BENCH_RESULT natoms=$(v_bench_n:%.0f) pe=$(v_bench_pe:%.17g) pxx=$(v_bench_pxx:%.17g) pyy=$(v_bench_pyy:%.17g) pzz=$(v_bench_pzz:%.17g) pxy=$(v_bench_pxy:%.17g) pxz=$(v_bench_pxz:%.17g) pyz=$(v_bench_pyz:%.17g)"',
        "write_dump all custom forces.dump id type x y z fx fy fz modify sort id",
    ]


def _run0_input(case: Case) -> str:
    lines = _common_input(case)
    lines.extend(["run 0", *_result_tail()])
    return "\n".join(lines) + "\n"


def _md_input(case: Case) -> str:
    lines = _common_input(case)
    lines.extend(
        [
            "timestep 0.001",
            "velocity all create 300.0 4928459 mom yes rot no dist gaussian",
            "fix bench_nve all nve",
            "run 20",
            "unfix bench_nve",
            "run 0 post no",
            *_result_tail(),
        ]
    )
    return "\n".join(lines) + "\n"


def _phonon_input(case: Case) -> str:
    lines = _common_input(case)
    # APEX truncates its ordinary input immediately after pair_coeff before
    # invoking phonoLAMMPS. Match that contract exactly.
    pair_index = next(i for i, line in enumerate(lines) if line.startswith("pair_coeff"))
    return "\n".join(lines[: pair_index + 1]) + "\n"


def _validate_dpa4_input(text: str, case_name: str, mode: str) -> None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for required in (
        "atom_style atomic",
        "atom_modify map yes",
        "read_data structure.data",
    ):
        if lines.count(required) != 1:
            raise ValueError(
                f"{case_name}/{mode}: expected exactly one {required!r} line"
            )
    atom_style = lines.index("atom_style atomic")
    atom_map = lines.index("atom_modify map yes")
    read_data = lines.index("read_data structure.data")
    if not atom_style < atom_map < read_data:
        raise ValueError(
            f"{case_name}/{mode}: atom_modify map yes must precede read_data"
        )
    if any(line.startswith("plugin load ") for line in lines):
        raise ValueError(
            f"{case_name}/{mode}: explicit plugin load is forbidden; "
            "use LAMMPS_PLUGIN_PATH auto-loading"
        )
    pair_styles = [line for line in lines if line.startswith("pair_style deepmd ")]
    if pair_styles != ["pair_style deepmd ${runtime_model}"]:
        raise ValueError(
            f"{case_name}/{mode}: pair_style must use the runtime-model placeholder"
        )


def generate(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    descriptors = []
    for case in _cases():
        case_dir = output / case.name
        rendered_inputs = {
            "in.run0.lammps": _run0_input(case),
            "in.md.lammps": _md_input(case),
            "in.phonon.lammps": _phonon_input(case),
        }
        for name, text in rendered_inputs.items():
            _validate_dpa4_input(text, case.name, name)
        write_text_if_identical(case_dir / "POSCAR", _poscar(case))
        write_text_if_identical(case_dir / "structure.data", _lammps_data(case))
        for name, text in rendered_inputs.items():
            write_text_if_identical(case_dir / name, text)
        artifacts = [
            describe_file(case_dir / name, output)
            for name in (
                "POSCAR",
                "structure.data",
                "in.run0.lammps",
                "in.md.lammps",
                "in.phonon.lammps",
            )
        ]
        descriptors.append(
            {
                "name": case.name,
                "formula": case.formula,
                "elements": list(case.elements),
                "atoms": len(case.atoms),
                "crystal": case.crystal,
                "status": "untested",
                "artifacts": artifacts,
            }
        )
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "benchmark_id": BENCHMARK_ID,
        "generated_at": utc_now(),
        "status": "untested",
        "reason_codes": ["NO_RUNTIME_EXECUTION_EVIDENCE"],
        "cases": descriptors,
    }
    write_json_new(output / "cases.json", manifest)
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("workspace/cases"),
        help="case directory to create (default: workspace/cases)",
    )
    args = parser.parse_args(argv)
    manifest = generate(args.output)
    print(f"generated {len(manifest['cases'])} deterministic cases in {args.output}")
    print("status=untested (generation is not runtime compatibility evidence)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
