---
name: apex-flow
description: Batch multi-property materials calculations (EOS, 0K elastic constant, surface energy, phonon, finite temperature elastic constant, gamma surface, gamma line, cohesive energy) and random-solid-solution structure generation via APEX calculator backends VASP/ABACUS/LAMMPS. The bundled DPA-3.2-5M OMat24 model is a DeePMD potential for LAMMPS. Use when the user mentions APEX, apex, alloy property, generate/give random solid solution, generate/give solid solution, generate/give high-entropy alloy, generate/give high-entropy oxide, generate/give high-entropy material, or multi-property DFT/MLIP screening.
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

1. **Prepare inputs** — Check `BOHRIUM_ACCESS_KEY`, then generate `param.json` + `global.json` (including a fresh ticket) and copy structure/model files into a job directory using `scripts/generate_config.py create ...`. Never hand-write either JSON file.
2. **Submit outer Bohrium job** — A lightweight client (`c1_m2_cpu`, recommended) that runs `apex submit ...` without `-s`, connects to the dflow orchestration server, and waits for completion
3. **dflow executes** — Inner containers (LAMMPS/ABACUS/VASP) run the actual calculations, managed by `workflows.deepmodeling.com`
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
   **Step B — only if Step A is LAMMPS + DeePMD/DPA: use the model bundled with this skill.**
  - Copy `models/DPA-3.2-5M/DPA-3.2-5M-OMat24.pth` into the job directory.
  - This is the frozen, single-task `OMat24` branch of DPA-3.2-5M. It is
    ready for APEX/LAMMPS, includes oxygen, and has 89 observed elements.
  - Set `interaction.model` to the copied filename and set `"type_map": "auto"`.
  APEX reads the structure at submission time and writes a zero-based,
  contiguous element map. Do not derive indices from atomic numbers or the
  model's internal type order.
  - Do not invent a model path, download another model, or use the source multi-head `.pt` directly. Use a user-provided or separately frozen task head only when the user explicitly requests it or the bundled OMat24 model is unsuitable; explain the choice and obtain confirmation first.
   **Skip Step A/B ONLY if** the user already stated them in THIS message
   (e.g. “用 EAM”, “用 ABACUS 做 EOS”, “用 DPA-3.2-5M-OMat24.pth”).
   **If AskQuestion times out or fails**: state the intended APEX backend and bundled model selection (if LAMMPS+DPA) in plain text and WAIT. Never silently submit.
2. **STOP: Confirm property parameters before submission — DO NOT PROCEED WITHOUT USER ANSWER.** Before submitting, present the full `properties` configuration (JSON) to the user. Show the defaults that will be used and highlight:
  - Miller indices / slip systems (for surface/gamma/gamma_surface/decohesive)
  - Supercell sizes (for vacancy/interstitial/phonon/gruneisen/finite-T)
  - Temperature ranges (for finite_t_latt/finite_t_elastic/annealing)
  - Number of deformation/step points
   For crystallographic planes:
  - `gamma` / `gamma_surface`: pick from the canonical FCC/BCC/HCP table in
    repository **README §4.10** (see also `reference/properties.md` §8–9). Do not
    invent slip systems; do not silently change an approved plane/direction.
  - `decohesive`: pick `miller_index` from the crystal-family table in
    **README §4.5** / `reference/properties.md` §10 (FCC/BCC/Diamond/ZB/Rocksalt/
    HCP/Perovskite). HCP must use **3-index** only.
   Let the user approve or modify. **Skip ONLY if** the user provided explicit property parameters already.
   **If AskQuestion times out or fails**: display the parameters in your message and WAIT for confirmation before submitting.
3. **Two-layer architecture.** The outer Bohrium job is a thin submission client only. Never attempt `apex do` for production workflows — use `apex submit` which delegates to dflow. See `reference/submission.md` for the full architecture diagram.
  For agent-managed Bohrium runs, always use `apex submit ...` without `-s`.
   The outer client must remain active while dflow runs so APEX can monitor the
   workflow and retrieve results automatically.
   Immediately preserve the exact inner dflow workflow ID printed by
   `apex submit` and report it to the user. Keep this ID available for all later
   monitoring, retrieval, and workflow-control actions; do not confuse it with
   the outer Bohrium job ID. Follow the exact status-query and reporting
   protocol in `reference/workflow-control.md`; never infer workflow identity
   or material identity from the outer job name alone. Use the returned
   workflow/step phases and durations to track progress. A
   long-running or failed step should be investigated by its step ID/key rather
   than treated as successful completion. After the workflow reaches
   `Succeeded`, verify that automatic result retrieval completed as described
   in `reference/submission.md`.
