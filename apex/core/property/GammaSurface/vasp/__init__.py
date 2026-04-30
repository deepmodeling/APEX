"""VASP binding for GammaSurface."""

from apex.core.property._interaction_helpers import ensure_vasp_interaction
from ..logic import GammaSurface as SharedGammaSurface


PROPERTY_TYPE = "gamma_surface"


class GammaSurface(SharedGammaSurface):
    """GammaSurface implementation bound to the VASP backend."""

    def __init__(self, parameter, inter_param=None):
        super().__init__(parameter, ensure_vasp_interaction(inter_param))


__all__ = ["GammaSurface", "PROPERTY_TYPE"]