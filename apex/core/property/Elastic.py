import glob
import logging
import os
import re
from shutil import copyfile

from monty.serialization import dumpfn, loadfn
from pymatgen.analysis.elasticity.elastic import ElasticTensor
from pymatgen.analysis.elasticity.strain import DeformedStructureSet, Strain
from pymatgen.analysis.elasticity.stress import Stress
from pymatgen.core.structure import Structure
from pymatgen.core.tensors import Tensor
from pymatgen.core.operations import SymmOp
from pymatgen.io.vasp import Incar, Kpoints

from apex.core.calculator.lib import abacus_utils
from apex.core.calculator.lib import vasp_utils
from apex.core.calculator.lib import abacus_scf
from apex.core.property.Property import Property
from apex.core.structure import StructureInfo
from apex.core.refine import make_refine
from apex.core.calculator.lib.vasp_utils import incar_upper
from dflow.python import upload_packages
upload_packages.append(__file__)


class Elastic(Property):
    def __init__(self, parameter, inter_param=None):
        if not ("init_from_suffix" in parameter and "output_suffix" in parameter):
            parameter.setdefault("norm_deform", 1e-2)
            self.norm_deform = parameter["norm_deform"]
            parameter.setdefault("shear_deform", 1e-2)
            self.shear_deform = parameter["shear_deform"]
            parameter.setdefault("conventional", False)
            self.conventional = parameter["conventional"]
            parameter.setdefault("ieee", False)
            self.ieee = parameter["ieee"]
            parameter.setdefault("modulus_type", "voigt")
            self.modulus_type = parameter["modulus_type"]
        parameter.setdefault("cal_type", "relaxation")
        self.cal_type = parameter["cal_type"]
        default_cal_setting = {
            "relax_pos": True,
            "relax_shape": False,
            "relax_vol": False,
        }
        parameter.setdefault("cal_setting", {})
        parameter["cal_setting"].setdefault("relax_pos", True)
        parameter["cal_setting"].setdefault("relax_shape", False)
        parameter["cal_setting"].setdefault("relax_vol", False)
        self.cal_setting = parameter["cal_setting"]
        # parameter['reproduce'] = False
        # self.reprod = parameter['reproduce']
        self.parameter = parameter
        self.inter_param = inter_param or {"type": "vasp"}

    def make_confs(self, path_to_work, path_to_equi, refine=False):
        path_to_work = os.path.abspath(path_to_work)
        if os.path.exists(path_to_work):
            logging.debug("%s already exists" % path_to_work)
        else:
            os.makedirs(path_to_work)
        path_to_equi = os.path.abspath(path_to_equi)

        if "start_confs_path" in self.parameter and os.path.exists(
            self.parameter["start_confs_path"]
        ):
            init_path_list = glob.glob(
                os.path.join(self.parameter["start_confs_path"], "*")
            )
            struct_init_name_list = [os.path.basename(ii) for ii in init_path_list]
            struct_output_name = os.path.basename(os.path.dirname(path_to_work))
            assert struct_output_name in struct_init_name_list
            path_to_equi = os.path.abspath(
                os.path.join(
                    self.parameter["start_confs_path"],
                    struct_output_name,
                    "relaxation",
                    "relax_task",
                )
            )

        task_list = []
        cwd = os.getcwd()

        CONTCAR = "CONTCAR" if self.inter_param["type"] != "abacus" else abacus_utils.final_stru(path_to_equi)
        POSCAR = "POSCAR" if self.inter_param["type"] != "abacus" else "STRU"
        equi_contcar = os.path.join(path_to_equi, CONTCAR)

        os.chdir(path_to_work)
        if os.path.exists(POSCAR) or os.path.islink(POSCAR):
            os.remove(POSCAR)
        os.symlink(os.path.relpath(equi_contcar), POSCAR)
        # stress, deal with unsupported stress in dpdata
        equi_result = loadfn(os.path.join(path_to_equi, "result.json"))
        equi_stress = equi_result["stress"][-1]
        dumpfn(equi_stress, "equi.stress.json", indent=4)
        os.chdir(cwd)

        if refine:
            print("elastic refine starts")
            task_list = make_refine(
                self.parameter["init_from_suffix"],
                self.parameter["output_suffix"],
                path_to_work,
            )

            # record strain
            # df = Strain.from_deformation(dfm_ss.deformations[idid])
            # dumpfn(df.as_dict(), 'strain.json', indent=4)
            init_from_path = re.sub(
                self.parameter["output_suffix"][::-1],
                self.parameter["init_from_suffix"][::-1],
                path_to_work[::-1],
                count=1,
            )[::-1]
            task_list_basename = list(map(os.path.basename, task_list))

            for ii in task_list_basename:
                init_from_task = os.path.join(init_from_path, ii)
                output_task = os.path.join(path_to_work, ii)
                os.chdir(output_task)
                if os.path.isfile("strain.json"):
                    os.remove("strain.json")
                copyfile(os.path.join(init_from_task, "strain.json"), "strain.json")
                # os.symlink(os.path.relpath(
                #    os.path.join((re.sub(self.parameter['output_suffix'], self.parameter['init_from_suffix'], ii)),
                #                 'strain.json')),
                #           'strain.json')
        else:
            norm_def = self.norm_deform
            shear_def = self.shear_deform
            norm_strains = [-norm_def, -0.5 * norm_def, 0.5 * norm_def, norm_def]
            shear_strains = [-shear_def, -0.5 * shear_def, 0.5 * shear_def, shear_def]

            if not os.path.exists(equi_contcar):
                raise RuntimeError("please do relaxation first")

            if self.inter_param["type"] == "abacus":
                ss = abacus_utils.stru2Structure(equi_contcar)
            else:
                ss = Structure.from_file(equi_contcar)
            # find conventional cell
            if self.conventional:
                st = StructureInfo(ss)
                ss = st.conventional_structure
                ss.to(os.path.join(path_to_work, "POSCAR.conv"), "POSCAR")

            # convert to IEEE-standard
            if self.ieee:
                rot = Tensor.get_ieee_rotation(ss)
                op = SymmOp.from_rotation_and_translation(rot)
                ss.apply_operation(op)
                ss.to(os.path.join(path_to_work, "POSCAR.ieee"), "POSCAR")

            dfm_ss = DeformedStructureSet(
                ss,
                symmetry=False,
                norm_strains=norm_strains,
                shear_strains=shear_strains,
            )
            n_dfm = len(dfm_ss)

            print("gen with norm " + str(norm_strains))
            print("gen with shear " + str(shear_strains))
            for ii in range(n_dfm):
                output_task = os.path.join(path_to_work, "task.%06d" % ii)
                os.makedirs(output_task, exist_ok=True)
                os.chdir(output_task)
                for jj in [
                    "INCAR",
                    "POTCAR",
                    "POSCAR",
                    "conf.lmp",
                    "in.lammps",
                    "STRU",
                ]:
                    if os.path.exists(jj):
                        os.remove(jj)
                task_list.append(output_task)
                dfm_ss.deformed_structures[ii].to("POSCAR", "POSCAR")
                if self.inter_param["type"] == "abacus":
                    abacus_utils.poscar2stru("POSCAR", self.inter_param, "STRU")
                    #os.remove("POSCAR")
                # record strain
                df = Strain.from_deformation(dfm_ss.deformations[ii])
                dumpfn(df.as_dict(), "strain.json", indent=4)
        os.chdir(cwd)
        return task_list

    def post_process(self, task_list):
        if self.inter_param["type"] == "abacus":
            POSCAR = "STRU"
            INCAR = "INPUT"
            KPOINTS = "KPT"
        else:
            POSCAR = "POSCAR"
            INCAR = "INCAR"
            KPOINTS = "KPOINTS"

        cwd = os.getcwd()
        poscar_start = os.path.abspath(os.path.join(task_list[0], "..", POSCAR))
        os.chdir(os.path.join(task_list[0], ".."))
        if os.path.isfile(os.path.join(task_list[0], INCAR)):
            if self.inter_param["type"] == "abacus":
                input_aba = abacus_scf.get_abacus_input_parameters("INPUT")
                if "kspacing" in input_aba:
                    kspacing = float(input_aba["kspacing"])
                    kpt = abacus_utils.make_kspacing_kpt(poscar_start, kspacing)
                    kpt += [0, 0, 0]
                    abacus_utils.write_kpt("KPT", kpt)
                    del input_aba["kspacing"]
                    os.remove("INPUT")
                    abacus_utils.write_input("INPUT", input_aba)
                else:
                    os.rename(os.path.join(task_list[0], "KPT"), "./KPT")
            else:
                incar = incar_upper(
                    Incar.from_file(os.path.join(task_list[0], "INCAR"))
                )
                kspacing = incar.get("KSPACING")
                kgamma = incar.get("KGAMMA", False)
                ret = vasp_utils.make_kspacing_kpoints(poscar_start, kspacing, kgamma)
                kp = Kpoints.from_str(ret)
                if os.path.isfile("KPOINTS"):
                    os.remove("KPOINTS")
                kp.write_file("KPOINTS")

            os.chdir(cwd)
            kpoints_universal = os.path.abspath(
                os.path.join(task_list[0], "..", KPOINTS)
            )
            for ii in task_list:
                if os.path.exists(os.path.join(ii, KPOINTS)):
                    os.remove(os.path.join(ii, KPOINTS))
                os.chdir(ii)
                os.symlink(os.path.relpath(kpoints_universal), KPOINTS)

        os.chdir(cwd)

    def task_type(self):
        return self.parameter["type"]

    def task_param(self):
        return self.parameter

    def _compute_lower(self, output_file, all_tasks, all_res):
        output_file = os.path.abspath(output_file)
        res_data = {}
        ptr_data = os.path.dirname(output_file) + "\n"

        equi_stress = Stress(
            loadfn(os.path.join(os.path.dirname(output_file), "equi.stress.json"))
        )
        equi_stress *= -1000
        lst_strain = []
        lst_stress = []
        for ii in all_tasks:
            strain = loadfn(os.path.join(ii, "strain.json"))
            # stress, deal with unsupported stress in dpdata
            stress = loadfn(os.path.join(ii, "result_task.json"))["stress"][-1]
            lst_strain.append(strain)
            lst_stress.append(Stress(stress * -1000))

        et = ElasticTensor.from_independent_strains(
            lst_strain, lst_stress, eq_stress=equi_stress, vasp=False
        )
        res_data["elastic_tensor"] = []
        for ii in range(6):
            c_ii = []
            for jj in range(6):
                c_ii.append(et.voigt[ii][jj] / 1e4)
                ptr_data += "%7.2f " % (et.voigt[ii][jj] / 1e4)
            res_data["elastic_tensor"].append(c_ii)
            ptr_data += "\n"

        if self.modulus_type == "voigt":
            BV = et.k_voigt / 1e4
            GV = et.g_voigt / 1e4
        elif self.modulus_type == "reuss":
            BV = et.k_reuss / 1e4
            GV = et.g_reuss / 1e4
        elif self.modulus_type == "vrh":
            BV = et.k_vrh / 1e4
            GV = et.g_vrh / 1e4

        EV = 9 * BV * GV / (3 * BV + GV)
        uV = 0.5 * (3 * BV - 2 * GV) / (3 * BV + GV)

        res_data["B"] = BV
        res_data["G"] = GV
        res_data["E"] = EV
        res_data["u"] = uV
        ptr_data += "# Bulk   Modulus B = %.2f GPa\n" % BV
        ptr_data += "# Shear  Modulus G = %.2f GPa\n" % GV
        ptr_data += "# Youngs Modulus E = %.2f GPa\n" % EV
        ptr_data += "# Poission Ratio u = %.2f\n " % uV

        dumpfn(res_data, output_file, indent=4)

        return res_data, ptr_data
