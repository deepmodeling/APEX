import numpy as np
from typing import Dict, Union, Any, Tuple
from dflow.python import upload_packages
from apex.core.lib.trans_tools import (plane_miller_bravais_to_miller,
                                       direction_miller_bravais_to_miller)

upload_packages.append(__file__)


class SlabSlipSystem(object):
    __dict_atomic_system = {
        'fcc': {
            '001x100': {'plane': (0, 0, 1), 'x': (1, 0, 0), 'xy': (0, 1, 0),
                        'default_frac_slip_len': 1},
            '110x-110': {'plane': (1, 1, 0), 'x': (-1, 1, 0), 'xy': (0, 0, 1),
                         'default_frac_slip_len': float(np.sqrt(2))},
            '111x11-2': {'plane': (1, 1, 1), 'x': (1, 1, -2), 'xy': (-1, 1, 0),
                         'default_frac_slip_len': float(np.sqrt(6))},
            '111x-1-12': {'plane': (1, 1, 1), 'x': (-1, -1, 2), 'xy': (1, -1, 0),
                          'default_frac_slip_len': float(np.sqrt(6))},
            '111x-110': {'plane': (1, 1, 1), 'x': (-1, 1, 0), 'xy': (-1, -1, 2),
                         'default_frac_slip_len': float(np.sqrt(2))},
            '111x1-10': {'plane': (1, 1, 1), 'x': (1, -1, 0), 'xy': (1, 1, -2),
                         'default_frac_slip_len': float(np.sqrt(2))}
        },
        
        'bcc': {
            '001x100': {'plane': (0, 0, 1), 'x': (1, 0, 0), 'xy': (0, 1, 0),
                        'default_frac_slip_len': 1},
            '111x-110': {'plane': (1, 1, 1), 'x': (-1, 1, 0), 'xy': (-1, -1, 2),
                         'default_frac_slip_len': float(np.sqrt(2)/2)},
            '110x-111': {'plane': (1, 1, 0), 'x': (-1, 1, 1), 'xy': (0, 0, -1),
                         'default_frac_slip_len': float(np.sqrt(3)/2)},
            '110x1-1-1': {'plane': (1, 1, 0), 'x': (1, -1, -1), 'xy': (0, 0, 1),
                          'default_frac_slip_len': float(np.sqrt(3)/2)},
            '112x11-1': {'plane': (1, 1, 2), 'x': (1, 1, -1), 'xy': (-1, 1, 0),
                         'default_frac_slip_len': float(np.sqrt(3)/2)},
            '112x-1-11': {'plane': (1, 1, 2), 'x': (-1, -1, 1), 'xy': (1, -1, 0),
                          'default_frac_slip_len': float(np.sqrt(3)/2)},
            '123x11-1': {'plane': (1, 2, 3), 'x': (1, 1, -1), 'xy': (-2, 1, 0),
                         'default_frac_slip_len': float(np.sqrt(3)/2)},
            '123x-1-11': {'plane': (1, 2, 3), 'x': (-1, -1, 1), 'xy': (2, -1, 0),
                          'default_frac_slip_len': float(np.sqrt(3)/2)}
        },

        'hcp': {
            # Basal, cleavage and shear (non SF)
            '0001x2-1-10': {'plane': plane_miller_bravais_to_miller([0, 0, 0, 1]),
                            'x': direction_miller_bravais_to_miller([2, -1, -1, 0]),
                            'xy': direction_miller_bravais_to_miller([0, 1, -1, 0]),
                            'default_frac_slip_len': 1},

            # Basal, shear SF1 along x
            '0001x1-100': {'plane': plane_miller_bravais_to_miller([0, 0, 0, 1]),
                           'x': direction_miller_bravais_to_miller([1, -1, 0, 0]),
                           'xy': direction_miller_bravais_to_miller([0, 1, -1, 0]),
                           'default_frac_slip_len': float(np.sqrt(3))},

            # Basal, shear opposite to SF1 along x, climbing the hill
            '0001x10-10': {'plane': plane_miller_bravais_to_miller([0, 0, 0, 1]),
                           'x': direction_miller_bravais_to_miller([1, 0, -1, 0]),
                           'xy': direction_miller_bravais_to_miller([0, 1, -1, 0]),
                           'default_frac_slip_len': float(np.sqrt(3))},

            # Prism I, cleavage and SF1 along x
            '01-10x-2110': {'plane': plane_miller_bravais_to_miller([0, 1, -1, 0]),
                            'x': direction_miller_bravais_to_miller([-2, 1, 1, 0]),
                            'xy': direction_miller_bravais_to_miller([0, 0, 0, -1]),
                            'default_frac_slip_len': 1},

            # Prism I, cleavage and SF2 along xy
            '01-10x0001': {'plane': plane_miller_bravais_to_miller([0, 1, -1, 0]),
                            'x': direction_miller_bravais_to_miller([0, 0, 0, 1]),
                            'xy': direction_miller_bravais_to_miller([-2, 1, 1, 0]),
                            'default_frac_slip_len': (0, 0, 1)},

            # Prism I, shear SF2 along x
            '01-10x-2113': {'plane': plane_miller_bravais_to_miller([0, 1, -1, 0]),
                            'x': direction_miller_bravais_to_miller([-2, 1, 1, 3]),
                            'xy': direction_miller_bravais_to_miller([0, 0, 0, -1]),
                            'default_frac_slip_len': (1, 0, 1)},

            # Prism II, cleavage
            '-12-10x-1010': {'plane': plane_miller_bravais_to_miller([-1, 2, -1, 0]),
                             'x': direction_miller_bravais_to_miller([-1, 0, 1, 0]),
                             'xy': direction_miller_bravais_to_miller([0, 0, 0, -1]),
                             'default_frac_slip_len': float(np.sqrt(3))},

            # Prism II, cleavage along c
            '-12-10x0001': {'plane': plane_miller_bravais_to_miller([-1, 2, -1, 0]),
                            'x': direction_miller_bravais_to_miller([0, 0, 0, 1]),
                            'xy': direction_miller_bravais_to_miller([-1, 0, 1, 0]),
                            'default_frac_slip_len': (0, 0, 1)},

            # Pyramidal I, cleavage and <a> slip along x
            '01-11x-2110': {'plane': plane_miller_bravais_to_miller([0, 1, -1, 1]),
                            'x': direction_miller_bravais_to_miller([-2, 1, 1, 0]),
                            'xy': direction_miller_bravais_to_miller([-1, 2, -1, -3]),
                            'default_frac_slip_len': 1},

            # Pyramidal I, <c+a> slip along x
            '01-11x-12-1-3': {'plane': plane_miller_bravais_to_miller([0, 1, -1, 1]),
                            'x': direction_miller_bravais_to_miller([-1, 2, -1, -3]),
                            'xy': direction_miller_bravais_to_miller([2, -1, -1, 0]),
                            'default_frac_slip_len': (1, 0, 1)},

            # Pyramidal I, shear SF2 along x
            '01-11x0-112': {'plane': plane_miller_bravais_to_miller([0, 1, -1, 1]),
                            'x': direction_miller_bravais_to_miller([0, -1, 1, 2]),
                            'xy': direction_miller_bravais_to_miller([-1, 2, -1, -3]),
                            'default_frac_slip_len': (float(np.sqrt(3)), 0, 2)},

            # Pyramidal II, Cleavage
            '-12-12x10-10': {'plane': plane_miller_bravais_to_miller([-1, 2, -1, 2]),
                             'x': direction_miller_bravais_to_miller([1, 0, -1, 0]),
                             'xy': direction_miller_bravais_to_miller([1, -2, 1, 3]),
                             'default_frac_slip_len': float(np.sqrt(3))},

            # Pyramidal II, shear SF2 along x
            '-12-12x1-213': {'plane': plane_miller_bravais_to_miller([-1, 2, -1, 2]),
                             'x': direction_miller_bravais_to_miller([1, -2, 1, 3]),
                             'xy': direction_miller_bravais_to_miller([-1, 0, 1, 0]),
                             'default_frac_slip_len': (1, 0, 1)},

            # Pyramidal II, shear SF2 along x by climbing the hill
            '-12-12x-12-1-3': {'plane': plane_miller_bravais_to_miller([-1, 2, -1, 2]),
                             'x': direction_miller_bravais_to_miller([-1, 2, -1, -3]),
                             'xy': direction_miller_bravais_to_miller([1, 0, -1, 0]),
                             'default_frac_slip_len': (1, 0, 1)}
        }
    }

    @classmethod
    def atomic_system_dict(cls):
        return cls.__dict_atomic_system

    @classmethod
    def hint_string(cls):
        print_str = 'structure  \tplane_index    \tslip_direction\n'
        for struct in cls.__dict_atomic_system.keys():
            for orient in cls.__dict_atomic_system[struct].keys():
                plane, slip = orient.split('x')
                print_str += f'{struct}       \t{plane}         \t{slip}\n'
        return print_str


if __name__ == '__main__':
    print(SlabSlipSystem.hint_string())
