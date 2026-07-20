# APEX LAMMPS Potential Types — Detailed Reference

## Overview

APEX supports 10 LAMMPS potential types through the `interaction.type` field. Each maps to a specific LAMMPS `pair_style` and requires corresponding model/parameter files.

---

## Machine Learning Potentials (GPU-recommended)

### 1. DeePMD (`deepmd`)

**pair_style**: `deepmd`

```json
{
    "type": "deepmd",
    "model": "frozen_model.pb",
    "type_map": "auto"
}
```

**Model files**: `.pb` (frozen graph) or `.pth` (PyTorch)
**Notes**:
- Most widely used MLIP in APEX workflows
- GPU strongly recommended for large systems
- APEX infers the local element ordering from the submitted structure
- Supports model deviation with multiple models: `"model": "model_0.pb model_1.pb"`

### 2. MACE (`mace`)

**pair_style**: `mace no_domain_decomposition`

```json
{
    "type": "mace",
    "model": "MACE_model.model",
    "type_map": "auto"
}
```

**Model files**: `.model` (MACE checkpoint)
**Notes**:
- `no_domain_decomposition` is required (single GPU)
- Excellent for multi-element systems
- GPU recommended

### 3. NEP (`nep`)

**pair_style**: `nep`

```json
{
    "type": "nep",
    "model": "nep.txt",
    "type_map": "auto"
}
```

**Model files**: `nep.txt` (NEP parameter file)
**Notes**:
- Neuroevolution potential from GPUMD ecosystem
- Very fast on GPU
- Compact model files

### 4. GAP (`gap`)

**pair_style**: `quip`

```json
{
    "type": "gap",
    "model": "GAP.xml",
    "type_map": "auto"
}
```

**Model files**: `.xml` (GAP descriptor file) + associated sparse point files
**Notes**:
- Gaussian Approximation Potential
- CPU-based (no GPU acceleration in LAMMPS)
- Can be slow for large systems
- Requires QUIP compiled with LAMMPS

### 5. SNAP (`snap`)

**pair_style**: `snap`

```json
{
    "type": "snap",
    "model": "W.snapcoeff W.snapparam",
    "type_map": "auto"
}
```

**Model files**: `.snapcoeff` + `.snapparam`
**Notes**:
- Spectral Neighbor Analysis Potential
- CPU-based
- Good for single-element systems

### 6. RANN (`rann`)

**pair_style**: `rann`

```json
{
    "type": "rann",
    "model": "Fe.nn",
    "type_map": "auto"
}
```

**Model files**: `.nn` (neural network file)
**Notes**:
- Rapid Artificial Neural Network potential
- CPU-based

---

## Classical Potentials (CPU)

### 7. EAM Alloy (`eam_alloy`)

**pair_style**: `eam/alloy`

```json
{
    "type": "eam_alloy",
    "model": "AlCu.eam.alloy",
    "type_map": "auto"
}
```

**Model files**: `.eam.alloy` (DYNAMO setfl format)
**Notes**:
- Standard embedded atom method for alloys
- Very fast, CPU sufficient
- Large library of potentials available (e.g., from NIST)

### 8. EAM Finnis-Sinclair (`eam_fs`)

**pair_style**: `eam/fs`

```json
{
    "type": "eam_fs",
    "model": "Fe.eam.fs",
    "type_map": "auto"
}
```

**Model files**: `.eam.fs` (Finnis-Sinclair format)
**Notes**:
- Variant of EAM with element-pair-dependent density functions
- Fast, CPU sufficient

### 9. MEAM (`meam`)

**pair_style**: `meam`

```json
{
    "type": "meam",
    "model": "library.meam TiAl.meam",
    "type_map": "auto"
}
```

**Model files**: Two files — library file + parameter file (space-separated in `model`)
**Notes**:
- Modified EAM with angular-dependent terms
- Better for covalent/metallic bonding mix
- CPU-based

### 10. MEAM Spline (`meam_spline`)

**pair_style**: `meam/spline`

```json
{
    "type": "meam_spline",
    "model": "Ti.meam.spline",
    "type_map": "auto"
}
```

**Model files**: `.meam.spline`
**Notes**:
- Spline-based MEAM (no library file needed)
- More flexible functional form
- CPU-based

---

## type_map Convention

Use automatic inference for LAMMPS interactions:

```json
"type_map": "auto"
```

At submission time, APEX reads the first matching structure, creates a
zero-based contiguous element map, and writes the resolved dictionary back to
`param.json`. Do not derive values from atomic numbers or a model's internal
type indices.

Use a manual dictionary only when the user explicitly needs a fixed custom
ordering. If multiple matched structures contain different element sets,
split them into compatible submissions or provide and verify one complete
manual map.

---

## Image Selection Guide

| Potential Type | Recommended Image | GPU? |
|---------------|-------------------|------|
| deepmd | `registry.dp.tech/dptech/dp/native/prod-397637/apex-flow:1.3.0.post` | Yes |
| mace | `registry.dp.tech/dptech/dp/native/prod-397637/apex-flow:1.3.0.post` | Yes |
| nep | `registry.dp.tech/dptech/dp/native/prod-397637/apex-flow:1.3.0.post` | Yes |
| gap | `registry.dp.tech/dptech/dp/native/prod-397637/apex-flow:1.3.0.post` | No (CPU) |
| snap | `registry.dp.tech/dptech/dp/native/prod-397637/apex-flow:1.3.0.post` | No |
| rann | `registry.dp.tech/dptech/dp/native/prod-397637/apex-flow:1.3.0.post` | No |
| eam_alloy | `registry.dp.tech/dptech/dp/native/prod-397637/apex-flow:1.3.0.post` | No |
| eam_fs | `registry.dp.tech/dptech/dp/native/prod-397637/apex-flow:1.3.0.post` | No |
| meam | `registry.dp.tech/dptech/dp/native/prod-397637/apex-flow:1.3.0.post` | No |
| meam_spline | `registry.dp.tech/dptech/dp/native/prod-397637/apex-flow:1.3.0.post` | No |

All potential types are supported by the unified APEX image. The APEX 1.3.0 image ships LAMMPS compiled with DeePMD, MACE, NEP, and standard LAMMPS potentials.

---

## Troubleshooting

| Error | Cause | Fix |
|-------|-------|-----|
| `pair_style not found` | LAMMPS not compiled with required package | Use APEX official image |
| `type_map` inference failure | Structure file is missing or matched structures use incompatible element sets | Fix `structures` paths or provide one verified manual map |
| `model file not found` | File not in job directory | Ensure model file is copied to submission dir |
| `GPU not available` | Running GPU potential on CPU node | Switch to GPU machine type |
| `segfault in mace` | Domain decomposition issue | `no_domain_decomposition` is already set by APEX |
