# APEX Examples — Common Workflow Configurations

## Example 1: BCC Mo with DeePMD — EOS + Elastic

**Scenario**: Quick property screening of Mo using a DeePMD potential.

### Directory Structure
```
job_mo_deepmd/
├── confs/std-bcc/POSCAR
├── frozen_model.pb
├── global.json
└── param.json
```

### POSCAR (BCC Mo)
```
Mo
1.0
3.168 0.000 0.000
0.000 3.168 0.000
0.000 0.000 3.168
Mo
2
Direct
0.000 0.000 0.000
0.500 0.500 0.500
```

### global.json
```json
{
    "machine": {
        "batch_type": "Shell",
        "context_type": "LazyLocalContext",
        "local_root": "."
    },
    "resources": {
        "number_node": 1,
        "cpu_per_node": 4,
        "group_size": 1
    },
    "run_command": "lmp -in in.lammps"
}
```

### param.json
```json
{
    "structures": ["confs/std-bcc"],
    "interaction": {
        "type": "deepmd",
        "model": "frozen_model.pb",
        "type_map": "auto"
    },
    "relaxation": {
        "cal_setting": {
            "etol": 0,
            "ftol": 1e-10,
            "maxiter": 5000,
            "maximal": 500000
        }
    },
    "properties": [
        {
            "type": "eos",
            "vol_start": 0.8,
            "vol_end": 1.2,
            "vol_step": 0.05
        },
        {
            "type": "elastic",
            "norm_deform": 0.01,
            "shear_deform": 0.01
        }
    ]
}
```

### Preferred: APEX workflow submission
```
image: registry.dp.tech/dptech/dp/native/prod-397637/apex:1.3.0
machine: c2_m4_cpu
cmd: apex submit param.json -c global.json -f joint -s -n "mo-bcc-eos-elastic" > log 2>&1
```

Use `apex submit` for production and Bohrium workflows. It delegates the
calculation to dflow. For agent-managed runs, `-s` lets the lightweight outer
job exit after submission; the agent must retain the workflow ID, monitor the
inner workflow, and run `apex retrieve` after completion.

### Alternative: local step-by-step debugging
```bash
apex do param.json make_relax -c global.json &&
apex do param.json run_relax -c global.json &&
apex do param.json post_relax -c global.json &&
apex do param.json make_props -c global.json &&
apex do param.json run_props -c global.json &&
apex do param.json post_props -c global.json
```

---

## Example 2: FCC Cu with ABACUS — Surface + Vacancy

**Scenario**: DFT-level surface energy and vacancy formation energy for Cu.

### Directory Structure
```
job_cu_abacus/
├── confs/std-fcc/POSCAR
├── INPUT
├── Cu_ONCV_PBE-1.0.upf
├── Cu_gga_7au_100Ry_4s2p2d1f.orb
├── global.json
└── param.json
```

### INPUT
```
INPUT_PARAMETERS
calculation     relax
basis_type      lcao
ecutwfc         100
scf_thr         1.0e-7
force_thr_ev    0.02
stress_thr      0.5
relax_method    cg
relax_nmax      200
smearing_method gaussian
smearing_sigma  0.002
kspacing        0.15
```

### param.json
```json
{
    "structures": ["confs/std-fcc"],
    "interaction": {
        "type": "abacus",
        "incar": "INPUT",
        "potcar_prefix": ".",
        "potcars": {"Cu": "Cu_ONCV_PBE-1.0.upf"},
        "orb_files": {"Cu": "Cu_gga_7au_100Ry_4s2p2d1f.orb"}
    },
    "relaxation": {
        "cal_setting": {
            "relax_pos": true,
            "relax_shape": true,
            "relax_vol": true
        }
    },
    "properties": [
        {
            "type": "surface",
            "min_slab_size": 40,
            "min_vacuum_size": 15,
            "max_miller": 2
        },
        {
            "type": "vacancy",
            "supercell": [3, 3, 3]
        }
    ]
}
```

### global.json
```json
{
    "machine": {
        "batch_type": "Shell",
        "context_type": "LazyLocalContext",
        "local_root": "."
    },
    "resources": {
        "number_node": 1,
        "cpu_per_node": 8,
        "group_size": 1
    },
    "run_command": "mpirun -n 8 abacus"
}
```

### Preferred: APEX workflow submission
```
image: registry.dp.tech/dptech/dp/native/prod-397637/apex:1.3.0
machine: c2_m4_cpu
cmd: apex submit param.json -c global.json -f joint -s -n "cu-fcc-surface-vacancy" > log 2>&1
```

Use `apex submit` for production and Bohrium workflows. The calculator resources
are configured for the inner dflow tasks in `global.json`; the outer submission
client only needs a small CPU machine. For agent-managed runs, use `-s`, retain
the workflow ID, monitor the inner workflow, and retrieve results explicitly.

### Alternative: local step-by-step debugging
```bash
apex do param.json make_relax -c global.json &&
apex do param.json run_relax -c global.json &&
apex do param.json post_relax -c global.json &&
apex do param.json make_props -c global.json &&
apex do param.json run_props -c global.json &&
apex do param.json post_props -c global.json
```

---

## Example 3: HEA CoCrFeMnNi with DeePMD — RSS + Properties

**Scenario**: High-entropy alloy property screening using random solid solutions.

### Step 1: Generate RSS structures (run locally)
```bash
# Generate 5 random configurations of equiatomic CoCrFeMnNi in FCC
apex rss --composition "Co0.2Cr0.2Fe0.2Mn0.2Ni0.2" \
         --prototype fcc \
         --supercell 3 3 3 \
         --n-configs 5 \
         --output-dir confs/rss_fcc
```

