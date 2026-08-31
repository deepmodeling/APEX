# APEX Calculator Backends — Configuration Guide

## Overview

APEX supports three calculator **backends**: LAMMPS, ABACUS, and VASP.
Each requires specific configuration in `param.json` under the `"interaction"` key.

> APEX **backend** = calculator (`lammps` / `abacus` / `vasp`).
> DPA4 alloytongqi is a DeePMD **model** under LAMMPS (`interaction.type: deepmd`).
> Ask calculator backend first; if LAMMPS+DeePMD, then ask which model file.

---

## 1. LAMMPS (Classical & ML Potentials)

### Basic Configuration

```json
{
    "interaction": {
        "type": "<potential_type>",
        "model": "<model_file>",
        "type_map": "auto"
    }
}
```

### Potential Types

| type | pair_style | Model File | Notes |
|------|-----------|-----------|-------|
| `deepmd` | `deepmd` | `.pb`, `.pth`, or compatible single-task `.pt` | DeePMD-kit model |
| `mace` | `mace no_domain_decomposition` | `.model` | MACE model |
| `nep` | `nep` | `nep.txt` | NEP potential |
| `gap` | `quip` | `.xml` + `.xml.sparseX.TERM` | GAP/QUIP potential |
| `snap` | `snap` | coeff + param files | SNAP potential |
| `rann` | `rann` | `.nn` | RANN potential |
| `eam_alloy` | `eam/alloy` | `.eam.alloy` | EAM alloy |
| `eam_fs` | `eam/fs` | `.eam.fs` | Finnis-Sinclair |
| `meam` | `meam` | library + parameter files | MEAM |
| `meam_spline` | `meam/spline` | `.meam.spline` | MEAM spline |

### DeePMD Example

```json
{
    "interaction": {
        "type": "deepmd",
        "model": "model.pt",
        "type_map": "auto"
    }
}
```

### MACE Example

```json
{
    "interaction": {
        "type": "mace",
        "model": "mace_model.model",
        "type_map": "auto"
    }
}
```

### NEP Example

```json
{
    "interaction": {
        "type": "nep",
        "model": "nep.txt",
        "type_map": "auto"
    }
}
```

### EAM Example

```json
{
    "interaction": {
        "type": "eam_alloy",
        "model": "AlCu.eam.alloy",
        "type_map": "auto"
    }
}
```

### MEAM Example

```json
{
    "interaction": {
        "type": "meam",
        "model": "library.meam TiAl.meam",
        "type_map": "auto"
    }
}
```

### Default LAMMPS image and DPA4 compatibility

GPU image (`deepmd`, `mace`, `nep`):
`registry.dp.tech/dptech/dp/native/prod-16664/dpa4-phonolammps:0.0.2`

CPU image (`gap`, `snap`, `rann`, `eam_alloy`, `eam_fs`, `meam`,
`meam_spline`):
`registry.dp.tech/dptech/dp/native/prod-397637/apex-flow:1.3.0.post`

This image uses integrated USER-DEEPMD and CUDA 12.8. Do not add
`plugin load libdeepmd_lmp.so`; the plugin command is unsupported. Its unified
`lmp` entry dispatches RTX 4090/L20-class GPUs to the sm89 build and RTX 5090
to sm120. The L20 and RTX 4090 sm89 paths are validated; L20 is the default
because it has greater resource availability. Test sm120 on a real 5090 before
relying on that path.

For provenance, an older phonoLAMMPS 3.1.3 tag was
available from the Bohrium registry mirror at repo digest
`sha256:43a27ca4a7bba7f774bbd56104d205a6a80cd9d65928f249f6109e9ef37b8402`.
That image reports DeepMD-kit 3.1.3 and phonoLAMMPS 0.10.1. `dp show` failed
with `RuntimeError: Unknown model type: dpa4`, and LAMMPS aborted while
initializing `pair_style deepmd`. Therefore the tested old runtime cannot
execute the bundled DPA4 model. Do not submit the bundled model with that old
image or select another image without an explicit qualified profile.

> **LAMMPS phonon and Grüneisen**: for GPU potentials, `apex submit` forces
> the DPA4 image above. It is validated on NVIDIA L20 and RTX 4090 and includes
> phonoLAMMPS. L20 is the default resource.
> CPU potentials keep the CPU image. Do not use the DPA4 image on a CPU
> machine; sequential CPU jobs stalled during container preparation.

### Bundled DPA4 model (`models/`)

The skill ships one DeePMD model and no alternate checkpoint downloader:

