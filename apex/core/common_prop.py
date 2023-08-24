import glob
import os

from apex.core.calculator.calculator import make_calculator
from apex.core.property.Elastic import Elastic
from apex.core.property.EOS import EOS
from apex.core.property.Gamma import Gamma
from apex.core.property.Interstitial import Interstitial
from apex.core.lib.utils import create_path
from apex.core.property.Surface import Surface
from apex.core.property.Vacancy import Vacancy
from apex.lib.util import sepline
from dflow.python import upload_packages
upload_packages.append(__file__)

lammps_task_type = ["deepmd", "meam", "eam_fs", "eam_alloy"]


def make_property_instance(parameters, inter_param):
    """
    Make an instance of Property
    """
    prop_type = parameters["type"]
    if prop_type == "eos":
        return EOS(parameters, inter_param)
    elif prop_type == "elastic":
        return Elastic(parameters, inter_param)
    elif prop_type == "vacancy":
        return Vacancy(parameters, inter_param)
    elif prop_type == "interstitial":
        return Interstitial(parameters, inter_param)
    elif prop_type == "surface":
        return Surface(parameters, inter_param)
    elif prop_type == "gamma":
        return Gamma(parameters, inter_param)
    else:
        raise RuntimeError(f"unknown dflowautotest type {prop_type}")


def make_property(confs, inter_param, property_list):
    # find all POSCARs and their name like mp-xxx
    # ...
    # conf_dirs = glob.glob(confs)
    # conf_dirs.sort()
    conf_dirs = []
    for conf in confs:
        conf_dirs.extend(glob.glob(conf))
    conf_dirs.sort()
    for ii in conf_dirs:
        sepline(ch=ii, screen=True)
        for jj in property_list:
            if jj.get("skip", False):
                continue
            if "init_from_suffix" and "output_suffix" in jj:
                do_refine = True
                suffix = jj["output_suffix"]
            elif "reproduce" in jj and jj["reproduce"]:
                do_refine = False
                suffix = "reprod"
            else:
                do_refine = False
                suffix = "00"
            # generate working directory like mp-xxx/eos_00 if jj['type'] == 'eos'
            # handel the exception that the working directory exists
            # ...

            # determine the suffix: from scratch or refine
            # ...

            property_type = jj["type"]
            path_to_equi = os.path.join(ii, "relaxation", "relax_task")
            path_to_work = os.path.join(ii, property_type + "_" + suffix)

            create_path(path_to_work)

            inter_param_prop = inter_param
            if "cal_setting" in jj and "overwrite_interaction" in jj["cal_setting"]:
                inter_param_prop = jj["cal_setting"]["overwrite_interaction"]

            prop = make_property_instance(jj, inter_param_prop)
            task_list = prop.make_confs(path_to_work, path_to_equi, do_refine)

            for kk in task_list:
                poscar = os.path.join(kk, "POSCAR")
                inter = make_calculator(inter_param_prop, poscar)
                inter.make_potential_files(kk)
                #dlog.debug(prop.task_type())  ### debug
                inter.make_input_file(kk, prop.task_type(), prop.task_param())

            prop.post_process(
                task_list
            )  # generate same KPOINTS file for elastic when doing VASP


def post_property(confs, inter_param, property_list):
    # find all POSCARs and their name like mp-xxx
    # ...
    #    task_list = []
    # conf_dirs = glob.glob(confs)
    # conf_dirs.sort()
    conf_dirs = []
    for conf in confs:
        conf_dirs.extend(glob.glob(conf))
    conf_dirs.sort()
    for ii in conf_dirs:
        for jj in property_list:
            # determine the suffix: from scratch or refine
            # ...
            if jj.get("skip", False):
                continue
            if "init_from_suffix" and "output_suffix" in jj:
                suffix = jj["output_suffix"]
            elif "reproduce" in jj and jj["reproduce"]:
                suffix = "reprod"
            else:
                suffix = "00"

            inter_param_prop = inter_param
            if "cal_setting" in jj and "overwrite_interaction" in jj["cal_setting"]:
                inter_param_prop = jj["cal_setting"]["overwrite_interaction"]

            property_type = jj["type"]
            path_to_work = os.path.join(ii, property_type + "_" + suffix)
            prop = make_property_instance(jj, inter_param_prop)
            prop.compute(
                os.path.join(path_to_work, "result.json"),
                os.path.join(path_to_work, "result.out"),
                path_to_work,
            )
