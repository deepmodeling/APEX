# RSS Examples (HEA and HEO)

This folder provides examples for generating random solid
solution (RSS) structures with:

- HEA example: `examples/rss/HEA/`
- HEO example: `examples/rss/HEO/`

Both examples are driven by `apex rss <rss.json>`.

## Quick Start

Run HEA:

```bash
apex rss examples/rss/HEA/rss.json
```

Run HEO:

```bash
apex rss examples/rss/HEO/rss.json
```

Generated structures are written to the output location configured in each
`rss.json` as separate `conf_###/POSCAR` folders under the configured output
root (for example `RSS_HEA/conf_001/POSCAR` or `RSS_HEO/conf_001/POSCAR`).

If `metadata` is enabled, an additional `rss_metadata.json` is generated in
the same output directory.

## Example Folder Layout

```text
examples/rss/
├── README.md
├── HEA/
│   ├── rss.json
│   ├── parent/
│   └── RSS_HEA/
│       ├── conf_001/
│       ├── conf_002/
│       ├── conf_003/
│       └── rss_metadata.json
└── HEO/
    ├── rss.json
    ├── parent/
    └── RSS_HEO/
        ├── conf_001/
        ├── conf_002/
        ├── conf_003/
        └── rss_metadata.json
```

## Full `rss.json` Key Reference

The following keys are supported by the current implementation in
`apex/rss.py` and `apex/core/lib/rss.py`.

### 1) Structure Input

1. `parent_structure`
- Type: `string`
- Meaning: Relative path (from the `rss.json` directory) to a parent structure,
    typically POSCAR.
- Notes: Provide either `parent_structure` or `parent_lattice`.

2. `parent_lattice`
- Type: `object`
- Meaning: Build the parent structure directly in code (without POSCAR).
- Supported sub-keys:
    - `type`: one of `fcc` / `bcc` / `sc` / `hcp` / `diamond` / `B2` / `L12` / `L10`
    - `element`: initial element symbol, default `Ni`
    - `a`: lattice parameter, default `3.6`; use `"auto"` for supported
      parent lattices
    - `supercell`: internal pre-expansion, e.g. `[5, 5, 5]`; use `"auto"` for
      supported parent lattices

For `B2`, `L12`, and `L10`, `rss.py` can also infer the ordered prototype species
labels from the composition blocks and fill in `a` / `supercell` when they are
set to `"auto"`. This is useful when you want to drive RSS from sublattice
fractions without hand-tuning the parent lattice first.

3. `composition_tolerance`
- Type: `float`, default `0.005`
- Meaning: Accuracy target for automatic supercell generation. Smaller values
    request a larger cell that better resolves small composition fractions.

4. `supercell_shape`
- Type: `string`, default `"near_cubic"`
- Values: `"near_cubic"` / `"xy_equal_z_free"`
- Meaning: Shape preference for automatic supercell generation. Use
    `"xy_equal_z_free"` when the in-plane shape should be preserved, such as
    gamma surface or slab workflows.

5. `maxmium_nums_atoms`
- Type: `int`, optional
- Meaning: Maximum number of atoms allowed in the automatically generated
    parent-lattice supercell. If the composition tolerance would require a
    larger cell, RSS chooses the best composition approximation within this
    atom budget.
- Notes: `maximum_num_atoms` and `maximum_nums_atoms` are accepted aliases.

6. `supercell`
- Type: `array[int, int, int]`
- Meaning: Expand the loaded/built parent structure after loading/building.
- Notes: If both `parent_lattice.supercell` and top-level `supercell` are set,
    both expansions are applied.

7. `output_structure`
- Type: `string`
- Meaning: Output root directory (relative to `rss.json`), default `RSS`.
- Notes: Each generated configuration is written to its own `conf_###`
    directory and the structure file inside is always named `POSCAR`.

### 2) Composition and Sublattice

8. `compositions`
- Type: `object`
- Meaning: Species fractions on each sublattice; each sublattice must sum to
    `1.0`.
- Modes:
    - Single-sublattice: `{"all": {...}}`
    - Multi-sublattice: e.g. `{"cation": {...}, "anion": {...}}`

