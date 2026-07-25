---
name: apex-flow
description: Run APEX alloy-property workflows from a locally installed Agent using Bohrium cloud, local debug execution, or a local Slurm/PBS cluster. Supports VASP, ABACUS, LAMMPS, RSS generation, workflow monitoring, and result extraction.
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
  from this machine. No access-key ticket and no outer Bohrium submission job.
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
4. Run the submit command from the installed execution profile without `-s`
   unless the user explicitly requests submit-only lifecycle management.
5. Preserve the workflow ID, monitor it when applicable, and verify result
   retrieval.
6. Read `confs/<structure>/<property>_00/result.json`. Use
   `scripts/parse_results.py` for multi-result summaries. Use `apex archive`
   only when a consolidated `all_result.json`, database storage, or
   `apex report` is required.

## Calculator Rules

- **LAMMPS + DeePMD/DPA**: use the bundled frozen model
  `models/DPA-3.2-5M/DPA-3.2-5M-OMat24.pth` unless the user requests another
  compatible model. Copy it into the job and use `"type_map": "auto"`.
- **VASP**: confirm a licensed executable/image appropriate to the selected
  profile. Verify every POTCAR locally. Cloud-only image discovery rules apply
  only to the Bohrium profile.
- **ABACUS/VASP k-points**: VASP must set `KSPACING`; ABACUS must set
  `kspacing` or `cal_setting.K_POINTS`.
- **VASP Gamma executable**: use `vasp_gam` only when validation proves the
  generated grid is Gamma-centered `1x1x1`; `KGAMMA=True` alone is not proof.
  It requires `KPAR=1`. MPI ranks must match the Bohrium CPU count; `KPAR`
  must divide ranks and `NCORE` must divide ranks/KPAR. Do not combine
  `NCORE` with `NPAR`; a missing `NCORE` is a warning.
- Model, pseudopotential, orbital, and structure paths must be valid from the
  execution environment; do not assume a host path exists in a container or
  on a compute node.

See `reference/calculators.md` and `reference/lammps_potentials.md`.

## Property Rules

- Supported types and complete defaults are in `reference/properties.md`.
- `finite_t_latt`, `finite_t_elastic`, and `annealing` are LAMMPS-only.
- For vacancy/interstitial/phonon/Grüneisen/finite-T calculations, confirm the
  final atom count after expansion. Avoid expanding an existing supercell
  twice; use `[1,1,1]` after user confirmation when appropriate.
- For gamma/gamma-surface/decohesive, use the canonical crystallographic tables
  in `reference/properties.md`; do not invent slip systems.
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
