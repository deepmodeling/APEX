import os
import logging

from dpdata import LabeledSystem
from monty.serialization import dumpfn
from pymatgen.core.structure import Structure
from pymatgen.io.vasp import Incar, Kpoints

from apex.core.calculator.Task import Task
from apex.core.calculator.lib import vasp_utils
from apex.core.calculator.lib.vasp_utils import incar_upper
from apex.utils import sepline
from dflow.python import upload_packages
upload_packages.append(__file__)


class VASP(Task):
    def __init__(self, inter_parameter, path_to_poscar):
        self.inter = inter_parameter
        self.inter_type = inter_parameter["type"]
        self.incar = inter_parameter["incar"]
        self.potcar_prefix = inter_parameter.get("potcar_prefix", "")
        self.potcars = inter_parameter["potcars"]
        self.path_to_poscar = path_to_poscar

    def make_potential_files(self, output_dir):
        potcar_not_link_list = {"vacancy", "interstitial"}
        task_type = output_dir.split("/")[-2].split("_")[0]

        poscar = os.path.abspath(os.path.join(output_dir, "POSCAR"))
        pos_str = Structure.from_file(poscar)
        ele_pos_list_tmp = [ii.as_dict()["element"] for ii in pos_str.species]

        ele_pos_list = [ele_pos_list_tmp[0]]
        for ii in range(1, len(ele_pos_list_tmp)):
            if not ele_pos_list_tmp[ii] == ele_pos_list_tmp[ii - 1]:
                ele_pos_list.append(ele_pos_list_tmp[ii])

        def write_potcar(ele_list, potcar_path):
            with open(potcar_path, "w") as fp:
                for element in ele_list:
                    potcar_file = os.path.join(self.potcar_prefix, self.potcars[element])
                    with open(potcar_file,"r") as fc:
                        fp.write(fc.read())
        
        if task_type in potcar_not_link_list:
            write_potcar(ele_pos_list, output_dir+"/POTCAR")
        else:
            potcar_path = output_dir+"/../POTCAR"
            if not os.path.exists(potcar_path):
                write_potcar(ele_pos_list, potcar_path)
            potcar_link = output_dir+"/POTCAR"
            if not os.path.islink(potcar_link) or "../POTCAR" != os.readlink(potcar_path):
                if os.path.exists(potcar_link):
                    os.remove(potcar_link)
                os.symlink("../POTCAR", potcar_link)
            
        dumpfn(self.inter, output_dir+"/inter.json", indent=4)

    def make_input_file(self, output_dir, task_type, task_param):
        sepline(ch=output_dir)
        dumpfn(task_param, os.path.join(output_dir, "task.json"), indent=4)

        assert os.path.exists(self.incar), "no INCAR file for relaxation"
        relax_incar_path = os.path.abspath(self.incar)
        incar_relax = incar_upper(Incar.from_file(relax_incar_path))

        # deal with relaxation
        prop_type = task_param.get("type", "relaxation")
        cal_type = task_param["cal_type"]
        cal_setting = task_param["cal_setting"]

        # user input INCAR for APEX calculation
        if "input_prop" in cal_setting and os.path.isfile(cal_setting["input_prop"]):
            incar_prop = os.path.abspath(cal_setting["input_prop"])
            incar = incar_upper(Incar.from_file(incar_prop))
            logging.info(f"Will use user specified INCAR (path: {incar_prop}) for {prop_type} calculation")

        # revise INCAR based on the INCAR provided in the "interaction"
        else:
            approach = None
            if prop_type == "phonon":
                approach = task_param.get("approach")
                logging.info(f"No specification of INCAR for {prop_type} calculation, will auto-generate")
                if approach == "linear":
                    incar = incar_upper(Incar.from_str(
                        vasp_utils.make_vasp_phonon_dfpt_incar(
                            ecut=650, ediff=0.0000001, npar=None, kpar=None, kspacing=0.1
                        )))
                elif approach == "displacement":
                    incar = incar_upper(Incar.from_str(
                        vasp_utils.make_vasp_static_incar(
                            ecut=650, ediff=0.0000001, ismear=0, sigma=0.01, npar=8, kpar=1, kspacing=0.1
                        )))

            else:
                if not prop_type == "relaxation":
                    logging.info(f"No specification of INCAR for {prop_type} calculation, will use INCAR in relaxation")
                incar = incar_relax

            if cal_type == "relaxation":
                relax_pos = cal_setting["relax_pos"]
                relax_shape = cal_setting["relax_shape"]
                relax_vol = cal_setting["relax_vol"]
                if [relax_pos, relax_shape, relax_vol] == [True, False, False]:
                    isif = 2
                elif [relax_pos, relax_shape, relax_vol] == [True, True, True]:
                    isif = 3
                elif [relax_pos, relax_shape, relax_vol] == [True, True, False]:
                    isif = 4
                elif [relax_pos, relax_shape, relax_vol] == [False, True, False]:
                    isif = 5
                elif [relax_pos, relax_shape, relax_vol] == [False, True, True]:
                    isif = 6
                elif [relax_pos, relax_shape, relax_vol] == [False, False, True]:
                    isif = 7
                elif [relax_pos, relax_shape, relax_vol] == [False, False, False]:
                    nsw = 0
                    isif = 2
                    if not ("NSW" in incar and incar.get("NSW") == nsw):
                        logging.info(
                            "%s setting NSW to %d"
                            % (self.make_input_file.__name__, nsw)
                        )
                        incar["NSW"] = nsw
                else:
                    raise RuntimeError("not supported calculation setting for VASP")

                if not ("ISIF" in incar and incar.get("ISIF") == isif):
                    logging.info(
                        "%s setting ISIF to %d" % (self.make_input_file.__name__, isif)
                    )
                    incar["ISIF"] = isif

            elif cal_type == "static" and not approach == "linear":
                nsw = 0
                if not ("NSW" in incar and incar.get("NSW") == nsw):
                    logging.info(
                        "%s setting NSW to %d" % (self.make_input_file.__name__, nsw)
                    )
                    incar["NSW"] = nsw
            elif cal_type == "static" and approach == "linear":
                pass
            else:
                raise RuntimeError("not supported calculation type for VASP")

            if "ediff" in cal_setting:
                logging.info(
                    "%s setting EDIFF to %s"
                    % (self.make_input_file.__name__, cal_setting["ediff"])
                )
                incar["EDIFF"] = cal_setting["ediff"]

            if "ediffg" in cal_setting:
                logging.info(
                    "%s setting EDIFFG to %s"
                    % (self.make_input_file.__name__, cal_setting["ediffg"])
                )
                incar["EDIFFG"] = cal_setting["ediffg"]

            if "encut" in cal_setting:
                logging.info(
                    "%s setting ENCUT to %s"
                    % (self.make_input_file.__name__, cal_setting["encut"])
                )
                incar["ENCUT"] = cal_setting["encut"]

            if "kspacing" in cal_setting:
                logging.info(
                    "%s setting KSPACING to %s"
                    % (self.make_input_file.__name__, cal_setting["kspacing"])
                )
                incar["KSPACING"] = cal_setting["kspacing"]

            if "kgamma" in cal_setting:
                logging.info(
                    "%s setting KGAMMA to %s"
                    % (self.make_input_file.__name__, cal_setting["kgamma"])
                )
                incar["KGAMMA"] = cal_setting["kgamma"]

        kspacing = incar.get("KSPACING", None)
        if kspacing is None:
            raise RuntimeError("KSPACING must be given in INCAR")
        kgamma = incar.get("KGAMMA", False)

        self._write_incar_and_kpoints(incar, output_dir, kspacing, kgamma)

    def _write_incar_and_kpoints(self, incar, output_dir, kspacing, kgamma):
        incar.write_file(os.path.join(output_dir, "../INCAR"))
        self._link_file("../INCAR", os.path.join(output_dir, "INCAR"))
        ret = vasp_utils.make_kspacing_kpoints(self.path_to_poscar, kspacing, kgamma)
        Kpoints.from_str(ret).write_file(os.path.join(output_dir, "KPOINTS"))
    
    def _link_file(self, target, link_name):
        if not os.path.islink(link_name):
            os.symlink(target, link_name)
        elif os.readlink(link_name) != target:
            os.remove(link_name)
            os.symlink(target, link_name)

    def compute(self, output_dir):
        outcar = os.path.join(output_dir, "OUTCAR")
        if not os.path.isfile(outcar):
            logging.warning("cannot find OUTCAR in " + output_dir + " skip")
            return None
        
        stress = []
        with open(outcar, "r") as fin:
            lines = fin.read().split("\n")
        for line in lines:
            if "in kB" in line:
                stress_xx = float(line.split()[2])
                stress_yy = float(line.split()[3])
                stress_zz = float(line.split()[4])
                stress_xy = float(line.split()[5])
                stress_yz = float(line.split()[6])
                stress_zx = float(line.split()[7])
                stress.append([])
                stress[-1].append([stress_xx, stress_xy, stress_zx])
                stress[-1].append([stress_xy, stress_yy, stress_yz])
                stress[-1].append([stress_zx, stress_yz, stress_zz])

        ls = LabeledSystem(outcar)
        outcar_dict = ls.as_dict()
        outcar_dict["data"]["stress"] = {
            "@module": "numpy",
            "@class": "array",
            "dtype": "float64",
            "data": stress,
        }

        return outcar_dict

    def forward_files(self, property_type="relaxation"):
        return ["INCAR", "POSCAR", "KPOINTS", "POTCAR"]

    def forward_common_files(self, property_type="relaxation"):
        potcar_not_link_list = ["vacancy", "interstitial"]
        if property_type == "elastic":
            return ["INCAR", "KPOINTS", "POTCAR"]
        elif property_type in potcar_not_link_list:
            return ["INCAR"]
        else:
            return ["INCAR", "POTCAR"]

    def backward_files(self, property_type="relaxation"):
        if property_type == "phonon":
            return ["OUTCAR", "outlog", "CONTCAR", "OSZICAR", "XDATCAR", "vasprun.xml"]
        else:
            return ["OUTCAR", "outlog", "CONTCAR", "OSZICAR", "XDATCAR"]

