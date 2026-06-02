import json
import tempfile
import unittest
from pathlib import Path

import pytest

from apex.core.calculator.lib import lammps_utils


TYPE_MAP = {"Al": 0}
PARAM = {"type": "deepmd"}


def dummy_interaction(param):
    return "pair_style dummy\npair_coeff * * dummy Al\n"


def assert_common_lammps_setup(script):
    assert script.startswith("clear\n")
    assert "units" in script
    assert "dimension" in script
    assert "boundary" in script
    assert "atom_style" in script
    assert "box         tilt large" in script
    assert "read_data   conf.lmp" in script
    assert "mass            1 26.982" in script
    assert "neigh_modify    every 1 delay 0 check no" in script
    assert "pair_style dummy" in script
    assert "pair_coeff * * dummy Al" in script
    assert "compute         mype all pe" in script
    assert "thermo_style    custom step pe pxx pyy pzz pxy pxz pyz lx ly lz vol c_mype" in script
    assert "variable        N equal count(all)" in script
    assert 'variable        E equal "c_mype"' in script
    assert "variable        Epa equal ${E}/${N}" in script
    assert 'print "All done"' in script
    assert 'print "Final Stress (xx yy zz xy xz yz) = ${Pxx} ${Pyy} ${Pzz} ${Pxy} ${Pxz} ${Pyz}"' in script


def test_element_list_orders_by_lammps_type_id():
    assert lammps_utils.element_list({"O": 1, "Al": 0, "Ti": 2}) == [
        "Al",
        "O",
        "Ti",
    ]


def test_make_lammps_eval_static_input_generation():
    script = lammps_utils.make_lammps_eval(
        "conf.lmp", TYPE_MAP, dummy_interaction, PARAM
    )

    assert_common_lammps_setup(script)
    assert "dump            1 all custom 100 dump.relax id type xs ys zs fx fy fz" in script
    assert "run    0" in script
    assert "minimize" not in script
    assert "fix             1 all box/relax" not in script
    assert "variable        AA equal (${tmplx}*${tmply})" in script
    assert 'print "Final Base area = ${AA}"' in script


def test_make_lammps_eval_mace_adds_atom_map_and_newton():
    script = lammps_utils.make_lammps_eval(
        "conf.lmp", TYPE_MAP, dummy_interaction, {"type": "mace"}
    )

    assert "atom_modify map yes" in script
    assert "newton on" in script


@pytest.mark.parametrize(
    "builder",
    [
        lambda: lammps_utils.make_lammps_equi(
            "conf.lmp", TYPE_MAP, dummy_interaction, {"type": "mace"}
        ),
        lambda: lammps_utils.make_lammps_elastic(
            "conf.lmp", TYPE_MAP, dummy_interaction, {"type": "mace"}
        ),
        lambda: lammps_utils.make_lammps_press_relax(
            "conf.lmp", TYPE_MAP, 0.95, dummy_interaction, {"type": "mace"}
        ),
    ],
)
def test_standard_lammps_builders_mace_add_atom_map_and_newton(builder):
    script = builder()

    assert "atom_modify map yes" in script
    assert "newton on" in script


def test_make_lammps_equi_relaxation_default_change_box():
    script = lammps_utils.make_lammps_equi(
        "conf.lmp",
        TYPE_MAP,
        dummy_interaction,
        PARAM,
        etol=1e-6,
        ftol=1e-8,
        maxiter=12,
        maxeval=34,
    )

    assert_common_lammps_setup(script)
    assert "dump            1 all custom 100 dump.relax id type xs ys zs fx fy fz" in script
    assert "min_style       cg" in script
    assert "fix             1 all box/relax iso 0.0" in script
    assert "fix             1 all box/relax aniso 0.0" in script
    assert "fix             1 all box/relax tri 0.0" in script
    assert script.count("minimize        1.000000e-06 1.000000e-08 12 34") == 3
    assert 'print "Final Base area = ${AA}"' in script


def test_make_lammps_equi_without_box_relax_for_fixed_cell_property():
    script = lammps_utils.make_lammps_equi(
        "conf.lmp",
        TYPE_MAP,
        dummy_interaction,
        PARAM,
        etol=0,
        ftol=1e-10,
        maxiter=5,
        maxeval=6,
        change_box=False,
    )

    assert_common_lammps_setup(script)
    assert "fix             1 all box/relax" not in script
    assert script.count("minimize        0.000000e+00 1.000000e-10 5 6") == 1


