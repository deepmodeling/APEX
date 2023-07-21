import pymatgen.core
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer


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

    def get_structure_type(self) -> str:
        if self.lattice_type == 'cubic':
            if 'F' in self.space_group_symbol:
                structure_type = 'fcc'
            elif 'I' in self.space_group_symbol:
                structure_type = 'bcc'
        elif self.lattice_type == 'hexagonal':
            if 'P6' in self.space_group_symbol:
                structure_type = 'hcp'
        else:
            structure_type = 'other'

        return structure_type