| Path | Format | Use |
|------|--------|-----|
| `models/DPA4-alloytongqi/model.pt` | Single-task DPA4 PyTorch checkpoint (30,403,297 bytes) | Source identity/provenance only; never the image-profile LAMMPS input |

Safe static inspection confirms a valid PyTorch ZIP checkpoint with
`model_params.type=dpa4`, fitting type `dpa4_ener`, and an empty
`model_branch_alias` list, consistent with a default/single task. The
`alloytongqi` branch identity is user-provided provenance; no Git branch or
commit string is embedded in the file. A type map does not by itself prove
training-domain coverage.

After publication, generate (do not hand-write) this image-resident contract:

```json
{
    "interaction": {
        "type": "deepmd",
        "deepmd_runtime": "dpa4_pt2",
        "model_in_image": true,
        "model": "/opt/dpa4-runtime/models/DPA4-alloytongqi/alloytongqi.t4-sm75.pt2",
        "runtime_model_sha256": "2614db9463f5864d80a78fec037aeae26930df2004bb9f1148a69b83c25b3daf",
        "source_checkpoint": "/opt/dpa4-runtime/models/DPA4-alloytongqi/model.pt",
        "source_checkpoint_sha256": "c84b268cc6191afc72bd2d5c001cbe526a0d2e04ebf6dbd7df021306e9abe9ad",
        "type_map": "auto"
    }
}
```

> Use `generate_config.py create --runtime-profile dpa4-alloytongqi-t4`.
> It is currently locked by placeholder image identity and
> `pre_snapshot_only` qualification. Pre-snapshot checks completed 12 LAMMPS
> runs, 6 CPU/GPU parity checks, and one phonoLAMMPS smoke on one rank/one GPU
> `c4_m15_1 * NVIDIA T4`; this is not exact-image acceptance. After a digest
> rerun, only that exact SKU is eligible; c8/c16 T4 and non-T4 GPUs remain
> unverified. The generated global config uses
> `/usr/local/bin/dpa4-lmp -in in.lammps` and
> `/usr/local/bin/dpa4-phonolammps {input_file} -c {poscar} --dim {dim}
> {primitive_axes}` exactly. V100/SM 7.0 and older devices and NVIDIA Linux
> drivers below 580.65.06 are prohibited by the CUDA 13 contract. Use
> `"type_map": "auto"` by default. APEX infers the local contiguous type
> mapping from the structure; do not copy atomic-number or model-internal
> indices. A `.pt` checkpoint is directly usable only when it is a compatible
> single-task model supported by the selected runtime; do not generalize this
> bundled model's handling to arbitrary multi-task training checkpoints.

### Relaxation cal_setting (LAMMPS)

```json
{
    "relaxation": {
        "cal_setting": {
            "etol": 0,
            "ftol": 1e-10,
            "maxiter": 5000,
            "maximal": 500000
        }
    }
}
```

Key LAMMPS-specific relaxation settings:
- `etol`: Energy tolerance for convergence (0 = disabled)
- `ftol`: Force tolerance (eV/Å)
- `maxiter`: Max minimization iterations
- `maximal`: Max force/energy evaluations

---

## 2. ABACUS (DFT)

### Basic Configuration

```json
{
    "interaction": {
        "type": "abacus",
        "incar": "abacus_input/INPUT",
        "potcar_prefix": "abacus_input",
        "potcars": {
            "Cu": "Cu_ONCV_PBE-1.0.upf"
        },
        "orb_files": {
            "Cu": "Cu_gga_9au_100Ry_4s2p2d1f.orb"
        }
    }
}
```

### Required Files

| File | Description | Location |
|------|-------------|----------|
| INPUT | ABACUS input control file | `incar` path |
| INPUT_phonon | (Optional) INPUT for phonon/force SCF | Same directory |
| `*.upf` | Pseudopotential files | `potcar_prefix` directory |
| `*.orb` | Numerical orbital files (LCAO) | `potcar_prefix` directory |
| STRU | Structure file with PP/orbital refs | `confs/<name>/` |

### ⚠️ Critical: STRU File Required

APEX converts POSCAR → STRU via dpdata, but the generated STRU **lacks PP filename and NUMERICAL_ORBITAL section**, causing ABACUS to crash silently.

**Always provide a STRU file** alongside POSCAR in `confs/<name>/`:

