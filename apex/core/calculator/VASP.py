import os
import logging
import re

from dpdata import LabeledSystem
from monty.serialization import dumpfn, loadfn
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

    @staticmethod
    def _validate_md_encut(incar, potcar_path):
        """Validate ENCUT against POTCAR ENMAX values without retaining POTCAR data."""
        if not os.path.isfile(potcar_path) or "ENCUT" not in incar:
            return
        enmax_values = []
        with open(potcar_path, encoding="utf-8", errors="ignore") as fp:
            for line in fp:
                match = re.search(r"\bENMAX\s*=\s*([-+0-9.eE]+)", line)
                if match:
                    enmax_values.append(float(match.group(1)))
        if not enmax_values:
            return
        required = 1.3 * max(enmax_values)
        actual = float(incar["ENCUT"])
        if actual + 1.0e-12 < required:
            raise ValueError(
                f"ENCUT={actual:g} eV is below the MD minimum of 1.3 * "
                f"max(POTCAR ENMAX)={required:g} eV"
            )

    @classmethod
    def _md_base_incar(
        cls, template, cal_setting, timestep_fs, pressure, gamma
    ):
        defaults = {
            "PREC": "Accurate",
            "EDIFF": 1.0e-6,
            "ISMEAR": 1,
            "SIGMA": 0.2,
            "LASPH": True,
            "LREAL": "Auto",
            "ALGO": "Normal",
            "IBRION": 0,
            "MDALGO": 3,
            "ISIF": 3,
            "ISYM": 0,
            "LWAVE": False,
            "LCHARG": False,
            "NBLOCK": 1,
        }
        base = Incar(dict(template))
        for tag, default in defaults.items():
            base[tag] = cal_setting.get(tag.lower(), cal_setting.get(tag, default))
        base.update(
            {
                "POTIM": timestep_fs,
                "PSTRESS": pressure,
                "LANGEVIN_GAMMA": gamma,
                "LANGEVIN_GAMMA_L": float(
                    cal_setting.get("langevin_gamma_l", 10.0)
                ),
                "PMASS": float(cal_setting.get("pmass", 1000.0)),
            }
        )
        base.pop("SMASS", None)
        for key in ("ediffg", "encut", "kspacing", "kgamma"):
            if key in cal_setting:
                base[key.upper()] = cal_setting[key]
        return base

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
        md_incar = incar_relax
        if "input_prop" in cal_setting and os.path.isfile(cal_setting["input_prop"]):
            md_incar = incar_upper(
                Incar.from_file(os.path.abspath(cal_setting["input_prop"]))
            )

        if task_type == "finite_t_latt":
            metadata = loadfn(os.path.join(output_dir, "FiniteTlatt.json"))
            temperature = float(metadata["temperature"])
            timestep_fs = float(
                cal_setting.get(
                    "timestep_fs", 1000.0 * float(cal_setting.get("timestep", 0.001))
                )
            )
            pressure = float(cal_setting.get("pressure_kbar", 0.0))
            species = []
            for site in Structure.from_file(self.path_to_poscar):
                name = site.specie.symbol
                if name not in species:
                    species.append(name)
            gamma = cal_setting.get("langevin_gamma", 10.0)
            if isinstance(gamma, (int, float)):
                gamma = [float(gamma)] * len(species)
            else:
                gamma = [float(value) for value in gamma]
            if len(gamma) != len(species):
                raise ValueError(
                    "langevin_gamma must contain one value per POSCAR species"
                )

            base = self._md_base_incar(
                md_incar, cal_setting, timestep_fs, pressure, gamma
            )
            base.update({"TEBEG": temperature, "TEEND": temperature})
            self._validate_md_encut(base, os.path.join(output_dir, "POTCAR"))
            equi = Incar(dict(base))
            equi["NSW"] = int(cal_setting["equi_step"])
            production = Incar(dict(base))
            production["NSW"] = int(cal_setting["ave_step"])
            equi.write_file(os.path.join(output_dir, "INCAR.equi"))
            production.write_file(os.path.join(output_dir, "INCAR.production"))

            kspacing = base.get("KSPACING")
            if kspacing is None:
                raise RuntimeError("KSPACING must be given in INCAR")
            kgamma = base.get("KGAMMA", False)
            ret = vasp_utils.make_kspacing_kpoints(
                self.path_to_poscar, kspacing, kgamma
            )
            Kpoints.from_str(ret).write_file(os.path.join(output_dir, "KPOINTS"))
            with open(os.path.join(output_dir, "run_command"), "w") as fp:
                fp.write(
                    "set -e\n"
                    "cp INCAR.equi INCAR\n"
                    'eval "$APEX_RUN_COMMAND"\n'
                    "mv OUTCAR OUTCAR.equi\n"
                    "[ ! -f XDATCAR ] || mv XDATCAR XDATCAR.equi\n"
                    "cp CONTCAR POSCAR\n"
                    "cp INCAR.production INCAR\n"
                    'eval "$APEX_RUN_COMMAND"\n'
                )
            return

        if task_type == "annealing":
            metadata = loadfn(os.path.join(output_dir, "Annealing.json"))
            timestep_fs = float(metadata["timestep_fs"])
            pressure = float(cal_setting.get("pressure_kbar", 0.0))
            species = []
            for site in Structure.from_file(self.path_to_poscar):
                name = site.specie.symbol
                if name not in species:
                    species.append(name)
            gamma = cal_setting.get("langevin_gamma", 10.0)
            if isinstance(gamma, (int, float)):
                gamma = [float(gamma)] * len(species)
            else:
                gamma = [float(value) for value in gamma]
            if len(gamma) != len(species):
                raise ValueError(
                    "langevin_gamma must contain one value per POSCAR species"
                )
            base = self._md_base_incar(
                md_incar, cal_setting, timestep_fs, pressure, gamma
            )
            self._validate_md_encut(base, os.path.join(output_dir, "POTCAR"))
            if metadata.get("protocol", "ramp_cool") == "coexistence":
                stages = [
                    (
                        "equi",
                        metadata["target_temp"],
                        metadata["target_temp"],
                        metadata["equi_step"],
                    ),
                    (
                        "production",
                        metadata["target_temp"],
                        metadata["target_temp"],
                        metadata["production_step"],
                    ),
                ]
            else:
                stages = [
                    (
                        "eq",
                        metadata["start_temp"],
                        metadata["start_temp"],
                        metadata["equi_step"],
                    ),
                    (
                        "ramp",
                        metadata["start_temp"],
                        metadata["target_temp"],
                        metadata["ramp_step"],
                    ),
                    (
                        "decline",
                        metadata["target_temp"],
                        metadata["end_temp"],
                        metadata["cool_step"],
                    ),
                    (
                        "final_eq",
                        metadata["end_temp"],
                        metadata["end_temp"],
                        metadata["final_equi_step"],
                    ),
                ]
            for name, first_temp, last_temp, nsteps in stages:
                incar = Incar(dict(base))
                incar.update(
                    {
                        "TEBEG": float(first_temp),
                        "TEEND": float(last_temp),
                        "NSW": int(nsteps),
                    }
                )
                incar.write_file(os.path.join(output_dir, f"INCAR.{name}"))
            # Keep a stable transfer manifest across both protocols.
            if metadata.get("protocol", "ramp_cool") == "coexistence":
                aliases = {
                    "eq": "equi",
                    "ramp": "equi",
                    "decline": "production",
                    "final_eq": "production",
                }
            else:
                aliases = {"equi": "eq", "production": "final_eq"}
            for alias, source in aliases.items():
                Incar.from_file(
                    os.path.join(output_dir, f"INCAR.{source}")
                ).write_file(os.path.join(output_dir, f"INCAR.{alias}"))

            kspacing = base.get("KSPACING")
            if kspacing is None:
                raise RuntimeError("KSPACING must be given in INCAR")
            ret = vasp_utils.make_kspacing_kpoints(
                self.path_to_poscar, kspacing, base.get("KGAMMA", False)
            )
            Kpoints.from_str(ret).write_file(os.path.join(output_dir, "KPOINTS"))
            with open(os.path.join(output_dir, "run_command"), "w") as fp:
                fp.write("set -e\nrm -f OUTCAR.apex XDATCAR.apex\n")
                for name, _first, _last, nsteps in stages:
                    if int(nsteps) <= 0:
                        continue
                    fp.write(
                        f"cp INCAR.{name} INCAR\n"
                        'eval "$APEX_RUN_COMMAND"\n'
                        f"printf '\\nAPEX_STAGE {name}\\n' >> OUTCAR.apex\n"
                        "cat OUTCAR >> OUTCAR.apex\n"
                        f"printf '\\nAPEX_STAGE {name}\\n' >> XDATCAR.apex\n"
                        "[ ! -f XDATCAR ] || cat XDATCAR >> XDATCAR.apex\n"
                        "cp CONTCAR POSCAR\n"
                    )
                fp.write(
                    "mv OUTCAR.apex OUTCAR\n"
                    "[ ! -f XDATCAR.apex ] || mv XDATCAR.apex XDATCAR\n"
                )
            return

        # user input INCAR for APEX calculation
        if "input_prop" in cal_setting and os.path.isfile(cal_setting["input_prop"]):
            incar_prop = os.path.abspath(cal_setting["input_prop"])
            incar = incar_upper(Incar.from_file(incar_prop))
            logging.info(f"Will use user specified INCAR (path: {incar_prop}) for {prop_type} calculation")

        # revise INCAR based on the INCAR provided in the "interaction"
        else:
            approach = None
            if prop_type in {"phonon", "gruneisen"}:
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
        task_json = os.path.join(output_dir, "task.json")
        task_param = loadfn(task_json) if os.path.isfile(task_json) else {}
        if task_param.get("type") in {"finite_t_latt", "annealing"}:
            return None
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
        if property_type == "finite_t_latt":
            return [
                "INCAR.equi",
                "INCAR.production",
                "run_command",
                "POSCAR",
                "KPOINTS",
                "POTCAR",
            ]
        if property_type == "annealing":
            return [
                "INCAR.eq",
                "INCAR.ramp",
                "INCAR.decline",
                "INCAR.final_eq",
                "INCAR.equi",
                "INCAR.production",
                "run_command",
                "POSCAR",
                "KPOINTS",
                "POTCAR",
            ]
        return ["INCAR", "POSCAR", "KPOINTS", "POTCAR"]

    def forward_common_files(self, property_type="relaxation"):
        if property_type in {"finite_t_latt", "annealing"}:
            return ["POTCAR"]
        potcar_not_link_list = ["vacancy", "interstitial"]
        if property_type == "elastic":
            return ["INCAR", "KPOINTS", "POTCAR"]
        elif property_type in potcar_not_link_list:
            return ["INCAR"]
        else:
            return ["INCAR", "POTCAR"]

    def backward_files(self, property_type="relaxation"):
        if property_type == "finite_t_latt":
            return ["OUTCAR", "CONTCAR", "XDATCAR"]
        if property_type == "annealing":
            return ["OUTCAR", "XDATCAR", "CONTCAR"]
        if property_type in {"phonon", "gruneisen"}:
            return ["OUTCAR", "outlog", "CONTCAR", "OSZICAR", "XDATCAR", "vasprun.xml"]
        else:
            return ["OUTCAR", "outlog", "CONTCAR", "OSZICAR", "XDATCAR"]
