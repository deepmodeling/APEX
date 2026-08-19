# APEX Properties — Complete Parameter Reference (Stable Defaults)

> **Version**: Updated with tested, stable defaults that prevent KeyError failures.
> Every property below includes a **complete working default JSON** that will run
> without modification for typical FCC/BCC metals.

## Common Parameters

These parameters appear in most property configurations:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `type` | str | — | Property type identifier (**REQUIRED**) |
| `cal_type` | str | varies | Calculation type: `"relaxation"` or `"static"` |
| `reproduce` | bool | `false` | Reproduce mode: re-run from prior data |
| `init_from_suffix` | str | `"00"` | Source suffix for refine/reproduce mode |
| `init_data_path` | str | — | Path to prior data (required in reproduce mode) |
| `start_confs_path` | str | — | Override structure path |
| `skip` | bool | `false` | Skip this property entirely |
| `req_calc` | bool | `true` | Whether to include in workflow |

### cal_setting common keys

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `relax_pos` | bool | varies | Allow ionic relaxation |
| `relax_shape` | bool | varies | Allow cell shape change |
| `relax_vol` | bool | varies | Allow volume change |
| `etol` | float | — | Energy tolerance (LAMMPS) |
| `ftol` | float | — | Force tolerance (LAMMPS) |
| `maxiter` | int | — | Max iterations (LAMMPS) |
| `overwrite_interaction` | dict | — | Override global interaction for this property |

---

## 1. EOS (Equation of State)

**type**: `"eos"`

| Parameter | Required? | Type | Default | Description |
|-----------|-----------|------|---------|-------------|
| `vol_start` | **REQUIRED** | float | `0.8` | Start volume ratio (or absolute Å³ if vol_abs=true) |
| `vol_end` | **REQUIRED** | float | `1.2` | End volume ratio |
| `vol_step` | **REQUIRED** | float | `0.05` | Volume step |
| `vol_abs` | optional | bool | `false` | Use absolute volumes instead of ratios |

**cal_setting defaults**: `relax_pos=true`, `relax_shape=true`, `relax_vol=false`

**Complete working default**:
```json
{
    "type": "eos",
    "vol_start": 0.8,
    "vol_end": 1.2,
    "vol_step": 0.05
}
```

