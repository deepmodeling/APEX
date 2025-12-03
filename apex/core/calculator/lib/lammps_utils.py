#!/usr/bin/env python3

import os
import re

import dpdata
from dpdata.periodic_table import Element
from packaging.version import Version

from apex.core.lib import util
from apex.core.constants import PERIOD_ELEMENTS_BY_SYMBOL
from dflow.python import upload_packages
upload_packages.append(__file__)


def cvt_lammps_conf(fin, fout, type_map, ofmt="lammps/data"):
    """
    Format convert from fin to fout, specify the output format by ofmt
    Incomplete situation
    """
    supp_ofmt = ["lammps/dump", "lammps/data", "vasp/poscar"]
    supp_exts = ["dump", "lmp", "poscar/POSCAR"]

    if "dump" in fout:
        ofmt = "lammps/dump"
    elif "lmp" in fout:
        ofmt = "lammps/data"
    elif "poscar" in fout or "POSCAR" in fout:
        ofmt = "vasp/poscar"
    if not ofmt in supp_ofmt:
        raise RuntimeError(
            "output format " + ofmt + " is not supported. use one of " + str(supp_ofmt)
        )

    if "lmp" in fout:
        d_poscar = dpdata.System(fin, fmt="vasp/poscar", type_map=type_map)
        d_poscar.to_lammps_lmp(fout, frame_idx=0)
    elif "poscar" in fout or "POSCAR" in fout:
        d_dump = dpdata.System(fin, fmt="lammps/dump", type_map=type_map)
        d_dump.to_vasp_poscar(fout, frame_idx=-1)


def apply_type_map(conf_file, deepmd_type_map, ptypes):
    """
    apply type map.
    conf_file:          conf file converted from POSCAR
    deepmd_type_map:    deepmd atom type map
    ptypes:             atom types defined in POSCAR
    """
    natoms = _get_conf_natom(conf_file)
    ntypes = len(deepmd_type_map)
    with open(conf_file, "r") as fp:
        lines = fp.read().split("\n")
    # with open(conf_file+'.bk', 'w') as fp:
    #     fp.write("\n".join(lines))
    new_lines = lines
    # revise ntypes
    idx_ntypes = -1
    for idx, ii in enumerate(lines):
        if "atom types" in ii:
            idx_ntypes = idx
    if idx_ntypes == -1:
        raise RuntimeError("cannot find the entry 'atom types' in ", conf_file)
    words = lines[idx_ntypes].split()
    words[0] = str(ntypes)
    new_lines[idx_ntypes] = " ".join(words)
    # find number of atoms
    idx_atom_entry = -1
    for idx, ii in enumerate(lines):
        if "Atoms" in ii:
            idx_atom_entry = idx
    if idx_atom_entry == -1:
        raise RuntimeError("cannot find the entry 'Atoms' in ", conf_file)
    # revise atom type
    for idx in range(idx_atom_entry + 2, idx_atom_entry + 2 + natoms):
        ii = lines[idx]
        words = ii.split()
        assert len(words) >= 5
        old_id = int(words[1])
        new_id = deepmd_type_map.index(ptypes[old_id - 1]) + 1
        words[1] = str(new_id)
        ii = " ".join(words)
        new_lines[idx] = ii
    with open(conf_file, "w") as fp:
        fp.write("\n".join(new_lines))


def _get_ntype(conf):
    with open(conf, "r") as fp:
        lines = fp.read().split("\n")
    for ii in lines:
        if "atom types" in ii:
            return int(ii.split()[0])
    raise RuntimeError("cannot find line indicate atom types in ", conf)


def _get_conf_natom(conf):
    with open(conf, "r") as fp:
        lines = fp.read().split("\n")
    for ii in lines:
        if "atoms" in ii:
            return int(ii.split()[0])
    raise RuntimeError("cannot find line indicate atom types in ", conf)


def inter_deepmd(param):
    models = param["model_name"]
    deepmd_version = param["deepmd_version"]
    ret = "pair_style deepmd "
    model_list = ""
    type_map_list = [i for i in param["param_type"]]
    type_map_list_str = " ".join(type_map_list)
    for ii in models:
        model_list += ii + " "
    if Version(deepmd_version) < Version("1"):
        ## DeePMD-kit version == 0.x
        if len(models) > 1:
            ret += "%s 10 model_devi.out\n" % model_list
        else:
            ret += models[0] + "\n"
    else:
        ## DeePMD-kit version >= 1
        if len(models) > 1:
            ret += "%s out_freq 10 out_file model_devi.out\n" % model_list
        else:
            ret += models[0] + "\n"
    ret += "pair_coeff * * %s\n" % type_map_list_str
    return ret