def test_make_lammps_equi_new_deepmd_relaxation_uses_large_dump_step():
    script = lammps_utils.make_lammps_equi(
        "conf.lmp",
        TYPE_MAP,
        dummy_interaction,
        {"type": "deepmd", "deepmd_version": "2.1.5"},
        prop_type="relaxation",
    )

    assert "dump            1 all custom 100000 dump.relax id type xs ys zs fx fy fz" in script


def test_make_lammps_equi_new_deepmd_property_detour_skips_middle_minimizations():
    script = lammps_utils.make_lammps_equi(
        "conf.lmp",
        TYPE_MAP,
        dummy_interaction,
        {"type": "deepmd", "deepmd_version": "2.1.5"},
        etol=0,
        ftol=1e-10,
        maxiter=5,
        maxeval=6,
        prop_type="elastic",
    )

    assert "fix             1 all box/relax iso 0.0" in script
    assert "fix             1 all box/relax aniso 0.0" not in script
    assert "fix             1 all box/relax tri 0.0" not in script
    assert script.count("minimize        0.000000e+00 1.000000e-10 5 6") == 1


def test_make_lammps_elastic_input_generation():
    script = lammps_utils.make_lammps_elastic(
        "conf.lmp",
        TYPE_MAP,
        dummy_interaction,
        PARAM,
        etol=1e-7,
        ftol=1e-9,
        maxiter=11,
        maxeval=22,
    )

    assert_common_lammps_setup(script)
    assert "dump            1 all custom 100 dump.relax id type xs ys zs fx fy fz" in script
    assert "min_style       cg" in script
    assert "minimize        1.000000e-07 1.000000e-09 11 22" in script
    assert "variable        AA equal" not in script
    assert "Final Base area" not in script


def test_make_lammps_press_relax_eos_input_generation():
    script = lammps_utils.make_lammps_press_relax(
        "conf.lmp",
        TYPE_MAP,
        0.95,
        dummy_interaction,
        PARAM,
        B0=80,
        bp=4,
        etol=1e-7,
        ftol=1e-9,
        maxiter=11,
        maxeval=22,
    )

    assert "clear\n" in script
    assert "variable        GPa2bar" in script
    assert "variable        B0\t\tequal 80.000000" in script
    assert "variable        bp\t\tequal 4.000000" in script
    assert "variable\t    xx\t\tequal 0.950000" in script
    assert "variable        Px\t\tequal ${Px0}*${GPa2bar}" in script
    assert "read_data   conf.lmp" in script
    assert "pair_style dummy" in script
    assert "fix             1 all box/relax iso ${Px}" in script
    assert "fix             1 all box/relax aniso ${Px}" in script
    assert script.count("minimize        1.000000e-07 1.000000e-09 11 22") == 2
    assert 'print "Relax at Press         = ${Px} Bar"' in script
    assert 'print "Final energy per atoms = ${Epa} eV"' in script


def make_finite_t_elastic_input(tmp_path, role):
    with open(tmp_path / "FiniteTelastic.json", "w") as fp:
        json.dump({"role": role}, fp)
    return lammps_utils.make_lammps_FiniteTelastic(
        "conf.lmp",
        TYPE_MAP,
        dummy_interaction,
        PARAM,
        tmp_path,
    )


def test_make_lammps_finite_t_latt_default_cal_setting():
    script = lammps_utils.make_lammps_FiniteTlatt(
        "conf.lmp", TYPE_MAP, dummy_interaction, PARAM
    )

    assert "include  variable_FiniteTlatt.in" in script
    assert "read_data   conf.lmp" in script
    assert "replicate   ${nx} ${ny} ${nz}" in script
    assert "pair_style dummy" in script
    assert (
        "velocity all create ${temperature} 12345 mom yes rot yes dist gaussian"
        in script
    )
    assert (
        "fix 1 all npt temp ${temperature} ${temperature} ${tdamp} "
        "aniso 0.0 0.0 ${pdamp}"
    ) in script
    assert "dump            1 all custom  100 dump.relax id type xs ys zs fx fy fz" in script
    assert (
        "fix 2 all ave/time ${N_every} ${N_repeat} ${N_freq}  v_lx v_ly v_lz  "
        "ave running file average_box.txt"
    ) in script
    assert "run ${equi_step}" in script
    assert "run ${ave_step}" in script
    assert 'print "Final Length (box_x box_y box_z) = ${lx} ${ly} ${lz}"' in script