```
ATOMIC_SPECIES
Cu 63.546 Cu_ONCV_PBE-1.0.upf

NUMERICAL_ORBITAL
Cu_gga_9au_100Ry_4s2p2d1f.orb

LATTICE_CONSTANT
1.8897261246

LATTICE_VECTORS
0.000000000000   1.807500000000   1.807500000000
1.807500000000   0.000000000000   1.807500000000
1.807500000000   1.807500000000   0.000000000000

ATOMIC_POSITIONS
Direct

Cu
0.0
1
0.000000000000 0.000000000000 0.000000000000 1 1 1
```

### ⚠️ Critical: Do NOT Set pseudo_dir/orbital_dir in INPUT

APEX's `modify_stru_path()` automatically prefixes PP/orbital filenames in STRU with `pp_orb/`. If INPUT also has `pseudo_dir ./pp_orb`, ABACUS looks for `./pp_orb/pp_orb/file.upf` (double prefix) → crash.

**Correct**: INPUT has NO `pseudo_dir` / `orbital_dir` lines.

### INPUT File — System-Dependent Defaults

> ⚠️ **mixing_beta 是收敛关键参数**：金属体系用 0.7 会震荡不收敛！必须根据体系类型选择。推荐使用 `mixing_beta -10.0`（ABACUS 3.4+ 自动检测）或按下表手动设置。

**体系类型判断规则：**
- 金属：含 Li/Na/K/Ca/Al/Fe/Cu/Ni/Co/Ti/Zr/Hf/Ag/Au/Pt/Pd/W/Mo/Nb/Ta/V/Cr/Mn/Zn 等且无大带隙
- 过渡金属：含 3d/4d/5d 元素（Fe/Co/Ni/Ti/V/Cr/Mn/Cu/Zn/Zr/Nb/Mo/Pd/Ag/Hf/Ta/W/Pt/Au）
- 非金属/半导体：Si/Ge/GaAs/ZnO/TiO2/MgO 等有明确带隙的体系

**Relaxation INPUT — 按体系分类：**

| 参数 | 金属/合金 | 过渡金属/磁性 | 非金属/半导体 |
|------|-----------|--------------|--------------|
| `mixing_beta` | 0.2 | 0.2 | 0.7 |
| `mixing_gg0` | 0.0 | 1.5 (Kerker) | 0.0 |
| `smearing_method` | mp | mp | gauss |
| `smearing_sigma` | 0.01 | 0.01 | 0.01 |
| `scf_nmax` | 200 | 200 | 100 |
| `force_thr_ev` | 0.03 | 0.03 | 0.02 |
| `relax_nmax` | 100 | 100 | 50 |

**通用 Relaxation INPUT（金属/合金，推荐默认）：**
```
INPUT_PARAMETERS
calculation     cell-relax
basis_type      lcao
ecutwfc         100
scf_thr         1.0e-5
scf_nmax        200
smearing_method mp
smearing_sigma  0.01
mixing_type     broyden
mixing_beta     0.2
mixing_gg0      1.5
cal_force       1
cal_stress      1
force_thr_ev    0.03
stress_thr      1.0
relax_nmax      100
kspacing        0.20
```

**非金属/半导体 Relaxation INPUT：**
```
INPUT_PARAMETERS
calculation     cell-relax
basis_type      lcao
ecutwfc         100
scf_thr         1.0e-5
scf_nmax        100
smearing_method gauss
smearing_sigma  0.01
mixing_type     broyden
mixing_beta     0.7
cal_force       1
cal_stress      1
force_thr_ev    0.02
stress_thr      0.5
relax_nmax      50
kspacing        0.20
```

**Phonon/Force SCF INPUT（通用）：**
```
INPUT_PARAMETERS
calculation     scf
basis_type      lcao
ecutwfc         100
scf_thr         1.0e-7
scf_nmax        200
smearing_method mp
smearing_sigma  0.01
mixing_type     broyden
mixing_beta     0.2
mixing_gg0      1.5
cal_force       1
kspacing        0.15
```

### 收敛困难排查

如果 relaxation 不收敛：
1. **先检查 mixing_beta** — 金属体系必须 ≤ 0.2
2. **加 Kerker 预条件** — 设 `mixing_gg0 1.5`
3. **增大 mixing_ndim** — 从 8 增到 20
4. **减小 mixing_beta** — 试 0.1 或更小
5. **增大 scf_nmax** — 从 100 增到 300
6. **增大 relax_nmax** — 从 50 增到 200

### Key ABACUS Parameters