def inter_mace(param):
    ret = ""
    line = "pair_style      mace no_domain_decomposition \n"
    line += "pair_coeff      * * %s " % param["model_name"][0]
    for ii in param["param_type"]:
        line += ii + " "
    line += "\n"
    ret += line
    return ret

def inter_nep(param):
    ret = ""
    line = "pair_style      nep \n"
    line += "pair_coeff      * * %s " % param["model_name"][0]
    for ii in param["param_type"]:
        line += ii + " "
    line += "\n"
    ret += line
    return ret


def inter_snap(param):
    ret = ""
    line = "pair_style      snap \n"
    line += "pair_coeff      * * %s " % param["model_name"][0]
    line += "%s " % param["model_name"][1]
    for ii in param["param_type"]:
        line += ii + " "
    line += "\n"
    ret += line
    return ret


def inter_gap(param):
    init_string = param["init_string"]
    atomic_num_list = param["atomic_num_list"]
    if init_string is None:
        with open(param["model_name"][0], "r") as fp:
            xml_contents = fp.read()
        init_string = re.search(r'label="([^"]*)"', xml_contents).group(1)
    if atomic_num_list is None:
        atomic_num_list = [PERIOD_ELEMENTS_BY_SYMBOL.index(e) + 1 for e in param["param_type"]]

    ret = ""
    line = "pair_style      quip \n"
    line += f'pair_coeff      * * {param["model_name"][0]} "Potential xml_label={init_string}"  '
    for ii in atomic_num_list:
        line += str(ii) + " "
    line += "\n"
    ret += line
    return ret


def inter_rann(param):
    ret = ""
    line = "pair_style      rann \n"
    line += "pair_coeff      * * %s " % param["model_name"][0]
    for ii in param["param_type"]:
        line += ii + " "
    line += "\n"
    ret += line
    return ret


def inter_meam(param):
    ret = ""
    line = "pair_style      meam \n"
    line += "pair_coeff      * * %s " % param["model_name"][0]
    for ii in param["param_type"]:
        line += ii + " "
    line += "%s " % param["model_name"][1]
    for ii in param["param_type"]:
        line += ii + " "
    line += "\n"
    ret += line
    return ret


def inter_meam_spline(param):
    ret = ""
    line = "pair_style      meam/spline \n"
    line += "pair_coeff      * * %s " % param["model_name"][0]
    for ii in param["param_type"]:
        line += ii + " "
    line += "\n"
    ret += line
    return ret


def inter_eam_fs(param):  # 06/08 eam.fs interaction
    ret = ""
    line = "pair_style      eam/fs \n"
    line += "pair_coeff      * * %s " % param["model_name"][0]
    for ii in param["param_type"]:
        line += ii + " "
    line += "\n"
    ret += line
    return ret


def inter_eam_alloy(param):  # 06/08 eam.alloy interaction
    ret = ""
    line = "pair_style      eam/alloy \n"
    line += "pair_coeff      * * %s " % param["model_name"][0]
    for ii in param["param_type"]:
        line += ii + " "
    line += "\n"
    ret += line
    return ret


def element_list(type_map):
    type_map_reverse = {k: v for v, k in type_map.items()}
    type_map_list = []
    tmp_list = list(type_map_reverse.keys())
    tmp_list.sort()
    for ii in tmp_list:
        type_map_list.append(type_map_reverse[ii])
    return type_map_list