### Step 2: Property calculation

### param.json
```json
{
    "structures": ["confs/rss_fcc"],
    "interaction": {
        "type": "deepmd",
        "model": "CoCrFeMnNi.pb",
        "type_map": "auto"
    },
    "relaxation": {
        "cal_setting": {
            "etol": 0,
            "ftol": 1e-10,
            "maxiter": 5000,
            "maximal": 500000
        }
    },
    "properties": [
        {"type": "eos", "vol_start": 0.85, "vol_end": 1.15, "vol_step": 0.05},
        {"type": "elastic", "norm_deform": 0.01, "shear_deform": 0.01}
    ]
}
```

---

## Example 4: Al with LAMMPS — Finite-T Elastic + Annealing

**Scenario**: Temperature-dependent elastic constants and annealing for Al.

### param.json
```json
{
    "structures": ["confs/std-fcc"],
    "interaction": {
        "type": "eam_alloy",
        "model": "Al99.eam.alloy",
        "type_map": "auto"
    },
    "relaxation": {
        "cal_setting": {
            "etol": 0,
            "ftol": 1e-10
        }
    },
    "properties": [
        {
            "type": "finite_t_elastic",
            "supercell_size": [4, 4, 4],
            "cal_setting": {
                "temperature": [200, 300, 400, 500, 600],
                "strain": 0.001,
                "equi_step": 20000,
                "response_step": 20000
            }
        },
        {
            "type": "annealing",
            "supercell_size": [5, 5, 5],
            "cal_setting": {
                "start_temp": 4,
                "target_temp": 900,
                "end_temp": 300,
                "temp_ramp_rate": 500,
                "cool_rate": 100,
                "hold_step": 40000
            }
        }
    ]
}
```

---

## Example 5: Si with VASP — Phonon

**Scenario**: Phonon band structure for Si using VASP.

> ⚠️ Requires user to provide VASP image and POTCAR path.

### param.json
```json
{
    "structures": ["confs/std-diamond"],
    "interaction": {
        "type": "vasp",
        "incar": "INCAR",
        "potcar_prefix": "/opt/vasp/potcar/PBE",
        "potcars": {"Si": "Si"}
    },
    "relaxation": {
        "cal_setting": {
            "relax_pos": true,
            "relax_shape": true,
            "relax_vol": true
        }
    },
    "properties": [
        {
            "type": "phonon",
            "supercell_size": [3, 3, 3],
            "MESH": [20, 20, 20],
            "BAND_POINTS": 51
        }
    ]
}
```

### INCAR
```
PREC = Accurate
ENCUT = 400
EDIFF = 1E-8
IBRION = -1
NSW = 0
ISMEAR = 0
SIGMA = 0.05
LREAL = .FALSE.
```

---

## Example 6: BCC W — Gamma Surface (111) plane

**Scenario**: 2D generalized stacking fault energy map for W {111} plane.

### param.json
```json
{
    "structures": ["confs/std-bcc"],
    "interaction": {
        "type": "deepmd",
        "model": "W_model.pb",
        "type_map": "auto"
    },
    "relaxation": {
        "cal_setting": {
            "etol": 0,
            "ftol": 1e-10
        }
    },
    "properties": [
        {
            "type": "gamma_surface",
            "plane_miller": [1, 1, 0],
            "slip_direction": [1, -1, 1],
            "supercell_size": [1, 1, 8],
            "n_steps_x": 12,
            "n_steps_y": 12,
            "cal_setting": {
                "relax_pos": true,
                "relax_shape": false,
                "relax_vol": false,
                "etol": 0,
                "ftol": 1e-10
            }
        }
    ]
}
```

---

## Example 7: Grüneisen & Thermal Expansion for Cu

**Scenario**: Compute Grüneisen parameters and thermal expansion coefficient.

### param.json
```json
{
    "structures": ["confs/std-fcc"],
    "interaction": {
        "type": "deepmd",
        "model": "Cu_model.pb",
        "type_map": "auto"
    },
    "relaxation": {
        "cal_setting": {
            "etol": 0,
            "ftol": 1e-10
        }
    },
    "properties": [
        {
            "type": "gruneisen",
            "supercell_size": [3, 3, 3],
            "volume_strains": [-0.02, -0.01, 0.0, 0.01, 0.02],
            "temperatures": [100, 200, 300, 400, 500, 600, 800, 1000],
            "alpha_mode": "full",
            "MESH": [20, 20, 20],
            "cal_setting": {
                "relax_pos": true,
                "relax_shape": false,
                "relax_vol": false,
                "etol": 0,
                "ftol": 1e-10
            }
        }
    ]
}
```

---

## Example 8: Decohesive + Cohesive Energy

**Scenario**: Ideal work of separation for (110) plane and cohesive energy curve.

### param.json
```json
{
    "structures": ["confs/std-bcc"],
    "interaction": {
        "type": "deepmd",
        "model": "Fe_model.pb",
        "type_map": "auto"
    },
    "relaxation": {
        "cal_setting": {
            "etol": 0,
            "ftol": 1e-10
        }
    },
    "properties": [
        {
            "type": "cohesive",
            "latt_start": 0.8,
            "latt_end": 1.5,
            "latt_step": 0.02,
            "cal_type": "static"
        },
        {
            "type": "decohesive",
            "miller_index": [1, 1, 0],
            "min_slab_size": 40,
            "max_vacuum_size": 20,
            "vacuum_size_step": 0.5
        }
    ]
}
```
