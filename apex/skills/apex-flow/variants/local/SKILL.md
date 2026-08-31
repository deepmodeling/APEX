---
name: apex-flow
description: Run locally installed APEX relaxation and 15-property workflows through Bohrium direct, local debug, or Slurm/PBS with VASP, ABACUS, or LAMMPS. Includes parent-aware Gamma line/surface and diagnostic views, finite-temperature and two-phase melting-point workflows, restart transport, RSS/high-entropy generation, monitoring, reporting, and result extraction.
---

# APEX Flow — Local Agent Edition

Use APEX for automated relaxation and batch property workflows. This edition is
installed on the same machine as the Agent and must contain
`reference/execution-profile.md`, selected during installation.

## Start Here

1. Read `reference/execution-profile.md`. If it is missing, stop and ask the
   user to reinstall with `apex skill`.
2. Follow that profile's authentication, global configuration, and submit
   command. Do not mix commands from another profile.
3. Confirm the calculator backend: LAMMPS, ABACUS, or VASP.
4. Read the input structure and report formula, atom count, lattice lengths,
   and whether it is already a supercell.
5. Present the complete property JSON and wait for user approval.

## Execution Profiles

- **Bohrium cloud (direct)**: use `apex account`; run `apex submit` directly
  from this machine. A saved AccessKey is exchanged by dflow for a short-lived
  ticket, but no ticket is serialized into `global.json` and no outer Bohrium
  submission job is created.
- **local**: use `apex submit -d` with Local/Shell configuration. No Bohrium
  login, ticket, cloud image, or Argo server is required.
- **local cluster**: use `apex submit -d` and DPDispatcher with Local +
  Slurm/PBS from a login node. Confirm scheduler resources and modules first.

The exact installed choice is in `reference/execution-profile.md`.

## Workflow

1. Prepare structures, calculator inputs, models/potentials, `param.json`, and
   the profile-specific `global.json`.
2. Validate inside the job directory:

   ```bash
   python <skill-root>/scripts/validate_inputs.py \
     --param param.json --global global.json
   ```

   For Gamma properties, read the printed representative-slab report and stop
   on atom-limit, thickness, distance, or KPOINTS errors. Missing
   `min_slab_height`/`max_atoms` is a compatibility warning that requires
   manual confirmation.
3. For `gamma_surface`, run `apex preview param.json` and stop if stderr
   contains `Generated Gamma surface contains overlapping atoms.`
   The default `--gif-view auto` writes both Gamma/GammaSurface projections;
   human-requested diagnostics can select `default`, `slip-plane`, `parent-bc`,
   or `both`. Agents still decide safety from text validation and the overlap
   warning, not by opening the GIF.
4. Run the submit command from the installed execution profile without `-s`
   unless the user explicitly requests submit-only lifecycle management.
5. Preserve the workflow ID, monitor it when applicable, and verify result
   retrieval.
6. Read `confs/<structure>/<property>_00/result.json`. Use
   `scripts/parse_results.py` for multi-result summaries. Use `apex archive`
   only when a consolidated `all_result.json`, database storage, or
   `apex report` is required.

## Calculator Rules

- **LAMMPS + DeePMD/DPA**: treat the bundled DPA4 single-task checkpoint
  `models/DPA4-alloytongqi/model.pt` as provenance, not a direct LAMMPS input.
  Use `"type_map": "auto"`. The old image's Bohrium registry mirror (digest
  `sha256:43a27ca4a7bba7f774bbd56104d205a6a80cd9d65928f249f6109e9ef37b8402`)
  fails with `Unknown model type: dpa4`, so do not submit this model with the
  default old LAMMPS image. The candidate `dpa4-alloytongqi-t4` profile is
  `pre_snapshot_only` and must fail closed until an exact image ref/digest
  passes the packaged benchmark. Its only tested candidate is one rank on one
  `c4_m15_1 * NVIDIA T4`; other T4 SKUs, non-T4 GPUs, CPU, multi-rank, and
  multi-GPU remain unverified/prohibited. Do not route automatically.
  After publication, require absolute `/usr/local/bin/dpa4-lmp` and
  `/usr/local/bin/dpa4-phonolammps` entrypoints from the generated profile.
- **VASP**: confirm a licensed executable/image appropriate to the selected
  profile. Verify every POTCAR locally. Cloud-only image discovery rules apply
  only to the Bohrium profile.
- **ABACUS/VASP k-points**: VASP must set `KSPACING`; ABACUS must set
  `kspacing` or `cal_setting.K_POINTS`.
- **VASP executable selection**: APEX reads each generated task `KPOINTS`.
  Gamma-centered `1x1x1` always uses `vasp_gam`; every other grid uses
  `vasp_std`, for all properties and relaxation. `KGAMMA=True` alone is not
  proof. A task selected for `vasp_gam` requires `KPAR=1`. MPI ranks must
  match the Bohrium CPU count; `KPAR` must divide ranks and `NCORE` must
  divide ranks/KPAR. Do not combine `NCORE` with `NPAR`; a missing `NCORE`
  is a warning.
- Model, pseudopotential, orbital, and structure paths must be valid from the
  execution environment; do not assume a host path exists in a container or
  on a compute node.

See `reference/calculators.md` and `reference/lammps_potentials.md`.

## Property Rules

- Supported types and complete defaults are in `reference/properties.md`.
- `finite_t_elastic` and `melting_point` are LAMMPS-only.
- `finite_t_latt` and `annealing` support LAMMPS and VASP, but not ABACUS. VASP uses `MDALGO=3` and requires a binary compiled with `-Dtbdyn`; annealing `protocol="coexistence"` is a fixed-temperature equilibration plus production run.
- For vacancy/interstitial/phonon/Grüneisen/finite-T calculations, confirm the
  final atom count after expansion. Avoid expanding an existing supercell
  twice; use `[1,1,1]` after user confirmation when appropriate.
- For gamma/gamma-surface, start from the recommended crystallographic tables
  in `reference/properties.md`. Non-tabulated systems are allowed only when the
  direction lies on the plane; report the warning and inspect generated
  geometry. For disordered RSS/SQS cells, set `parent_lattice` explicitly.
- Gamma and GammaSurface default to 20 Å vacuum. Gamma line supports explicit
  `displacement_points`, which must include `0`; the task count is the number
  of supplied fractions.
- For `melting_point`, confirm temperatures, replicas, final atom count, stage
  lengths, task count, and resources. Optional `restart_files` must contain one
  existing file per temperature and are forwarded to each matching replica as
  `restart.coexistence.start`; the generated input is not changed to
  `read_restart`. `finite_t_latt` never receives this file. Missing, duplicate,
  or disagreeing configured replicas keep the bracket `inconclusive`.
- Generate optional Gamma overrides with the material-independent
  `--gamma-*` arguments documented in `reference/properties.md`. Always show
  the final Gamma JSON and expected task count before submission.

## RSS

For random solid solutions and high-entropy materials, read
`reference/rss_workflow.md`. Set `"show_progress": false`, run `apex rss`, and
judge success from generated `conf_*/POSCAR` files plus `rss_metadata.json`.

## Safety

- Never store credentials in `param.json`, commit them, or print passwords.
- Never silently choose a backend, potential, VASP license resource,
  crystallographic plane, temperature range, or cluster queue.
- Stop on failed validation. Do not describe a workflow as successful until
  expected result files exist.
- To terminate a cloud workflow, terminate the inner dflow workflow before any
  wrapper process. See `reference/workflow-control.md`.