def make_lammps_eval(conf, type_map, interaction, param):
    type_map_list = element_list(type_map)

    """
    make lammps input for static calcualtion
    """
    ret = ""
    ret += "clear\n"
    ret += "units 	metal\n"
    ret += "dimension	3\n"
    ret += "boundary	p p p\n"
    ret += "atom_style	atomic\n"
    if param["type"] == "mace":
        ret += "atom_modify map yes\n"
        ret += "newton on\n"
    ret += "box         tilt large\n"
    ret += "read_data   %s\n" % conf
    for ii in range(len(type_map)):
        ret += "mass            %d %.3f\n" % (ii + 1, Element(type_map_list[ii]).mass)
    ret += "neigh_modify    every 1 delay 0 check no\n"
    ret += interaction(param)
    ret += "compute         mype all pe\n"
    ret += "thermo          100\n"
    ret += (
        "thermo_style    custom step temp pe pxx pyy pzz pxy pxz pyz lx ly lz vol c_mype\ntimestep ${timestep}\nvariable        N equal step\nvariable        V equal vol\nvariable        Vatom equal ${V}/count(all)\nvariable        Temp equal temp\nvariable        pote equal c_mype\nvariable        Etotal equal etotal\nvariable        Press equal press\nvariable        stepVal equal step\nvariable        stepVal equal step\ncompute         myRDF all rdf ${rdf_bins} cutoff ${rdf_cutoff}\n"
    )
    ret += "dump            1 all custom 100 dump.relax id type xs ys zs fx fy fz\n"  # 06/09 give dump.relax
    ret += "run    0\n"
    ret += "variable        N equal step\n"
    ret += "variable        V equal vol\n"
    ret += 'variable        E equal "c_mype"\n'
    ret += "variable        tmplx equal lx\n"
    ret += "variable        tmply equal ly\n"
    ret += "variable        Pxx equal pxx\n"
    ret += "variable        Pyy equal pyy\n"
    ret += "variable        Pzz equal pzz\n"
    ret += "variable        Pxy equal pxy\n"
    ret += "variable        Pxz equal pxz\n"
    ret += "variable        Pyz equal pyz\n"
    ret += "variable        Epa equal ${E}/${N}\n"
    ret += "variable        Vpa equal ${V}/${N}\n"
    ret += "variable        AA equal (${tmplx}*${tmply})\n"
    ret += 'print "All done"\n'
    ret += 'print "Total number of atoms = ${N}"\n'
    ret += 'print "Final energy per atoms = ${Epa}"\n'
    ret += 'print "Final volume per atoms = ${Vpa}"\n'
    ret += 'print "Final Base area = ${AA}"\n'
    ret += 'print "Final Stress (xx yy zz xy xz yz) = ${Pxx} ${Pyy} ${Pzz} ${Pxy} ${Pxz} ${Pyz}"\n'
    return ret


def make_lammps_equi(
    conf,
    type_map,
    interaction,
    param,
    etol=0,
    ftol=1e-10,
    maxiter=5000,
    maxeval=500000,
    change_box=True,
    *args,
    **kwargs
):
    type_map_list = element_list(type_map)

    """
    make lammps input for equilibritation
    """
    deepmd_version = param.get("deepmd_version", None)
    is_new_dpmd = False
    if deepmd_version:
        split_v = deepmd_version.split('.')
        is_new_dpmd = bool(int(split_v[0]) >= 2 and int(split_v[1]) >= 1 and int(split_v[2]) >= 5)
    prop_type = kwargs.get("prop_type", "others")
    dump_step = 100
    # detour sychronizing problem of dumping in new version of deepmd-kit >=2.1.5
    if is_new_dpmd and prop_type == "relaxation":
        # make dump_step as large as possible to omit all middle frames and force sychronizing
        dump_step = 100000

    # in.lammps
    ret = ""
    ret += "clear\n"
    ret += "units 	metal\n"
    ret += "dimension	3\n"
    ret += "boundary	p p p\n"
    ret += "atom_style	atomic\n"
    if param["type"] == "mace":
        ret += "atom_modify map yes\n"
        ret += "newton on\n"
    ret += "box         tilt large\n"
    ret += "read_data   %s\n" % conf
    for ii in range(len(type_map)):
        ret += "mass            %d %.3f\n" % (ii + 1, Element(type_map_list[ii]).mass)
    ret += "neigh_modify    every 1 delay 0 check no\n"
    ret += interaction(param)
    ret += "compute         mype all pe\n"
    ret += "thermo          100\n"
    ret += (
        "thermo_style    custom step pe pxx pyy pzz pxy pxz pyz lx ly lz vol c_mype\n"
    )
    ret += f"dump            1 all custom {dump_step} dump.relax id type xs ys zs fx fy fz\n"
    ret += "min_style       cg\n"
    if change_box:
        ret += "fix             1 all box/relax iso 0.0 \n"
        if is_new_dpmd and prop_type != "relaxation":
            # detour synchronizing problem of property calculation by doing one minimization only
            pass
        else:
            ret += "minimize        %e %e %d %d\n" % (etol, ftol, maxiter, maxeval)
            ret += "fix             1 all box/relax aniso 0.0 \n"
            ret += "minimize        %e %e %d %d\n" % (etol, ftol, maxiter, maxeval)
            ret += "fix             1 all box/relax tri 0.0 \n"
    ret += "minimize        %e %e %d %d\n" % (etol, ftol, maxiter, maxeval)
    ret += "variable        N equal step\n"
    ret += "variable        V equal vol\n"
    ret += 'variable        E equal "c_mype"\n'
    ret += "variable        tmplx equal lx\n"
    ret += "variable        tmply equal ly\n"
    ret += "variable        Pxx equal pxx\n"
    ret += "variable        Pyy equal pyy\n"
    ret += "variable        Pzz equal pzz\n"
    ret += "variable        Pxy equal pxy\n"
    ret += "variable        Pxz equal pxz\n"
    ret += "variable        Pyz equal pyz\n"
    ret += "variable        Epa equal ${E}/${N}\n"
    ret += "variable        Vpa equal ${V}/${N}\n"
    ret += "variable        AA equal (${tmplx}*${tmply})\n"
    ret += 'print "All done"\n'
    ret += 'print "Total number of atoms = ${N}"\n'
    ret += 'print "Final energy per atoms = ${Epa}"\n'
    ret += 'print "Final volume per atoms = ${Vpa}"\n'
    ret += 'print "Final Base area = ${AA}"\n'
    ret += 'print "Final Stress (xx yy zz xy xz yz) = ${Pxx} ${Pyy} ${Pzz} ${Pxy} ${Pxz} ${Pyz}"\n'
    return ret


