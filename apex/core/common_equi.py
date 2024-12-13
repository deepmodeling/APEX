import glob
import os
import shutil
import logging
import dpdata
from monty.serialization import dumpfn
from pymatgen.core.structure import Structure
from pymatgen.analysis.structure_matcher import StructureMatcher
from apex.core.calculator.lib import abacus_utils
from apex.core.lib import crys
from apex.core.calculator.calculator import make_calculator
from apex.core.lib.utils import create_path
from apex.core.lib.dispatcher import make_submission
from apex.core.mpdb import get_structure
from apex.core.structure import StructureInfo
from dflow.python import upload_packages
upload_packages.append(__file__)
lammps_task_type = ['deepmd', 'eam_alloy', 'meam', 'eam_fs', 'meam_spline', 'snap', 'gap', 'rann', 'mace', 'nep']


def make_equi(confs, inter_param, relax_param):
    # find all POSCARs and their name like mp-xxx
    logging.debug("debug info make equi")
    if "type_map" in inter_param:
        ele_list = list(inter_param["type_map"].keys())
    else:
        ele_list = list(inter_param["potcars"].keys())

    logging.debug("ele_list %s" % ":".join(ele_list))
    conf_dirs = []
    for conf in confs:
        conf_dirs.extend(glob.glob(conf))
    conf_dirs = list(set(conf_dirs))
    conf_dirs.sort()

    # generate a list of task names like mp-xxx/relaxation/relax_task
    cwd = os.getcwd()
    # generate poscar for single element crystal
    if len(ele_list) == 1 or "single" in inter_param:
        element_label = int(inter_param.get("single", 0))
        for ii in conf_dirs:
            os.chdir(ii)
            crys_type = ii.split("/")[-1]
            logging.debug(f"crys_type: {crys_type}, pwd: {os.getcwd()}")
            crystal_generators = {
                "std-fcc": crys.fcc1,
                "std-hcp": crys.hcp,
                "std-dhcp": crys.dhcp,
                "std-bcc": crys.bcc,
                "std-diamond": crys.diamond,
                "std-sc": crys.sc,
            }
            if crys_type in crystal_generators and not os.path.exists("POSCAR"):
                crystal_generators[crys_type](ele_list[element_label]).to("POSCAR", "POSCAR")
                        
            if inter_param["type"] == "abacus" and not os.path.exists("STRU"):
                abacus_utils.poscar2stru("POSCAR", inter_param, "STRU")
                os.remove("POSCAR")

            os.chdir(cwd)
    task_dirs = []
    # make task directories like mp-xxx/relaxation/relax_task
    # if mp-xxx/exists then print a warning and exit.
    for ii in conf_dirs:
        crys_type = ii.split("/")[-1]
        logging.debug(f"crys_type: {crys_type}")

        if "mp-" in crys_type and not os.path.exists(os.path.join(ii, "POSCAR")):
            get_structure(crys_type).to("POSCAR", os.path.join(ii, "POSCAR"))
            if inter_param["type"] == "abacus" and not os.path.exists("STRU"):
                abacus_utils.poscar2stru(
                    os.path.join(ii, "POSCAR"), inter_param, os.path.join(ii, "STRU")
                )
                os.remove(os.path.join(ii, "POSCAR"))

        poscar = os.path.abspath(os.path.join(ii, "POSCAR"))
        POSCAR = "POSCAR"
        if inter_param["type"] == "abacus":
            stru = os.path.join(ii, "STRU")
            # if no STRU found, try to convert POSCAR to STRU
            if not os.path.isfile(stru):
                logging.warning(msg='No STRU found...')
                if os.path.isfile(poscar):
                    logging.info(msg=f'will convert {poscar} into STRU...')
                    sys = dpdata.System(poscar, fmt="vasp/poscar")
                    sys.to("abacus/stru", stru)
                else:
                    raise FileNotFoundError("No file %s" % stru)
            if not os.path.exists(os.path.join(ii, POSCAR)):
                sys = dpdata.System(stru, fmt="abacus/stru")
                sys.to("vasp/poscar", os.path.join(ii, POSCAR))

            shutil.copyfile(stru, os.path.join(ii, "STRU.bk"))
            abacus_utils.modify_stru_path(stru, "pp_orb/", inter_param)
            orig_poscar = poscar
            orig_POSCAR = POSCAR
            poscar = os.path.abspath(stru)
            POSCAR = "STRU"
        if not os.path.exists(poscar):
            raise FileNotFoundError("no configuration for APEX")
        if os.path.exists(os.path.join(ii, "relaxation", "jr.json")):
            os.remove(os.path.join(ii, "relaxation", "jr.json"))

        relax_dirs = os.path.abspath(
            os.path.join(ii, "relaxation", "relax_task")
        )  # to be consistent with apex in make dispatcher
        create_path(relax_dirs)
        task_dirs.append(relax_dirs)
        os.chdir(relax_dirs)
        # copy POSCARs to mp-xxx/relaxation/relax_task
        if os.path.isfile(POSCAR):
            os.remove(POSCAR)
        os.symlink(os.path.relpath(poscar), POSCAR)
        if inter_param["type"] == "abacus":
            os.symlink(os.path.relpath(orig_poscar), orig_POSCAR)
        os.chdir(cwd)
    task_dirs.sort()
    # generate task files
    relax_param["cal_type"] = "relaxation"
    relax_param.setdefault("cal_setting", {}).setdefault("relax_pos", True)
    relax_param["cal_setting"].setdefault("relax_shape", True)
    relax_param["cal_setting"].setdefault("relax_vol", True)
    
    for ii in task_dirs:
        poscar = os.path.join(ii, "POSCAR")
        logging.debug(f"task_dir {ii}")
        inter = make_calculator(inter_param, poscar)
        inter.make_potential_files(ii)
        inter.make_input_file(ii, "relaxation", relax_param)


