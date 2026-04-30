"""ABACUS binding for GammaSurface."""

import os

import dpdata
from pymatgen.core.structure import Structure

from apex.core.calculator.lib import abacus_utils
from apex.core.calculator.lib import vasp_utils
from apex.core.property._interaction_helpers import ensure_abacus_interaction
from ..logic import GammaSurface as SharedGammaSurface


PROPERTY_TYPE = "gamma_surface"


class GammaSurface(SharedGammaSurface):
    """GammaSurface implementation bound to the ABACUS backend."""

    def __init__(self, parameter, inter_param=None):
        super().__init__(parameter, ensure_abacus_interaction(inter_param))

    def _resolve_equilibrium_structure(self, path_to_equi):
        return os.path.join(path_to_equi, abacus_utils.final_stru(path_to_equi)), "STRU"

    def _load_equilibrium_structure(self, equi_contcar):
        stru = dpdata.System(equi_contcar, fmt="stru")
        stru.to("contcar", "CONTCAR.tmp")
        try:
            ptypes = vasp_utils.get_poscar_types("CONTCAR.tmp")
            ss = Structure.from_file("CONTCAR.tmp")
        finally:
            os.remove("CONTCAR.tmp")
        return ptypes, ss

    def _finalize_task_structure(self):
        abacus_utils.poscar2stru("POSCAR", self.inter_param, "STRU")

    def _fix_task_output(self, task_dir, first_task):
        self._GammaSurface__stru_fix(os.path.join(task_dir, "STRU"))


__all__ = ["GammaSurface", "PROPERTY_TYPE"]