def make_lammps_elastic(
    conf, type_map, interaction, param, etol=0, ftol=1e-10, maxiter=5000, maxeval=500000
):
    type_map_list = element_list(type_map)

    """
    make lammps input for elastic calculation
    """
    ret = ""
    ret += "clear\n"
    ret += "units 	metal\n"
    ret += "dimension	3\n"
    ret += "boundary	p p p\n"
    ret += "atom_style	atomic\n"
    if param["type"] == "mace":
        ret += "atom_modify map yes\n"
        ret += "newton on\n"
    ret += "box         tilt large\n"
    ret += "read_data   %s\n" % conf
    for ii in range(len(type_map)):
        ret += "mass            %d %.3f\n" % (ii + 1, Element(type_map_list[ii]).mass)
    ret += "neigh_modify    every 1 delay 0 check no\n"
    ret += interaction(param)
    ret += "compute         mype all pe\n"
    ret += "thermo          100\n"
    ret += (
        "thermo_style    custom step pe pxx pyy pzz pxy pxz pyz lx ly lz vol c_mype\n"
    )
    ret += "dump            1 all custom 100 dump.relax id type xs ys zs fx fy fz\n"
    ret += "min_style       cg\n"
    ret += "minimize        %e %e %d %d\n" % (etol, ftol, maxiter, maxeval)
    ret += "variable        N equal step\n"
    ret += "variable        V equal vol\n"
    ret += 'variable        E equal "c_mype"\n'
    ret += "variable        Pxx equal pxx\n"
    ret += "variable        Pyy equal pyy\n"
    ret += "variable        Pzz equal pzz\n"
    ret += "variable        Pxy equal pxy\n"
    ret += "variable        Pxz equal pxz\n"
    ret += "variable        Pyz equal pyz\n"
    ret += "variable        Epa equal ${E}/${N}\n"
    ret += "variable        Vpa equal ${V}/${N}\n"
    ret += 'print "All done"\n'
    ret += 'print "Total number of atoms = ${N}"\n'
    ret += 'print "Final energy per atoms = ${Epa}"\n'
    ret += 'print "Final volume per atoms = ${Vpa}"\n'
    ret += 'print "Final Stress (xx yy zz xy xz yz) = ${Pxx} ${Pyy} ${Pzz} ${Pxy} ${Pxz} ${Pyz}"\n'
    return ret

