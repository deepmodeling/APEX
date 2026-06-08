# APEX LAMMPS Tutorial - Streamlined Examples Guide

This guide demonstrates how to use APEX for alloy property calculations using LAMMPS, organized into three progressive tutorial levels.

## Table of Contents

- [Prerequisites](#prerequisites)
- [Tutorial 1: Quick Start Guide](#tutorial-1-quick-start-guide)
  - [Example 1.1: Mo - Joint Calculation](#example-11-mo---joint-calculation)
- [Tutorial 2: Submission Methods](#tutorial-2-submission-methods)
  - [Example 2.1: Bohrium Cloud Submission](#example-21-bohrium-cloud-submission)
  - [Example 2.2: Local Debug Mode](#example-22-local-debug-mode)
  - [Example 2.3: SLURM HPC Submission](#example-23-slurm-hpc-submission)
- [Tutorial 3: Potentials & Properties](#tutorial-3-potentials--properties)
  - [Example 3.1: Different Potentials and Various Properties](#example-31-different-potentials-and-various-properties)
- [Reference Resources](#reference-resources)

---

## Prerequisites

- APEX installed (`pip install apex-flow` or `git clone https://github.com/deepmodeling/APEX.git && cd APEX && pip install .`)
- LAMMPS installed (for local debug mode)
- Bohrium account (for cloud submission)
- Basic knowledge of computational materials science

---

## Tutorial 1: Quick Start Guide

### Overview

This section introduces APEX workflows through a practical quick-start example demonstrating a **joint calculation workflow** that combines structure relaxation and property calculations in a single submission.

---

### Example 1.1: Mo - Joint Calculation

**Path**: `lammps_tutorial1_quick_start/lammps_example1.1_Mo/`

#### Purpose

Demonstrates a **joint calculation workflow** for molybdenum (Mo), combining structure relaxation and property calculations in a single submission for rapid property exploration.

#### Directory Structure

```
lammps_example1.1_Mo/
├── confs/
│   └── std-bcc/
│       └── POSCAR              # Mo structure (BCC phase)
├── param_joint.json            # Joint calculation parameters
├── global_bohrium.json         # Bohrium cloud configuration
└── frozen_model.pb             # Deep Potential model
```

#### Configuration Files

**`param_joint.json`**: Defines both relaxation and property parameters
- `structures`: Path to initial structures
- `interaction`: Deep Potential model specification
- `relaxation`: Structure optimization settings (tolerance, iterations)
- `properties`: Property calculations to perform (EOS, elastic, etc.)

**`global_bohrium.json`**: Cloud platform configuration
- Server URL and authentication
- Computational resources (GPU/CPU)
- Software images for execution

#### Submission

```bash
apex submit param_joint.json -c global_bohrium.json
```

**To run in background**:
```bash
nohup apex submit param_joint.json -c global_bohrium.json > apex.log 2>&1 &
```

#### Result

Results automatically saved to `all_result.json` containing both relaxation and property data.

#### Visualization
In the directory contanting the `all_result.json` file, run the following command to generate the visualization report:
```bash
apex report
```

---

## Tutorial 2: Submission Methods

APEX supports multiple submission methods for different computational environments.

---

### Example 2.1: Bohrium Cloud Submission

**Path**: `lammps_tutorial2_submission_methods/lammps_example2.1_bohrium/`

#### Introduction

Bohrium platform provides pre-configured environments, automated scheduling, and visual monitoring.

#### Configuration: `global_bohrium.json`

```json
{
    "dflow_host": "https://workflows.deepmodeling.com",
    "k8s_api_server": "https://workflows.deepmodeling.com",
    "batch_type": "Bohrium",
    "context_type": "Bohrium",
    "email": "your_email@example.com",
    "password": "your_password",
    "program_id": 12345,
    "apex_image_name": "registry.dp.tech/dptech/dp/native/prod-397637/apex:1.3.0",
    "lammps_image_name": "registry.dp.tech/dptech/prod-11045/deepmdkit-phonolammps:3.1.1",
    "lammps_run_command": "lmp -in in.lammps",
    "scass_type": "c8_m31_1 * NVIDIA T4"
}
```

#### Key Parameters

- **Authentication**: Email, password, and Bohrium program ID
- **Images**: APEX and LAMMPS Docker images (check [Bohrium Registry](https://www.bohrium.com/web-images/public))
- **Resources**: `scass_type` specifies CPU cores, memory, and GPU (check [Bohrium Profiler](https://www.bohrium.com/profiler))

#### Submission

```bash
apex submit param_relax.json -c global_bohrium.json
```

#### Monitoring

- Dashboard: check [Web Argo](https://workflows.deepmodeling.com)


---

### Example 2.2: Local Debug Mode

**Path**: `lammps_tutorial2_submission_methods/lammps_example2.2_local/`

#### Introduction

Run LAMMPS directly on your local machine without requiring a cluster or cloud account. Ideal for quick testing and small-scale calculations.

#### Prerequisites

- LAMMPS executable installed and in PATH
- APEX installed in local Python

#### Configuration: `global_local_debug.json`

```json
{
    "run_command": "lmp -in in.lammps",
    "context_type": "Local",
    "batch_type": "Shell"
}
```

#### Directory Structure

```
lammps_example2.2_local/
├── confs/
│   └── std-fcc/
│       └── POSCAR
├── param_joint.json            # or separate relax/props files
├── param_props.json            # property calculation parameters
├── param_relax.json            # relaxation parameters
├── global_local_debug.json     # Local configuration
└── Al.eam.alloy                # EAM potential file
```

#### Submission with Debug Flag

```bash
apex submit -d param_joint.json -c global_local_debug.json
```

**Key points**:
- `-d` flag enables debug mode (no containerization)
- Output streams directly to terminal in real-time
- Suitable for parameter verification and algorithm testing

---

### Example 2.3: SLURM HPC Submission

**Path**: `lammps_tutorial2_submission_methods/lammps_example2.3_slurm/`

#### Introduction

Submit jobs to remote HPC clusters managed by SLURM scheduler.

#### Configuration: `global_hpc.json`

```json
{
    "run_command":"mpirun -np 4 lmp -in in.lammps",
    "context_type": "Local",
    "machine":{
      "batch_type": "Slurm",
      "context_type": "Local",
      "local_root" : "./",
      "remote_root": "./",
      "clean_asynchronously": true
    },
    "resources":{
        "number_node": 1,
        "cpu_per_node": 4,
        "gpu_per_node": 0,
        "group_size": 1,
        "module_list": ["deepmd-kit/3.1.0/cpu_binary_release"],
        "custom_flags": [
            "#SBATCH --partition=xlong",
            "#SBATCH --ntasks=4",
            "#SBATCH --ntasks-per-core=1",
            "#SBATCH --cpus-per-task=1",
            "#SBATCH --mem=10G",
            "#SBATCH --nodes=1",
            "#SBATCH --time=1-00:00:00"
            ]
       }
}
```

#### Submission

```bash
apex submit -d param_joint.json -c global_hpc.json
```
**To run in background**:
```bash
nohup apex submit -d param_joint.json -c global_hpc.json > apex.log 2>&1 &
```

#### Monitoring

Monitor on the remote HPC system:
```bash
squeue -u your_username
```

---

## Tutorial 3: Potentials & Properties

### Example 3.1: Different Potentials and Various Properties

**Path**: `lammps_tutorial3_potentials_and_properties/lammps_example3.1_potentials_and_properties/`

#### Purpose

Demonstrates calculations using different interatomic potentials (Deep Potential, EAM, MEAM) and various property types.

#### Directory Structure

```
lammps_example3.1_potentials_and_properties/
├── confs/
│   └── std-fcc/
│       └── POSCAR
├── Al.eam.alloy                # EAM potential
├── Al.meam                     # MEAM potential
├── library.meam                # MEAM library
├── param_relax_eam.json        # EAM relaxation parameters
├── param_relax_meam.json       # MEAM relaxation parameters
├── param_props_meam.json       # MEAM property parameters
├── global_local_debug.json     # Local debug configuration
└── support_potentials.txt      # List of supported potentials
```

#### Supported Potentials

APEX supports:
```
['deepmd', 'eam_alloy', 'meam', 'eam_fs', 'meam_spline', 'snap', 'gap', 'rann', 'mace']
```

#### EAM Potential Configuration

**`param_relax_eam.json`**:

```json
{
    "structures": ["confs/std-*"],
    "interaction": {
        "type": "eam_alloy",
        "model": "Al.eam.alloy",
        "type_map": {"Al": 0}
    },
    "relaxation": {
        "cal_setting": {
            "etol": 0,
            "ftol": 1e-10
        }
    }
}
```

#### MEAM Potential Configuration

**`param_relax_meam.json`**:

```json
{
    "structures": ["confs/std-*"],
    "interaction": {
        "type": "meam",
        "model": ["library.meam", "Al.meam"],
        "type_map": {"Al": 0}
    },
    "relaxation": {
        "cal_setting": {
            "etol": 0,
            "ftol": 1e-10
        }
    }
}
```

**Key Difference**: MEAM requires two files, library file and element-specific file.

#### Property Calculations Configuration

**`param_props_meam.json`**:

```json
{
    "structures": ["confs/std-*"],
    "interaction": {
        "type": "meam",
        "model": ["library.meam", "Al.meam"],
        "type_map": {"Al": 0}
    },
    "properties": [
        {
            "type": "eos",
            "skip": false,
            "vol_start": 0.6,
            "vol_end": 1.4,
            "vol_step": 0.1
        },
        {
            "type": "cohesive",
            "latt_start": 0.6,
            "latt_end": 1.4,
            "latt_step": 0.1
        },
        {
            "type": "decohesive",
            "min_slab_size": 15,
            "max_vacuum_size": 10,
            "vacuum_size_step": 2,
            "miller_index": [0, 0, 1]
        },
        {
            "type": "finite_t_latt",
            "supercell_size": [2, 2, 2],
            "cal_setting": {
            "temperature": [200, 400, 600, 800],
            "equi_step": 20000,
            "N_every": 100,
            "N_repeat": 10,
            "N_freq": 2000,
            "ave_step": 20000,
            "timestep": 0.001,
            "tdamp": 0.1,
            "pdamp": 1.0}
        },
        {
            "type": "elastic",
            "skip": false
        },
        {
            "type": "surface",
            "skip": true
        },
        {
            "type": "vacancy",
            "skip": true,
            "supercell": [2, 2, 2]
        },
        {
            "type": "interstitial",
            "skip": true,
            "insert_ele": ["Al"]
        },
        {
            "type": "gamma",
            "skip": true,
            "plane_miller": [1, 1, 1],
            "slip_direction": [1, 1, -2]
        }
    ]
}
```

#### Supported Property Types

| Property | Description | Key Parameters |
|----------|-------------|-----------------|
| **eos** | Equation of State | vol_start, vol_end, vol_step |
| **cohesive** | Cohesive energy line | latt_start, latt_end, latt_step |
| **decohesive** | Decohesive energy line | min_slab_size, miller_index, max_vacuum_size, vacuum_size_step | 
| **elastic** | Elastic Constants | norm_deform, shear_deform |
| **surface** | Surface Energy | min_slab_size, max_miller |
| **vacancy** | Vacancy Formation | supercell |
| **interstitial** | Interstitial Formation | insert_ele, supercell |
| **gamma** | Stacking Fault Energy | plane_miller, slip_direction |
| **finite_t_latt** | Lattice parameters at finite temperatures | supercell_size |
| **phonon** | Phonon Spectra | supercell_size, MESH |

#### Workflow Execution


**Step 1: Relaxation with MEAM**

Run MEAM relaxation:
```bash
apex submit -d param_relax_meam.json -c global_local_debug.json
```

**Step 2: Property Calculation with MEAM**

Then compute properties:
```bash
apex submit -d param_props_meam.json -c global_local_debug.json
```

#### Controlling Calculations

Use `"skip": true/false` to enable/disable properties:
- `"skip": false`: Calculate property
- `"skip": true`: Skip property

Simply delete unused property blocks to simplify the configuration.

---

## Reference Resources

- [APEX GitHub Repository](https://github.com/deepmodeling/APEX)
- [APEX Publication](https://doi.org/10.1038/s41524-025-01580-y)
- [Bohrium Platform](https://bohrium.dp.tech/)
- [Bohrium Workflow Dashboard](https://workflows.deepmodeling.com)
- [Bohrium Public Image Registry](https://bohrium.dp.tech/apps/web-images)
- [DPDispatcher Documentation](https://docs.deepmodeling.com/projects/dpdispatcher)
- [Dflow Framework](https://github.com/deepmodeling/dflow)
- [Materials Project](https://materialsproject.org/)

---

**Last Updated**: December 2025  
**APEX Version**: 1.3+  
**Document Status**: Streamlined Tutorial Series