def test_make_lammps_finite_t_latt_adiabatic_ensemble():
    script = lammps_utils.make_lammps_FiniteTlatt(
        "conf.lmp",
        TYPE_MAP,
        dummy_interaction,
        PARAM,
        {"ensemble": "adiabatic"},
    )

    assert "fix 1 all nph aniso 1.0 1.0 ${pdamp} drag 1.0" in script
    assert "fix 1 all npt temp" not in script
    assert "fix 5 all langevin" not in script


def test_make_lammps_finite_t_latt_langevin_thermostat():
    script = lammps_utils.make_lammps_FiniteTlatt(
        "conf.lmp",
        TYPE_MAP,
        dummy_interaction,
        PARAM,
        {"thermostat": "langevin"},
    )

    assert "fix 1 all nph aniso 1.0 1.0 ${pdamp} drag 1.0" in script
    assert "fix 5 all langevin ${temperature} ${temperature} ${tdamp} 12345" in script
    assert "fix 1 all npt temp" not in script


def test_make_lammps_finite_t_latt_custom_settings():
    script = lammps_utils.make_lammps_FiniteTlatt(
        "conf.lmp",
        TYPE_MAP,
        dummy_interaction,
        PARAM,
        {
            "dump_step": 250,
            "tdamp": "0.25",
            "pdamp": "2.5",
            "velocity_seed": 98765,
        },
    )

    assert (
        "velocity all create ${temperature} 98765 mom yes rot yes dist gaussian"
        in script
    )
    assert (
        "fix 1 all npt temp ${temperature} ${temperature} 0.25 "
        "aniso 0.0 0.0 2.5"
    ) in script
    assert "dump            1 all custom  250 dump.relax id type xs ys zs fx fy fz" in script


def test_make_lammps_finite_t_elastic_equi_role(tmp_path):
    script = make_finite_t_elastic_input(tmp_path, "equi")

    assert "clear\ninclude  variable_FiniteTelastic.in" in script
    assert "read_data   conf.lmp" in script
    assert "replicate   ${nx} ${ny} ${nz}" in script
    assert "pair_style dummy" in script
    assert "velocity all create ${temperature} ${seed} mom yes rot yes dist gaussian" in script
    assert "include  output_FiniteTelastic.in" in script
    assert (
        "fix             1 all npt temp ${temperature} ${temperature} ${tdamp} "
        "aniso 0.0 0.0 ${pdamp}"
    ) in script
    assert "run             ${equi_step}" in script
    assert "write_restart   ${equi_restart}" in script
    assert "read_restart ${restart_source}" not in script
    assert "include  deform_FiniteTelastic.in" not in script
    assert "run             ${response_step}" not in script


def test_make_lammps_finite_t_elastic_mace_setup(tmp_path):
    with open(tmp_path / "FiniteTelastic.json", "w") as fp:
        json.dump({"role": "reference"}, fp)

    script = lammps_utils.make_lammps_FiniteTelastic(
        "conf.lmp",
        TYPE_MAP,
        dummy_interaction,
        {"type": "mace"},
        tmp_path,
    )

    assert script.count("atom_modify map yes") == 2
    assert script.count("newton on") == 2


@pytest.mark.parametrize("role", ["reference", "strained"])
def test_make_lammps_finite_t_elastic_response_roles(tmp_path, role):
    script = make_finite_t_elastic_input(tmp_path, role)

    assert script.count("clear\ninclude  variable_FiniteTelastic.in") == 2
    assert "read_data   conf.lmp" in script
    assert "read_restart ${restart_source}" in script
    assert "write_restart   ${equi_restart}" in script
    assert "change_box all triclinic" in script
    assert "include  deform_FiniteTelastic.in" in script
    assert "include  output_FiniteTelastic.in" in script
    assert "fix             1 all nve" in script
    assert (
        "fix             2 all langevin ${temperature} ${temperature} ${tdamp} "
        "${seed} zero yes"
    ) in script
    assert "run             ${equi_step}" in script
    assert "run             ${response_step}" in script
    assert script.count("pair_style dummy") == 2


def test_make_lammps_finite_t_elastic_invalid_role_raises(tmp_path):
    with open(tmp_path / "FiniteTelastic.json", "w") as fp:
        json.dump({"role": "bad-role"}, fp)

    with pytest.raises(RuntimeError, match="unsupported FiniteTelastic role bad-role"):
        lammps_utils.make_lammps_FiniteTelastic(
            "conf.lmp",
            TYPE_MAP,
            dummy_interaction,
            PARAM,
            tmp_path,
        )