def make_lammps_FiniteTlatt(conf, type_map, interaction, param, cal_setting=None):
    """Build LAMMPS input for finite-T lattice parameter sampling.

    This mirrors the TiAl workflow: equilibrate, then time-average box lengths.

    - Uses variables defined in `variable_FiniteTlatt.in` for temperature and
      averaging controls (N_every/N_repeat/N_freq/equi_step/ave_step/nx/ny/nz).
    - Supports thermostat/ensemble selection via optional `cal_setting` keys:
      thermostat: "nose_hoover" (default) | "langevin"
      ensemble:   "isothermal" (default) | "adiabatic"
      tdamp/pdamp: damping parameters; velocity_seed/dump_step optional.
    """
    type_map_list = element_list(type_map)
    deepmd_version = param.get("deepmd_version", None)
    dump_step = 100
    tdamp = "${tdamp}"
    pdamp = "${pdamp}"
    thermostat = "nose_hoover"
    ensemble = "isothermal"
    velocity_seed = 12345
    if cal_setting is not None:
        dump_step = int(cal_setting.get("dump_step", dump_step))
        tdamp = cal_setting.get("tdamp", tdamp)
        pdamp = cal_setting.get("pdamp", pdamp)
        thermostat = cal_setting.get("thermostat", thermostat)
        ensemble = cal_setting.get("ensemble", ensemble)
        velocity_seed = cal_setting.get("velocity_seed", velocity_seed)

    ret = ""
    ret += "include  variable_FiniteTlatt.in\n"
    ret += "clear\n"
    ret += "units 	metal\n"
    ret += "dimension	3\n"
    ret += "boundary	p p p\n"
    ret += "atom_style	atomic\n"
    ret += "box         tilt large\n"
    ret += "read_data   %s\n" % conf
    ret += "replicate   ${nx} ${ny} ${nz}\n"
    for ii in range(len(type_map)):
        ret += "mass            %d %.3f\n" % (ii + 1, Element(type_map_list[ii]).mass)
    ret += "neigh_modify    every 1 delay 0 check no\n"
    ret += interaction(param)
    ret += "compute         mype all pe\n"
    ret += "thermo          100\n"
    ret += ("thermo_style    custom step pe pxx pyy pzz pxy pxz pyz lx ly lz vol c_mype\n")

    ret += f"velocity all create ${{temperature}} {int(velocity_seed)} mom yes rot yes dist gaussian\n"

    if ensemble == "adiabatic":
        ret += f"fix 1 all nph aniso 1.0 1.0 {pdamp} drag 1.0\n"
    elif thermostat == "langevin":
        ret += f"fix 1 all nph aniso 1.0 1.0 {pdamp} drag 1.0\n"
        ret += f"fix 5 all langevin ${{temperature}} ${{temperature}} {tdamp} {int(velocity_seed)}\n"
    else:
        ret += (
            f"fix 1 all npt temp ${{temperature}} ${{temperature}} {tdamp} "
            f"aniso 0.0 0.0 {pdamp}\n"
        )

    ret += "run ${equi_step}\n"
    ret += "reset_timestep 0 \n"

    # Sampling stage
    ret += f"dump            1 all custom  {dump_step} dump.relax id type xs ys zs fx fy fz\n"
    ret += "variable lx equal lx \n"
    ret += "variable ly equal ly \n"
    ret += "variable lz equal lz \n"
    ret += "fix 2 all ave/time ${N_every} ${N_repeat} ${N_freq}  v_lx v_ly v_lz  ave running file average_box.txt\n"
    ret += "run ${ave_step} \n"

    # Bookkeeping outputs
    ret += "variable        N equal step\n"
    ret += "variable        V equal vol\n"
    ret += 'variable        E equal "c_mype"\n'
    ret += "variable        tmplx equal lx\n"
    ret += "variable        tmply equal ly\n"
    ret += "variable        Pxx equal pxx\n"
    ret += "variable        Pyy equal pyy\n"
    ret += "variable        Pzz equal pzz\n"
    ret += "variable        Pxy equal pxy\n"
    ret += "variable        Pxz equal pxz\n"
    ret += "variable        Pyz equal pyz\n"
    ret += "variable        Epa equal ${E}/${N}\n"
    ret += "variable        Vpa equal ${V}/${N}\n"
    ret += "variable        AA equal (${tmplx}*${tmply})\n"
    ret += 'print "All done"\n'
    ret += 'print "Total number of atoms = ${N}"\n'
    ret += 'print "Final energy per atoms = ${Epa}"\n'
    ret += 'print "Final volume per atoms = ${Vpa}"\n'
    ret += 'print "Final Base area = ${AA}"\n'
    ret += 'print "Final Stress (xx yy zz xy xz yz) = ${Pxx} ${Pyy} ${Pzz} ${Pxy} ${Pxz} ${Pyz}"\n'
    ret += 'print "Final Length (box_x box_y box_z) = ${lx} ${ly} ${lz}"\n'
    return ret

