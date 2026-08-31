---
name: apex-flow
description: Run APEX relaxation and 15-property materials workflows with VASP, ABACUS, or LAMMPS, including EOS, elastic, surface/defect, phonon/Grüneisen, parent-aware Gamma line/surface, finite-temperature lattice/elastic/annealing, two-phase melting-point brackets, previews, reporting, and RSS/high-entropy generation. Supports the validated general DPA4 phonoLAMMPS runtime and a bundled alloytongqi checkpoint with a separate fail-closed T4 profile. Use for APEX, alloy properties, melting/coexistence, stacking faults, random solid solutions, or multi-property DFT/MLIP screening.
---

# APEX Flow — Alloy Properties EXplorer

APEX is an automated workflow for computing alloy/material properties via batch DFT or MLIP calculations. It handles the full pipeline: structure preparation → task generation → computation → result extraction.

## When to Use APEX vs Other Skills

**Definitely use APEX when:**

- User explicitly says "APEX" or "apex"
- Batch multi-property calculation (EOS + elastic + surface + ... together)
- Multi-structure screening (e.g. 10 compositions × 3 properties)
- RSS (random solid solution) generation + property evaluation
- High-entropy alloy / multi-component alloy property exploration

**APEX is one option (present as choice) when:**

- User requests a single property (elastic, EOS, surface energy, etc.) — could also be done with mlips/vasp/abacus/lammps skills directly
- User has a single structure and one property — APEX adds orchestration overhead vs direct calculation

**Do NOT use APEX when:**

- Single-point energy/force evaluation with MLIP → use `mlips` skill
- Custom LAMMPS simulation (GCMC, shock, free-form script) → use `lammps` skill
- VASP/ABACUS input preparation only (no execution) → use `vasp`/`abacus` skill
- Phonopy phonon calculation with existing DFT forces → use the DFT skill directly

When the task is ambiguous (e.g. "help me calculate elastic constants of Cu"), present options:

```
Options to offer via AskQuestion:
1. APEX workflow (automated pipeline, supports multiple properties at once, uses dflow orchestration)
2. Direct engine (e.g. mlips skill for MLIP, vasp skill for DFT — simpler for single property)
```



## High-Level Workflow (5 Steps)

1. **Select the execution profile and prepare inputs** — For the Cloud/MatMaster zip profile, check `BOHRIUM_ACCESS_KEY`, then use `scripts/generate_config.py create ...` to generate `param.json` + ticket-bearing `global.json` and stage structures/models. For an installed local edition, read `reference/execution-profile.md`: Bohrium direct uses the masked `apex account` credentials, while local/debug and Slurm/PBS use their own profile and do not require a Bohrium ticket. Never mix profile instructions.
2. **Submit from the selected client** — Cloud/MatMaster uses a lightweight outer Bohrium client (`c1_m2_cpu`, recommended). The installed Bohrium-direct profile runs `apex submit` on the Agent machine and must not create an outer job. Local/debug and Slurm/PBS follow their installed profile.
3. **The selected runtime executes** — Bohrium uses dflow-managed inner containers; local debug runs on the workstation, and local cluster profiles dispatch through Slurm/PBS.
4. **Monitor and retrieve results** — `apex submit` monitors the inner workflow and retrieves results after completion; parse `confs/<structure>/<prop>_00/result.json`
5. **Present results** — Summarize in a table with physical units (GPa for elastic, J/m² for surface, eV for energies)



## Critical Rules

