# APEX RSS (Random Solid Solution) Workflow

## Overview

APEX's RSS workflow generates occupationally disordered structures for random
solid solutions, solid solutions, high-entropy alloys (HEA), high-entropy
oxides/ceramics (HEO), and other high-entropy materials. RSS generates
structures locally; it is not one of the 14 APEX properties and does not require
choosing a calculator backend unless the user also asks to calculate properties.

When the user's request contains any of the material classes above, explicitly
offer or use `apex rss <rss.json>`. Do not substitute the legacy
`scripts/generate_rss.py` interface: the current APEX CLI is JSON-driven.

## Supported Prototypes

| Prototype | Atoms/Cell | Description |
|-----------|-----------|-------------|
| `fcc` | 1 | Primitive face-centered cubic |
| `bcc` | 2 | Body-centered cubic |
| `hcp` | 2 | Hexagonal close-packed |
| `sc` | 1 | Simple cubic |
| `tetragonal` | 1 | Simple tetragonal |
| `diamond` | 2 | Primitive diamond cubic |
| `B2` | 2 | CsCl-type ordered |
| `L12` | 4 | Cu₃Au-type ordered (3:1 sublattice) |
| `L10` | 2 | CuAu-type ordered (1:1 sublattice) |

## Sublattice-Aware Generation

For ordered phases (B2, L12, L10), APEX respects sublattice occupancy:

### B2 Structure
- **`corner`**: corner-site composition
- **`body`**: body-center composition
- Example: (TiZr)(NiCu) — Ti/Zr on A-sites, Ni/Cu on B-sites

### L12 Structure
- **`corner`**: corner-site composition
- **`face`**: face-center composition (3 sites per cell)
- Example: (CoCrFe)₃(Al) — Co/Cr/Fe on A-sites, Al on B-sites

### L10 Structure
- **`layer_A`**: first alternating layer
- **`layer_B`**: second alternating layer
- Example: (FeNi)(PtPd) — Fe/Ni on A-sites, Pt/Pd on B-sites

## Required User QA

Do not invent RSS chemistry or sublattices. Ask the following in small batches
(normally one or two questions per turn), reusing information already supplied:

1. **Parent structure**: use an existing POSCAR/CIF (`parent_structure`) or
   build a prototype (`parent_lattice.type`)? For a built prototype, ask for
   `fcc`, `bcc`, `hcp`, `sc`, `tetragonal`, `diamond`, `B2`, `L12`, or `L10`,
   and ask whether lattice constants should be explicit or `"auto"`.
2. **Sublattice compositions**: ask which species and fractions occupy each
   sublattice. Every composition block must sum to `1.0`. A single disordered
   lattice uses `{"all": {...}}`; HEOs normally need separate cation/anion (or
   A/B/O) blocks. Ask explicitly if vacancies are intended.
3. **Cell sizing**: ask whether to use an explicit supercell or automatic
   sizing. For automatic sizing, present defaults
   `composition_tolerance=0.005`, `supercell_shape="near_cubic"`, and an atom
   budget (`maximum_num_atoms`) for approval. Warn that top-level `supercell`
   and `parent_lattice.supercell` are both applied if both are present.
4. **Order and sampling**: ask whether the target is random SRO
   (`sro_targets` omitted, target zero) or specified Warren-Cowley values. Then
   confirm `num_configs` (default 1), random `seed`, and output directory.

If the user supplies an ordered B2/L12/L10 parent, confirm which species belong
to each crystallographic sublattice. For an arbitrary parent file, use explicit
`sublattices.site_indices` when automatic assignment cannot be proven.

## Current CLI and `rss.json`

Run:

```bash
apex rss path/to/rss.json
```

The JSON must contain `compositions` and exactly one parent source:

- `parent_structure`: structure path relative to `rss.json`; or
- `parent_lattice`: object with `type`, optional `element`/`species`, `a`,
  optional `c`, and optional `supercell`.

Core optional keys are:

