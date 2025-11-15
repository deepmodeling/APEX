import pymatgen.core
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer
from dflow.python import upload_packages

upload_packages.append(__file__)

class StructureInfo(object):
    """Analyze structure type
    Arg:
        structure: pymatgen.core.Structure object
    """
    def __init__(self, structure: pymatgen.core.Structure, **kwargs) -> None:
        analyzer = SpacegroupAnalyzer(structure, **kwargs)
        self.__space_group_symbol = analyzer.get_space_group_symbol()
        self.__space_group_number = analyzer.get_space_group_number()
        self.__point_group_symbol = analyzer.get_point_group_symbol()
        self.__crystal_system = analyzer.get_crystal_system()
        self.__lattice_type = analyzer.get_lattice_type()
        self.__num_atoms = structure.num_sites
        self.__crystal_structure = self.__indentify_crystal()
        # standard structure
        self.orig_structure = structure
        self.primitive_structure = analyzer.find_primitive()
        self.conventional_structure = analyzer.get_conventional_standard_structure()

    def __indentify_crystal(self) -> str:
        if self.__lattice_type == 'cubic':
            if self.__num_atoms == 1 and self.__space_group_symbol == 'Pm-3m':
                structure_type = 'sc'
            elif self.__num_atoms == 2 and self.__space_group_symbol == 'Im-3m':
                structure_type = 'bcc'
            elif self.__num_atoms == 4 and self.__space_group_symbol == 'Fm-3m':
                structure_type = 'fcc'
            elif self.__num_atoms == 8 and self.__space_group_symbol == 'Fd-3m':
                structure_type = 'diamond'
            else:
                structure_type = 'other'

        elif self.__lattice_type == 'hexagonal':
            if self.__num_atoms == 2 and self.__space_group_symbol == 'P6_3/mmc':
                structure_type = 'hcp'
            elif self.__space_group_symbol == 'P6/mmm':
                structure_type = 'c32'
            else:
                structure_type = 'other'
        else:
            structure_type = 'other'

        return structure_type

    @property
    def space_group_symbol(self):
        return self.__space_group_symbol

    @property
    def space_group_number(self):
        return self.__space_group_number

    @property
    def point_group_symbol(self):
        return self.__point_group_symbol

    @property
    def crystal_system(self):
        return self.__crystal_system

    @property
    def lattice_type(self):
        return self.__lattice_type

    @property
    def num_atoms(self):
        return self.__num_atoms

    @property
    def lattice_structure(self):
        return self.__crystal_structure
