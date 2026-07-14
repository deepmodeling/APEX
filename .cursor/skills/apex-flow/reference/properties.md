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
| `phonolammps_run_command` | optional | str | `null` | Custom phonoLAMMPS command |

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
| `slip_length` | optional | float | `null` | Total slip distance (Å); auto if null |
| `plane_shift` | optional | int/float | `0` | Shift of slip plane position |
| `supercell_size` | optional | list[int] | `[1,1,5]` | Supercell for slab |
| `vacuum_size` | optional | float | `0` | Vacuum above slab (Å) |
| `n_steps` | optional | int | `10` | Number of slip increments |
| `add_fix` | optional | list[str] | `["true","true","false"]` | Selective dynamics per axis |

**cal_setting defaults**: `relax_pos=true`, `relax_shape=false`, `relax_vol=false`

**Complete working default**:
```json
{
    "type": "gamma",
    "plane_miller": [1, 1, 1],
    "slip_direction": [-1, 1, 0],
    "supercell_size": [1, 1, 5],
    "n_steps": 10
}
```

**Output**: Stacking fault energy (mJ/m²) vs displacement fraction.

⚠️ **CRITICAL CONSTRAINT**: The `slip_direction` **must be a vector ON the slip plane** (dot product with `plane_miller` must equal zero).

**Valid FCC (111) slip systems**:
| plane_miller | slip_direction | System name |
|---|---|---|
| `[1,1,1]` | `[-1,1,0]` | `111x-110` |
| `[1,1,1]` | `[1,-1,0]` | `111x1-10` |
| `[1,1,1]` | `[1,1,-2]` | `111x11-2` |

**INVALID examples** (will produce wrong results or error):
- `[1,1,1]` + `[1,1,0]` → dot product = 2 ≠ 0, **NOT on the plane**
- `[1,1,1]` + `[0,0,1]` → dot product = 1 ≠ 0, **NOT on the plane**

**Common slip systems for other structures**:
| Structure | plane_miller | slip_direction |
|---|---|---|
| BCC {110} | `[1,1,0]` | `[-1,1,1]` |
| BCC {112} | `[1,1,2]` | `[-1,1,1]` |
| HCP {0001} | `[0,0,0,1]` | `[1,1,-2,0]` (use 3-index) |

---

## 9. Gamma Surface (2D GSFE Map)

**type**: `"gamma_surface"`

| Parameter | Required? | Type | Default | Description |
|-----------|-----------|------|---------|-------------|
| `plane_miller` | **REQUIRED** | list[int] | `[1,1,1]` | Slip plane |
| `slip_direction` | **REQUIRED** | list[int] | `[-1,1,0]` | x-direction of 2D grid (**must lie ON the plane**) |
| `slip_length` | optional | float | `null` | Slip distance in x |
| `slip_length_y` | optional | float | `null` | Slip distance in y |
| `plane_shift` | optional | int/float | `0` | Slip plane shift |
| `supercell_size` | optional | list[int] | `[1,1,5]` | Supercell |
| `vacuum_size` | optional | float | `0` | Vacuum (Å) |
| `n_steps_x` | optional | int | `10` | Grid points in x |
| `n_steps_y` | optional | int | `n_steps_x` | Grid points in y |
| `add_fix` | optional | list[str] | `["true","true","false"]` | Selective dynamics |

**cal_setting defaults**: `relax_pos=true`, `relax_shape=false`, `relax_vol=false`

**Complete working default**:
```json
{
    "type": "gamma_surface",
    "plane_miller": [1, 1, 1],
    "slip_direction": [-1, 1, 0],
    "supercell_size": [1, 1, 5],
    "n_steps_x": 10,
    "n_steps_y": 10
}
```

**Output**: 2D grid of SFE values (mJ/m²), (n_steps_x+1) × (n_steps_y+1) points.

⚠️ **Same constraint as gamma**: `slip_direction` must have zero dot product with `plane_miller`. See valid systems table in §8.

**Notes**:
- The y-direction is automatically computed as `plane_miller × slip_direction`.
- Total calculation count = (n_steps_x+1) × (n_steps_y+1) = 121 for 10×10 grid. Expensive with DFT!
- For quick tests, use `"n_steps_x": 5, "n_steps_y": 5` (36 calculations).

---

## 10. Decohesive Energy

**type**: `"decohesive"`

| Parameter | Required? | Type | Default | Description |
|-----------|-----------|------|---------|-------------|
| `miller_index` | **REQUIRED** | list[int] | `[1,1,1]` | Specific Miller plane for cleavage |
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