def make_annealing_input(cal_setting):
    defaults = {
        "dump_step": 500,
        "tdamp": "tdamp_var",
        "pdamp": "pdamp_var",
        "velocity_seed": 24680,
    }
    defaults.update(cal_setting)
    return lammps_utils.make_lammps_annealing(
        "conf.lmp",
        TYPE_MAP,
        dummy_interaction,
        PARAM,
        defaults,
    )


def assert_common_annealing_script(script):
    assert "include  variable_Annealing.in" in script
    assert "read_data   conf.lmp" in script
    assert "replicate   ${nx} ${ny} ${nz}" in script
    assert "pair_style dummy" in script
    assert "compute         myRDF all rdf ${rdf_bins} cutoff ${rdf_cutoff}" in script
    assert "run ${equi_step}" in script
    assert "run ${ramp_step}" in script
    assert "run ${cool_step}" in script
    assert (
        'if "${hold_step} > 0" then "fix 1 all nvt temp ${target_temp} '
        '${target_temp} tdamp_var" "run ${hold_step}" "unfix 1"'
    ) in script
    assert "dump            1 all custom  500 dump.anneal_ramp id type xs ys zs fx fy fz" in script
    assert "dump            2 all custom  500 dump.anneal_cool id type xs ys zs fx fy fz" in script
    assert (
        "fix rdf_ramp all ave/time ${rdf_interval} 1 ${rdf_interval} c_myRDF[*] "
        "file rdf_ramp.dat mode vector"
    ) in script
    assert (
        "fix rdf_cool all ave/time ${rdf_interval} 1 ${rdf_interval} c_myRDF[*] "
        "file rdf_cool.dat mode vector"
    ) in script
    assert "file heating_interval.dat" in script
    assert "file cooling_interval.dat" in script
    assert 'print "All done"' in script


def test_make_lammps_annealing_default_nose_hoover_npt():
    script = make_annealing_input({})

    assert_common_annealing_script(script)
    assert "velocity all create ${start_temp} 24680 mom yes rot yes dist gaussian" in script
    assert (
        "fix 1 all npt temp ${start_temp} ${start_temp} tdamp_var "
        "x 0.0 0.0 pdamp_var y 0.0 0.0 pdamp_var z 0.0 0.0 pdamp_var"
    ) in script
    assert (
        "fix 1 all npt temp ${start_temp} ${target_temp} tdamp_var "
        "x 0.0 0.0 pdamp_var y 0.0 0.0 pdamp_var z 0.0 0.0 pdamp_var"
    ) in script
    assert (
        "fix 1 all npt temp ${target_temp} ${end_temp} tdamp_var "
        "x 0.0 0.0 pdamp_var y 0.0 0.0 pdamp_var z 0.0 0.0 pdamp_var"
    ) in script
    assert "fix tg all langevin" not in script


def test_make_lammps_annealing_nose_hoover_nvt():
    script = make_annealing_input({"ensemble": "nvt"})

    assert_common_annealing_script(script)
    assert "fix 1 all nvt temp ${start_temp} ${start_temp} tdamp_var" in script
    assert "fix 1 all nvt temp ${start_temp} ${target_temp} tdamp_var" in script
    assert "fix 1 all nvt temp ${target_temp} ${end_temp} tdamp_var" in script
    assert "fix 1 all npt temp" not in script
    assert "fix tg all langevin" not in script


def test_make_lammps_annealing_langevin_nph():
    script = make_annealing_input({"thermostat": "langevin", "ensemble": "nph"})

    assert_common_annealing_script(script)
    assert script.count("fix 1 all nph aniso 0.0 0.0 pdamp_var drag 1.0") == 3
    assert "fix tg all langevin ${start_temp} ${start_temp} tdamp_var 24680" in script
    assert "fix tg all langevin ${start_temp} ${target_temp} tdamp_var 24680" in script
    assert "fix tg all langevin ${target_temp} ${end_temp} tdamp_var 24680" in script
    assert script.count("unfix tg") == 3
    assert "fix 1 all npt temp" not in script


