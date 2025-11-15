# APEX Tutorial

This tutorial demonstrates how to use APEX (Alloy Property EXplorer) for alloy property calculations, showcasing different task submission methods and workflows through practical examples.

## Table of Contents

- [Prerequisites](#prerequisites)
- [Related Resources](#related-resources)
- [Tutorial 1: Quick Start Guide](#tutorial-1-quick-start-guide)
  - [Example 1.1: Mo - Joint Calculation Workflow](#example-11-mo---joint-calculation-workflow)
  - [Example 1.2: Al - Sequential Calculation Workflow](#example-12-al---sequential-calculation-workflow)
- [Tutorial 2: Different Task Submission Methods](#tutorial-2-different-task-submission-methods)
  - [Example 2.1: Bohrium Cloud Platform Submission](#example-21-bohrium-cloud-platform-submission)
  - [Example 2.3: Local Debug Mode](#example-23-local-debug-mode)
- [Tutorial 3: Potentials and Properties](#tutorial-3-potentials-and-properties)
  - [Example 3.1: Using Different Potentials](#example-31-using-different-potentials)
  - [Example 3.2: Computing Different Properties](#example-32-computing-different-properties)
- [Reference Resources](#reference-resources)

---

## Prerequisites

- APEX and dependencies installed (`pip install apex-flow`)
- Basic knowledge of LAMMPS/VASP/ABACUS or similar computational tools
- Configured computational environment (Bohrium account, HPC cluster SSH access, or local LAMMPS installation)

---

## Related Resources

- [APEX GitHub Repository](https://github.com/deepmodeling/APEX)
- [APEX Official Documentation](https://github.com/deepmodeling/APEX/blob/master/README.md)
- [Bohrium Official Website](https://bohrium.dp.tech/)
- [Bohrium Public Image Registry](https://bohrium.dp.tech/apps/web-images)
- [DPDispatcher Documentation](https://docs.deepmodeling.com/projects/dpdispatcher)
- [Dflow Workflow Framework](https://github.com/deepmodeling/dflow)

---

## Tutorial 1: Quick Start Guide

### Overview

APEX workflows consist of two main types: **Structure Relaxation** and **Property Calculations**.
- **Relaxation**: Optimizes atomic structures to obtain stable crystal structures and related thermodynamic quantities
- **Properties**: Calculates various physical properties (elastic constants, surface energy, etc.) on given structures
- **Joint**: Performs both relaxation and property calculations simultaneously

This section presents two examples: Mo (molybdenum) joint calculation and Al (aluminum) sequential calculation.

---

### Example 1.1: Mo - Joint Calculation Workflow

**Example Path**: `lammps_tutorial1_quick_start/lammps_example1.1_Mo/`

#### Overview

In this example, we perform **joint calculation** on molybdenum (Mo) crystals, combining structure relaxation and property calculations. The terminal remains connected, receiving real-time calculation logs.

#### Directory Structure

```
lammps_example1.1_Mo/
├── confs/
│   └── POSCAR              # Mo initial structure file
├── param_joint.json        # Parameters for relaxation and property calculations
├── global_bohrium.json     # Bohrium cloud platform configuration
└── frozen_model.pb         # Deep Potential model file
```

#### Configuration Files Explanation

**`param_joint.json`** contains three sections:
- **structures**: Paths to initial structure files
- **interaction**: Computational model used (e.g., Deep Potential)
- **relaxation**: Structure relaxation parameters (tolerance, max iterations, etc.)
- **properties**: Property calculation parameters (EOS, elastic constants, etc.)

**`global_bohrium.json`** cloud platform configuration:
- Cloud service address and authentication credentials
- Computational resources (GPU/CPU configuration)
- Computational software image versions

#### Task Submission

Execute the following command to submit a joint calculation task:

```bash
apex submit param_joint.json -c global_bohrium.json
```

**Note**: This command keeps the terminal connected, displaying real-time calculation progress and logs. To run in the background, use the `nohup` command:

```bash
nohup apex submit param_joint.json -c global_bohrium.json > apex.log 2>&1 &
```

Where:
- `nohup`: Keeps the program running in the background even if the terminal closes
- `> apex.log 2>&1`: Redirects all output (stdout and stderr) to the `apex.log` file
- `&`: Places the process in the background

#### Result Retrieval

After computation completes, results are automatically saved to:
- `all_result.json`: Complete computation result summary
- Detailed result files for each property calculation

---

### Example 1.2: Al - Sequential Calculation Workflow

**Example Path**: `lammps_tutorial1_quick_start/lammps_example1.2_Al/`

#### Overview

This example performs **sequential calculation** on aluminum (Al), first relaxing the structure, then calculating properties. This approach is advantageous when computing new properties: no need to repeat relaxation, simply use the already-relaxed structure for new property calculations.

#### Directory Structure

```
lammps_example1.2_Al/
├── confs/
│   └── POSCAR              # Al initial structure file
├── param_relax.json        # Structure relaxation parameters
├── param_props.json        # Property calculation parameters
├── global_bohrium.json     # Bohrium cloud platform configuration
└── Al.eam.alloy            # EAM potential file
```

#### Configuration Files Explanation

**Parameter file types**:
- `param_relax.json`: Contains only **structures**, **interaction**, and **relaxation** fields
- `param_props.json`: Contains only **structures**, **interaction**, and **properties** fields
- `param_joint.json`: Contains all fields (not present in this example)

#### Sequential Calculation Workflow

**Step 1: Structure Relaxation**

First, submit the structure relaxation task:

```bash
apex submit param_relax.json -c global_bohrium.json
```

Wait for relaxation to complete. The terminal will display progress and completion messages.

**Step 2: Property Calculation**

After relaxation completes, APEX automatically saves the relaxed structure. Subsequently, submit the property calculation task:

```bash
apex submit param_props.json -c global_bohrium.json
```

APEX will automatically use the relaxed structure for property calculations.

**Important Note**: Must wait for the relaxation process to completely finish before submitting property calculations. Confirm relaxation completion by:
- Monitoring the work directory's result files
- Checking task status on [Bohrium Platform](https://workflows.deepmodeling.com)
- Using `apex list` or `apex get` commands to query status

#### Advantages and Use Cases

- **Parameter Scanning**: Fix structure, vary only property calculation parameters
- **New Property Computation**: Calculate new physical quantities based on relaxed structures without recalculating relaxation
- **Resource Optimization**: Separate different calculation stages for flexible resource allocation

---

## Tutorial 2: Different Task Submission Methods

APEX supports multiple task submission methods, adapting to different computational needs and environments.

### Example 2.1: Bohrium Cloud Platform Submission

**Example Path**: `lammps_tutorial2_submission_methods/lammps_example2.1_bohrium/`

#### Introduction

Bohrium is a cloud computing platform provided by the DeepModeling team, offering pre-configured computational environments, automated workflow scheduling, and comprehensive visual monitoring interfaces. This is the simplest and most feature-complete submission method.

#### Global Configuration File Example

Create a `global_bohrium.json` file:

```json
{
    "dflow_host": "https://workflows.deepmodeling.com",
    "k8s_api_server": "https://workflows.deepmodeling.com",
    "batch_type": "Bohrium",
    "context_type": "Bohrium",
    "email": "YOUREMAIL@abc.com",
    "password": "your_password",
    "program_id": 1234,
    "apex_image_name": "registry.dp.tech/dptech/prod-11045/apex-dependency:1.2.0",
    "lammps_image_name": "registry.dp.tech/dptech/prod-11045/deepmdkit-phonolammps:3.1.1",
    "lammps_run_command": "lmp -in in.lammps",
    "scass_type": "c8_m31_1 * NVIDIA T4"
}
```

#### Configuration Parameters Explanation

**Authentication and Server**:
- `dflow_host`: Dflow workflow engine URL
- `k8s_api_server`: Kubernetes API server URL
- `email` / `password`: Bohrium account credentials
- `program_id`: Project ID on Bohrium platform

**Images and Computational Environment**:
- `apex_image_name`: APEX dependency image used for make and post steps
- `lammps_image_name`: Image containing LAMMPS and related libraries
- `lammps_run_command`: LAMMPS execution command
- `scass_type`: Computational resource specification (CPU/GPU count, memory, etc.)

#### Image Version Selection

**LAMMPS Image Versions**:

Visit [Bohrium Public Image Registry](https://bohrium.dp.tech/apps/web-images) to view available LAMMPS/DeepMD versions, such as:
- `deepmdkit-phonolammps:3.1.1`: Includes DeepMD-kit 3.1.1 and LAMMPS with phonon support
- `deepmdkit-phonolammps:2.1.0`: Includes DeepMD-kit 2.1.0

**Computational Resource Types**:

Visit [Bohrium Resource Configuration Page](https://bohrium.dp.tech/) to view available `scass_type` options, common configurations include:
- `c8_m31_1 * NVIDIA T4`: 8-core CPU, 31GB memory, 1 NVIDIA T4 GPU
- `c4_m15_1 * NVIDIA V100`: 4-core CPU, 15GB memory, 1 NVIDIA V100 GPU
- `c16_m61`: 16-core CPU, 61GB memory (no GPU)

**APEX Image Versions**:

Select from [registry.dp.tech](https://registry.dp.tech) repository as needed, typically choosing a version matching the computational image.

#### Task Submission Commands

```bash
apex submit param_relax.json -c global_bohrium.json
```

Or submit multiple parameter files:

```bash
apex submit param_relax.json param_props.json -c global_bohrium.json
```

#### Monitoring and Management

- View task progress on [Bohrium Workflow Platform](https://workflows.deepmodeling.com) Web interface
- Use APEX commands to query status:
  ```bash
  apex list -c global_bohrium.json
  apex get -i <workflow_id> -c global_bohrium.json
  ```
- Results automatically download to local directory upon completion

---

### Example 2.3: Local Debug Mode

**Example Path**: `lammps_tutorial2_submission_methods/lammps_example2.3_debug/`

#### Introduction

For small-scale tasks or algorithm testing, run LAMMPS directly on your local system without requiring a cluster or cloud platform. This method is suitable for quick verification and small-scale computations.

**Prerequisites**:
- LAMMPS installed on local system
- LAMMPS executable available in PATH

#### Global Configuration File Example

Create a `global_local_debug.json` file:

```json
{
    "run_command": "lmp -in in.lammps",
    "context_type": "Local",
    "batch_type": "Shell",
    "machine": {
        "batch_type": "Shell",
        "context_type": "Local",
        "local_root": "./",
        "remote_root": "./"
    }
}
```

#### Configuration Parameters Explanation

- `context_type`: `Local` indicates local execution without remote connection
- `batch_type`: `Shell` indicates direct shell command execution
- `local_root`: Local working directory (typically `./`)
- `remote_root`: Same as `local_root` for local execution

#### Task Submission Commands

Use the `-d` (debug mode) flag:

```bash
apex submit -d param_relax.json -c global_local_debug.json
```

**Important Note**:
- `-d` flag enables debug mode, running **without containerization** directly in system Python
- APEX must be properly installed in current Python environment
- All dependencies (LAMMPS, model files, etc.) must be locally available

#### Command Line Output

In debug mode, calculation logs output in real-time to terminal, facilitating progress observation and debugging.

#### Application Scenarios

- Quickly verify calculation parameter settings
- Performance benchmarking for small-scale structures (few atoms)
- Algorithm optimization and debugging
- Preliminary testing on personal laptops

---

## Tutorial 3: Potentials and Properties

### Example 3.1: Using Different Potentials

**Example Path**: `lammps_tutorial3_potentials_and_properties/lammps_example3.1_potentials/`

#### Overview

APEX provides multiple potential types to choose from. Currently supported potential types include:

```python
LAMMPS_INTER_TYPE = ['deepmd', 'eam_alloy', 'meam', 'eam_fs', 'meam_spline', 'snap', 'gap', 'rann', 'mace']
```

#### Directory Structure

```
lammps_example3.1_potentials/
├── confs/
│   └── std-fcc/
│       └── POSCAR              # Al initial structure file
├── Al.eam.alloy                # EAM potential file
├── Al.meam                     # MEAM potential file
├── library.meam                # MEAM library file
├── param_relax_eam.json        # EAM potential relaxation parameters
├── param_relax_meam.json       # MEAM potential relaxation parameters
├── global_local_debug.json     # Local debug configuration
├── support_potentials.txt      # Supported potentials list
└── dpdispatcher.log            # Log file
```

#### EAM Potential Configuration

For most potentials, only one potential file is needed. Using EAM as an example, `param_relax_eam.json` is configured as follows:

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

**Parameter Explanation**:
- `type`: Potential type, here is `eam_alloy`
- `model`: Path to potential file
- `type_map`: Element type mapping

#### MEAM Potential Configuration

For MEAM potential, two files are required: `library.meam` and `Metal.meam` (here is `Al.meam`), so the configuration is slightly different.

`param_relax_meam.json` is configured as follows:

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

**Key Difference**:
- MEAM's `model` field is a list containing two files: `["library.meam", "Metal.meam"]`
- The first file is the library file, the second is the element-specific file

#### Task Submission

Submit using EAM potential:
```bash
apex submit -d param_relax_eam.json -c global_local_debug.json
```

Submit using MEAM potential:
```bash
apex submit -d param_relax_meam.json -c global_local_debug.json
```

---

### Example 3.2: Computing Different Properties

**Example Path**: `lammps_tutorial3_potentials_and_properties/lammps_example3.2_properties/`

#### Overview

APEX provides multiple property calculation capabilities, which can be flexibly selected through configuration files.

#### Directory Structure

```
lammps_example3.2_properties/
├── confs/
│   └── std-fcc/
│       └── POSCAR              # Al initial structure file
├── Al.eam.alloy                # EAM potential file
├── param_relax.json            # Structure relaxation parameters
├── param_props.json            # Property calculation parameters
└── global_local_debug.json     # Local debug configuration
```

#### Property Calculation Configuration

`param_props.json` (simplified version) is configured as follows:

```json
{
    "structures": ["confs/std-*"],
    "interaction": {
        "type": "eam_alloy",
        "model": "Al.eam.alloy",
        "type_map": {"Al": 0}
    },
    "properties": [
        {
            "type": "eos",
            "skip": false,
            "vol_start": 0.6,
            "vol_end": 1.4,
            "vol_step": 0.4
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
            "type": "interstitial",
            "skip": true,
            "insert_ele": ["Al"]
        },
        {
            "type": "vacancy",
            "skip": true,
            "supercell": [2, 2, 2]
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

**1. EOS (Equation of State)**
- `vol_start`: Starting volume (relative to equilibrium volume)
- `vol_end`: Ending volume
- `vol_step`: Volume increment

**2. Elastic - Elastic Constants**
- Calculates material's elastic moduli and elastic constant matrix

**3. Surface - Surface Energy**
- Calculates surface energy of different crystal facets

**4. Interstitial - Interstitial Formation Energy**
- `insert_ele`: Element type to be inserted

**5. Vacancy - Vacancy Formation Energy**
- `supercell`: Supercell size

**6. Gamma - Generalized Stacking Fault Energy**
- `plane_miller`: Miller indices of slip plane
- `slip_direction`: Slip direction

#### Controlling Property Calculations

Use `"skip": false/true` to control whether to calculate a property:
- `"skip": false`: Calculate this property
- `"skip": true`: Skip this property

If you don't want to calculate a certain property, you can also directly delete that property configuration block from the JSON file.

#### Task Submission

First perform structure relaxation:
```bash
apex submit -d param_relax.json -c global_local_debug.json
```

After relaxation completes, perform property calculations:
```bash
apex submit -d param_props.json -c global_local_debug.json
```

#### Important Notes

- Each property has adjustable parameters, details can be found in the [APEX Official Documentation](https://github.com/deepmodeling/APEX/blob/master/README.md)
- Structure relaxation must be completed before property calculations
- Computational time cost varies greatly for different properties, choose according to your needs

---

## Reference Resources

- [APEX GitHub Repository](https://github.com/deepmodeling/APEX)
- [APEX Paper](https://doi.org/10.1038/s41524-025-01580-y)
- [Bohrium Official Website](https://bohrium.dp.tech/)
- [Bohrium Workflow Platform](https://workflows.deepmodeling.com)
- [Bohrium Public Image Registry](https://bohrium.dp.tech/apps/web-images)
- [DPDispatcher Documentation](https://docs.deepmodeling.com/projects/dpdispatcher)
- [Dflow Workflow Framework](https://github.com/deepmodeling/dflow)

---

**Last Updated**: November 2025
**Version**: 3.1