| Key | Default | Purpose |
|-----|---------|---------|
| `composition_tolerance` | `0.005` | Accuracy target for automatic cell sizing |
| `supercell_shape` | `"near_cubic"` | Or `"xy_equal_z_free"` |
| `maximum_num_atoms` | unset | Atom budget; old misspelled aliases are accepted but do not generate them |
| `supercell` | unset | Additional expansion after loading/building the parent |
| `sublattices` | auto/none | Explicit `{name, site_indices}` mappings |
| `sro_targets` | zero/random | Per-shell pair targets such as `"Co-Ni": -0.1` |
| `shell_cutoffs` | inferred first shell | Positive ascending neighbor cutoffs |
| `shell_weights` | all `1.0` | SRO objective weights |
| `max_steps` | `20000` | Monte Carlo attempts |
| `temperature` | `0.05` | Metropolis temperature |
| `tol` | `1e-3` | Numerical/target tolerance |
| `allow_vacancies` | `false` | Permit vacancy aliases, normalized to `X` |
| `num_configs` | `1` | Number of unique structures |
| `interval` | `100` | Candidate-cache update interval |
| `seed` | unset | Reproducible random seed |
| `metadata` | `true` | Write `rss_metadata.json` |
| `output_structure` | `"RSS"` | Output root relative to `rss.json` |

Minimal single-sublattice example:

```json
{
    "parent_lattice": {
        "type": "fcc",
        "a": "auto",
        "supercell": "auto"
    },
    "compositions": {
        "all": {"Co": 0.2, "Cr": 0.2, "Fe": 0.2, "Mn": 0.2, "Ni": 0.2}
    },
    "composition_tolerance": 0.005,
    "maximum_num_atoms": 200,
    "num_configs": 5,
    "seed": 21,
    "output_structure": "RSS_HEA"
}
```

## Workflow Integration

### Step 1: Generate RSS structures
```bash
# Done locally; no Bohrium job is needed.
apex rss rss.json
```

### Step 2: Use in property calculation
```json
{
    "structures": ["RSS_HEA/conf_*"],
    "interaction": { ... },
    "properties": [ ... ]
}
```

Each matched directory contains a `POSCAR`.

## Output Structure

Generated structures are saved as APEX-ready directories:
```
RSS_HEA/
├── conf_001/POSCAR
├── conf_002/POSCAR
└── rss_metadata.json
```

After generation, verify every POSCAR's atom count, realized composition,
lattice, and duplicate status. Read `rss_metadata.json` and report convergence
or composition warnings instead of describing every generated structure as
valid automatically.

## Visualize Before Handoff

Always give the user a visual check of at least the first generated structure.
Use the environment's supported structure/image artifact viewer first. In this
repository, the reliable headless fallback is the same ASE plotting stack used
by `apex/preview.py`: read the POSCAR with ASE, render it with
`ase.visualize.plot.plot_atoms`, save a PNG, and present that image artifact.
`apex preview` itself expects a property parameter file and is not an RSS
preview command.

Example static fallback:

```python
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from ase.io import read
from ase.visualize.plot import plot_atoms

atoms = read("RSS_HEA/conf_001/POSCAR", format="vasp")
fig, ax = plt.subplots(figsize=(7, 7))
plot_atoms(atoms, ax, rotation="10x,20y,0z", show_unit_cell=2)
ax.set_axis_off()
fig.savefig("RSS_HEA/rss_preview.png", dpi=180, bbox_inches="tight")
```

If no supported static/artifact visualization works and a desktop display is
available, attempt `ase.visualize.view(atoms)` (ASE view). In a headless
environment, report the display limitation and still return the POSCAR/CIF and
metadata; do not claim that an unseen GUI opened successfully.

## Best Practices

1. **Supercell size**: Choose a cell that realizes the requested fractions
   within tolerance. Do not blindly apply a second 3×3×3 expansion to a parent
   that is already a supercell.

2. **Number of configurations**: Generate 5-10 for property averaging when the
   cost is acceptable; use a fixed seed for reproducibility.

3. **Lattice constant**: Treat `"auto"` as an estimate. Prefer an experimental
   or relaxed DFT value when quantitative fidelity matters.

4. **B2/L12/L10 and HEOs**: Confirm sublattice chemistry and mapping. Random
   mixing across unintended sublattices changes the physical system.

5. **Large systems**: For subsequent calculations on large RSS cells, present
   LAMMPS+MLIP as the practical option before proposing DFT.