RSS rounds sublattice fractions to the nearest integer site counts while keeping
the total number of sites conserved. If the requested fractions are only
approximately realizable for the chosen supercell, the runner emits a warning
and proceeds with the nearest valid integerized assignment.

For an A/B/O-type sublattice system, define three composition blocks such as
`A`, `B`, and `O`. Put the tunable species on `A` and `B`, and keep `O` fixed
to `1.0` when oxygen should remain fully occupied.

Example:

```json
{
    "compositions": {
        "A": {
            "Mg": 0.5,
            "Co": 0.5
        },
        "B": {
            "Cr": 0.5,
            "Fe": 0.5
        },
        "O": {
            "O": 1.0
        }
    }
}
```

9. `sublattices` (optional)
- Type: `array[object]`
- Meaning: Explicit site-index mapping for each sublattice.
- Item format:
    - `name`: sublattice name (must match keys in `compositions`)
    - `site_indices`: integer list
- Notes: If omitted and top-level `supercell` is provided, the runner tries
    automatic assignment.

10. `allow_vacancies`
- Type: `bool`, default `false`
- Meaning: Allow vacancy-like species aliases (`vac`, `vacancy`, `x`, `none`),
    internally mapped to `X`.

### 3) SRO Targets

11. `sro_targets` (optional)
- Type: `object`
- Meaning: Warren-Cowley SRO target values.
- Format:
    - top-level keys: `shell0`, `shell1`, ... (or `0`, `1`, ...)
    - each shell contains pair targets, e.g. `"Co-Ni": 0.0`
- Notes: If omitted, all pair targets default to `0.0` for all shells, which means generate a random solid solution as closely as the sampler allows.

12. `shell_cutoffs`
- Type: `array[float]`
- Meaning: Neighbor-shell cutoffs (ascending, positive).
- Notes:
    - If omitted, a default first-shell cutoff is inferred from structure.

13. `shell_weights` (optional)
- Type: `array[float]`
- Meaning: Weight of each shell in the objective function.
- Notes: Length must match `shell_cutoffs`; defaults to all `1.0`.

### 4) Sampling and Convergence Control

14. `max_steps`
- Type: `int`, default `20000`
- Meaning: Maximum Monte Carlo swap attempts.

15. `temperature`
- Type: `float`, default `0.05`
- Meaning: Metropolis temperature parameter.

16. `tol`
- Type: `float`, default `1e-3`
- Meaning: Numerical tolerance and near-target criterion.

17. `patience`
- Type: `int | null`, default `null`
- Meaning: Early-stop patience.
- Notes: `null` disables early stopping.

18. `seed` (optional)
- Type: `int`
- Meaning: Random seed for reproducibility.

19. `show_progress`
- Type: `bool`, default `true`
- Meaning: Show tqdm progress bar and live gap metrics.

### 5) Multi-Configuration Output

20. `num_configs`
- Type: `int`, default `1`
- Meaning: Number of structures to output, useful when generating multiple
    configurations for averaging.

21. `interval`
- Type: `int`, default `100`
- Meaning: Checkpoint interval (in MC steps) used to update the hash-table cache of the lowest-RMS unique configurations for multi-configuration output.

22. `metadata`
- Type: `bool`, default `true`
- Meaning: Write `rss_metadata.json`.

## Minimal HEA-Style Example (No POSCAR)

```json
{
    "parent_lattice": {
        "type": "B2",
        "a": "auto",
        "supercell": "auto"
    },
    "composition_tolerance": 0.001,
    "supercell_shape": "near_cubic",
    "maxmium_nums_atoms": 128,
    "compositions": {
        "corner": {
            "Co": 0.2,
            "Cr": 0.2,
            "Fe": 0.2,
            "Mn": 0.2,
            "Ni": 0.2
        },
        "body": {
            "Co": 0.2,
            "Cr": 0.2,
            "Fe": 0.2,
            "Mn": 0.2,
            "Ni": 0.2
        }
    },
    "shell_cutoffs": [2.8],
    "output_structure": "./RSS_HEA"
}
```
