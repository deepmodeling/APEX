#!/usr/bin/env python3
"""
APEX RSS Structure Generator

Generates Random Solid Solution structures for multi-component alloys.
Uses pymatgen for structure generation.

Usage:
    python generate_rss.py \
        --composition "Co0.2Cr0.2Fe0.2Mn0.2Ni0.2" \
        --prototype fcc \
        --supercell 3 3 3 \
        --n-configs 10 \
        --output-dir confs/rss
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

import numpy as np

try:
    from pymatgen.core import Structure, Lattice
    from pymatgen.io.vasp import Poscar
    from pymatgen.transformations.standard_transformations import (
        SupercellTransformation,
    )
except ImportError:
    print("ERROR: pymatgen is required. Install with: pip install pymatgen",
          file=sys.stderr)
    sys.exit(1)


# Metallic radii (Å) for lattice constant estimation
METALLIC_RADII = {
    "Li": 1.52, "Be": 1.12, "Na": 1.86, "Mg": 1.60, "Al": 1.43,
    "K": 2.27, "Ca": 1.97, "Sc": 1.62, "Ti": 1.47, "V": 1.34,
    "Cr": 1.28, "Mn": 1.27, "Fe": 1.26, "Co": 1.25, "Ni": 1.24,
    "Cu": 1.28, "Zn": 1.34, "Ga": 1.35, "Ge": 1.22, "Sr": 2.15,
    "Y": 1.80, "Zr": 1.60, "Nb": 1.46, "Mo": 1.39, "Tc": 1.36,
    "Ru": 1.34, "Rh": 1.34, "Pd": 1.37, "Ag": 1.44, "Cd": 1.51,
    "In": 1.67, "Sn": 1.58, "Hf": 1.59, "Ta": 1.46, "W": 1.39,
    "Re": 1.37, "Os": 1.35, "Ir": 1.36, "Pt": 1.39, "Au": 1.44,
    "Pb": 1.75, "Bi": 1.56,
}

# Packing factors for different prototypes
PACKING_FACTORS = {
    "fcc": np.sqrt(2) * 2,  # a = 2√2 * r
    "bcc": 4 / np.sqrt(3),  # a = 4r/√3
    "hcp": 2.0,             # a = 2r
    "sc": 2.0,              # a = 2r
    "diamond": 8 / np.sqrt(3),  # a = 8r/√3
}

# Prototype structures (fractional coordinates)
PROTOTYPES = {
    "fcc": {
        "sites": [[0, 0, 0], [0.5, 0.5, 0], [0.5, 0, 0.5], [0, 0.5, 0.5]],
        "n_atoms": 4,
    },
    "bcc": {
        "sites": [[0, 0, 0], [0.5, 0.5, 0.5]],
        "n_atoms": 2,
    },
    "hcp": {
        "sites": [[0, 0, 0], [1/3, 2/3, 0.5]],
        "n_atoms": 2,
        "angles": [90, 90, 120],
        "c_over_a": 1.633,
    },
    "sc": {
        "sites": [[0, 0, 0]],
        "n_atoms": 1,
    },
    "diamond": {
        "sites": [[0, 0, 0], [0.5, 0.5, 0], [0.5, 0, 0.5], [0, 0.5, 0.5],
                  [0.25, 0.25, 0.25], [0.75, 0.75, 0.25],
                  [0.75, 0.25, 0.75], [0.25, 0.75, 0.75]],
        "n_atoms": 8,
    },
    "B2": {
        "sites": [[0, 0, 0], [0.5, 0.5, 0.5]],
        "n_atoms": 2,
        "sublattices": {"A": [0], "B": [1]},
    },
    "L12": {
        "sites": [[0, 0, 0], [0.5, 0.5, 0], [0.5, 0, 0.5], [0, 0.5, 0.5]],
        "n_atoms": 4,
        "sublattices": {"A": [1, 2, 3], "B": [0]},  # 3:1 ratio
    },
    "L10": {
        "sites": [[0, 0, 0], [0.5, 0.5, 0], [0.5, 0, 0.5], [0, 0.5, 0.5]],
        "n_atoms": 4,
        "sublattices": {"A": [0, 1], "B": [2, 3]},  # 1:1 ratio
    },
}


def parse_composition(comp_str: str) -> dict:
    """Parse composition string like 'Co0.2Cr0.2Fe0.2Mn0.2Ni0.2' or 'CoCrFeMnNi'."""
    # Try pattern: Element + number
    pattern = r"([A-Z][a-z]?)(\d*\.?\d*)"
    matches = re.findall(pattern, comp_str)
    
    composition = {}
    for element, fraction in matches:
        if not element:
            continue
        if fraction:
            composition[element] = float(fraction)
        else:
            composition[element] = 1.0
    
    # Normalize to fractions
    total = sum(composition.values())
    if total > 0:
        composition = {k: v / total for k, v in composition.items()}
    
    return composition


def estimate_lattice_constant(composition: dict, prototype: str) -> float:
    """Estimate lattice constant from composition-weighted radii."""
    avg_radius = 0.0
    for element, fraction in composition.items():
        if element in METALLIC_RADII:
            avg_radius += fraction * METALLIC_RADII[element]
        else:
            avg_radius += fraction * 1.4  # default fallback
    
    factor = PACKING_FACTORS.get(prototype, 2.0)
    return avg_radius * factor


def build_prototype_structure(prototype: str, lattice_constant: float,
                              elements: list) -> Structure:
    """Build a prototype unit cell with placeholder species."""
    proto_info = PROTOTYPES[prototype]
    n_atoms = proto_info["n_atoms"]
    
    # Build lattice
    if prototype == "hcp":
        c_over_a = proto_info.get("c_over_a", 1.633)
        lattice = Lattice.hexagonal(lattice_constant, lattice_constant * c_over_a)
    else:
        lattice = Lattice.cubic(lattice_constant)
    
    # Use first element as placeholder
    species = [elements[0]] * n_atoms
    coords = proto_info["sites"]
    
    return Structure(lattice, species, coords)


def generate_random_config(structure: Structure, composition: dict,
                           prototype: str = None, sublattice_map: dict = None,
                           seed: int = None) -> Structure:
    """Generate a random configuration by assigning elements to sites."""
    if seed is not None:
        np.random.seed(seed)
    
    n_sites = len(structure)
    elements = list(composition.keys())
    fractions = np.array(list(composition.values()))
    
    proto_info = PROTOTYPES.get(prototype, {})
    sublattices = proto_info.get("sublattices")
    
    if sublattices and sublattice_map:
        # Sublattice-aware assignment
        new_species = list(structure.species)
        
        for sub_name, site_indices_in_unit in sublattices.items():
            sub_elements = sublattice_map.get(sub_name, elements)
            # Find all sites belonging to this sublattice in the supercell
            # (site_indices_in_unit are for the unit cell; supercell replicates)
            n_unit = proto_info["n_atoms"]
            n_replicas = n_sites // n_unit
            
            sub_site_indices = []
            for replica in range(n_replicas):
                for idx in site_indices_in_unit:
                    sub_site_indices.append(replica * n_unit + idx)
            
            # Randomly assign from sub_elements (equimolar within sublattice)
            n_sub = len(sub_site_indices)
            sub_fracs = np.ones(len(sub_elements)) / len(sub_elements)
            counts = np.round(sub_fracs * n_sub).astype(int)
            # Adjust for rounding
            diff = n_sub - counts.sum()
            counts[0] += diff
            
            assignment = []
            for elem, count in zip(sub_elements, counts):
                assignment.extend([elem] * count)
            np.random.shuffle(assignment)
            
            for i, site_idx in enumerate(sub_site_indices):
                new_species[site_idx] = assignment[i]
        
        new_structure = structure.copy()
        for i, sp in enumerate(new_species):
            new_structure[i] = sp
        return new_structure
    
    else:
        # Random assignment based on composition
        counts = np.round(fractions * n_sites).astype(int)
        # Adjust for rounding errors
        diff = n_sites - counts.sum()
        counts[np.argmax(fractions)] += diff
        
        assignment = []
        for elem, count in zip(elements, counts):
            assignment.extend([elem] * count)
        np.random.shuffle(assignment)
        
        new_structure = structure.copy()
        for i, elem in enumerate(assignment):
            new_structure[i] = elem
        return new_structure


def main():
    parser = argparse.ArgumentParser(
        description="Generate Random Solid Solution structures"
    )
    parser.add_argument("--composition", "-c", required=True,
                        help="Composition string (e.g., 'Co0.2Cr0.2Fe0.2Mn0.2Ni0.2')")
    parser.add_argument("--prototype", "-p", required=True,
                        choices=list(PROTOTYPES.keys()),
                        help="Crystal prototype")
    parser.add_argument("--supercell", "-s", nargs=3, type=int, default=[3, 3, 3],
                        help="Supercell dimensions (default: 3 3 3)")
    parser.add_argument("--n-configs", "-n", type=int, default=1,
                        help="Number of configurations to generate")
    parser.add_argument("--lattice-constant", type=float,
                        help="Override lattice constant (Å)")
    parser.add_argument("--sublattice-A", nargs="+",
                        help="Elements for sublattice A (B2/L12/L10)")
    parser.add_argument("--sublattice-B", nargs="+",
                        help="Elements for sublattice B (B2/L12/L10)")
    parser.add_argument("--output-dir", "-o", default="./confs/rss",
                        help="Output directory")
    parser.add_argument("--seed", type=int,
                        help="Random seed for reproducibility")
    parser.add_argument("--format", default="poscar",
                        choices=["poscar", "cif"],
                        help="Output format")

    args = parser.parse_args()

    # Parse composition
    composition = parse_composition(args.composition)
    if not composition:
        print("ERROR: Could not parse composition string", file=sys.stderr)
        sys.exit(1)

    print(f"Composition: {composition}")
    print(f"Prototype: {args.prototype}")
    print(f"Supercell: {args.supercell}")

    # Estimate or use provided lattice constant
    elements = list(composition.keys())
    if args.lattice_constant:
        a = args.lattice_constant
    else:
        a = estimate_lattice_constant(composition, args.prototype)
    print(f"Lattice constant: {a:.4f} Å")

    # Build prototype and make supercell
    unit_cell = build_prototype_structure(args.prototype, a, elements)
    
    # Apply supercell transformation
    supercell_matrix = np.diag(args.supercell)
    transformation = SupercellTransformation(supercell_matrix.tolist())
    supercell = transformation.apply_transformation(unit_cell)
    
    print(f"Supercell: {len(supercell)} atoms")

    # Sublattice mapping
    sublattice_map = None
    if args.sublattice_A or args.sublattice_B:
        sublattice_map = {}
        if args.sublattice_A:
            sublattice_map["A"] = args.sublattice_A
        if args.sublattice_B:
            sublattice_map["B"] = args.sublattice_B

    # Create output directory
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Generate configurations
    base_seed = args.seed if args.seed else 42
    generated = []
    
    for i in range(args.n_configs):
        seed = base_seed + i
        config = generate_random_config(
            supercell, composition, args.prototype, sublattice_map, seed
        )
        
        # Write output
        if args.format == "poscar":
            filename = f"POSCAR_{i+1:04d}"
            filepath = output_dir / filename
            poscar = Poscar(config)
            poscar.write_file(str(filepath))
        else:
            filename = f"config_{i+1:04d}.cif"
            filepath = output_dir / filename
            config.to(str(filepath))
        
        generated.append(str(filepath))
        
        # Print composition check
        actual_comp = config.composition.fractional_composition
        print(f"  Config {i+1}: {filename} "
              f"({config.composition.reduced_formula})")

    print(f"\nGenerated {len(generated)} configurations in {output_dir}")
    print(f"Total atoms per config: {len(supercell)}")


if __name__ == "__main__":
    main()