**Output**: Volume-energy data points, Birch-Murnaghan fit (V₀, B₀, B₀').

**Notes**:
- 9 volume points from 0.8 to 1.2 in steps of 0.05 is a good balance of accuracy vs cost.
- For strongly anharmonic systems, narrow to 0.9–1.1 with step 0.02.

---

## 2. Cohesive Energy

**type**: `"cohesive"`

| Parameter | Required? | Type | Default | Description |
|-----------|-----------|------|---------|-------------|
| `latt_start` | **REQUIRED** | float | `0.8` | Start lattice constant ratio |
| `latt_end` | **REQUIRED** | float | `1.5` | End lattice constant ratio |
| `latt_step` | **REQUIRED** | float | `0.05` | Lattice constant step |
| `latt_abs` | optional | bool | `false` | Use absolute lattice constants |
| `cal_type` | optional | str | `"static"` | Should be "static" for cohesive |

**cal_setting defaults**: none explicitly set (static by default)

**Complete working default**:
```json
{
    "type": "cohesive",
    "latt_start": 0.8,
    "latt_end": 1.5,
    "latt_step": 0.05,
    "cal_type": "static"
}
```

**Output**: Lattice constant vs. cohesive energy (E_atom − E_isolated).

**Notes**:
- Range 0.8–1.5 is wide enough to capture the energy minimum and repulsive wall.
- `cal_type: "static"` is correct — no relaxation at each lattice point.

---

## 3. Elastic Constants

**type**: `"elastic"`

| Parameter | Required? | Type | Default | Description |
|-----------|-----------|------|---------|-------------|
| `norm_deform` | optional | float | `0.01` | Normal strain magnitude |
| `shear_deform` | optional | float | `0.01` | Shear strain magnitude |
| `conventional` | optional | bool | `false` | Use conventional cell |
| `ieee` | optional | bool | `false` | Apply IEEE rotation |
| `modulus_type` | optional | str | `"voigt"` | Averaging: `"voigt"`, `"reuss"`, or `"vrh"` |

**cal_setting defaults**: `relax_pos=true`, `relax_shape=false`, `relax_vol=false`

**Complete working default**:
```json
{
    "type": "elastic",
    "norm_deform": 0.01,
    "shear_deform": 0.01
}
```

**Output**: Elastic tensor Cij (6×6), bulk modulus B, shear modulus G, Young's modulus E, Poisson's ratio ν.

**Notes**:
- Strain magnitude 0.01 (1%) is standard for metals. Reduce to 0.005 for stiff ceramics.
- No required parameters beyond `type` — all have safe defaults.

---

## 4. Surface Energy

**type**: `"surface"`

| Parameter | Required? | Type | Default | Description |
|-----------|-----------|------|---------|-------------|
| `min_slab_size` | **REQUIRED** | float | `50` | Minimum slab thickness (Å) |
| `min_vacuum_size` | **REQUIRED** | float | `20` | Minimum vacuum layer (Å) |
| `max_miller` | optional | int | `2` | Maximum Miller index for enumeration |
| `pert_xz` | optional | float | `0.01` | Perturbation to break symmetry |

**cal_setting defaults**: `relax_pos=true`, `relax_shape=true`, `relax_vol=false`

**Complete working default**:
```json
{
    "type": "surface",
    "min_slab_size": 50,
    "min_vacuum_size": 20,
    "max_miller": 2
}
```

**Output**: Surface energy (J/m²) for each inequivalent (hkl) surface.

**Notes**:
- `max_miller=2` enumerates (100), (110), (111), (210), (211), (221) etc.
- 50 Å slab + 20 Å vacuum is conservative; ensures convergence for most metals.
- Many surfaces generated → can be costly with DFT. Use `max_miller=1` for quick tests.

---

## 5. Vacancy Formation Energy

**type**: `"vacancy"`

| Parameter | Required? | Type | Default | Description |
|-----------|-----------|------|---------|-------------|
| `supercell` | optional | list[int] | `[3,3,3]` | Supercell dimensions [nx, ny, nz] |

**cal_setting defaults**: `relax_pos=true`, `relax_shape=true`, `relax_vol=true`

**Complete working default**:
```json
{
    "type": "vacancy",
    "supercell": [3, 3, 3]
}
```

**Output**: Formation energy (eV) for each symmetrically inequivalent vacancy site.

**Notes**:
- [3,3,3] supercell = 27 atoms for simple cubic, 108 for FCC — adequate for convergence.
- [2,2,2] is minimum acceptable; [4,4,4] for publication-quality DFT.

---

## 6. Interstitial Formation Energy

**type**: `"interstitial"`

| Parameter | Required? | Type | Default | Description |
|-----------|-----------|------|---------|-------------|
| `supercell` | optional | list[int] | `[3,3,3]` | Supercell dimensions |
| `insert_ele` | optional | str/list | `null` | Element(s) to insert (null = use host species) |
| `lattice_type` | optional | str | `null` | Crystal type for special sites: `"bcc"`, `"fcc"`, `"hcp"` |
| `special_list` | optional | list[str] | `["bcc","fcc","hcp"]` | Lattice types eligible for predefined sites |
| `voronoi_param` | optional | dict | `{}` | Parameters for VoronoiInterstitialGenerator |

**cal_setting defaults**: `relax_pos=true`, `relax_shape=true`, `relax_vol=true`

**Complete working default**:
```json
{
    "type": "interstitial",
    "supercell": [3, 3, 3]
}
```

**Output**: Formation energy (eV) for each interstitial configuration.

**Notes**:
- Without `insert_ele`, inserts the host element as self-interstitial.
- `lattice_type` enables predefined octahedral/tetrahedral sites for known crystal types.
- For H/C/N interstitials, specify `"insert_ele": "H"` (or list for multiple).

---

## 7. Phonon Spectra

**type**: `"phonon"`

| Parameter | Required? | Type | Default | Description |
|-----------|-----------|------|---------|-------------|
| `supercell_size` | **REQUIRED** | list[int] | `[3,3,3]` | Phonon supercell |
| `primitive` | optional | bool | `false` | Use primitive cell |
| `approach` | optional | str | `"linear"` | `"linear"` (finite diff) or `"displacement"` |
| `seekpath_from_original` | optional | bool | `false` | Band path from original cell |
| `MESH` | optional | list[int] | `null` | Phonopy DOS mesh (e.g., [20,20,20]) |
| `BAND_POINTS` | optional | int | `51` | Points per band segment |
| `BAND_CONNECTION` | optional | bool | `true` | Connect bands |
| `PRIMITIVE_AXES` | optional | str | `"P"` | Phonopy PRIMITIVE_AXES |
| `BAND` | optional | str | `null` | Explicit band path |
| `BAND_LABELS` | optional | list[str] | `null` | High-symmetry labels |
| `phonolammps_run_command` | optional | str | `null` | Custom phonoLAMMPS command; supports `{primitive_axes}`, otherwise the matching `-pa` argument is appended |

**cal_setting defaults**: `relax_pos=true`, `relax_shape=false`, `relax_vol=false`, `cal_type="static"`

**Complete working default**:
```json
{
    "type": "phonon",
    "supercell_size": [3, 3, 3],
    "BAND_POINTS": 51
}
```

**Output**: Phonon band structure, DOS, force constants.

**Notes**:
- ⚠️ **[3,3,3] strongly recommended.** [2,2,2] may cause failures or inaccurate results with LAMMPS phonoLAMMPS.
- For LAMMPS, uses phonoLAMMPS for direct force constant calculation. For VASP/ABACUS, generates displaced supercells.
- Add `"MESH": [20, 20, 20]` to compute phonon DOS alongside the band structure.

---

## 8. Gamma Line (1D Stacking Fault)

**type**: `"gamma"`

| Parameter | Required? | Type | Default | Description |
|-----------|-----------|------|---------|-------------|
| `plane_miller` | **REQUIRED** | list[int] | `[1,1,1]` | Slip plane Miller indices |
| `slip_direction` | **REQUIRED** | list[int] | `[-1,1,0]` | Slip direction (**must lie ON the plane**) |
| `parent_lattice` | optional | str/null | `null` | Parent lattice for RSS/SQS. Gamma line automatically infers the integer parent-supercell mapping and treats the Miller/direction inputs as parent indices without symmetrizing the supplied geometry |
| `slip_length` | optional | float | `null` | Total slip distance (Å); auto if null |
| `plane_shift` | optional | int/float | `0` | Shift of slip plane position |
| `supercell_size` | optional | list[int] | `[1,1,5]` | In-plane replication and target Miller-plane spacings |
| `min_slab_height` | optional | float/null | `null` | Auto-add oriented-cell repeats until this material thickness (Å) is reached |
| `max_atoms` | optional | int/null | `null` | Stop if the generated slab exceeds this atom count |
| `min_distance` | optional | float | `0.2` | Stop if a periodic atom-pair distance is below this value (Å) |
| `vacuum_size` | optional | float | `20` | Vacuum above slab (Å) |
| `require_orthogonal_cell` | optional | bool | `false` | Fail unless the generated slab is an orthogonal Cartesian-z zero-tilt cell; never changes periodic boundaries |
| `n_steps` | optional | int | `10` | Number of slip increments |
| `displacement_points` | optional | list[float]/null | `null` | Explicit unique fractions in `[0,1]`; must include `0` and, when set, replaces the uniform `n_steps` grid |
| `add_fix` | optional | list[str] | `["true","true","false"]` | Selective dynamics per axis |

**cal_setting defaults**: `relax_pos=true`, `relax_shape=false`, `relax_vol=false`

**Backward-compatible default** (the safety limits remain unset unless the
user supplies them):
```json
{
    "type": "gamma",
    "plane_miller": [1, 1, 1],
    "slip_direction": [-1, 1, 0],
    "supercell_size": [1, 1, 5],
    "vacuum_size": 20,
    "n_steps": 10
}
```

For endpoint-only work, for example, set
`"displacement_points": [0.0, 0.5]`. APEX sorts the fractions and fails if
they are duplicated, outside `[0,1]`, non-finite, or omit the required zero
reference. The generated task count is exactly the number of explicit points.

**Output**: Stacking fault energy (J/m²) vs displacement fraction. Multiply by
1000 only when a plot or table explicitly uses mJ/m².

⚠️ **CRITICAL CONSTRAINT**: The `slip_direction` **must be a vector ON the slip plane** (dot product with `plane_miller` must equal zero).

**Physically recommended slip systems (FCC / BCC / HCP):** use the table in
the APEX repository **README §4.10 Gamma line/surface**. Systems outside that
registry are allowed, but APEX warns and falls back to checking only that the
direction lies on the plane; inspect the generated slab manually. Nested
`fcc` / `bcc` / `hcp` blocks in `param.json` override top-level
`plane_miller` / `slip_direction` for the matching lattice type (see README).
When chemical disorder or a large supercell makes symmetry detection return
`other`, set `parent_lattice` explicitly. For Gamma line APEX automatically
maps parent indices into the actual relaxed supercell, uses the true reciprocal
normal and elementary parent Burgers translation, freezes the fault split before
vacuum is added, and writes `gamma_geometry.json`. Mapping, layer-gap split,
minimum distance, and parent-translation topology are fail-closed validations;
RSS relaxed-coordinate and chemical `u=1` mismatches are diagnostic metadata.

Quick primary picks when the user has not specified a system (still confirm):

| Structure | plane_miller | slip_direction |
|---|---|---|
| FCC {111} | `[1,1,1]` | `[-1,1,0]` |
| BCC {110} | `[1,1,0]` | `[-1,1,1]` |
| HCP basal | see README §4.10 (Miller–Bravais OK for `gamma`) | see README §4.10 |

**INVALID examples** (will produce wrong results or error):
- `[1,1,1]` + `[1,1,0]` → dot product = 2 ≠ 0, **NOT on the plane**
- `[1,1,1]` + `[0,0,1]` → dot product = 1 ≠ 0, **NOT on the plane**

---

## 9. Gamma Surface (2D GSFE Map)

**type**: `"gamma_surface"`

| Parameter | Required? | Type | Default | Description |
|-----------|-----------|------|---------|-------------|
| `plane_miller` | **REQUIRED** | list[int] | `[1,1,1]` | Slip plane |
| `slip_direction` | **REQUIRED** | list[int] | `[-1,1,0]` | x-direction of 2D grid (**must lie ON the plane**) |
| `parent_lattice` | optional | str/null | `null` | Explicit `bcc`, `fcc`, or `hcp` parent hint for RSS/disordered supercells; infers the integer parent-supercell mapping and treats plane/direction as parent indices without changing the supplied geometry |
| `slip_length` | optional | float | `null` | Slip distance in x |
| `slip_length_y` | optional | float | `null` | Slip distance in y |
| `plane_shift` | optional | int/float | `0` | Slip plane shift |
| `supercell_size` | optional | list[int] | `[1,1,5]` | In-plane replication and target Miller-plane spacings |
| `min_slab_height` | optional | float/null | `null` | Auto-add oriented-cell repeats until this material thickness (Å) is reached |
| `max_atoms` | optional | int/null | `null` | Stop if the generated slab exceeds this atom count |
| `min_distance` | optional | float | `0.2` | Stop if a periodic atom-pair distance is below this value (Å) |
| `vacuum_size` | optional | float | `20` | Vacuum (Å); set `0` explicitly only for a bulk-like periodic fault model |
| `require_orthogonal_cell` | optional | bool | `false` | Fail unless the generated slab is an orthogonal Cartesian-z zero-tilt cell; never Gram-Schmidts the lattice |
| `closed_loop` | optional | bool | `false` | Derive a periodic, possibly oblique in-plane basis |
| `n_steps_x` | optional | int | `10` | Grid increments in x; produces `n_steps_x+1` fractions |
| `n_steps_y` | optional | int | `n_steps_x` | Grid increments in y; produces `n_steps_y+1` fractions |
| `add_fix` | optional | list[str] | `["true","true","false"]` | Selective dynamics |

**cal_setting defaults**: `relax_pos=true`, `relax_shape=false`, `relax_vol=false`

**Backward-compatible core default** (the safety limits remain unset unless
the user supplies them):
```json
{
    "type": "gamma_surface",
    "plane_miller": [1, 1, 1],
    "slip_direction": [-1, 1, 0],
    "supercell_size": [1, 1, 5],
    "vacuum_size": 20,
    "closed_loop": false,
    "n_steps_x": 10,
    "n_steps_y": 10
}
```

The shipped generator and GUI profile templates set `closed_loop=true` as the
recommended 2D default. Omitting the field still preserves the legacy core
behavior above.

**Output**: 2D grid of SFE values (J/m²), (n_steps_x+1) × (n_steps_y+1) points.
With `closed_loop=true`, `slip_length` and `slip_length_y` must be omitted.
APEX records the periodic basis vectors and the true Cartesian displacement of
every grid point; use this mode for oblique or disordered supercells.
Both properties also write `slab_generation.json`. The third
`supercell_size` value is passed to Pymatgen as a plane count
(`in_unit_planes=true`), preventing an intended two-layer slab from being
promoted by floating-point round-off.
Both also write `gamma_geometry.json`, use the same frozen material-internal
fault split, and divide by its recorded `interface_count`: one with vacuum,
two for a fully periodic zero-vacuum cell. Task areas must match the reference.
`orthogonalize_cell=true` is accepted as a backward-compatible alias for the
strict `require_orthogonal_cell` gate; it never modifies the lattice.

### Generator options and pre-submit validation

`generate_config.py create` keeps the legacy `--properties` interface and
defaults unchanged. The following optional flags only apply when `gamma`
and/or `gamma_surface` is requested:

| CLI option | JSON field | Applies to |
|---|---|---|
| `--gamma-parent-lattice {bcc,fcc,hcp}` | `parent_lattice` | both |
| `--gamma-plane-miller <...>` | `plane_miller` | both |
| `--gamma-slip-direction <...>` | `slip_direction` | both |
| `--gamma-supercell-size <x> <y> <planes>` | `supercell_size` | both |
| `--gamma-vacuum-size <angstrom>` | `vacuum_size` | both |
| `--gamma-require-orthogonal-cell` | `require_orthogonal_cell=true` | both |
| `--gamma-min-slab-height <angstrom>` | `min_slab_height` | both |
| `--gamma-max-atoms <count>` | `max_atoms` | both |
| `--gamma-min-distance <angstrom>` | `min_distance` | both |
| `--gamma-n-steps <count>` | `n_steps` | `gamma` |
| `--gamma-displacement-points <u...>` | `displacement_points` | `gamma` |
| `--gamma-n-steps-x <count>` | `n_steps_x` | `gamma_surface` |
| `--gamma-n-steps-y <count>` | `n_steps_y` | `gamma_surface` |
| `--gamma-closed-loop` | `closed_loop=true` | `gamma_surface` |

The generator prints the final Gamma JSON and expected task count. The input
validator then uses the APEX Gamma core in a temporary directory to generate a
representative slab for every local input structure. It reports parent/final
atom count, material thickness, oriented-cell repeats, effective plane count,
minimum periodic pair distance, expected task count, and generated VASP
KPOINTS when applicable.

Explicit safety limits are enforced before submission. Missing
`min_slab_height` or `max_atoms` produces a compatibility warning rather than
changing legacy behavior. This representative check does not replace the full
displacement overlap check performed by `apex preview`.

⚠️ **Same constraint and fallback as gamma**: `slip_direction` must have zero
dot product with `plane_miller`. Physically recommended FCC/BCC/HCP systems are
listed in **README §4.10**. Other systems warn and use geometry-only checking.

⚠️ **Mandatory pre-submit check**: run `apex preview <param.json>` before submitting
any `gamma_surface` job. Preview builds the displacement slabs and, if any atom
pair is closer than `0.2` Å, prints to stderr:

`Generated Gamma surface contains overlapping atoms.`

If that warning appears, do not submit until geometry/parameters are fixed.
Agents must check this stderr message only — **do not open or read the GIF**
(the GIF is optional for humans).

The default `--gif-view auto` writes slip-plane and parent-`bc` projections for
both Gamma lines and Gamma surfaces. Humans can select a single projection with
`--gif-view default`, `slip-plane`, or `parent-bc`, or request the pair
explicitly with `--gif-view both`. Projected unit-cell boundaries are retained
so the vacuum region is not cropped from side-facing projections. These views
are diagnostic renderings and do not replace the fail-closed geometry checks.

**Notes**:
- The y-direction is automatically computed as `plane_miller × slip_direction`.
- Total calculation count = (n_steps_x+1) × (n_steps_y+1) = 121 for 10×10 grid. Expensive with DFT!
- For quick tests, use `"n_steps_x": 5, "n_steps_y": 5` (36 calculations).

---

## 10. Decohesive Energy

**type**: `"decohesive"`

| Parameter | Required? | Type | Default | Description |
|-----------|-----------|------|---------|-------------|
| `miller_index` | **REQUIRED** | list[int] | `[1,1,1]` | Specific Miller plane for cleavage (**3-index only**) |
| `min_slab_size` | **REQUIRED** | float | `40` | Minimum slab thickness (Å) |
| `max_vacuum_size` | optional | float | `15` | Maximum vacuum to open (Å) |
| `vacuum_size_step` | optional | float | `1.0` | Vacuum increment (Å) |
| `pert_xz` | optional | float | `0.01` | Symmetry-breaking perturbation |

**cal_setting defaults**: `relax_pos=false`, `relax_shape=false`, `relax_vol=false` (all static)

**Complete working default**:
```json
{
    "type": "decohesive",
    "miller_index": [1, 1, 1],
    "min_slab_size": 40,
    "max_vacuum_size": 15,
    "vacuum_size_step": 1.0
}
```

**Output**: Work of separation (J/m²) vs vacuum gap distance.

⚠️ **`miller_index` is REQUIRED.** Without it → `KeyError: 'miller_index'` at runtime.

**Supported crystal families & recommended planes** (aligned with README §4.5).
Decohesive has **no** auto plane enumeration and **no** `fcc`/`bcc`/`hcp` nested
overrides — detect the lattice, pick from this table, and confirm with the user.

| Crystal structure | Recommended planes | JSON `miller_index` |
|---|---|---|
| FCC | $(100)$, $(110)$, $(111)$ | `[1,0,0]`, `[1,1,0]`, `[1,1,1]` |
| BCC | $(100)$, $(110)$, $(111)$ | `[1,0,0]`, `[1,1,0]`, `[1,1,1]` |
| Diamond | $(100)$, $(110)$, $(111)$ | `[1,0,0]`, `[1,1,0]`, `[1,1,1]` |
| Zinc blende | $(100)$, $(110)$, $(111)$ | `[1,0,0]`, `[1,1,0]`, `[1,1,1]` |
| Rocksalt | $(100)$, $(110)$, $(111)$ | `[1,0,0]`, `[1,1,0]`, `[1,1,1]` |
| HCP | $(0001)$, $(10\bar{1}0)$, $(11\bar{2}0)$ | `[0,0,1]`, `[1,0,0]`, `[1,1,0]` |
| Perovskite | $(001)$, $(110)$, $(111)$ | `[0,0,1]`, `[1,1,0]`, `[1,1,1]` |

**Notes**:
- Unlike `surface` (which auto-enumerates all planes), `decohesive` requires a specific plane.
- HCP: use **3-index only** (`[0,0,1]` not `[0,0,0,1]`). Four-index Miller–Bravais fails at slab generation.
- Polar / multi-termination faces (e.g. zinc blende $(111)$) still run; APEX keeps the first pymatgen match.
- Do not silently change an approved `miller_index`.
- 15 vacuum steps at 1 Å spacing = 15 single-point calculations.
- All calculations are fully static (no relaxation) — measuring rigid separation.

---

## 11. Finite-Temperature Lattice

**type**: `"finite_t_latt"`

| Parameter | Required? | Type | Default | Description |
|-----------|-----------|------|---------|-------------|
| `supercell_size` | optional | list[int] | `[3,3,3]` | MD supercell |

**cal_setting keys**:

| Key | Required? | Type | Default | Description |
|-----|-----------|------|---------|-------------|
| `temperature` | optional | list[float] | `[200,400,600,800]` LAMMPS; `[300,500,700,900,1100,1300,1500]` VASP | Target temperatures (K) |
| `equi_step` | optional | int | `80000` LAMMPS; `5000` VASP | Equilibration steps |
| `ave_step` | optional | int | `40000` LAMMPS; `10000` VASP | Production/statistics steps |
| `timestep` | optional | float | `0.001` | Timestep (ps) |
| `tdamp` | optional | float | `0.1` | Thermostat damping |
| `pdamp` | optional | float | `1.0` | Barostat damping |
| `N_every` | optional | int | `100` | Sample frequency |
| `N_repeat` | optional | int | `10` | Repeat count |
| `N_freq` | optional | int | `2000` | Output frequency |
| `timestep_fs` | VASP optional | float | `1.0` | VASP timestep (fs) |
| `pressure_kbar` | VASP optional | float | `0.0` | VASP target pressure (kbar) |

**Complete working default**:
```json
{
    "type": "finite_t_latt",
    "supercell_size": [3, 3, 3],
    "cal_setting": {
        "temperature": [200, 400, 600, 800]
    }
}
```

**Output**: The legacy temperature-to-`[a,b,c,T]` mapping plus rich `_metadata` statistics (mean, standard deviation, block standard error, and sample count) for cell tensors, lengths, angles, and volume.

Supported backends are LAMMPS and VASP; ABACUS is rejected. VASP uses Langevin–Parrinello–Rahman NpT (`MDALGO=3`, `ISIF=3`) and requires a VASP binary compiled with `-Dtbdyn`.

**Notes**:
- Each temperature is a separate NPT MD run.
- [3,3,3] = 108 atoms for FCC — adequate for thermal expansion.
- Temperatures should stay below melting point. For Cu: safe up to ~1200 K.

---

## 12. Finite-Temperature Elastic Constants (LAMMPS only)

**type**: `"finite_t_elastic"` **[LAMMPS only]**

> ⚠️ This property requires MD sampling (NPT molecular dynamics with paired Langevin thermostat). It is **incompatible with DFT backends** (VASP/ABACUS) — only LAMMPS with a classical or ML potential can be used.

| Parameter | Required? | Type | Default | Description |
|-----------|-----------|------|---------|-------------|
| `supercell_size` | optional | list[int] | `[3,3,3]` | MD supercell |

**cal_setting keys**:

| Key | Required? | Type | Default | Description |
|-----|-----------|------|---------|-------------|
| `temperature` | **REQUIRED** | list[float] | `[300]` | Target temperatures |
| `strain` | optional | float | `0.001` | Strain magnitude ε |
| `strain_components` | optional | list | `[0,1,2,3,4,5]` | Voigt indices |
| `equi_step` | optional | int | `16000` | Equilibration steps |
| `response_step` | optional | int | `16000` | Response measurement steps |
| `stress_output_every` | optional | int | `100` | Stress output interval |
| `timestep` | optional | float | `0.001` | Timestep (ps) |
| `tdamp` | optional | float | `0.1` | Thermostat damping |
| `pdamp` | optional | float | `1.0` | Barostat damping |
| `seed` | optional | int | `12345` | Random seed |
| `n_blocks` | optional | int | `10` | Block averaging for error bars |
| `method` | optional | str | `"paired_langevin"` | **Only** `"paired_langevin"` supported |

**Complete working default**:
```json
{
    "type": "finite_t_elastic",
    "supercell_size": [3, 3, 3],
    "cal_setting": {
        "temperature": [300],
        "strain": 0.001
    }
}
```

**Output**: Elastic tensor Cij at each temperature, with error estimates.

⚠️ **LAMMPS-only** — paired Langevin method for noise cancellation.

**Notes**:
- Small strain (0.001) needed for linear response regime.
- Each temperature × strain component = one MD run. 6 components × N temperatures can be expensive.
- [3,3,3] supercell is minimum for finite-T elastic; [4,4,4] for publication quality.

---

## 13. Grüneisen Parameters & Thermal Expansion

**type**: `"gruneisen"`

| Parameter | Required? | Type | Default | Description |
|-----------|-----------|------|---------|-------------|
| `supercell_size` | optional | list[int] | `[2,2,2]` | Phonon supercell |
| `MESH` | optional | list[int] | `[20,20,20]` | Three positive reciprocal-space mesh dimensions used for mode summation |
| `volume_strains` | **REQUIRED** | list[float] | `[-0.02,-0.01,0.0,0.01,0.02]` | Must include 0.0, ≥3 points |
| `temperatures` | **REQUIRED** | list[float] | `[100,200,300,400,500]` | Temperature points for evaluation |
| `alpha_mode` | optional | str | `"sign_only"` | `"sign_only"` or `"full"` |

**Complete working default**:
```json
{
    "type": "gruneisen",
    "supercell_size": [2, 2, 2],
    "MESH": [20, 20, 20],
    "volume_strains": [-0.02, -0.01, 0.0, 0.01, 0.02],
    "temperatures": [100, 200, 300, 400, 500],
    "alpha_mode": "full"
}
```

**Output**: Mode Grüneisen parameters γ_i, thermal expansion coefficient α(T).

⚠️ **Both `volume_strains` and `temperatures` are REQUIRED.** Missing either → KeyError at runtime.

**Notes**:
- `volume_strains` MUST contain `0.0` (equilibrium reference) and have ≥3 points for numerical differentiation.
- Phonon calculations are performed at each strained volume → total cost = N_strains × phonon cost.
- 5 volume points is standard for reliable Grüneisen parameter extraction.
- `"alpha_mode": "full"` uses the full phonon spectrum; `"debye"` uses Debye model approximation.

---

## 14. Annealing

**type**: `"annealing"`

| Parameter | Required? | Type | Default | Description |
|-----------|-----------|------|---------|-------------|
| `supercell_size` | optional | list[int] | `[3,3,3]` | MD supercell |
| `supercell_length` | optional | float | `null` | Auto-size from target edge length |
| `protocol` | optional | str | `"ramp_cool"` | Legacy heat/cool schedule, or VASP-only `"coexistence"` |

**cal_setting keys (temperature cycle)**:

| Key | Required? | Type | Default | Description |
|-----|-----------|------|---------|-------------|
| `start_temp` | optional | float | `4` | Initial temperature (K) |
| `target_temp` | optional | float | `300` | Peak temperature (K) |
| `end_temp` | optional | float | `4` | Final temperature (K) |
| `temp_ramp_rate` | optional | float | `1000` | Heating rate (K/ps) |
| `cool_rate` | optional | float | = ramp_rate | Cooling rate (K/ps) |
| `equi_step` | optional | int | `20000` | Initial equilibration steps |
| `hold_step` | optional | int | `20000` | Hold at target_temp steps |
| `production_step` | optional | int | `10000` | Fixed-T production steps for `protocol="coexistence"` |
| `timestep` | optional | float | `0.001` | Timestep (ps) |
| `thermostat` | optional | str | `"nose_hoover"` | Thermostat type |
| `ensemble` | optional | str | `"npt"` | Ensemble type |
| `velocity_seed` | optional | int | `123457` | Random seed |
| `timestep_fs` | VASP optional | float | `1.0` | VASP timestep (fs) |
| `pressure_kbar` | VASP optional | float | `0.0` | VASP target pressure (kbar) |

**cal_setting keys (analysis)**:

| Key | Required? | Type | Default | Description |
|-----|-----------|------|---------|-------------|
| `req_compute_rdf` | optional | bool | `true` | Compute RDF |
| `rdf_bins` | optional | int | `100` | RDF histogram bins |
| `rdf_cutoff` | optional | float | `6.0` | RDF cutoff (Å) |
| `req_compute_msd` | optional | bool | `true` | Compute MSD |

**Complete working default**:
```json
{
    "type": "annealing",
    "supercell_size": [3, 3, 3],
    "cal_setting": {
        "start_temp": 4,
        "target_temp": 300,
        "end_temp": 4,
        "temp_ramp_rate": 1000
    }
}
```

**Output**: RDF and MSD by stage, plus volume, pressure, temperature, potential energy, and total energy series. Ramp/cool also produces a final quenched structure.

Supported backends are LAMMPS and VASP; ABACUS is rejected. `protocol="ramp_cool"` retains the existing heating/cooling semantics. VASP-only `protocol="coexistence"` holds `target_temp` for an equilibration stage and then a production stage, both at fixed target temperature. VASP uses `MDALGO=3` and requires `-Dtbdyn`.

**Notes**:
- [3,3,3] supercell recommended for statistical sampling (108+ atoms).
- `target_temp=300` is safe; for studying phase transitions, increase to above melting.
- `temp_ramp_rate=1000` K/ps is relatively fast — use 100 K/ps for more physical quenching.

---

## 15. Two-Phase Coexistence Melting Point (LAMMPS only)

**type**: `"melting_point"` **[LAMMPS only]**

This property constructs a solid/liquid interface, premelts the upper region
with the lower crystal pinned, conditions the liquid at each target
temperature, and releases the complete cell under NPT dynamics. The bracket
uses the sign and 95% interval of the q6-derived interface velocity.

```json
{
  "type": "melting_point",
  "method": "two_phase",
  "supercell_size": [1, 1, 2],
  "cal_setting": {
    "temperature": [1600, 1650, 1700],
    "premelt_temperature": 4500,
    "premelt_steps": 5000,
    "conditioning_steps": 5000,
    "production_steps": 100000,
    "timestep": 0.001,
    "tdamp": 0.1,
    "pdamp": 1.0,
    "pressure": 0.0,
    "barostat": "iso",
    "interface_axis": "z",
    "liquid_fraction": 0.5,
    "dump_step": 100,
    "thermo_step": 100,
    "restart_interval": 10000,
    "q6_cutoff": 3.5,
    "q6_neighbors": 12,
    "replicas": 3,
    "velocity_seeds": {
      "premelt": 324159,
      "condition": 271828,
      "release": 161803
    }
  }
}
```

Optional continuation inputs are temperature-indexed:

```json
"cal_setting": {
  "temperature": [1600, 1650, 1700],
  "restart_files": [
    "restart.1600",
    "restart.1650",
    "restart.1700"
  ]
}
```

`restart_files` must contain exactly one existing file per temperature. The
matching file is copied into every replica task for that temperature and
forwarded as `restart.coexistence.start`. This is a transport contract only:
the generated LAMMPS input is not automatically rewritten to `read_restart`.
`finite_t_latt` does not accept `restart_files` and never forwards
`restart.coexistence.start`.

The generator exposes `--melting-temperatures`, `--melting-replicas`, and
`--melting-restart-files`; restart inputs are staged into the generated job
directory before `param.json` is written.

Each temperature/replica pair is one GPU/CPU LAMMPS task. Provide independent
alloy chemical realizations as separate `structures`; the property never
silently randomizes atom types. Before paid submission, report the relaxed
base-cell atom count, `supercell_size`, final atom count, temperatures,
replicas, total task count, runtime, and accelerator resources.

Aggregation is fail-closed against the configured matrix. Every requested
temperature must have exactly the configured number of distinct replicas; a
missing or duplicate replica makes that temperature's consensus
`inconclusive`, so it cannot establish a melting bracket.

**Outputs**: `result.json`, `result.out`, `melting_point_tidy.csv`, solid
fraction versus time, interface velocity versus temperature, q6 interface
snapshots, and the raw `dump.melting`/`log.lammps` evidence in every task.
Each task also retrieves alternating `restart.melting.1/2` checkpoints and the
normal-completion `restart.melting.final`; `restart_interval` is in timesteps.

---

## RSS Structure Generation

Use `apex rss <rss.json>` to generate input structures.

It generates random-solid-solution configurations for multi-component alloys,
high-entropy oxides, and related materials. Use the generated
`conf_###/POSCAR` directories as inputs to the property calculations above.

See `reference/rss_workflow.md` for full details.

---

## Quick Reference: Stable Defaults Summary Table

| Property | Key Required Params | Safe Default Values | Common Pitfall |
|----------|-------------------|--------------------|----|
| `eos` | vol_start, vol_end, vol_step | 0.8, 1.2, 0.05 | — |
| `cohesive` | latt_start, latt_end, latt_step | 0.8, 1.5, 0.05 | Missing `cal_type` |
| `elastic` | (none beyond type) | norm/shear_deform=0.01 | — |
| `surface` | min_slab_size, min_vacuum_size | 50 Å, 20 Å | Too many surfaces with high max_miller |
| `vacancy` | (none beyond type) | supercell=[3,3,3] | Small supercell → size effects |
| `interstitial` | (none beyond type) | supercell=[3,3,3] | Missing insert_ele for foreign atoms |
| `phonon` | supercell_size | [3,3,3] | ⚠️ [2,2,2] may fail in LAMMPS |
| `gamma` | plane_miller, slip_direction | [1,1,1], [-1,1,0] | ⚠️ slip_dir NOT on plane |
| `gamma_surface` | plane_miller, slip_direction | [1,1,1], [-1,1,0] | ⚠️ slip_dir NOT on plane |
| `decohesive` | miller_index, min_slab_size | [1,1,1], 40 Å | ⚠️ Missing miller_index → KeyError |
| `finite_t_latt` | cal_setting.temperature | [200,400,600,800] | DFT MD is expensive |
| `finite_t_elastic` | cal_setting.temperature | [300] | LAMMPS-only |
| `gruneisen` | volume_strains, temperatures | [-0.02..0.02], [100..500] | ⚠️ Missing temperatures → KeyError |
| `annealing` | (none critical) | target_temp=300 | DFT MD is expensive |
| `melting_point` | temperature, supercell_size | three-point bracket, 100 ps release | LAMMPS-only; do not classify from energy alone |

---

## Validation Checklist Before Submission

Before submitting any APEX property calculation, verify:

1. ✅ **All REQUIRED parameters present** — check the table above
2. ✅ **Crystallographic constraints satisfied**:
   - `gamma` / `gamma_surface`: `dot(plane_miller, slip_direction) == 0`; system from README §4.10
   - `decohesive`: `miller_index` explicitly specified (3-index); plane from README §4.5 table for the crystal family
3. ✅ **Supercell sizes adequate**:
   - Vacancy/interstitial: ≥ [2,2,2], prefer [3,3,3]
   - Phonon: ≥ [3,3,3] for LAMMPS
   - Finite-T properties: ≥ [3,3,3]
4. ✅ **LAMMPS-only properties** not sent to DFT backend:
   - `finite_t_elastic`
   - `melting_point`
5. ✅ **Physical reasonableness**:
   - Thermal-expansion temperatures below melting; melting-point temperatures bracket both sides
   - Volume strains ≤ ±5% (avoid unphysical compression)
   - Slab thickness large enough for bulk-like interior