1. **STOP: Confirm the APEX calculator backend before submission.**
  In APEX, **backend** means the calculator that runs tasks: `lammps` / `abacus` / `vasp`.
   DPA / DeePMD model names are potential files used under the LAMMPS backend, not APEX backends.
   Ask in two steps when needed:
   **Step A — APEX calculator backend** (`AskQuestion`):
  - LAMMPS + MLIP (DeePMD / DPA / MACE / NEP): fast, GPU-friendly
  - LAMMPS + classical (EAM / MEAM / SNAP): fast, CPU
  - ABACUS (DFT)
  - VASP (DFT; license-gated — resolve image via Bohrium `list_images` keyword=`vasp` or a user-known authorized address; otherwise stop)
   **Step B — only if Step A is LAMMPS + DeePMD/DPA: identify the model and runtime separately.**
  - The skill bundles `models/DPA4-alloytongqi/model.pt` as source-checkpoint
    provenance. Never pass this `.pt` file directly to LAMMPS for the DPA4
    production profile; that profile uses a hashed image-resident `.pt2`.
  - This is the DPA4 single-task `alloytongqi` model supplied by the user.
    Static checkpoint inspection confirms DPA4 and an empty branch-alias list;
    the `alloytongqi` branch provenance is user-supplied rather than embedded.
    Do not infer training-domain coverage merely from the checkpoint type map.
  - **Compatibility stop:** The same legacy repository path/tag was tested from
    the accessible Bohrium registry mirror at digest
    `sha256:43a27ca4a7bba7f774bbd56104d205a6a80cd9d65928f249f6109e9ef37b8402`.
    Its DeepMD-kit 3.1.3 loader rejects this checkpoint with
    `RuntimeError: Unknown model type: dpa4`; LAMMPS also aborts while
    initializing `pair_style deepmd`. The configured `registry.dp.tech`
    endpoint itself was pull-denied, so retain the tested mirror digest in any
    report. Keep this old image as the general APEX default and the legacy
    phonon/Grüneisen forced image; do not submit DPA4 through it.
  - For DPA4, use `--runtime-profile dpa4-alloytongqi-t4`. It remains locked
    while its image ref/digest placeholders or `pre_snapshot_only` status are
    present. The recorded `c4_m15_1 * NVIDIA T4` run is candidate evidence,
    not exact-image acceptance or a current recommendation.
    Once published, the generator must write
    `/usr/local/bin/dpa4-lmp -in in.lammps` and the full
    `/usr/local/bin/dpa4-phonolammps {input_file} -c {poscar} --dim {dim}
    {primitive_axes}` template; never shorten or override these entrypoints.
  - Do not invent a model path or download another default model. Use a
    different user-provided compatible model only when the user explicitly
    requests it; explain the change and obtain confirmation first.
   **Skip Step A/B ONLY if** the user already stated them in THIS message
   (e.g. “用 EAM”, “用 ABACUS 做 EOS”, “用 DPA4 alloytongqi model.pt”).
   **If AskQuestion times out or fails**: state the intended APEX backend and bundled model selection (if LAMMPS+DPA) in plain text and WAIT. Never silently submit.
2. **STOP: Confirm property parameters before submission — DO NOT PROCEED WITHOUT USER ANSWER.** Before submitting, present the full `properties` configuration (JSON) to the user. Show the defaults that will be used and highlight:
  - Miller indices / slip systems (for surface/gamma/gamma_surface/decohesive)
  - Supercell sizes (for vacancy/interstitial/phonon/gruneisen/finite-T)
  - Temperature ranges (for finite_t_latt/finite_t_elastic/annealing/melting_point)
  - For `melting_point`: relaxed and expanded atom counts, temperatures,
    replicas, total task count, stage lengths, restart inputs, and resources
  - Number of deformation/step points
   For crystallographic planes:
  - `gamma` / `gamma_surface`: start from the physically recommended FCC/BCC/HCP
    table in repository **README §4.10** (see also
    `reference/properties.md` §8–9). A non-tabulated system is allowed only when
    the direction is in-plane; APEX warns and uses geometric construction, so
    report the warning and inspect the generated geometry. Never silently change
    an approved plane/direction.
  - `decohesive`: pick `miller_index` from the crystal-family table in
    **README §4.5** / `reference/properties.md` §10 (FCC/BCC/Diamond/ZB/Rocksalt/
    HCP/Perovskite). HCP must use **3-index** only.
   Let the user approve or modify. **Skip ONLY if** the user provided explicit property parameters already.
   **If AskQuestion times out or fails**: display the parameters in your message and WAIT for confirmation before submitting.
