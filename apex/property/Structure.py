import pymatgen.core
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer
from dflow.python import upload_packages

upload_packages.append(__file__)

class StructureType(object):
    """Analyze structure type
    Arg:
        structure: pymatgen.core.Structure object
    """
    def __init__(self, structure: pymatgen.core.Structure):
        analyzer = SpacegroupAnalyzer(structure)
        self.space_group_symbol = analyzer.get_space_group_symbol()
        self.space_group_number = analyzer.get_space_group_number()
        self.point_group_symbol = analyzer.get_point_group_symbol()
        self.crystal_system = analyzer.get_crystal_system()
        self.lattice_type = analyzer.get_lattice_type()
        self.num_atoms = structure.num_sites

    def get_structure_type(self) -> str:
        if self.lattice_type == 'cubic':
            if self.num_atoms == 1 and self.space_group_symbol == 'Pm-3m':
                structure_type = 'sc'
            elif self.num_atoms == 2 and self.space_group_symbol == 'Im-3m':
                structure_type = 'bcc'
            elif self.num_atoms == 4 and self.space_group_symbol == 'Fm-3m':
                structure_type = 'fcc'
            elif self.num_atoms == 8 and self.space_group_symbol == 'Fd-3m':
                structure_type = 'diamond'
            else:
                structure_type = 'other'

        elif self.lattice_type == 'hexagonal':
            if self.num_atoms == 2 and self.space_group_symbol == 'P6_3/mmc':
                structure_type = 'hcp'
            elif self.space_group_symbol == 'P6/mmm':
                structure_type = 'c32'
            else:
                structure_type = 'other'
        else:
            structure_type = 'other'

        return structure_type
