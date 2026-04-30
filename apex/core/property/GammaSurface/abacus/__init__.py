from ..logic import GammaSurface as SharedGammaSurface

PROPERTY_TYPE = "gamma_surface"


class GammaSurface(SharedGammaSurface):
    def __init__(self, parameter, inter_param=None):
        super().__init__(parameter, inter_param if inter_param is not None else {"type": "abacus"})


__all__ = ["GammaSurface", "PROPERTY_TYPE"]