3. **Profile-aware submission architecture.** Never attempt `apex do` for production workflows — use `apex submit` which delegates to dflow. In the Cloud/MatMaster profile, the outer Bohrium job is a thin submission client; in the installed Bohrium-direct profile, the Agent machine is the client and there is no outer job. See the applicable `reference/submission.md`.
  For agent-managed Bohrium runs, use `apex submit ...` without `-s`.
   The active submit client must remain active while dflow runs so APEX can monitor the
   workflow and retrieve results automatically.
   Immediately preserve the exact inner dflow workflow ID printed by
   `apex submit` and report it to the user. Keep this ID available for all later
   monitoring, retrieval, and workflow-control actions. In Cloud/MatMaster,
   do not confuse it with the outer Bohrium job ID. Follow the exact status-query and reporting
   protocol in `reference/workflow-control.md`; never infer workflow identity
   or material identity from the outer job name alone. Use the returned
   workflow/step phases and durations to track progress. In the Cloud/MatMaster
   profile, also retain the outer job ID; do not invent one for direct mode. A
   long-running or failed step should be investigated by its step ID/key rather
   than treated as successful completion. After the workflow reaches
   `Succeeded`, verify that automatic result retrieval completed as described
   in `reference/submission.md`.
4. **Kill = inner workflow first.** Terminate the inner dflow workflow before stopping any Cloud/MatMaster outer Bohrium node; otherwise compute can continue silently. Bohrium-direct has no outer node. See `reference/workflow-control.md`.
5. **Cloud/MatMaster MUST use `generate_config.py`; never hand-write `param.json` or `global.json`.** Installed local editions follow their selected profile and audited templates.
  - Create the complete job with `python <skill-root>/scripts/generate_config.py create ...`.
  - For multiple structures, pass repeated/space-separated `--structure` and/or `--structure-dir` to `create` (it copies each into `confs/<name>/` and fills `structures`); do not hand-edit `structures` after create.
  - To preserve an approved `param.json` while refreshing credentials, run
    `python <skill-root>/scripts/generate_config.py refresh-global --global global.json`
    from the task directory. This updates only `global.json`.
  - Do not invent unsupported flags or call the ticket API directly.
6. **Cloud/MatMaster only: generate the ticket before packaging; never refresh it in `run.sh`.**
  - First inspect the agent/local environment for `BOHRIUM_ACCESS_KEY`.
  - If it is missing, STOP and ask the user to provide/configure it. `generate_config.py` cannot generate a ticket without an access key.
  - If it exists, use `create` for a new job or `refresh-global` for an existing job; both convert the key to a fresh ticket and write it to `global.json`.
  - **Ticket API**: `GET https://openapi.dp.tech/openapi/v1/ticket/get?accessKey=<KEY>&expiration=<hours>`
    - Header: `x-app-key: ""` (empty string)
    - `expiration` 单位为**小时**，默认值 `168`（7 天）。`generate_config.py` 已内置此默认值。
    - 返回: `{"code": 0, "data": {"ticket": "UUID"}}`
  - Verify that `global.json` contains a non-empty `bohrium_config.ticket` before submission.
  - `run.sh` must only install/verify APEX and call `apex submit`. Do not add ticket API calls or depend on `BOHRIUM_ACCESS_KEY` inside the APEX container — 容器内没有此环境变量。
  - Install with `python3 -m pip install --upgrade --no-cache-dir apex-flow`.
   See `reference/submission.md`.
  - **Installed local edition exception:** follow
    `variants/local/profiles/bohrium-direct.md`; configure AccessKey or
    email/password with `apex account`. The saved AccessKey is passed to dflow
    for short-lived ticket exchange, is masked by `apex account --show`, and is
    not serialized into `global.json`. Do not require `BOHRIUM_ACCESS_KEY`,
    `refresh-global`, or an outer job for that profile.
