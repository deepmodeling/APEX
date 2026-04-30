"""LAMMPS binding for GammaSurface."""

from apex.core.property._generic_lammps import make_generic_lammps_backend_summary
from apex.core.property._interaction_helpers import ensure_lammps_interaction
from ..logic import GammaSurface as SharedGammaSurface


PROPERTY_TYPE = "gamma_surface"


class GammaSurface(SharedGammaSurface):
    """GammaSurface implementation bound to the LAMMPS backend."""

    def __init__(self, parameter, inter_param=None):
        super().__init__(parameter, ensure_lammps_interaction(inter_param))

    def _fix_task_output(self, task_dir, first_task):
        return None


LAMMPS_BACKEND_SUMMARY = make_generic_lammps_backend_summary(
    property_type="gamma_surface",
    what_it_computes="generalized stacking fault energies over a 2D displacement grid",
    default_cal_type="relaxation",
    default_cal_setting={
        "relax_pos": True,
        "relax_shape": False,
        "relax_vol": False,
    },
    structure_generation="generates slip-plane slabs and displaces top-half atoms on a 2D grid",
    task_metadata_files=[
        "miller.json",
        "slip_length_x.json",
        "slip_length_y.json",
        "displacement.json",
    ],
)


__all__ = ["GammaSurface", "PROPERTY_TYPE", "LAMMPS_BACKEND_SUMMARY"]