def test_make_lammps_annealing_langevin_nve():
    script = make_annealing_input({"thermostat": "langevin", "ensemble": "nve"})

    assert_common_annealing_script(script)
    assert script.count("fix 1 all nve") == 3
    assert "fix 1 all nph" not in script
    assert "fix tg all langevin ${start_temp} ${start_temp} tdamp_var 24680" in script
    assert "fix tg all langevin ${start_temp} ${target_temp} tdamp_var 24680" in script
    assert "fix tg all langevin ${target_temp} ${end_temp} tdamp_var 24680" in script
    assert script.count("unfix tg") == 3


class TestLammpsUtils(unittest.TestCase):
    def test_element_list_orders_by_lammps_type_id(self):
        test_element_list_orders_by_lammps_type_id()

    def test_make_lammps_eval_static_input_generation(self):
        test_make_lammps_eval_static_input_generation()

    def test_make_lammps_eval_mace_adds_atom_map_and_newton(self):
        test_make_lammps_eval_mace_adds_atom_map_and_newton()

    def test_standard_lammps_builders_mace_add_atom_map_and_newton(self):
        builders = [
            lambda: lammps_utils.make_lammps_equi(
                "conf.lmp", TYPE_MAP, dummy_interaction, {"type": "mace"}
            ),
            lambda: lammps_utils.make_lammps_elastic(
                "conf.lmp", TYPE_MAP, dummy_interaction, {"type": "mace"}
            ),
            lambda: lammps_utils.make_lammps_press_relax(
                "conf.lmp", TYPE_MAP, 0.95, dummy_interaction, {"type": "mace"}
            ),
        ]
        for builder in builders:
            with self.subTest(builder=builder):
                test_standard_lammps_builders_mace_add_atom_map_and_newton(builder)

    def test_make_lammps_equi_relaxation_default_change_box(self):
        test_make_lammps_equi_relaxation_default_change_box()

    def test_make_lammps_equi_without_box_relax_for_fixed_cell_property(self):
        test_make_lammps_equi_without_box_relax_for_fixed_cell_property()

    def test_make_lammps_equi_new_deepmd_relaxation_uses_large_dump_step(self):
        test_make_lammps_equi_new_deepmd_relaxation_uses_large_dump_step()

    def test_make_lammps_equi_new_deepmd_property_detour_skips_middle_minimizations(self):
        test_make_lammps_equi_new_deepmd_property_detour_skips_middle_minimizations()

    def test_make_lammps_elastic_input_generation(self):
        test_make_lammps_elastic_input_generation()

    def test_make_lammps_press_relax_eos_input_generation(self):
        test_make_lammps_press_relax_eos_input_generation()

    def test_make_lammps_finite_t_latt_default_cal_setting(self):
        test_make_lammps_finite_t_latt_default_cal_setting()

    def test_make_lammps_finite_t_latt_adiabatic_ensemble(self):
        test_make_lammps_finite_t_latt_adiabatic_ensemble()

    def test_make_lammps_finite_t_latt_langevin_thermostat(self):
        test_make_lammps_finite_t_latt_langevin_thermostat()

    def test_make_lammps_finite_t_latt_custom_settings(self):
        test_make_lammps_finite_t_latt_custom_settings()

    def test_make_lammps_finite_t_elastic_equi_role(self):
        with tempfile.TemporaryDirectory() as tmp:
            test_make_lammps_finite_t_elastic_equi_role(Path(tmp))

    def test_make_lammps_finite_t_elastic_mace_setup(self):
        with tempfile.TemporaryDirectory() as tmp:
            test_make_lammps_finite_t_elastic_mace_setup(Path(tmp))

    def test_make_lammps_finite_t_elastic_response_roles(self):
        for role in ["reference", "strained"]:
            with self.subTest(role=role), tempfile.TemporaryDirectory() as tmp:
                test_make_lammps_finite_t_elastic_response_roles(Path(tmp), role)

    def test_make_lammps_finite_t_elastic_invalid_role_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            test_make_lammps_finite_t_elastic_invalid_role_raises(Path(tmp))

    def test_make_lammps_annealing_default_nose_hoover_npt(self):
        test_make_lammps_annealing_default_nose_hoover_npt()

    def test_make_lammps_annealing_nose_hoover_nvt(self):
        test_make_lammps_annealing_nose_hoover_nvt()

    def test_make_lammps_annealing_langevin_nph(self):
        test_make_lammps_annealing_langevin_nph()

    def test_make_lammps_annealing_langevin_nve(self):
        test_make_lammps_annealing_langevin_nve()