4. **Kill = inner FIRST, outer SECOND.** If you only kill the outer Bohrium node, the dflow workflow continues consuming resources silently. Always terminate the inner dflow workflow first. See `reference/workflow-control.md`.
5. **MUST use `generate_config.py`; never hand-write `param.json` or `global.json`.**
  - Create the complete job with `python <skill-root>/scripts/generate_config.py create ...`.
  - For multiple structures, pass repeated/space-separated `--structure` and/or `--structure-dir` to `create` (it copies each into `confs/<name>/` and fills `structures`); do not hand-edit `structures` after create.
  - To preserve an approved `param.json` while refreshing credentials, run
    `python <skill-root>/scripts/generate_config.py refresh-global --global global.json`
    from the task directory. This updates only `global.json`.
  - Do not invent unsupported flags or call the ticket API directly.
6. **Generate the ticket before packaging the job; never refresh it in `run.sh`.**
  - First inspect the agent/local environment for `BOHRIUM_ACCESS_KEY`.
  - If it is missing, STOP and ask the user to provide/configure it. `generate_config.py` cannot generate a ticket without an access key.
  - If it exists, use `create` for a new job or `refresh-global` for an existing job; both convert the key to a fresh ticket and write it to `global.json`.
  - Verify that `global.json` contains a non-empty `bohrium_config.ticket` before submission.
  - `run.sh` must only install/verify APEX and call `apex submit`. Do not add ticket API calls or depend on `BOHRIUM_ACCESS_KEY` inside the APEX container.
  - Install with `python3 -m pip install --upgrade --no-cache-dir apex-flow`.
   See `reference/submission.md`.
7. **Project ID from environment only.** `generate_config.py` reads `BOHRIUM_PROJECT_ID` (or `--project-id`). Never hardcode a project ID (including old examples like `13529`) into `global.json`, docs, or prompts.
8. **Hard-validate inside the task directory before every upload.** Run:
  ```bash
  cd <job-dir>
  python <skill-root>/scripts/validate_inputs.py \
    --param param.json --global global.json
  ```
  Do not upload or submit unless it prints `Validation PASSED` and reports both
  `program_id` and `bohrium_config.project_id` with `type=int`. A quoted numeric
  string is invalid. Upload the newly validated directory as a new outer job;
  never retry an outer job whose input snapshot was invalid.
9. **Screen image × machine before submit.** Before writing `global.json` or submitting, run:
  ```bash
   python scripts/validate_apex_combo.py list-combos --backend lammps --prefer gpu
   python scripts/validate_apex_combo.py check \
     --image registry.dp.tech/dptech/dp/native/prod-397637/deepmd-kit-phonolammps:3.1.3 \
     --scass "c8_m31_1 * NVIDIA T4"
  ```
   Do **not** hardcode an unverified `scass_type`. Prefer `recommend` / `list-combos` output. Known failures include `deepmd-kit:3.1.0`, `3.1.1-cuda12.1`, `3.1.2`, the combination `deepmd-kit:3.1.1` × `NVIDIA T4`, `c4_m16_cpu`, and `c12_m46_1 * NVIDIA T4`. The default LAMMPS image is `registry.dp.tech/dptech/dp/native/prod-397637/deepmd-kit-phonolammps:3.1.3`; `apex submit` enforces it for LAMMPS phonon and Grüneisen workflows.
10. **MUST use the bundled frozen DPA model under** `models/` **for LAMMPS + DeePMD unless the user explicitly requests another compatible model.** The skill ships
   `models/DPA-3.2-5M/DPA-3.2-5M-OMat24.pth`, a ready-to-run frozen
   DPA-3.2-5M OMat24 model. Copy it into the job directory before generating
   `param.json`. The multi-head source checkpoint is **not** in the skill zip —
   fetch it only when the user explicitly needs another task head
   (`scripts/fetch_models.py --source-checkpoint` or
   `dp --pt pretrained download DPA-3.2-5M`) and freeze that head before use.
   See `models/README.md`.
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
15. **MUST use a Bohrium-safe VASP `vasp_run_command` — never bare `vasp_std`.**
   After a licensed VASP image is resolved (Rule 14), `global.json` should use a
   command that sources Intel oneAPI, raises stack limit, and calls an absolute
   binary. Typical Bohrium layout:
   ```text
   bash -c "source /opt/intel/oneapi/setvars.sh && ulimit -s unlimited && mpirun -n <N> /opt/vasp.5.4.4/bin/vasp_std"
   ```
   Constraints:
   - Always `source /opt/intel/oneapi/setvars.sh` (Intel MPI / MKL env).
   - Always `ulimit -s unlimited` (avoids stack overflow on large cells).
   - Prefer absolute binary path (PATH `vasp_std` is unreliable); adjust path if
     the user-approved image differs.
   - Align `<N>` with `scass_type` CPU count (`c32_*` → `-n 32`, `c16_*` → `-n 16`).
   - Do **not** use bare `mpirun -n 16 vasp_std`.
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
   Skip ONLY if the job has no `gamma_surface` property.