7. **Project ID from environment only.** `generate_config.py` reads `BOHRIUM_PROJECT_ID` (or `--project-id`). Never hardcode a project ID (including old examples like `13529`) into `global.json`, docs, or prompts.
8. **Hard-validate inside the task directory before every upload.** Run:
  ```bash
  cd <job-dir>
  python <skill-root>/scripts/validate_inputs.py \
    --param param.json --global global.json
  ```
  Do not upload or submit unless it prints `Validation PASSED`. In
  Cloud/MatMaster ticket mode it must also report both `program_id` and
  `bohrium_config.project_id` with `type=int`; a quoted numeric string is
  invalid. Upload that profile's newly validated directory as a new outer job;
  never retry an outer job whose input snapshot was invalid. Direct/local
  profiles do not invent these outer-job checks.
  For Gamma properties, also read the representative-slab report: parent/final
  atom count, thickness, layer repeats, minimum distance, task count, and
  generated KPOINTS. Stop on any explicit limit violation.
9. **Screen image × machine before submit.** Before writing `global.json` or submitting, run:
  ```bash
   python scripts/validate_apex_combo.py list-combos --backend lammps --prefer gpu
   python scripts/validate_apex_combo.py check \
     --image registry.dp.tech/dptech/dp/native/prod-16664/dpa4-phonolammps:0.0.2 \
     --scass "c16_m120_1 * NVIDIA L20"
  ```
   Do **not** hardcode an unverified `scass_type`. Prefer `recommend` / `list-combos` output. Known failures include `deepmd-kit:3.1.0`, `3.1.1-cuda12.1`, `3.1.2`, the combination `deepmd-kit:3.1.1` × `NVIDIA T4`, `c4_m16_cpu`, and `c12_m46_1 * NVIDIA T4`. GPU LAMMPS potentials (`deepmd`, `mace`, `nep`) use `registry.dp.tech/dptech/dp/native/prod-16664/dpa4-phonolammps:0.0.2` with NVIDIA L20 by default; RTX 4090 remains a validated compatible option. CPU LAMMPS potentials use `registry.dp.tech/dptech/dp/native/prod-397637/apex-flow:1.3.0.post`; never pair 0.0.2 with a `*_cpu` machine because sequential CPU validation stalled before the container command started. `apex submit` only enforces 0.0.2 for GPU-potential phonon and Grüneisen workflows. Do not emit `plugin load libdeepmd_lmp.so` for 0.0.2.
   Do **not** hardcode an unverified `scass_type`. Prefer `recommend` / `list-combos` output. GPU LAMMPS potentials (`deepmd`, `mace`, `nep`) use `registry.dp.tech/dptech/dp/native/prod-16664/dpa4-phonolammps:0.0.2` with NVIDIA L20 by default; RTX 4090 is also validated. CPU LAMMPS potentials use the APEX CPU image. Never pair 0.0.2 with a `*_cpu` machine, and do not emit `plugin load libdeepmd_lmp.so` for its integrated USER-DEEPMD build.
   For DPA4, inspect the locked candidate matrix with
   `list-combos --runtime-profile dpa4-alloytongqi-t4`; `recommend` must fail
   until exact `ref@sha256` post-snapshot qualification is recorded. After
   publication, only one rank/one GPU on exact `c4_m15_1 * NVIDIA T4` is
   eligible. Other T4 SKUs (including c8/c16), every non-T4 GPU, CPU,
   multi-rank, multi-GPU, and cross-architecture PT2 reuse remain unverified or
   prohibited and therefore fail closed. V100/SM 7.0 and older GPUs, and an
   NVIDIA Linux driver below 580.65.06, are prohibited by the CUDA 13 runtime.
10. **Treat the bundled DPA4 checkpoint as provenance, not a LAMMPS input.** The
   skill ships only `models/DPA4-alloytongqi/model.pt`, the user-supplied
   single-task `alloytongqi` checkpoint. Generate the image-resident contract
   only with `generate_config.py create --runtime-profile
   dpa4-alloytongqi-t4`; never hand-write its paths/hashes. The command remains
   blocked until publication. No alternate-model downloader is bundled. See
   `models/README.md`.