def make_lammps_press_relax(
    conf,
    type_map,
    scale2equi,
    interaction,
    param,
    B0=70,
    bp=0,
    etol=0,
    ftol=1e-10,
    maxiter=5000,
    maxeval=500000,
):
    type_map_list = element_list(type_map)

    """
    make lammps input for relaxation at a certain volume
    scale2equi: the volume scale with respect to equilibrium volume
    """
    ret = ""
    ret += "clear\n"
    ret += "variable        GPa2bar	equal 1e4\n"
    ret += "variable        B0		equal %f\n" % B0
    ret += "variable        bp		equal %f\n" % bp
    ret += "variable	    xx		equal %f\n" % scale2equi
    ret += "variable        yeta	equal 1.5*(${bp}-1)\n"
    ret += (
        "variable        Px0		equal 3*${B0}*(1-${xx})/${xx}^2*exp(${yeta}*(1-${xx}))\n"
    )
    ret += "variable        Px		equal ${Px0}*${GPa2bar}\n"
    ret += "units       metal\n"
    ret += "dimension   3\n"
    ret += "boundary	p p p\n"
    ret += "atom_style	atomic\n"
    if param["type"] == "mace":
        ret += "atom_modify map yes\n"
        ret += "newton on\n"
    ret += "box         tilt large\n"
    ret += "read_data   %s\n" % conf
    for ii in range(len(type_map)):
        ret += "mass            %d %.3f\n" % (ii + 1, Element(type_map_list[ii]).mass)
    ret += "neigh_modify    every 1 delay 0 check no\n"
    ret += interaction(param)
    ret += "compute         mype all pe\n"
    ret += "thermo          100\n"
    ret += (
        "thermo_style    custom step pe pxx pyy pzz pxy pxz pyz lx ly lz vol c_mype\n"
    )
    ret += "dump            1 all custom 100 dump.relax id type xs ys zs fx fy fz\n"
    ret += "min_style       cg\n"
    ret += "fix             1 all box/relax iso ${Px} \n"
    ret += "minimize        %e %e %d %d\n" % (etol, ftol, maxiter, maxeval)
    ret += "fix             1 all box/relax aniso ${Px} \n"
    ret += "minimize        %e %e %d %d\n" % (etol, ftol, maxiter, maxeval)
    ret += "variable        N equal step\n"
    ret += "variable        V equal vol\n"
    ret += 'variable        E equal "c_mype"\n'
    ret += "variable        Pxx equal pxx\n"
    ret += "variable        Pyy equal pyy\n"
    ret += "variable        Pzz equal pzz\n"
    ret += "variable        Pxy equal pxy\n"
    ret += "variable        Pxz equal pxz\n"
    ret += "variable        Pyz equal pyz\n"
    ret += "variable        Epa equal ${E}/${N}\n"
    ret += "variable        Vpa equal ${V}/${N}\n"
    ret += 'print "All done"\n'
    ret += 'print "Total number of atoms  = ${N}"\n'
    ret += 'print "Relax at Press         = ${Px} Bar"\n'
    ret += 'print "Final energy per atoms = ${Epa} eV"\n'
    ret += 'print "Final volume per atoms = ${Vpa} A^3"\n'
    ret += 'print "Final Stress (xx yy zz xy xz yz) = ${Pxx} ${Pyy} ${Pzz} ${Pxy} ${Pxz} ${Pyz}"\n'
    return ret

