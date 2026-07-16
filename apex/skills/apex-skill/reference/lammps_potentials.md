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
    "type_map": {"Mo": 0, "W": 1}
}
```

**Model files**: `.pb` (frozen graph) or `.pth` (PyTorch)
**Notes**:
- Most widely used MLIP in APEX workflows
- GPU strongly recommended for large systems
- `type_map` indices must match training order
- Supports model deviation with multiple models: `"model": "model_0.pb model_1.pb"`

### 2. MACE (`mace`)

**pair_style**: `mace no_domain_decomposition`

```json
{
    "type": "mace",
    "model": "MACE_model.model",
    "type_map": {"Al": 0, "Cu": 1}
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
    "type_map": {"W": 0, "Re": 1}
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
    "type_map": {"Si": 0}
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
    "type_map": {"W": 0}
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
    "type_map": {"Fe": 0}
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
    "type_map": {"Al": 0, "Cu": 1}
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
    "type_map": {"Fe": 0}
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
    "type_map": {"Ti": 0, "Al": 1}
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
    "type_map": {"Ti": 0}
}
```

**Model files**: `.meam.spline`
**Notes**:
- Spline-based MEAM (no library file needed)
- More flexible functional form
- CPU-based

---

## type_map Convention

The `type_map` dictionary maps element symbols to integer indices:

```json
"type_map": {"Element1": 0, "Element2": 1, "Element3": 2}
```

**Rules**:
- Indices must start from 0
- Order must match the model's training type order
- For multi-element POSCAR, the order in POSCAR species line must be consistent
- For DeePMD: matches `type_map` in training `input.json`
- For MACE: matches element ordering during training
- For classical potentials: matches element order in potential file header

---

## Image Selection Guide

| Potential Type | Recommended Image | GPU? |
|---------------|-------------------|------|
| deepmd | `registry.dp.tech/dptech/dp/native/prod-397637/apex:1.3.0` | Yes |
| mace | `registry.dp.tech/dptech/dp/native/prod-397637/apex:1.3.0` | Yes |
| nep | `registry.dp.tech/dptech/dp/native/prod-397637/apex:1.3.0` | Yes |
| gap | `registry.dp.tech/dptech/dp/native/prod-397637/apex:1.3.0` | No (CPU) |
| snap | `registry.dp.tech/dptech/dp/native/prod-397637/apex:1.3.0` | No |
| rann | `registry.dp.tech/dptech/dp/native/prod-397637/apex:1.3.0` | No |
| eam_alloy | `registry.dp.tech/dptech/dp/native/prod-397637/apex:1.3.0` | No |
| eam_fs | `registry.dp.tech/dptech/dp/native/prod-397637/apex:1.3.0` | No |
| meam | `registry.dp.tech/dptech/dp/native/prod-397637/apex:1.3.0` | No |
| meam_spline | `registry.dp.tech/dptech/dp/native/prod-397637/apex:1.3.0` | No |

All potential types are supported by the unified APEX image. The APEX 1.3.0 image ships LAMMPS compiled with DeePMD, MACE, NEP, and standard LAMMPS potentials.

---

## Troubleshooting

| Error | Cause | Fix |
|-------|-------|-----|
| `pair_style not found` | LAMMPS not compiled with required package | Use APEX official image |
| `type_map mismatch` | Element order differs from model training | Check training config for correct order |
| `model file not found` | File not in job directory | Ensure model file is copied to submission dir |
| `GPU not available` | Running GPU potential on CPU node | Switch to GPU machine type |
| `segfault in mace` | Domain decomposition issue | `no_domain_decomposition` is already set by APEX |
