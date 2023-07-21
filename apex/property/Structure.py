from pymatgen.io.vasp import Poscar
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer


class StructureType(object):
    """Analyze structure type
    Arg:
        poscar_file: target POSCAR file path
    """
    def __int__(self, poscar_file):
        poscar = Poscar.from_file(poscar_file)
        structure = poscar.structure
        analyzer = SpacegroupAnalyzer(structure)
        self.space_group_symbol = analyzer.get_space_group_symbol()
        self.space_group_number = analyzer.get_space_group_number()
        self.point_group_symbol = analyzer.get_point_group_symbol()
        self.crystal_system = analyzer.get_crystal_system()
        self.lattice_type = analyzer.get_lattice_type()

    def get_structure_type(self):
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