def make_lammps_annealing(conf, type_map, interaction, param, cal_setting):
    """LAMMPS input for annealing: equilibrate -> heat (ramp) -> optional hold -> cool.

    Uses variables provided by `variable_Annealing.in` in the task directory.
    - thermostat: nose_hoover | langevin
    - ensemble: for nose_hoover: npt|nvt; for langevin: nph|nve (barostat on/off)
    """

    # Power-user override: if a user template is provided, return its content.
    if cal_setting is not None:
        template_in = cal_setting.get("template_in")
        if template_in:
            try:
                with open(template_in, "r") as fp:
                    return fp.read()
            except Exception:
                pass
    type_map_list = element_list(type_map)
    dump_step = int(cal_setting.get("dump_step", 1000))
    tdamp = cal_setting.get("tdamp", 100)
    pdamp = cal_setting.get("pdamp", 1000)
    thermostat = cal_setting.get("thermostat", "nose_hoover")
    ensemble = cal_setting.get("ensemble", "npt")
    vseed = int(cal_setting.get("velocity_seed", 12345))

    ret = ""
    ret += "include  variable_Annealing.in\n"
    ret += "clear\n"
    ret += "units \tmetal\n"
    ret += "dimension\t3\n"
    ret += "boundary\tp p p\n"
    ret += "atom_style\tatomic\n"
    ret += "box         tilt large\n"
    ret += "read_data   %s\n" % conf
    ret += "replicate   ${nx} ${ny} ${nz}\n"
    for ii in range(len(type_map)):
        ret += "mass            %d %.3f\n" % (ii + 1, Element(type_map_list[ii]).mass)
    ret += "neigh_modify    every 1 delay 0 check no\n"
    ret += interaction(param)
    ret += "compute         mype all pe\n"
    ret += "thermo          100\n"
    ret += ("thermo_style    custom step temp pe pxx pyy pzz pxy pxz pyz lx ly lz vol c_mype\n")

    # Initialize velocities and equilibrate at start_temp
    ret += f"velocity all create ${{start_temp}} {vseed} mom yes rot yes dist gaussian\n"

    if thermostat == "langevin":
        # Langevin + barostat (nph) or without (nve)
        if ensemble == "nve":
            ret += "fix 1 all nve\n"
        else:
            ret += f"fix 1 all nph aniso 0.0 0.0 {pdamp} drag 1.0\n"
        ret += f"fix tg all langevin ${{start_temp}} ${{start_temp}} {tdamp} {vseed}\n"
    else:
        # Nose-Hoover NPT or NVT
        if ensemble == "nvt":
            ret += f"fix 1 all nvt temp ${{start_temp}} ${{start_temp}} {tdamp}\n"
        else:
            ret += f"fix 1 all npt temp ${{start_temp}} ${{start_temp}} {tdamp} x 0.0 0.0 {pdamp} y 0.0 0.0 {pdamp} z 0.0 0.0 {pdamp}\n"

    ret += "run ${equi_step}\n"
    ret += "unfix 1\n"
    if thermostat == "langevin":
        ret += "unfix tg\n"

    # Temperature ramp to target_temp
    if thermostat == "langevin":
        if ensemble == "nve":
            ret += "fix 1 all nve\n"
        else:
            ret += f"fix 1 all nph aniso 0.0 0.0 {pdamp} drag 1.0\n"
        ret += f"fix tg all langevin ${{start_temp}} ${{target_temp}} {tdamp} {vseed}\n"
    else:
        if ensemble == "nvt":
            ret += f"fix 1 all nvt temp ${{start_temp}} ${{target_temp}} {tdamp}\n"
        else:
            ret += f"fix 1 all npt temp ${{start_temp}} ${{target_temp}} {tdamp} x 0.0 0.0 {pdamp} y 0.0 0.0 {pdamp} z 0.0 0.0 {pdamp}\n"
    ret += f"dump            1 all custom  {dump_step} dump.anneal_ramp id type xs ys zs fx fy fz\n"
    ret += "fix heat_log all print ${rdf_interval} \"v_stepVal v_N v_Temp v_Vatom v_pote v_Etotal v_Press\" file heating_interval.dat screen no title \"# TimeStep v_N v_Temp v_Vatom v_pote v_Etotal v_Press\"\n"
    ret += "run ${ramp_step}\n"
    ret += "unfix heat_log\n"
    ret += "unfix rdf_ramp\n"
    ret += "undump 1\n"
    ret += "unfix 1\n"
    if thermostat == "langevin":
        ret += "unfix tg\n"

        # Optional hold at target_temp
    ret += "if \"${hold_step} > 0\" then \"fix 1 all nvt temp ${target_temp} ${target_temp} %d\" \"run ${hold_step}\" \"unfix 1\"\n" % tdamp

    # Cool to end_temp
    if thermostat == "langevin":
        if ensemble == "nve":
            ret += "fix 1 all nve\n"
        else:
            ret += f"fix 1 all nph aniso 0.0 0.0 {pdamp} drag 1.0\n"
        ret += f"fix tg all langevin ${{target_temp}} ${{end_temp}} {tdamp} {vseed}\n"
    else:
        if ensemble == "nvt":
            ret += f"fix 1 all nvt temp ${{target_temp}} ${{end_temp}} {tdamp}\n"
        else:
            ret += f"fix 1 all npt temp ${{target_temp}} ${{end_temp}} {tdamp} x 0.0 0.0 {pdamp} y 0.0 0.0 {pdamp} z 0.0 0.0 {pdamp}\n"
    ret += f"dump            2 all custom  {dump_step} dump.anneal_cool id type xs ys zs fx fy fz\n"
    ret += "fix rdf_cool all ave/time ${rdf_interval} 1 ${rdf_interval} c_myRDF[*] file rdf_cool.dat mode vector\n"
    ret += "fix cool_log all print ${rdf_interval} \"v_stepVal v_N v_Temp v_Vatom v_pote v_Etotal v_Press\" file cooling_interval.dat screen no title \"# TimeStep v_N v_Temp v_Vatom v_pote v_Etotal v_Press\"\n"
    ret += "run ${cool_step}\n"
    ret += "unfix cool_log\n"
    ret += "unfix rdf_cool\n"
    ret += "undump 2\n"
    ret += "unfix 1\n"
    if thermostat == "langevin":
        ret += "unfix tg\n"

    ret += 'print "All done"\n'
    return ret