11. **STOP: Check atom count / cell size before property submit — decide whether to expand.**
  APEX does not require a conventional cell. Do not convert a primitive cell or
   user-provided supercell to a conventional cell merely because an example uses
   `confs/std-fcc` or another `std-*` name.
  Before confirming property parameters, **always read the user's structure** and
  report: formula, atom count, lattice lengths, and whether it looks like a
  primitive / conventional / already-expanded supercell.
  Then decide with the user whether further bulk expansion is needed:
  - **Too small for the property** (typical primitive or tiny conventional cell)
    → recommend expanding; do not silently submit with an undersized cell.
  - **Already large enough / already a supercell** → ask whether to expand again;
    if no, set applicable volumetric `supercell` / `supercell_size` to `[1,1,1]`
    so APEX does not expand twice.
  - Keep `elastic.conventional` false unless the user explicitly requests a
    conventional-cell elastic calculation.
  - Slab properties (`surface` / `gamma` / `decohesive`) use `min_slab_size` /
    in-plane replication, not bulk `supercell`; confirm those separately.
  Use the helper wording and size guidance in
  `reference/workflow-control.md` → **Pre-Submission Structure Validation**.
12. **STOP: When the user gives a VASP POTCAR path, verify it and stage it into the job.**
   Host libraries such as `/share/PAW_PBE/...` are **not** available inside
   Bohrium/dflow containers. Leaving an absolute `potcar_prefix` in `param.json`
   causes `FileNotFoundError: .../Ti_pv/POTCAR` after upload.
   As soon as the user specifies a POTCAR library path:
   1. Confirm the path exists and is readable locally.
   2. Confirm every structure element has a POTCAR file under that library.
   3. Stage into the **job root** as flat files (e.g. `POTCAR_Ti`, `POTCAR_V`)
      via `generate_config.py create --potcar-prefix <lib> --potcars '...'`
      (or Agent `cp`/`mv`). Set `"potcar_prefix": "."`.
   4. Verify `param.json` uses `"potcar_prefix": "."` (not `/share/...`) and
      that `POTCAR_<Element>` files exist in the job directory; then run
      `validate_inputs.py`.
   If the library is missing/incomplete: **STOP**, tell the user the path is
   unusable, and ask for the correct POTCAR location. Never submit with an
   absolute host POTCAR path hoping the container can see it.
13. **MUST set DFT k-spacing; never rely on a hand-written KPOINTS/KPT file.**
   APEX auto-generates the k-mesh from spacing; omitting it fails at make time.
   - **VASP**: `INCAR` (or `cal_setting`) **must** contain `KSPACING`. Prefer also
     setting `KGAMMA` (`True` = Gamma-centered, `False` = Monkhorst-Pack).
     Do not hand-author per-task `KPOINTS`; APEX writes it from POSCAR + spacing.
   - **ABACUS**: `INPUT` **must** contain `kspacing` (1/Bohr), **or** set
     `cal_setting.K_POINTS` like `[nx, ny, nz, 0, 0, 0]`. APEX writes `KPT`.
   - **Screening defaults** (APEX is screening-oriented, not publication-grade):
     VASP `KSPACING=0.1–0.2` Å⁻¹; ABACUS `kspacing=0.20` (relax) / `0.15` (phonon SCF).
     Smaller spacing → denser mesh and much higher cost (~8× from 0.20→0.10 on ABACUS).
   - Confirm the spacing with the user before submit when they care about accuracy.
     Details: `reference/calculators.md` → k-spacing sections.
14. **STOP: Resolve VASP image via Bohrium `list_images` or a user-known authorized address — never invent a default.**
   VASP is commercial. There is **no** default `vasp_image_name`. Before any
   VASP `create` / submit, resolve an image by **exactly one** of these paths:
   1. **Private image discovery (preferred):** query the user's own private
      Bohrium Docker images filtered by keyword `vasp`:
      - If the MatMaster / Bohrium tool is available:
        `Bohrium(action="list_images", keyword="vasp")`
        (tool description: *list the user's own private Docker images
        (filtered by keyword)*).
      - Otherwise run the skill helper (same OpenAPI):
        ```bash
        python <skill-root>/scripts/list_bohrium_images.py \
          --keyword vasp --require
        ```
      Present matching image URL(s) to the user and get approval before use.
   2. **User-known authorized address:** the user explicitly provides a
      licensed/authorized VASP image path they are allowed to use.
   **If neither path yields an image → TERMINATE the VASP workflow.** Do not
   guess public tags (including `vasp:5.4.4-dflow`), do not submit, and tell
   the user that a private VASP image or an authorized image address is
   required. Only after a confirmed image exists, pass
   `--vasp-image <url>` to `generate_config.py create` (writes
   `vasp_image_name`).