| Parameter | 金属 | 非金属 | Description |
|-----------|------|--------|-------------|
| `ecutwfc` | 100 Ry | 100 Ry | PW cutoff for LCAO charge density |
| `scf_thr` | **1e-5** | **1e-5** | SCF convergence (relaxation) |
| `mixing_beta` | **0.2** | **0.7** | Mixing strength (⚠️ 关键!) |
| `mixing_gg0` | **1.5** | 0.0 | Kerker preconditioning |
| `smearing_method` | **mp** | gauss | Smearing type |
| `force_thr_ev` | 0.03 | 0.02 | Force convergence (eV/Å) |
| `stress_thr` | 1.0 | 0.5 | Stress convergence (GPa) |
| `kspacing` | **0.20 (required)** | **0.20 (required)** | K-point spacing (1/Bohr); APEX writes `KPT` from it |
| `scf_nmax` | **200** | 100 | Max SCF iterations |
| `relax_nmax` | **100** | 50 | Max relaxation steps |

### ABACUS k-spacing constraints (REQUIRED)

APEX does **not** accept a hand-written `KPT` as the primary control. At make time it requires either:
1. `kspacing` in `INPUT` (float, unit **1/Bohr**), or
2. `cal_setting.K_POINTS` as a 6-int list, e.g. `[6, 6, 6, 0, 0, 0]`.

Missing both → `RuntimeError: K point information is not defined`.

| Use case | Recommended `kspacing` | Notes |
|----------|------------------------|-------|
| Relaxation / screening | **0.20** | Default in skill templates |
| Phonon / force SCF | **0.15** | Slightly denser |
| Higher accuracy (user request) | 0.10 | ~8× cost vs 0.20 |

> **kspacing 对比**: 0.20 → ~6×6×6 k-mesh (216 pts), 0.10 → ~12×12×12 (1728 pts). 计算量差 **8 倍**。

### ABACUS global.json Settings (Bohrium/dflow)

```json
{
    "abacus_image_name": "registry.dp.tech/dptech/abacus:3.2.3",
    "abacus_run_command": "mpirun -n 16 abacus",
    "scass_type": "c16_m32_cpu"
}
```

### ⚠️ Critical: PRIMITIVE_AXES vs Cell Type (Phonon/Gruneisen)

`PRIMITIVE_AXES` in param.json tells phonopy how to extract a primitive cell from a conventional cell. **Using it when the input is already a primitive cell causes `RuntimeError: Remapping of atoms by TrimmedCell failed`.**

**Pre-submission check — MUST verify before setting PRIMITIVE_AXES:**

| Input structure type | Example | PRIMITIVE_AXES? |
|---------------------|---------|----------------|
| FCC primitive cell (1 atom, `[0,a/2,a/2]` vectors) | Cu STRU with rhombohedral vectors | ❌ Do NOT set |
| FCC conventional cell (4 atoms, cubic `a×a×a`) | Cu POSCAR with cubic cell | ✅ Set `"0 1/2 1/2  1/2 0 1/2  1/2 1/2 0"` |
| BCC primitive cell (1 atom) | Fe with BCC primitive vectors | ❌ Do NOT set |
| BCC conventional cell (2 atoms, cubic) | Fe POSCAR cubic cell | ✅ Set `"1/2 1/2 1/2  -1/2 1/2 1/2  1/2 -1/2 1/2"` |
| HCP primitive cell (2 atoms) | Ti with hexagonal cell | ❌ Do NOT set |
| Simple cubic (1 atom) | Any SC structure | ❌ Do NOT set |

**How to check**: Count atoms in the unit cell and examine lattice vectors.
- FCC with 1 atom + non-orthogonal vectors → already primitive → no PRIMITIVE_AXES
- FCC with 4 atoms + cubic orthogonal vectors → conventional → needs PRIMITIVE_AXES

**If unsure**: Omit PRIMITIVE_AXES entirely. phonopy will use the cell as-is (with primitive_matrix = identity from phonopy_disp.yaml). The band path may use the full BZ of the input cell, which is correct but gives a denser path.

### DFT Property Defaults (Auto-applied)

When `interaction.type` is `abacus` or `vasp`, the skill auto-applies smaller supercells:

| Property | LAMMPS Default | DFT Default | Reason |
|----------|---------------|-------------|--------|
| phonon supercell | 3×3×3 (27 atoms) | **2×2×2 (8 atoms)** | 3x fewer SCF tasks |
| gruneisen volumes | 5 points | **3 points** | 2x fewer |
| vacancy supercell | 3×3×3 | **2×2×2** | Fewer atoms |
| surface max_miller | 2 | **1** | Fewer surfaces |