**Notes**:
- Unlike `surface` (which auto-enumerates all planes), `decohesive` requires a specific plane.
- Common choices: `[1,1,1]` (close-packed, lowest surface energy), `[1,1,0]`, `[1,0,0]`.
- 15 vacuum steps at 1 Å spacing = 15 single-point calculations.
- All calculations are fully static (no relaxation) — measuring rigid separation.

---

## 11. Finite-Temperature Lattice (LAMMPS only)

**type**: `"finite_t_latt"`

| Parameter | Required? | Type | Default | Description |
|-----------|-----------|------|---------|-------------|
| `supercell_size` | optional | list[int] | `[3,3,3]` | MD supercell |

**cal_setting keys**:

| Key | Required? | Type | Default | Description |
|-----|-----------|------|---------|-------------|
| `temperature` | **REQUIRED** | list[float] | `[200,400,600,800]` | Target temperatures (K) |
| `equi_step` | optional | int | `80000` | Equilibration steps |
| `ave_step` | optional | int | `40000` | Averaging steps |
| `timestep` | optional | float | `0.001` | Timestep (ps) |
| `tdamp` | optional | float | `0.1` | Thermostat damping |
| `pdamp` | optional | float | `1.0` | Barostat damping |
| `N_every` | optional | int | `100` | Sample frequency |
| `N_repeat` | optional | int | `10` | Repeat count |
| `N_freq` | optional | int | `2000` | Output frequency |

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

**Output**: Lattice parameter a (and c/a for non-cubic) vs temperature.

⚠️ **LAMMPS-only** — raises error for VASP/ABACUS backends.

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
| `volume_strains` | **REQUIRED** | list[float] | `[-0.02,-0.01,0.0,0.01,0.02]` | Must include 0.0, ≥3 points |
| `temperatures` | **REQUIRED** | list[float] | `[100,200,300,400,500]` | Temperature points for evaluation |
| `alpha_mode` | optional | str | `"full"` | `"full"` or `"debye"` |

**Complete working default**:
```json
{
    "type": "gruneisen",
    "supercell_size": [2, 2, 2],
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

## 14. Annealing (LAMMPS only)

**type**: `"annealing"`

| Parameter | Required? | Type | Default | Description |
|-----------|-----------|------|---------|-------------|
| `supercell_size` | optional | list[int] | `[3,3,3]` | MD supercell |
| `supercell_length` | optional | float | `null` | Auto-size from target edge length |

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
| `timestep` | optional | float | `0.001` | Timestep (ps) |
| `thermostat` | optional | str | `"nose_hoover"` | Thermostat type |
| `ensemble` | optional | str | `"npt"` | Ensemble type |
| `velocity_seed` | optional | int | `123457` | Random seed |

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

**Output**: RDF at each stage, MSD, volume-temperature curves, final quenched structure.

⚠️ **LAMMPS-only** — full heat-hold-quench MD cycle.

**Notes**:
- [3,3,3] supercell recommended for statistical sampling (108+ atoms).
- `target_temp=300` is safe; for studying phase transitions, increase to above melting.
- `temp_ramp_rate=1000` K/ps is relatively fast — use 100 K/ps for more physical quenching.

---

## 15. RSS (Random Solid Solution) — Structure Generation

**type**: Not a property calculation per se. Use `apex rss` CLI command.

This generates random solid solution configurations for multi-component alloys. The generated structures are then used as input to property calculations above.

```bash
apex rss --composition "CoCrFeMnNi" --prototype bcc --supercell 3 3 3 --n-configs 10
```

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
| `finite_t_latt` | cal_setting.temperature | [200,400,600,800] | LAMMPS-only |
| `finite_t_elastic` | cal_setting.temperature | [300] | LAMMPS-only |
| `gruneisen` | volume_strains, temperatures | [-0.02..0.02], [100..500] | ⚠️ Missing temperatures → KeyError |
| `annealing` | (none critical) | target_temp=300 | LAMMPS-only |

---

## Validation Checklist Before Submission

Before submitting any APEX property calculation, verify:

1. ✅ **All REQUIRED parameters present** — check the table above
2. ✅ **Crystallographic constraints satisfied**:
   - `gamma` / `gamma_surface`: `dot(plane_miller, slip_direction) == 0`
   - `decohesive`: `miller_index` explicitly specified
3. ✅ **Supercell sizes adequate**:
   - Vacancy/interstitial: ≥ [2,2,2], prefer [3,3,3]
   - Phonon: ≥ [3,3,3] for LAMMPS
   - Finite-T properties: ≥ [3,3,3]
4. ✅ **LAMMPS-only properties** not sent to DFT backend:
   - `finite_t_latt`, `finite_t_elastic`, `annealing`
5. ✅ **Physical reasonableness**:
   - Temperatures below melting point
   - Volume strains ≤ ±5% (avoid unphysical compression)
   - Slab thickness large enough for bulk-like interior