def run_equi(confs, inter_param, mdata):
    # find all POSCARs and their name like mp-xxx
    conf_dirs = []
    for conf in confs:
        conf_dirs.extend(glob.glob(conf))
    conf_dirs = list(set(conf_dirs))
    conf_dirs.sort()

    processes = len(conf_dirs)

    # generate a list of task names like mp-xxx/relaxation/relax_task
    work_path_list = []
    for ii in conf_dirs:
        work_path_list.append(os.path.join(ii, "relaxation"))
    all_task = []
    for ii in work_path_list:
        all_task.append(os.path.join(ii, "relax_task"))
    run_tasks = all_task

    # dispatch the tasks
    # POSCAR here is useless
    virtual_calculator = make_calculator(inter_param, "POSCAR")
    forward_files = virtual_calculator.forward_files()
    forward_common_files = virtual_calculator.forward_common_files()
    backward_files = virtual_calculator.backward_files()
    #    backward_files += logs
    machine = mdata.get("machine", None)
    resources = mdata.get("resources", None)
    command = mdata.get("run_command", None)
    group_size = mdata.get("group_size", 1)
    work_path = os.getcwd()
    print("%s --> Runing... " % (work_path))

    submission = make_submission(
        mdata_machine=machine,
        mdata_resources=resources,
        commands=[command],
        work_path=work_path,
        run_tasks=run_tasks,
        group_size=group_size,
        forward_common_files=forward_common_files,
        forward_files=forward_files,
        backward_files=backward_files,
        outlog="outlog",
        errlog="errlog",
    )
    submission.run_submission()


def post_equi(confs, inter_param):
    # find all POSCARs and their name like mp-xxx
    conf_dirs = []
    for conf in confs:
        conf_dirs.extend(glob.glob(conf))
    conf_dirs = list(set(conf_dirs))
    conf_dirs.sort()
    task_dirs = []
    for ii in conf_dirs:
        task_dirs.append(os.path.abspath(os.path.join(ii, "relaxation", "relax_task")))
    task_dirs.sort()

    # generate a list of task names like mp-xxx/relaxation
    # dump the relaxation result.
    for ii in task_dirs:
        poscar = os.path.join(ii, "POSCAR")
        inter = make_calculator(inter_param, poscar)
        res = inter.compute(ii)
        contcar = os.path.join(ii, "CONTCAR")
        try:
            ss = Structure.from_file(contcar)
        except FileNotFoundError:
            logging.warning(f"No CONTCAR found in {ii}, skip")
            continue
        try:
            init_ss = Structure.from_file(poscar)
        except FileNotFoundError:
            logging.warning(f"No POSCAR found in {ii}, skip")
            continue
        st = StructureInfo(ss)
        matcher = StructureMatcher()
        is_match = matcher.fit(init_ss, ss)
        if not is_match:
            logging.warning(f"Structure mismatch after relaxation in {ii}")
        struct_info_dict = {
            "space_group_symbol": st.space_group_symbol,
            "space_group_number": st.space_group_number,
            "point_group_symbol": st.point_group_symbol,
            "crystal_system": st.crystal_system,
            "lattice_type": st.lattice_type,
            "mismatch": not is_match,
        }

        dumpfn(struct_info_dict, os.path.join(ii, "structure.json"), indent=4)
        dumpfn(res, os.path.join(ii, "result.json"), indent=4)