15. **MUST use a Bohrium-safe VASP `vasp_run_command`.**
   After a licensed VASP image is resolved (Rule 14), `global.json` should use a
   command that sources Intel oneAPI, raises stack limit, and calls an absolute
   binary. Typical Bohrium layout:
   ```text
   bash -c "source <ONEAPI_SETVARS> && ulimit -s unlimited && mpirun -n <RANKS> <ABSOLUTE_VASP_BINARY>"
   ```
   Constraints:
   - Always `source /opt/intel/oneapi/setvars.sh` (Intel MPI / MKL env).
   - Always `ulimit -s unlimited` (avoids stack overflow on large cells).
   - Prefer an absolute `vasp_std`/`vasp_gam` binary path; adjust it for the
     user-approved image.
   - Align `<RANKS>` with the CPU count encoded by `scass_type`.
   - APEX selects the executable per generated task: Gamma-centered `1x1x1`
     uses `vasp_gam`; every other grid uses `vasp_std`. This applies to every
     property and relaxation, not only Gamma/GammaSurface workflows.
     `KGAMMA=True` alone is not proof—the generated `KPOINTS` is authoritative.
   - Any task that resolves to `vasp_gam` requires `KPAR=1`. In general,
     `KPAR` must divide ranks, and `NCORE` must divide ranks/KPAR. Do not
     combine `NCORE` and `NPAR`; missing `NCORE` is a warning.
   `generate_config.py` writes the run_command template for `--backend vasp`
   and sets `vasp_image_name` only from `--vasp-image`.
16. **STOP: Before submitting `gamma_surface`, run `apex preview` to check for overlapping atoms.**
   Disordered / RSS / non-standard cells often produce slab displacements with
   unphysically close atom pairs. Preview generates the displacement POSCARs
   and prints a text warning when any pair distance is `< 0.2` Å:
   ```bash
   apex preview param.json
   ```
   - **Must check stderr** for exactly:
     `Generated Gamma surface contains overlapping atoms.`
   - If that line appears → **STOP**, report it to the user, and do not submit
     until the slip system / cell / `supercell_size` / `closed_loop` choice is
     fixed and preview is clean.
   - **Do not open, read, or visually inspect the generated GIF.** The Agent
     only needs the stderr overlap warning (or its absence). The GIF is for
     optional human viewing only.
   - For human-requested diagnostics, `--gif-view` accepts `auto`, `default`,
     `slip-plane`, `parent-bc`, or `both`. `auto` is the default and writes the
     slip-plane and parent-`bc` projections for both Gamma lines and Gamma
     surfaces. The viewport retains projected unit-cell boundaries so vacuum
     is not cropped from side-facing projections. These views do not replace
     geometry validation.
   Skip ONLY if the job has no `gamma_surface` property.
17. **Gamma uses current parent-aware geometry and 20 Å vacuum defaults.** For
   RSS/SQS or other disordered cells classified as `other`, set
   `parent_lattice` to `bcc`, `fcc`, or `hcp`; APEX interprets the Miller plane
   and direction in that parent basis without symmetrizing the supplied cell.
   Both `gamma` and `gamma_surface` default to `vacuum_size=20`. For endpoint
   Gamma lines, use `displacement_points` (for example `[0.0, 0.5]`); values
   must be unique, finite, within `[0,1]`, and include the zero-energy
   reference. Read `gamma_geometry.json` and `slab_generation.json`; mapping,
   layer split, minimum distance, and parent-translation topology fail closed.
18. **Melting restart and evidence boundaries.** `melting_point` is the
   LAMMPS-only q6/interface-velocity workflow. If `cal_setting.restart_files`
   is provided, supply exactly one existing file per temperature; APEX copies
   the temperature-matched file into every replica as
   `restart.coexistence.start` and forwards it. This is transport only—the
   generated input does not automatically issue `read_restart`.
   `finite_t_latt` never receives this file. A temperature contributes to a
   bracket only when all configured, distinct replicas are present and agree;
   missing/duplicate/mixed replicas remain `inconclusive`.



