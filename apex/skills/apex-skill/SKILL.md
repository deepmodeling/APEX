---
name: apex-skill
description: Batch multi-property materials calculations (EOS, elastic, surface, phonon, etc.) via VASP/ABACUS/LAMMPS backends orchestrated through dflow. Use when the user mentions APEX, apex submit, alloy property workflows, or multi-property DFT/MLIP screening.
---

# APEX Skill — Alloy Properties EXplorer

APEX is an automated workflow for computing alloy/material properties via batch DFT or MLIP calculations. It handles the full pipeline: structure preparation → task generation → computation → result extraction.

> **Version**: 1.3.0  
> **Paper**: DOI 10.1038/s41524-025-01580-y

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

1. **Prepare inputs** — Generate `param.json` + `global.json` + copy structure/model files into a job directory using `scripts/generate_config.py`
2. **Submit outer Bohrium job** — A thin client (`c2_m4_cpu`) that runs `apex submit` to connect to the dflow orchestration server
3. **dflow executes** — Inner containers (LAMMPS/ABACUS/VASP) run the actual calculations, managed by `workflows.deepmodeling.com`
4. **Retrieve results** — Outer job blocks until completion (no `-s` flag), auto-retrieves results; parse `confs/<structure>/<prop>_00/result.json`
5. **Present results** — Summarize in a table with physical units (GPa for elastic, J/m² for surface, eV for energies)

## Critical Rules

1. **STOP: Confirm calculation engine before submission — DO NOT PROCEED WITHOUT USER ANSWER.** Before preparing an APEX workflow, you MUST use `AskQuestion` to confirm the backend. Do NOT proceed with any default engine silently. Present options:
   - LAMMPS + MLIP (DeePMD/MACE/NEP): fast, GPU-friendly, requires trained model file
   - LAMMPS + classical (EAM/MEAM/SNAP): fast, CPU, limited to elements with available potentials
   - ABACUS (DFT): high accuracy, slower, needs pseudopotentials + orbital files
   - VASP (DFT): high accuracy, commercial license required, user must provide image
   
   **Skip ONLY if** the user has already explicitly stated the backend in THIS message (e.g. "用 EAM 算弹性常数" or "用 ABACUS 做EOS").
   
   **If AskQuestion times out or fails**: state your intended default in plain text (e.g. "我将使用 LAMMPS + EAM，如果需要更换请告诉我") and WAIT for user's next message before submitting. Never silently choose an engine.

2. **STOP: Confirm property parameters before submission — DO NOT PROCEED WITHOUT USER ANSWER.** Before submitting, present the full `properties` configuration (JSON) to the user. Show the defaults that will be used and highlight:
   - Miller indices (for surface/gamma/decohesive)
   - Supercell sizes (for vacancy/interstitial/phonon/gruneisen/finite-T)
   - Temperature ranges (for finite_t_latt/finite_t_elastic/annealing)
   - Number of deformation/step points
   
   Let the user approve or modify. **Skip ONLY if** the user provided explicit property parameters already.
   
   **If AskQuestion times out or fails**: display the parameters in your message and WAIT for confirmation before submitting.

3. **Two-layer architecture.** The outer Bohrium job is a thin submission client only. Never attempt `apex do` for production workflows — use `apex submit` which delegates to dflow. See `reference/submission.md` for the full architecture diagram.

4. **Kill = inner FIRST, outer SECOND.** If you only kill the outer Bohrium node, the dflow workflow continues consuming resources silently. Always terminate the inner dflow workflow first. See `reference/workflow-control.md`.

5. **Ticket refresh every submission.** Tickets expire in ~1 week. Always refresh via the ticket API before `apex submit`. See `reference/submission.md`.

6. **Project ID from environment only.** `generate_config.py` reads `BOHRIUM_PROJECT_ID` (or `--project-id`). Never hardcode a project ID (including old examples like `13529`) into `global.json`, docs, or prompts.

7. **Screen image × machine before submit.** Before writing `global.json` or submitting, run:
   ```bash
   python scripts/validate_apex_combo.py list-combos --backend lammps --prefer gpu
   python scripts/validate_apex_combo.py check \
     --image registry.dp.tech/dptech/deepmd-kit:3.1.3 \
     --scass "c8_m31_1 * NVIDIA T4"
   ```
   Do **not** hardcode an unverified `scass_type`. Prefer `recommend` / `list-combos` output. Known failures include `deepmd-kit:3.1.0`, `3.1.1-cuda12.1`, `3.1.2`, `c4_m16_cpu`, and `c12_m46_1 * NVIDIA T4`.

## Supported Properties (15 types)