"""
def make_lammps_phonon(
    conf, masses, interaction, param, etol=0, ftol=1e-10, maxiter=5000, maxeval=500000
):
    ret = ""
    ret += "clear\n"
    ret += "units 	metal\n"
    ret += "dimension	3\n"
    ret += "boundary	p p p\n"
    ret += "atom_style	atomic\n"
    ret += "box         tilt large\n"
    ret += "read_data   %s\n" % conf
    ntypes = len(masses)
    for ii in range(ntypes):
        ret += "mass            %d %f\n" % (ii + 1, masses[ii])
    ret += "neigh_modify    every 1 delay 0 check no\n"
    ret += interaction(param)
    return ret
"""

def _get_epa(lines):
    for ii in lines:
        if ("Final energy per atoms" in ii) and (not "print" in ii):
            return float(ii.split("=")[1].split()[0])
    raise RuntimeError(
        'cannot find key "Final energy per atoms" in lines, something wrong'
    )


def _get_vpa(lines):
    for ii in lines:
        if ("Final volume per atoms" in ii) and (not "print" in ii):
            return float(ii.split("=")[1].split()[0])
    raise RuntimeError(
        'cannot find key "Final volume per atoms" in lines, something wrong'
    )


def _get_natoms(lines):
    for ii in lines:
        if ("Total number of atoms" in ii) and (not "print" in ii):
            return int(ii.split("=")[1].split()[0])
    raise RuntimeError(
        'cannot find key "Total number of atoms" in lines, something wrong'
    )


def get_nev(log):
    """
    get natoms, energy_per_atom and volume_per_atom from lammps log
    """
    with open(log, "r") as fp:
        lines = fp.read().split("\n")
    epa = _get_epa(lines)
    vpa = _get_vpa(lines)
    natoms = _get_natoms(lines)
    return natoms, epa, vpa


def get_base_area(log):
    """
    get base area
    """
    with open(log, "r") as fp:
        lines = fp.read().split("\n")
    for ii in lines:
        if ("Final Base area" in ii) and (not "print" in ii):
            return float(ii.split("=")[1].split()[0])


def get_stress(log):
    """
    get stress from lammps log
    """
    with open(log, "r") as fp:
        lines = fp.read().split("\n")
    for ii in lines:
        if ("Final Stress" in ii) and (not "print" in ii):
            vstress = [float(jj) for jj in ii.split("=")[1].split()]
    stress = util.voigt_to_stress(vstress)
    return stress


def poscar_from_last_dump(dump, poscar_out, deepmd_type_map):
    """
    get poscar from the last frame of a lammps MD traj (dump format)
    """
    with open(dump, "r") as fp:
        lines = fp.read().split("\n")
    step_idx = -1
    for idx, ii in enumerate(lines):
        if "ITEM: TIMESTEP" in ii:
            step_idx = idx
    if step_idx == -1:
        raise RuntimeError("cannot find timestep in lammps dump, something wrong")
    with open("tmp_dump", "w") as fp:
        fp.write("\n".join(lines[step_idx:]))
    cvt_lammps_conf("tmp_dump", poscar_out, ofmt="vasp")
    os.remove("tmp_dump")
    with open(poscar_out, "r") as fp:
        lines = fp.read().split("\n")
    types = [deepmd_type_map[int(ii.split("_")[1])] for ii in lines[5].split()]
    lines[5] = " ".join(types)
    with open(poscar_out, "w") as fp:
        lines = fp.write("\n".join(lines))


def check_finished_new(fname, keyword):
    with open(fname, "r") as fp:
        lines = fp.read().split("\n")
    flag = False
    for jj in lines:
        if (keyword in jj) and (not "print" in jj):
            flag = True
    return flag


def check_finished(fname):
    with open(fname, "r") as fp:
        return "Total wall time:" in fp.read()
