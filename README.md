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
    - [3.2. Workflow submittion](#32-workflow-submittion)
    - [3.3. Output result](#33-output-result)
  - [4. User scenario examples](#4-user-scenario-examples)
  - [5. Extensibility](#5-extensibility)

## 1. Overview

APEX inherits the functionality of the second version of alloy properties calculations and is developed based on the [dflow](https://github.com/deepmodeling/dflow) framework. By incorporating the advantages of cloud-native workflows, APEX simplifies the complex process to automatically test multiple configurations and properties. Thanks to its cloud-native feature, APEX offers users an more intuitive and easy-to-use interaction, making the overall user experience more straightforward.

The overall architechture of APEX is demonstrated as followed:

<div>
    <img src="./docs/images/apex_demo.png" alt="Fig1" style="zoom: 35%;">
    <p style='font-size:1.0rem; font-weight:none'>Figure 1. APEX schematic diagram</p>
</div>

There are generally three types of pre-defined **workflow** in the APEX that user can submit: `relaxation`, `property` and `joint` workflows. The first two is consist of three sequential **sub-steps**: `Make`, `Run`, and `Post`. The third `joint` type basically connects the first two together into an overall workflow.

The `relaxation` workflow starts from initial `POSCAR` provided by user at the beginning, and outputs key information like final relaxed structure and corresponding energy. Such equilibrium state information is necessary for the `property` workflow as inputs to conduct further alloy property calculation. The final results when finished will be automatically retrieved and downloaded back to origial working direction.

Within both `relaxation` and `property` workflow, respective computational tasks are prepared during the `Make` step, which will be passed to the `Run` step for tasks dispatch, calculation monitoring and finished tasks retriving (This is realized via the [DPDispatcher](https://github.com/deepmodeling/dpdispatcher/tree/master) package). As all tasks are completed, the `Post` step will be invoked to collect data and calculate desired property results.

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
  | apex_image_name | String | None | Image address to run `Make` and `Post` steps |
  | dpmd_image_name | String | None | Image address for `Run` step using LAMMPS |
  | vasp_image_name | String | None | Image address for `Run` step using VASP |
  | abacus_image_name | String | None | Image address for `Run` step using ABACUS |
  | lammps_run_command | String | None | Command for `Run` step using LAMMPS|
  | vasp_run_command | String | None | Command for `Run` step using VASP|
  | abacus_run_command | String | None | Command for `Run` step using ABACUS|

* **DPDispatcher** (One may refer to [DPDispatcher’s documentation](https://docs.deepmodeling.com/projects/dpdispatcher/en/latest/index.html) for details of following parameters)
  | Key words | Data structure | Default | Description |
  | :------------ | ----- | ----- | ------------------- |
  | machine | Dict | None | Indication of machine and batch type |
  | resources | Dict | None | Indication of computing recources |
  | task | Dict | None | Indication of run command and essential files |

* **Bohrium** (to be specified when quickly adopt pre-built dflow servise or scientific computing on [Bohrium platform](https://bohrium.dp.tech) without indicate **DPDispatcher** related key words)
  | Key words | Data structure | Default | Description |
  | :------------ | ----- | ----- | ------------------- |
  | batch_type | String | None | Set to `"Bohrium"` to run tasks on the Bohrium platform |
  | context_type | String | None | Set to `"Bohrium"` to run tasks on the Bohrium |
  | s3_repo_key | String | None | Key of artifact repository. Set to `"oss-bohrium"` when adopt dflow servise on Bohrium |
  | s3_storage_client | String | None | client for plugin storage backend. Set to `"TiefblueClient"` when adopt dflow servise on Bohrium |
  | email | String | None | Email of your Bohrium account |
  | password | String | None | Password of your Bohrium account |
  | program_id | Int | None | Program ID of your Bohrium account |
  | cpu_scass_type | String | None | CPU node type on Bohrium to run the first principle jobs |
  | gpu_scass_type | String | None | GPU node type on Bohrium to run LAMMPS jobs |

Examples of `global.json` under different using scenario are provided at [User scenario examples](#4-Userscenarioexamples)

#### 3.1.2. Calculation parameters
The way of parameters indication of alloy property calculation is similar to that of previous `dpgen.autotest`. There are **tree** categories of `json` file that define those parameters can be passed to APEX according to what they contain. Here shows three examples (for specific meaning of each paramter, one can refer to [Hands-on_auto-test](./docs/Hands_on_auto-test.pdf) for furthe introduction):

* **Relaxation parameters**
  ```json
  
  ```



### 3.2. Workflow submittion

### 3.3. Output result

## 4. User scenario examples

## 5. Extensibility
