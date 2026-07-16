# APEX Calculator Backends — Configuration Guide

## Overview

APEX supports three calculator **backends**: LAMMPS, ABACUS, and VASP.
Each requires specific configuration in `param.json` under the `"interaction"` key.

> APEX **backend** = calculator (`lammps` / `abacus` / `vasp`).
> “DPA-2” / “DPA_alloy” / “DPA-3” are DeePMD **model files** under LAMMPS (`interaction.type: deepmd`).
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
| `deepmd` | `deepmd` | `.pb` or `.pth` | DeePMD-kit model |
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
        "model": "frozen_model.pb",
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

### ⚠️ LAMMPS Image Version

Default image: `registry.dp.tech/dptech/deepmd-kit:3.1.3`

> ⚠️ **Do NOT use `deepmd-kit:3.1.1`** — it has a known segfault bug when handling triclinic cells (non-orthogonal boxes). Use `3.1.3` or later.

### DPA-2 Multi-Head Model Preparation

DPA-2 pretrained models (`.pt` files, e.g. `dpa-2.4-7M.pt`) are **multi-head** and **cannot be used directly** as `interaction.model` in APEX. You must first freeze the model with a specific task head.

**Freeze command:**
```bash
dp --pt freeze -c dpa-2.4-7M.pt -o DPA2_alloy.pth --head Domains_Alloy
```

- `-c`: Input multi-head `.pt` checkpoint
- `-o`: Output frozen model (`.pth` for PyTorch, `.pb` for TensorFlow)
- `--head`: Which task head to extract

The **frozen** `.pth` or `.pb` file is what goes into `interaction.model`:
```json
{
    "interaction": {
        "type": "deepmd",
        "model": "DPA2_alloy.pth",
        "type_map": "auto"
    }
}
```

**Available heads** depend on the specific DPA-2 release. Common ones include:
- `Domains_Alloy` — general alloy energetics
- Check the model documentation / release notes for the full list of available heads.

> 💡 If you pass an unfrozen multi-head `.pt` file directly to LAMMPS, it will error with a message about missing head selection.

### Built-in DPA models (`models/`)

**Priority:** use bundled **frozen** models first. The skill zip only includes
small `.pb` files (not multi-head `.pt` / DPA3). Details: `models/README.md`.

| Path | Format | Use |
|------|--------|-----|
| `models/DPA2/DPA2.pb` | Frozen TF (~6MB, ready) | Default DPA-2 for APEX |
| `models/DPA_alloy/DPA_alloy.pb` | Frozen TF (~6MB, ready) | Prefer for alloys / HEA |

When the user requests DPA-2 without a custom model, **copy** `models/DPA2/DPA2.pb`
into the task directory (for alloys prefer `models/DPA_alloy/DPA_alloy.pb`) and set:

```json
{
    "interaction": {
        "type": "deepmd",
        "model": "DPA2.pb",
        "type_map": "auto"
    }
}
```

Large checkpoints are optional and downloaded only on explicit request:

```bash
python scripts/fetch_models.py --dpa2-pt   # ~76MB multi-head
python scripts/fetch_models.py --dpa3      # ~62MB, then freeze a head
```

> Use `"type_map": "auto"` by default. APEX infers the local contiguous type
> mapping from the structure; do not copy atomic-number or model-internal indices.
> Multi-head `.pt` files **must** be frozen (`dp --pt freeze ... --head <HEAD>`)
> before use as `interaction.model`.

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
| `kspacing` | 0.20 | 0.20 | K-point spacing (1/Bohr) |
| `scf_nmax` | **200** | 100 | Max SCF iterations |
| `relax_nmax` | **100** | 50 | Max relaxation steps |

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
        "potcar_prefix": "/path/to/POTCAR_LIBRARY",
        "potcars": {
            "Mo": "Mo_sv",
            "Al": "Al",
            "Fe": "Fe_pv"
        }
    }
}
```

### Required Files

| File | Description | User Responsibility |
|------|-------------|-------------------|
| INCAR | VASP input parameters | Provided or generated |
| POTCAR | Pseudopotentials | **User must provide** (license-restricted) |
| KPOINTS | K-mesh | Auto-generated by APEX or provided |

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
```

### VASP global.json Settings

```json
{
    "machine": {
        "batch_type": "Shell",
        "context_type": "LazyLocalContext",
        "local_root": "."
    },
    "resources": {
        "number_node": 1,
        "cpu_per_node": 16,
        "group_size": 1
    },
    "run_command": "mpirun -n 16 vasp_std"
}
```

### VASP POTCAR Handling

APEX expects POTCAR to be assembled from `potcar_prefix` using the element names in `potcars`. The directory structure should be:
```
/path/to/POTCAR_LIBRARY/
├── Mo_sv/POTCAR
├── Al/POTCAR
├── Fe_pv/POTCAR
└── ...
```

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
| LAMMPS + GPU potential (DeePMD/MACE/NEP) | `c8_m31_1 * NVIDIA T4` | GPU acceleration |
| LAMMPS + CPU potential (EAM/MEAM/SNAP) | `c16_m32_cpu` | CPU sufficient |
| ABACUS DFT (small cell <50 atoms) | `c16_m32_cpu` | 8 MPI ranks |
| ABACUS DFT (large cell 50-200 atoms) | `c32_m128_cpu` | 16-32 MPI ranks |
| VASP DFT | User choice | Depends on system size |
| Finite-T MD (long runs) | `c8_m31_1 * NVIDIA T4` | Long MD = GPU beneficial |