## Supported Properties (15 types)


| Type             | JSON `type` value  | Backend     | Description                               |
| ---------------- | ------------------ | ----------- | ----------------------------------------- |
| EOS              | `eos`              | All         | Equation of state (volume-energy curve)   |
| Elastic          | `elastic`          | All         | Elastic constants Cij, B, G, E, ν         |
| Surface          | `surface`          | All         | Surface formation energy                  |
| Vacancy          | `vacancy`          | All         | Vacancy formation energy                  |
| Interstitial     | `interstitial`     | All         | Interstitial formation energy             |
| Phonon           | `phonon`           | All         | Phonon dispersion & DOS                   |
| Gamma line       | `gamma`            | All         | 1D generalized stacking fault energy      |
| Gamma surface    | `gamma_surface`    | All         | 2D GSFE map                               |
| Cohesive         | `cohesive`         | All         | Cohesive energy curve                     |
| Decohesive       | `decohesive`       | All         | Ideal work of separation                  |
| Finite-T lattice | `finite_t_latt`    | LAMMPS/VASP | Lattice parameter vs temperature (NPT MD) |
| Finite-T elastic | `finite_t_elastic` | LAMMPS only | Elastic constants at finite temperature   |
| Grüneisen        | `gruneisen`        | All         | Grüneisen parameters & thermal expansion  |
| Annealing        | `annealing`        | LAMMPS/VASP | Heat-hold-quench MD cycle                 |
| Melting point    | `melting_point`     | LAMMPS only | Two-phase coexistence melting bracket     |


> See `reference/properties.md` for full parameter details of each property.



## LAMMPS Potential Types (Quick Reference)


| `interaction.type` | pair_style                     | Model file      | GPU? |
| ------------------ | ------------------------------ | --------------- | ---- |
| `deepmd`           | `deepmd`                       | `.pb`, `.pth`, or compatible single-task `.pt` | Yes  |
| `mace`             | `mace no_domain_decomposition` | `.model`        | Yes  |
| `nep`              | `nep`                          | `nep.txt`       | Yes  |
| `eam_alloy`        | `eam/alloy`                    | `.eam.alloy`    | No   |
| `eam_fs`           | `eam/fs`                       | `.eam.fs`       | No   |
| `meam`             | `meam`                         | library + param | No   |


> See `reference/lammps_potentials.md` for the full table and examples.



## Input File Format (Minimal)



### param.json (calculation parameters)

```json
{
    "structures": ["confs/std-fcc"],
    "interaction": {
        "type": "eam_alloy",
        "model": "Cu01.eam.alloy",
        "type_map": "auto"
    },
    "relaxation": {
        "cal_setting": {"etol": 0, "ftol": 1e-10, "maxiter": 5000, "maximal": 500000}
    },
    "properties": [
        {"type": "eos", "vol_start": 0.8, "vol_end": 1.2, "vol_step": 0.05},
        {"type": "elastic", "norm_deform": 0.01, "shear_deform": 0.01}
    ]
}
```



### global.json (workflow/machine config)

See `reference/submission.md` for the full validated template.

## Key Additional Rules

1. **Finite-temperature backend limits**: `finite_t_elastic` and `melting_point` are LAMMPS-only. `finite_t_latt` and `annealing` support LAMMPS and VASP, but not ABACUS. VASP uses `MDALGO=3` and requires a binary compiled with `-Dtbdyn`; annealing `protocol="coexistence"` is a fixed-temperature equilibration plus production run and is not the q6/interface-velocity `melting_point` method. Only `melting_point` transports `restart_files` as `restart.coexistence.start`.
2. **Model paths must match the runtime contract.** Ordinary MLIP model files
   must be in the job directory and use relative paths. The DPA4 T4 profile is
   the exception: do not stage or execute the bundled `.pt`; its exact contract
   uses a hashed image-resident `.pt2` and is generated only after publication.
   Default to `"type_map": "auto"`.