## Supported Properties (14 types)


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
| Finite-T lattice | `finite_t_latt`    | All         | Lattice parameter vs temperature (NPT MD) |
| Finite-T elastic | `finite_t_elastic` | LAMMPS only | Elastic constants at finite temperature   |
| Grüneisen        | `gruneisen`        | All         | Grüneisen parameters & thermal expansion  |
| Annealing        | `annealing`        | All         | Heat-hold-quench MD cycle                 |


> See `reference/properties.md` for full parameter details of each property.



## LAMMPS Potential Types (Quick Reference)


| `interaction.type` | pair_style                     | Model file      | GPU? |
| ------------------ | ------------------------------ | --------------- | ---- |
| `deepmd`           | `deepmd`                       | `.pb` or `.pth` | Yes  |
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

1. **LAMMPS-only properties**: `finite_t_elastic` only works with LAMMPS. `finite_t_latt` and `annealing` also support VASP Langevin–Parrinello–Rahman NpT and ABACUS Nose–Hoover-style NpT.
2. **Model files must be in job directory.** For MLIP workflows, the model file (`.pb`, `.pth`, `.model`, etc.) must be present in the submitted directory. Use relative paths in `param.json`. For DeePMD/DPA, copy `models/DPA-3.2-5M/DPA-3.2-5M-OMat24.pth`. Default to `"type_map": "auto"` for every LAMMPS interaction; specify a dictionary only when the user explicitly needs a fixed custom ordering.
3. **Joint workflow recommended.** Use `joint` flow (relaxation + properties) for most use cases to ensure proper relaxation before property calculations.
4. **GPU for ML potentials.** DeePMD, MACE, and NEP benefit from GPU acceleration. Set `scass_type` to a validated GPU SKU from `validate_apex_combo.py recommend --prefer gpu` (default: `"c8_m31_1 * NVIDIA T4"`).
5. **Supercell sizing depends on the input atom count, not only the default JSON.**
   Treat defaults as targets for **unit-cell inputs**. First inspect the user's
   structure; if it is already large enough, prefer `[1,1,1]` after confirmation.
   Rough total-size guidance (after any expansion):
   - vacancy / interstitial: ≳ [2,2,2] conventional-cell equivalent
   - phonon / gruneisen / finite-T: ≳ [3,3,3] (smaller cells often fail)
   - surface / gamma / decohesive: ensure slab thickness / in-plane size, not bulk supercell
   If the cell is too small, ask the user to expand before submit. See
   `reference/workflow-control.md`.
6. **Outer job machine.** Use `c1_m2_cpu` for the outer Bohrium job since it only calls `apex submit` and waits. Don't waste larger CPU or GPU resources on the submission client.



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
| `fetch_models.py`          | Optional: download the DPA-3.2-5M multi-head source `.pt` for freezing another head |
| `parse_results.py`         | Parse APEX output into summary                                                    |
| `validate_inputs.py`       | Validate configuration before submission                                          |




## Reference Index


| File                             | Content                                                                                                                                               |
| -------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| `reference/submission.md`        | Authentication (ticket API + refresh), run.sh template, Bohrium config (images/machines), global.json template, RFC 1123 naming, submission lifecycle |
| `reference/workflow-control.md`  | Running-task status/count format, live Argo link, stopping/killing procedure, and structure validation                                                |
| `reference/properties.md`        | Complete parameter reference for all 14 property types                                                                                                |
| `reference/calculators.md`       | Detailed backend configuration (VASP, ABACUS, LAMMPS)                                                                                                 |
| `reference/lammps_potentials.md` | LAMMPS potential type details and examples                                                                                                            |
| `reference/rss_workflow.md`      | RSS structure generation workflow                                                                                                                     |
| `reference/examples.md`          | Complete worked examples for common scenarios                                                                                                         |