---

## 3. VASP (DFT)

> ⚠️ **Commercial software**: VASP requires a valid license. Never assume VASP is available. Always confirm with user before proceeding.

### Basic Configuration

```json
{
    "interaction": {
        "type": "vasp",
        "incar": "INCAR",
        "potcar_prefix": ".",
        "potcars": {
            "Mo": "POTCAR_Mo",
            "Al": "POTCAR_Al",
            "Fe": "POTCAR_Fe"
        }
    }
}
```

### Required Files

| File | Description | User Responsibility |
|------|-------------|-------------------|
| INCAR | VASP input parameters | Provided or generated; **must include `KSPACING`** |
| POTCAR | Pseudopotentials | **User must provide** (license-restricted) |
| KPOINTS | K-mesh | **Auto-generated by APEX** from `KSPACING` (+ `KGAMMA`); do not hand-author |

### VASP k-spacing constraints (REQUIRED)

APEX **requires** `KSPACING` in `INCAR` (or `cal_setting.kspacing`). At make time it:
1. Reads `KSPACING` / `KGAMMA` from INCAR
2. Builds a Monkhorst/Gamma mesh from the POSCAR reciprocal lattice
3. Writes per-task `KPOINTS`

Missing `KSPACING` → `RuntimeError: KSPACING must be given in INCAR`.

| Tag | Required? | Default / recommendation | Notes |
|-----|-----------|--------------------------|-------|
| `KSPACING` | **Yes** | `0.1`–`0.2` (Å⁻¹) for screening | Smaller → denser mesh, higher cost |
| `KGAMMA` | Strongly recommended | `True` (Gamma) or `False` (MP) | Controls mesh centering |

Do **not** ship a static `KPOINTS` file expecting APEX to use it as the primary control — spacing drives generation. For `elastic`, APEX regenerates one shared `KPOINTS` from the undeformed cell and symlinks it into every deformation task.

### INCAR Example (Relaxation)

```
SYSTEM = APEX relaxation
PREC = Accurate
ENCUT = 520
EDIFF = 1E-6
EDIFFG = -0.01
IBRION = 2
NSW = 200
ISIF = 3
ISMEAR = 1
SIGMA = 0.1
LREAL = Auto
KSPACING = 0.15
KGAMMA = True
```

### VASP global.json Settings (Bohrium / dflow)

The Bohrium VASP image needs the Intel oneAPI environment and an absolute
VASP binary path. Use symbolic values when planning resources:

```json
{
    "dflow_host": "https://workflows.deepmodeling.com",
    "k8s_api_server": "https://workflows.deepmodeling.com",
    "batch_type": "Bohrium",
    "context_type": "Bohrium",
    "vasp_image_name": "<USER-PROVIDED licensed VASP image — never invent a default>",
    "vasp_run_command": "bash -c \"source <ONEAPI_SETVARS> && ulimit -s unlimited && mpirun -n <RANKS> <ABSOLUTE_VASP_BINARY>\"",
    "scass_type": "<CPU_PROFILE_WITH_RANKS_CORES>",
    "group_size": 1,
    "pool_size": 1
}
```

> ⚠️ **Do not auto-fill `vasp_image_name`.** VASP is commercial. Resolve the image
> first via Bohrium private-image listing or a user-known authorized address:
>
> ```text
> # Preferred when MatMaster Bohrium tool is available:
> Bohrium(action="list_images", keyword="vasp")
>
> # Skill helper (same OpenAPI /openapi/v2/image/private):
> python scripts/list_bohrium_images.py --keyword vasp --require
> ```
>
> Then pass the approved URL to `generate_config.py create --vasp-image <url>`.
> If `list_images` returns nothing **and** the user does not know an authorized
> image address → **terminate** the VASP workflow (do not invent a public tag).

`vasp_run_command` constraints (typical Bohrium VASP layout; adjust binary path if the user image differs):

| Piece | Why |
|-------|-----|
| `source /opt/intel/oneapi/setvars.sh` | Loads Intel MPI / MKL |
| `ulimit -s unlimited` | Avoids stack overflow on large cells |
| Absolute `vasp_std` or `vasp_gam` path | PATH lookup is unreliable |
| `mpirun -n <RANKS>` | `RANKS` must equal the CPU count encoded by `scass_type` |

Local/debug Shell jobs may use a simpler command only when the host already has VASP + MPI on PATH; for Bohrium use the template above. `generate_config.py` emits the run_command template for `--backend vasp` but leaves `vasp_image_name` unset.

