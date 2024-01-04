from apex.core.calculator.ABACUS import ABACUS
from apex.core.calculator.Lammps import Lammps
from apex.core.calculator.VASP import VASP
from dflow.python import upload_packages
from . import LAMMPS_INTER_TYPE
upload_packages.append(__file__)


def make_calculator(inter_parameter, path_to_poscar):
    """
    Make an instance of Task
    """
    inter_type = inter_parameter["type"]
    if inter_type == "vasp":
        return VASP(inter_parameter, path_to_poscar)
    elif inter_type == "abacus":
        return ABACUS(inter_parameter, path_to_poscar)
    elif inter_type in LAMMPS_INTER_TYPE:
        return Lammps(inter_parameter, path_to_poscar)
    #    if inter_type == 'siesta':
    #        return Siesta(inter_parameter, path_to_poscar)
    #        pass
    else:
        raise RuntimeError(f"unsupported interaction {inter_type}")

