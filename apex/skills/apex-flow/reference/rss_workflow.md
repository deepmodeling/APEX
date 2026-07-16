# APEX RSS (Random Solid Solution) Workflow

## Overview

APEX's RSS workflow generates random solid solution structures for multi-component alloys (e.g., high-entropy alloys). These structures serve as inputs for subsequent property calculations.

## Supported Prototypes

| Prototype | Atoms/Cell | Description |
|-----------|-----------|-------------|
| `fcc` | 4 | Face-centered cubic |
| `bcc` | 2 | Body-centered cubic |
| `hcp` | 2 | Hexagonal close-packed |
| `sc` | 1 | Simple cubic |
| `diamond` | 8 | Diamond cubic |
| `B2` | 2 | CsCl-type ordered |
| `L12` | 4 | Cu₃Au-type ordered (3:1 sublattice) |
| `L10` | 4 | CuAu-type ordered (1:1 sublattice) |

## Sublattice-Aware Generation

For ordered phases (B2, L12, L10), APEX respects sublattice occupancy:

### B2 Structure
- **Sublattice A** (corner): Element set 1
- **Sublattice B** (body center): Element set 2
- Example: (TiZr)(NiCu) — Ti/Zr on A-sites, Ni/Cu on B-sites

### L12 Structure
- **Sublattice A** (face centers, 3 atoms): Element set 1
- **Sublattice B** (corner, 1 atom): Element set 2
- Example: (CoCrFe)₃(Al) — Co/Cr/Fe on A-sites, Al on B-sites

### L10 Structure
- **Sublattice A** (2 atoms): Element set 1
- **Sublattice B** (2 atoms): Element set 2
- Example: (FeNi)(PtPd) — Fe/Ni on A-sites, Pt/Pd on B-sites

## CLI Usage

```bash
apex rss [options]
```

## Configuration via param.json

RSS can also be configured in the main param.json:

```json
{
    "structures": {
        "prototype": "fcc",
        "composition": {"Co": 0.2, "Cr": 0.2, "Fe": 0.2, "Mn": 0.2, "Ni": 0.2},
        "supercell": [3, 3, 3],
        "n_configs": 10
    }
}
```

### RSS Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `prototype` | str | **required** | Crystal prototype (fcc/bcc/hcp/B2/L12/L10/...) |
| `composition` | dict | **required** | Element: fraction mapping (must sum to 1.0) |
| `supercell` | list[int] | `[3,3,3]` | Supercell dimensions |
| `n_configs` | int | `1` | Number of random configurations to generate |
| `lattice_constant` | float | auto | Override auto-estimated lattice constant (Å) |
| `sublattice_map` | dict | auto | Explicit sublattice assignment for B2/L12/L10 |

### Automatic Lattice Constant

APEX estimates the lattice constant from composition-weighted atomic radii:
- Uses Goldschmidt/metallic radii
- Applies packing correction for prototype
- Can be overridden with explicit `lattice_constant`

### Automatic Supercell

When exact stoichiometry cannot be achieved with the given supercell, APEX can automatically find the minimum supercell that satisfies composition tolerance:

```json
{
    "structures": {
        "prototype": "fcc",
        "composition": {"Al": 0.33, "Cr": 0.33, "Ti": 0.34},
        "composition_tolerance": 0.02,
        "max_atoms": 200
    }
}
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `composition_tolerance` | float | `0.01` | Max deviation from target composition |
| `max_atoms` | int | `500` | Maximum atoms in auto-determined supercell |
| `supercell_shape` | str | `"near_cubic"` | Shape control: `"near_cubic"` or `"xy_equal_z_free"` |

## Warren-Cowley SRO

For controlling short-range order in generated structures:

```json
{
    "structures": {
        "prototype": "fcc",
        "composition": {"Cu": 0.5, "Zn": 0.5},
        "supercell": [4, 4, 4],
        "n_configs": 5,
        "sro_target": {"Cu-Zn": -0.1}
    }
}
```

This generates structures biased toward Cu-Zn nearest-neighbor pairing (negative SRO = ordering tendency).

## Workflow Integration

### Step 1: Generate RSS structures
```bash
# Done locally (no Bohrium needed)
python scripts/generate_rss.py \
    --composition "Co0.2Cr0.2Fe0.2Mn0.2Ni0.2" \
    --prototype fcc \
    --supercell 3 3 3 \
    --n-configs 10 \
    --output-dir confs/rss
```

### Step 2: Use in property calculation
```json
{
    "structures": ["confs/rss"],
    "interaction": { ... },
    "properties": [ ... ]
}
```

APEX will automatically discover all POSCAR files under `confs/rss/` and calculate properties for each.

## Output Structure

Generated structures are saved as POSCAR files:
```
confs/rss/
├── POSCAR_0001
├── POSCAR_0002
├── ...
└── POSCAR_0010
```

Each POSCAR includes:
- Randomly distributed elements on lattice sites
- Correct stoichiometry (within tolerance)
- Proper atom ordering consistent with type_map

## Best Practices

1. **Supercell size**: Use at least 3×3×3 for 5-component HEAs (108 atoms for FCC). This gives ~22 atoms per element for good statistics.

2. **Number of configurations**: Generate 5-10 for property averaging. More for systems with strong compositional fluctuation effects.

3. **Lattice constant**: Let APEX auto-estimate first. If results are unreasonable, provide an experimental or DFT lattice constant.

4. **B2/L12/L10**: Always specify which elements go on which sublattice. Random mixing across sublattices defeats the purpose of ordered-phase modeling.

5. **Large systems**: For >200 atoms, prefer LAMMPS+MLIP over DFT. DeePMD/MACE scale linearly with system size on GPU.