### VASP executable and parallel guards

The validator reads the run-command template, `scass_type`, representative
generated KPOINTS, and the INCAR parallel tags together. At runtime APEX reads
the actual `KPOINTS` in every task and selects the executable independently:

- Gamma-centered `1x1x1` always runs with `vasp_gam`; every other grid runs
  with `vasp_std`. The rule applies to relaxation and every property, not only
  Gamma/GammaSurface.
- `KGAMMA=True` only selects centering; it does not prove that the grid has one
  point. The generated task `KPOINTS` is authoritative.
- `vasp_run_command` may name either `vasp_std` or `vasp_gam`; APEX derives
  both sibling command variants and chooses after task generation.
- Any representative task that resolves to `vasp_gam` requires `KPAR=1`.
- Let `R` be MPI ranks and `K` be `KPAR` (default `1`). `K` must divide `R`.
- If `NCORE=C` is set, `C` must be a positive integer and divide `R/K`.
- `NCORE` and `NPAR` must not both be set. Every explicit `NPAR` and `KPAR`
  value must also be a positive integer.
- Missing `NCORE` is a warning, because the best value depends on the licensed
  executable, CPU profile, and system size.

The automatic selector uses `vasp_std` whenever the generated grid has more
than one k-point or is Monkhorst-Pack centered.

### VASP POTCAR Handling

APEX concatenates files at:

```text
os.path.join(potcar_prefix, potcars[element])   # must be a readable FILE
```

Source potpaw libraries often look like:

```text
/path/to/POTCAR_LIBRARY/
├── Mo_sv/POTCAR
├── Al/POTCAR
├── Fe_pv/POTCAR
└── ...
```

For Bohrium upload, **copy flat files into the job root** and use:

```json
"potcar_prefix": ".",
"potcars": {
    "Mo": "POTCAR_Mo",
    "Al": "POTCAR_Al",
    "Fe": "POTCAR_Fe"
}
```

(`generate_config.py create` does this staging automatically. Nested
`Ti_pv/POTCAR` under `potcar_prefix: "."` is **not** reliably uploaded.)

### Agent check (mandatory when user supplies a POTCAR path)

**Critical:** absolute host paths (e.g. `/share/PAW_PBE`) work only on that
machine. After Bohrium/dflow upload they vanish →
`FileNotFoundError: '/share/PAW_PBE/Ti_pv/POTCAR'`.

Before submit:

1. Confirm the user library exists and is readable locally.
2. For each element, confirm a readable POTCAR file.
3. Stage into the job root as `POTCAR_<Element>` (via create or `cp`/`mv`) and
   set `"potcar_prefix": "."`. Never ship absolute `/share/...`.
4. Confirm the job directory contains those flat POTCAR files before upload.
5. On failure: tell the user the path is unusable and ask for the correct
   library. Do not guess.

`scripts/validate_inputs.py` checks that staged relative POTCAR files exist.

---

## Backend Selection Guide

| Scenario | Recommended Backend | Rationale |
|----------|-------------------|-----------|
| Quick screening of many compositions | LAMMPS + DeePMD/MACE | Fast, GPU-accelerated |
| High-accuracy single material | VASP or ABACUS | Full DFT |
| High-entropy alloy (large supercell) | LAMMPS + MLIP | Scalable to 1000+ atoms |
| Finite-T properties | LAMMPS (required) | NPT MD required |
| Phonon (quick) | LAMMPS + phonoLAMMPS | Direct force constants |
| Phonon (accurate) | ABACUS/VASP | DFPT or finite differences |
| Surface/defect (accurate) | ABACUS/VASP | DFT accuracy needed |

---

## Machine Type Recommendations

| Workload | Bohrium Machine | Rationale |
|----------|----------------|-----------|
| LAMMPS + GPU potential (DeePMD/MACE/NEP) | `c16_m120_1 * NVIDIA L20` | Validated sm89 runtime; greater resource availability |
| LAMMPS + CPU potential (EAM/MEAM/SNAP) | `c16_m32_cpu` | CPU sufficient |
| ABACUS DFT (small cell <50 atoms) | `c16_m32_cpu` | 8 MPI ranks |
| ABACUS DFT (large cell 50-200 atoms) | `c32_m128_cpu` | 16-32 MPI ranks |
| VASP DFT | User choice | Depends on system size |
| Finite-T MD (long runs) | `c16_m120_1 * NVIDIA L20` | Long MD = GPU beneficial; validated sm89 runtime |
