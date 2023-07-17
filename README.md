# APEX: Alloy Property EXplorer using simulations

[APEX](https://github.com/deepmodeling/APEX): Alloy Property EXplorer using simulations is a part of [AI Square](https://aissquare.com/) project, in which we refactored the [DP-Gen](https://github.com/deepmodeling/dpgen) `auto_test` module to construct an extensible general alloy property test Python package. It allows users to construct a variety of property-test workflows easily using different computational methods (e.g. LAMMPS, VASP, and ABACUS are supported currently).

## Table of Contents

- [APEX: Alloy Property EXplorer using simulations](#apex-alloy-property-explorer-using-simulations)
  - [Table of Contents](#table-of-contents)
  - [1. Overview](#1-overview)
  - [2. Easy Install](#2-easy-install)
  - [3. User Guide](#3-user-guide)
    - [3.1. Input files preperation](#31-input-files-preperation)
      - [3.1.1. Global setting](#311-global-setting)
      - [3.1.2. Calculation parameters](#312-calculation-parameters)
    - [3.2. Submittion command](#32-submittion-command)
  - [4. Quick Start](#4-quick-start)
    - [4.1. On the Bohrium](#41-on-the-bohrium)
    - [4.2. On local Argo service](#42-on-local-argo-service)
    - [4.3. On local enviornment](#43-on-local-enviornment)

## 1. Overview

APEX inherits the functionality of the second version of alloy properties calculations and is developed based on the [dflow](https://github.com/deepmodeling/dflow) framework. By incorporating the advantages of cloud-native workflows, APEX simplifies the complex process to automatically test multiple configurations and properties. Thanks to its cloud-native feature, APEX offers users an more intuitive and easy-to-use interaction, making the overall user experience more straightforward without concerning about process control, task scheduling, observability and disaster tolerance.

The overall architechture of APEX is demonstrated as followed:

<div>
    <img src="./docs/images/apex_demo.png" alt="Fig1" style="zoom: 35%;">
    <p style='font-size:1.0rem; font-weight:none'>Figure 1. APEX schematic diagram</p>
</div>

There are generally three types of pre-defined **workflow** in the APEX that user can submit: `relaxation`, `property` and `joint` workflows. The first two is consist of three sequential **sub-steps**: `Make`, `Run`, and `Post`. The third `joint` type basically connects the first two together into an overall workflow.

The `relaxation` workflow starts from initial `POSCAR` provided by user at the beginning, and outputs key information like final relaxed structure and corresponding energy. Such equilibrium state information is necessary for the `property` workflow as inputs to conduct further alloy property calculation. The final results when finished will be automatically retrieved and downloaded back to origial working direction.

Within both `relaxation` and `property` workflow, respective computational tasks are prepared during the `Make` step, which will be passed to the `Run` step for tasks dispatch, calculation monitoring and finished tasks retriving (this is realized via the [DPDispatcher](https://github.com/deepmodeling/dpdispatcher/tree/master) plugin). As all tasks are completed, the `Post` step will be invoked to collect data and calculate desired property results.

So far, APEX provides calculation methods of following alloy properties:
* Equation of State (EOS)
* Elastic constants
* Surface energy
* Interstitial formation energy
* Vacancy formation energy
* Stacking fault energy (Gamma line)

Additionally, three types of calculator are currently supported: **LAMMPS** for molecular dynamics simulation, **VASP** and **ABACUS** for the first-principle calculation. For the extention of above functions, please refer to the [Extensibility](#5-extensibility).

## 2. Easy Install
Easy install by
```shell
pip install "git+https://github.com/deepmodeling/APEX.git"
```
You may also clone the package firstly by
```shell
git clone https://github.com/deepmodeling/APEX.git
```
then install APEX by
```shell
cd APEX
pip install .
```
## 3. User Guide

### 3.1. Input files preperation
All the key input parameters used in APEX should be prepared within specific *json* files **under current working direction** in the first place. There are two types of *json* file to be introduced respectively.

#### 3.1.1. Global setting
The indications with respect to global configuration, [dflow](https://github.com/deepmodeling/dflow) and [DPDispatcher](https://github.com/deepmodeling/dpdispatcher/tree/master) specific settings should be stored as *json* form in a file named exactly as `global.json`. Following table descripts some important key words classified into three groups:


* **Dflow**
  | Key words | Data structure | Default | Description |
  | :------------ | ----- | ----- | ------------------- |
  | dflow_host | String | https://127.0.0.1:2746 | Url of dflow server |
  | k8s_api_server | String | https://127.0.0.1:2746 | Url of kubernetes API server |
  | debug_mode | Boolean | False | Whether to run workflow with local debug mode of the dflow. Following `image_name` must be indicated when `debug_mode` is False |
  | apex_image_name | String | None | Image address to run `Make` and `Post` steps. One can build this Docker image via prepared [Dockerfile](./docs/Dockerfile)|
  | dpmd_image_name | String | None | Image address for `Run` step using LAMMPS |
  | vasp_image_name | String | None | Image address for `Run` step using VASP |
  | abacus_image_name | String | None | Image address for `Run` step using ABACUS |
  | lammps_run_command | String | None | Command for `Run` step using LAMMPS|
  | vasp_run_command | String | None | Command for `Run` step using VASP|
  | abacus_run_command | String | None | Command for `Run` step using ABACUS|

* **DPDispatcher** (One may refer to [DPDispatcher’s documentation](https://docs.deepmodeling.com/projects/dpdispatcher/en/latest/index.html) for details of following parameters)
  | Key words | Data structure | Default | Description |
  | :------------ | ----- | ----- | ------------------- |
  | context_type | String | None | Must be specified at the outermost level if adopt the DPDispather; Set to `"Bohrium"` to run tasks on the Bohrium |
  | batch_type | String | None | Set to `"Bohrium"` to run tasks on the Bohrium platform |
  | machine | Dict | None | Indication of machine and batch type |
  | resources | Dict | None | Indication of computing recources |
  | task | Dict | None | Indication of run command and essential files |

* **Bohrium** (to be specified when quickly adopt pre-built dflow servise or scientific computing on [Bohrium platform](https://bohrium.dp.tech) without indicate **DPDispatcher** related key words)
  | Key words | Data structure | Default | Description |
  | :------------ | ----- | ----- | ------------------- |
  | s3_repo_key | String | None | Key of artifact repository. Set to `"oss-bohrium"` when adopt dflow servise on Bohrium |
  | s3_storage_client | String | None | client for plugin storage backend. Set to `"TiefblueClient"` when adopt dflow servise on Bohrium |
  | email | String | None | Email of your Bohrium account |
  | password | String | None | Password of your Bohrium account |
  | program_id | Int | None | Program ID of your Bohrium account |
  | cpu_scass_type | String | None | CPU node type on Bohrium to run the first principle jobs |
  | gpu_scass_type | String | None | GPU node type on Bohrium to run LAMMPS jobs |

Examples of `global.json` under different using scenario are provided at [User scenario examples](#4-Userscenarioexamples)

#### 3.1.2. Calculation parameters
The way of parameters indication of alloy property calculation is similar to that of previous `dpgen.autotest`. There are **tree** categories of `json` file that define those parameters can be passed to APEX according to what they contain. Users can name these files arbitrarily.

Categories calculation parameter files:
| Type | File format | Dict contained | Usage |
| :------------ | ---- | ----- | ------------------- |
| Relaxation | json | `structures`; `interaction`; `Relaxation` | For `relaxation` worflow |
| Property | json |  `structures`; `interaction`; `Properties`  | For `property` worflow |
| Joint | json |  `structures`; `interaction`; `Relaxation`; `Properties` | For `relaxation`, `property` and `joint` worflow |

Notice that files like POSCAR under `structure` path or any other file indicated to be used within the `json` file should be prepared under current working direction in advance. 

Here shows three examples (for specific meaning of each paramter, one can refer to [Hands-on_auto-test](./docs/Hands_on_auto-test.pdf) for further introduction):
* **Relaxation parameter file**
  ```json
  {
    "structures":            ["confs/std-*"],
    "interaction": {
            "type":           "deepmd",
            "model":          "frozen_model.pb",
            "type_map":       {"Mo": 0}
	  },
    "relaxation": {
            "cal_setting":   {"etol":       0,
                              "ftol":     1e-10,
                              "maxiter":   5000,
                              "maximal":  500000}
	  }
  }
  ```
* **Property parameter file**
  ```json
  {
    "structures":    ["confs/std-*"],
    "interaction": {
        "type":          "deepmd",
        "model":         "frozen_model.pb",
        "type_map":      {"Mo": 0}
    },
    "properties": [
        {
          "type":         "eos",
          "skip":         false,
          "vol_start":    0.6,
          "vol_end":      1.4,
          "vol_step":     0.1,
          "cal_setting":  {"etol": 0,
                          "ftol": 1e-10}
        },
        {
          "type":         "elastic",
          "skip":         false,
          "norm_deform":  1e-2,
          "shear_deform": 1e-2,
          "cal_setting":  {"etol": 0,
                          "ftol": 1e-10}
        },
	      {
	        "type":               "gamma",
	        "skip":               true,
            "lattice_type":       "bcc",
            "miller_index":         [1,1,2],
            "supercell_size":       [1,1,5],
            "displace_direction":   [1,1,1],
            "min_vacuum_size":      0,
	        "add_fix":              ["true","true","false"], 
            "n_steps":             10
	      }
        ]
  }
  ```
* **Joint parameter file**
  ```json
  {
    "structures":            ["confs/std-*"],
    "interaction": {
          "type":           "deepmd",
          "model":          "frozen_model.pb",
          "type_map":       {"Mo": 0}
      },
    "relaxation": {
            "cal_setting":   {"etol":       0,
                            "ftol":     1e-10,
                            "maxiter":   5000,
                            "maximal":  500000}
      },
    "properties": [
      {
        "type":         "eos",
        "skip":         false,
        "vol_start":    0.6,
        "vol_end":      1.4,
        "vol_step":     0.1,
        "cal_setting":  {"etol": 0,
                        "ftol": 1e-10}
      },
      {
        "type":         "elastic",
        "skip":         false,
        "norm_deform":  1e-2,
        "shear_deform": 1e-2,
        "cal_setting":  {"etol": 0,
                        "ftol": 1e-10}
      },
      {
        "type":               "gamma",
        "skip":               true,
          "lattice_type":       "bcc",
          "miller_index":         [1,1,2],
          "supercell_size":       [1,1,5],
          "displace_direction":   [1,1,1],
          "min_vacuum_size":      0,
        "add_fix":              ["true","true","false"], 
          "n_steps":             10
      }
      ]
  }
  ```

### 3.2. Submittion command
APEX will submit a type of workflow on each invocation of command with format of `apex [file_names] [--optional_argument]`. By the type of parameter file user indicate, APEX will automatically determine the type of workflow and calculation method to adopt. User can also further specify the type via optional argument. Here is a list of command examples for tree type of workflow submittion:
* `relaxtion` workflow:
  ```shell
  apex relaxation.json
  ```
   ```shell
  apex joint.json --relax
  ```
   ```shell
  apex relaxation.json property.json --relax
  ```
* `property` workflow:
  ```shell
  apex property.json
  ```
  ```shell
  apex joint.json --props
  ```
  ```shell
  apex relaxation.json property.json --props
  ```
* `joint` workflow:
  ```shell
  apex joint.json
  ```
  ```shell
  apex property.json relaxation.json
  ```
APEX also provides a **single-step local debug mode**, which can run `Make` and `Post` step individually under local enviornment. User can invoke them by following optional arguments like:

  | Type of step | Optional argument | Shorten way |
  | :------------ | ----- | ----- |
  | `Make` of `relaxation` | `--make_relax` | `-mr` | 
  | `Post` of `relaxation` | `--post_relax` | `-pr` | 
  | `Make` of `property` | `--make_props` | `-mp` | 
  | `Post` of `proterty` | `--post_props` | `-pp` | 

## 4. Quick Start
We provide some cases as quick start examples of APEX based on different user scenario. A [lammps_example](./examples/lammps_demo/) to calculate EOS and elastic constants of molybdenum for both BCC and FCC phase will be adopted for demonstration here. First, let's check which files are prepared under the working directory of this case.
```
lammps_demo
├── confs
│   ├── std-bcc
│   │   └── POSCAR
│   └── std-fcc
│       └── POSCAR
├── frozen_model.pb
├── global.json
├── param_joint.json
├── param_props.json
└── param_relax.json
```
There are three type of parameters files and the `global.json`, as well as a force-field potential file of molybdenum `frozen_model.pb`. Under the direction of `confs`, structure file `POSCAR` of both phases have been prepared respectively.

### 4.1. On the Bohrium
The most convenient way to submit a APEX workflow is via the prebuilt running enviornment of dflow on the [Bohrium platform](https://bohrium.dp.tech). However, one may need to register an account of the Bohrium as needed. Here is an example of `global.json` to this way.
```json
{
    "dflow_host": "https://workflows.deepmodeling.com",
    "k8s_api_server": "https://workflows.deepmodeling.com",
    "s3_repo_key": "oss-bohrium",
    "s3_storage_client": "TiefblueClient",
    "email": "YOUR_EMAIL",
    "password": "YOUR_PASSWD",
    "program_id": 1234,
    "apex_image_name":"registry.dp.tech/dptech/dpgen:0.11.0",
    "dpmd_image_name": "registry.dp.tech/dptech/prod-11045/deepmd-kit:deepmd-kit2.1.1_cuda11.6_gpu",
    "lammps_run_command":"lmp -in in.lammps",
    "batch_type": "Bohrium",
    "context_type": "Bohrium",
    "cpu_scass_type":"c4_m8_cpu",
    "gpu_scass_type":"c8_m31_1 * NVIDIA T4"
}
```
Just replace values of `email`, `password` and `program_id` of your own before submit. As for image used, you can either built your own or use public images from Bohrium or pulling from the Docker Hub. Once the workflow is submitted, one can monitor it on https://workflows.deepmodeling.com.

### 4.2. On local Argo service
One can also build dflow enviornment on local computer by running [install scripts](https://github.com/deepmodeling/dflow/tree/master/scripts) at the dflow repository. For example to install on a linux system (not with a root account):
```shell
bash install-linux-cn.sh
```
This will automatcally setup local necessary tools of Docker, Minikube, and Argo service with port default to be `127.0.0.1:2746`. Thus one can rewrite `global.json` to submit workflow to this container without a Bohrium account, for example:
```json
{
    "apex_image_name": "zhuoyli/apex:amd64",
    "dpmd_image_name": "deepmodeling/deepmd-kit:2.2.1_cuda10.1_gpu",
    "lammps_run_command": "lmp -in in.lammps",
    "context_type": "SSHContext",
    "machine": {
        "batch_type": "Slurm",
        "context_type": "SSHContext",
        "local_root" : "/home/user123/workplace/22_new_project/",
        "remote_root": "/home/user123/dpdispatcher_work_dir/",
        "remote_profile": {
            "hostname": "39.106.xx.xxx",
            "username": "user123",
            "port": 22,
            "timeout": 10
            }
    }
}
```
In this example, we try to dispatch the tasks to a remote node managed by the [Slurm](https://slurm.schedmd.com). User needs to replace corresponding parameters within the `machine` dictionary or specify `resources` and `tasks` following the rule of [DPDispatcher](https://docs.deepmodeling.com/projects/dpdispatcher/en/latest/index.html).

For the APEX image, above is public on the [Docker Hub](https://hub.docker.com) that can be pulled automatically. User can also pull the image in advance or build your own Docker image in the Minikube environment locally via [Dockerfile](./docs/Dockerfile) (please refer to [Docker's instruction](https://docs.docker.com/engine/reference/commandline/build/) for `build` instruction) so that the pods could be initialized much faster. 

Once the workflow is submitted, One can monitor the process on https://127.0.0.1:2746.

### 4.3. On local enviornment
If your local computer has trouble accessing to the internet. APEX also provides a **workflow local debug mode** so that the flow could run on the local basic `Python3` enviornment independent of the Docker container. However, user will **not** be able to monitor the workflow on the Argo UI.

To activate this function, user can set `debug_mode` to be `true` within `global.json`, for example:
```json
{
    "debug_mode": true,
    "lammps_run_command": "lmp -in in.lammps",
    "context_type": "SSHContext",
    "machine": {
        "batch_type": "Slurm",
        "context_type": "SSHContext",
        "local_root" : "/home/user123/workplace/22_new_project/",
        "remote_root": "/home/user123/dpdispatcher_work_dir/",
        "remote_profile": {
            "hostname": "39.106.xx.xxx",
            "username": "user123",
            "port": 22,
            "timeout": 10
            }
    }
}
```
In this way, user do not need to indicate an image to run APEX. Instead, the APEX should be pre-installed within the default `Python3` environment to run normally.
