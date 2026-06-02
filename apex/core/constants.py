PERIOD_ELEMENTS_BY_SYMBOL = [
    "H", "He", "Li", "Be", "B", "C", "N", "O", "F", "Ne",
    "Na", "Mg", "Al", "Si", "P", "S", "Cl", "Ar", "K", "Ca",
    "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu", "Zn",
    "Ga", "Ge", "As", "Se", "Br", "Kr", "Rb", "Sr", "Y", "Zr",
    "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd", "In", "Sn",
    "Sb", "Te", "I", "Xe", "Cs", "Ba", "La", "Ce", "Pr", "Nd",
    "Pm", "Sm", "Eu", "Gd", "Tb", "Dy", "Ho", "Er", "Tm", "Yb",
    "Lu", "Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au", "Hg",
    "Tl", "Pb", "Bi", "Po", "At", "Rn", "Fr", "Ra", "Ac", "Th",
    "Pa", "U", "Np", "Pu", "Am", "Cm", "Bk", "Cf", "Es", "Fm",
    "Md", "No", "Lr", "Rf", "Db", "Sg", "Bh", "Hs", "Mt", "Ds",
    "Rg", "Cn", "Nh", "Fl", "Mc", "Lv", "Ts", "Og"
]

METALLIC_RADII_ANGSTROM = {
    "Al": 1.43,
    "Co": 1.25,
    "Cr": 1.28,
    "Fe": 1.26,
    "Mn": 1.27,
    "Ni": 1.24,
    "Ti": 1.47,
    "V": 1.34,
    "Cu": 1.28,
    "Zn": 1.33,
    "Mo": 1.39,
    "Nb": 1.46,
    "W": 1.39,
    "Ta": 1.46,
    "Zr": 1.60,
    "Hf": 1.58,
    "Ga": 1.35,
    "Ge": 1.25,
    "Pd": 1.37,
    "Pt": 1.39,
    "Ag": 1.44,
    "Au": 1.44,
    "Mg": 1.60,
    "Ca": 1.97,
    "Si": 1.11,
    "B": 0.85,
    "C": 0.70,
    "N": 0.65,
    "O": 0.60,
}

DEFAULT_HCP_C_OVER_A = 1.633
DEFAULT_L10_C_OVER_A = 1.0


def get_metallic_radius(symbol: str) -> float:
    """Return a metallic-radius-like heuristic in angstrom for lattice initialization."""
    if symbol not in METALLIC_RADII_ANGSTROM:
        raise KeyError(f"No metallic radius available for element: {symbol}")
    return METALLIC_RADII_ANGSTROM[symbol]