| Type | JSON `type` value | Backend | Description |
|------|-------------------|---------|-------------|
| EOS | `eos` | All | Equation of state (volume-energy curve) |
| Elastic | `elastic` | All | Elastic constants Cij, B, G, E, ν |
| Surface | `surface` | All | Surface formation energy |
| Vacancy | `vacancy` | All | Vacancy formation energy |
| Interstitial | `interstitial` | All | Interstitial formation energy |
| Phonon | `phonon` | All | Phonon dispersion & DOS |
| Gamma line | `gamma` | All | 1D generalized stacking fault energy |
| Gamma surface | `gamma_surface` | All | 2D GSFE map |
| Cohesive | `cohesive` | All | Cohesive energy curve |
| Decohesive | `decohesive` | All | Ideal work of separation |
| Finite-T lattice | `finite_t_latt` | LAMMPS only | Lattice parameter vs temperature (NPT MD) |
| Finite-T elastic | `finite_t_elastic` | LAMMPS only | Elastic constants at finite temperature |
| Grüneisen | `gruneisen` | All | Grüneisen parameters & thermal expansion |
| Annealing | `annealing` | LAMMPS only | Heat-hold-quench MD cycle |

> See `reference/properties.md` for full parameter details of each property.

## LAMMPS Potential Types (Quick Reference)

| `interaction.type` | pair_style | Model file | GPU? |
|--------------------|-----------|------------|------|
| `deepmd` | `deepmd` | `.pb` or `.pth` | Yes |
| `mace` | `mace no_domain_decomposition` | `.model` | Yes |
| `nep` | `nep` | `nep.txt` | Yes |
| `eam_alloy` | `eam/alloy` | `.eam.alloy` | No |
| `eam_fs` | `eam/fs` | `.eam.fs` | No |
| `meam` | `meam` | library + param | No |

> See `reference/lammps_potentials.md` for the full table and examples.

## Input File Format (Minimal)

### param.json (calculation parameters)

```json
{
    "structures": ["confs/std-fcc"],
    "interaction": {
        "type": "eam_alloy",
        "model": "Cu01.eam.alloy",
        "type_map": {"Cu": 0}
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

8. **LAMMPS-only properties**: `finite_t_latt`, `finite_t_elastic`, and `annealing` ONLY work with LAMMPS backends. Inform users of this limitation if they request these with VASP/ABACUS.

9. **Model files must be in job directory.** For MLIP workflows, the model file (`.pb`, `.pth`, `.model`, etc.) must be present in the submitted directory. Use relative paths in `param.json`.

10. **Joint workflow recommended.** Use `joint` flow (relaxation + properties) for most use cases to ensure proper relaxation before property calculations.

11. **GPU for ML potentials.** DeePMD, MACE, and NEP benefit from GPU acceleration. Set `scass_type` to a validated GPU SKU from `validate_apex_combo.py recommend --prefer gpu` (default: `"c8_m31_1 * NVIDIA T4"`).

12. **Supercell sizing.** For defect calculations (vacancy, interstitial), use at least [2,2,2] supercell. For phonon, [3,3,3] recommended (phonoLAMMPS may fail with [2,2,2]).

13. **Outer job machine.** The outer Bohrium job only needs `c2_m4_cpu` since it just calls `apex submit`. Don't waste GPU resources on the submission client.

## RSS (Random Solid Solution) Workflow

For high-entropy alloys/ceramics, use `apex rss` to generate structures, then run property calculations on them. See `reference/rss_workflow.md` for full details.

## Working Test Case (Reference)

Successfully validated workflow (ID: `cu-fcc-elastic-v3-joint-sdfml`):
- **System**: Cu FCC elastic constants
- **Potential**: EAM (Cu01.eam.alloy, Mishin 2001)
- **Flow**: joint (relaxation + elastic properties), `scass_type`: `c16_m32_cpu`

## Scripts

| Script | Purpose |
|--------|---------|
| `generate_config.py` | Generate global.json + param.json with ticket auth; requires `BOHRIUM_PROJECT_ID` |
| `validate_apex_combo.py` | List / check / recommend safe image × scass_type combos |
| `parse_results.py` | Parse APEX output into summary |
| `validate_inputs.py` | Validate configuration before submission |

## Reference Index

| File | Content |
|------|---------|
| `reference/submission.md` | Authentication (ticket API + refresh), run.sh template, Bohrium config (images/machines), global.json template, RFC 1123 naming, submission lifecycle |
| `reference/workflow-control.md` | Key commands table, stopping/killing procedure, pre-submission structure validation |
| `reference/properties.md` | Complete parameter reference for all 15 property types |
| `reference/calculators.md` | Detailed backend configuration (VASP, ABACUS, LAMMPS) |
| `reference/lammps_potentials.md` | LAMMPS potential type details and examples |
| `reference/rss_workflow.md` | RSS structure generation workflow |
| `reference/examples.md` | Complete worked examples for common scenarios |