3. **Joint workflow recommended.** Use `joint` flow (relaxation + properties) for most use cases to ensure proper relaxation before property calculations.
4. **GPU for ML potentials.** DeePMD, MACE, and NEP benefit from GPU acceleration. Set `scass_type` to a validated GPU SKU from `validate_apex_combo.py recommend --prefer gpu` (default: `"c16_m120_1 * NVIDIA L20"`; RTX 4090 remains compatible).
5. **Supercell sizing depends on the input atom count, not only the default JSON.**
   Treat defaults as targets for **unit-cell inputs**. First inspect the user's
   structure; if it is already large enough, prefer `[1,1,1]` after confirmation.
   Rough total-size guidance (after any expansion):
   - vacancy / interstitial: ≳ [2,2,2] conventional-cell equivalent
   - phonon / gruneisen / finite-T: ≳ [3,3,3] (smaller cells often fail)
   - surface / gamma / decohesive: ensure slab thickness / in-plane size, not bulk supercell
   If the cell is too small, ask the user to expand before submit. See
   `reference/workflow-control.md`.
6. **Cloud/MatMaster outer job machine.** Use `c1_m2_cpu` for that profile's outer Bohrium job since it only calls `apex submit` and waits. The installed Bohrium-direct profile has no outer job.



## RSS (Random Solid Solution) Workflow

For random solid solutions, solid solutions, high-entropy alloys, high-entropy
oxides/ceramics, and other high-entropy materials, use `apex rss` to generate
structures before property calculations. Read `reference/rss_workflow.md`
before asking the user questions or writing `rss.json`; it defines the required
QA, current JSON schema, output layout, and visualization fallback.

**Agent rules for RSS (mandatory):**
- Always set `"show_progress": false` in `rss.json`. tqdm step bars (default
  `max_steps=20000`) flood captured terminal output and waste context; do not
  leave progress enabled “to see if it is working.”
- After `apex rss`, judge success from files + metadata — not from live bars:
  count `conf_*/POSCAR`, then read `rss_metadata.json` for convergence /
  composition / duplicate warnings.
- If zero configs are written, do **not** re-call `generate_rss` from Python to
  bypass the CLI. Fix `rss.json` (`max_steps`, `interval`, `num_configs`,
  compositions, cell size) and re-run `apex rss` once; report the metadata
  diagnosis to the user.

## Working Test Case (Reference)

Successfully validated workflow (ID: `cu-fcc-elastic-v3-joint-sdfml`):

- **System**: Cu FCC elastic constants
- **Potential**: EAM (Cu01.eam.alloy, Mishin 2001)
- **Flow**: joint (relaxation + elastic properties), `scass_type`: `c16_m32_cpu`



## Scripts


| Script                     | Purpose                                                                           |
| -------------------------- | --------------------------------------------------------------------------------- |
| `generate_config.py`       | `create` a complete job or `refresh-global` credentials without changing param.json |
| `list_bohrium_images.py`   | List private Bohrium images by keyword (MatMaster `list_images` equivalent)       |
| `validate_apex_combo.py`   | List / check / recommend safe image × scass_type combos                           |
| `parse_results.py`         | Parse APEX output into summary                                                    |
| `validate_inputs.py`       | Validate configuration before submission                                          |




## Reference Index


| File                             | Content                                                                                                                                               |
| -------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| `reference/submission.md`        | Cloud/MatMaster outer-client authentication (ticket API + refresh), run.sh template, Bohrium config, RFC 1123 naming, and lifecycle; installed local editions use their profile-specific reference |
| `reference/workflow-control.md`  | Running-task status/count format, live Argo link, stopping/killing procedure, and structure validation                                                |
| `reference/properties.md`        | Complete parameter reference for all 15 property types                                                                                                |
| `reference/calculators.md`       | Detailed backend configuration (VASP, ABACUS, LAMMPS)                                                                                                 |
| `reference/lammps_potentials.md` | LAMMPS potential type details and examples                                                                                                            |
| `reference/rss_workflow.md`      | RSS structure generation workflow                                                                                                                     |
| `reference/examples.md`          | Complete worked examples for common scenarios                                                                                                         |
