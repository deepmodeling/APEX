# Bundled DPA4 model

> **Naming:** APEX **backend** = `lammps` / `abacus` / `vasp`.
> DPA4 alloytongqi is a DeePMD model used by the LAMMPS backend.

The skill ships exactly one model:

| Path | Size | Task/branch provenance |
|------|------|------------------------|
| `DPA4-alloytongqi/model.pt` | 30,403,297 bytes | Single/default task; `alloytongqi` supplied by the user |

SHA-256:
`c84b268cc6191afc72bd2d5c001cbe526a0d2e04ebf6dbd7df021306e9abe9ad`.

Safe static inspection confirms `model_params.type=dpa4`, fitting type
`dpa4_ener`, and `model_branch_alias=[]`. The file does not embed the literal
branch name or a Git commit, so `alloytongqi` is recorded as user-provided
provenance. Do not infer observed-element or training-domain support from the
type map alone.

## Runtime compatibility boundary

APEX keeps this old image as both its default LAMMPS image and its forced
LAMMPS phonon/Grüneisen image:

`registry.dp.tech/dptech/dp/native/prod-397637/deepmd-kit-phonolammps:3.1.3`

On 2026-08-10, the configured `registry.dp.tech` endpoint was pull-denied, but
the same repository path/tag was available from the Bohrium registry mirror.
The tested mirror artifact was linux/amd64 with repo digest
`sha256:43a27ca4a7bba7f774bbd56104d205a6a80cd9d65928f249f6109e9ef37b8402`,
DeepMD-kit 3.1.3, phonoLAMMPS 0.10.1, and LAMMPS 29 Aug 2024. Against the
bundled file matching the SHA-256 above:

- `dp show` failed with `RuntimeError: Unknown model type: dpa4`.
- LAMMPS 29 Aug 2024 aborted while initializing `pair_style deepmd`; it never
  reached `run 0`.

This proves that digest's DeepMD-kit 3.1.3 runtime cannot execute the model.
Retain the mirror registry domain and digest when reporting provenance; the
configured registry endpoint itself was not readable. Do not submit this model
with the default old image. Keep that image as the general default and legacy
phonon/Grüneisen forced runtime.

The new `dpa4-alloytongqi-t4` profile is still unpublished: its image ref and
digest are placeholders and its qualification is `pre_snapshot_only`.
Pre-snapshot checks completed 12 LAMMPS runs, 6 CPU/GPU parity comparisons,
and one phonoLAMMPS smoke on one rank/one GPU at
`c4_m15_1 * NVIDIA T4`. This is candidate evidence, not exact-image or registry
acceptance. c8/c16 T4, every non-T4 GPU, CPU, multi-rank, multi-GPU, and
cross-architecture PT2 reuse remain unverified or prohibited.
V100/SM 7.0 and older devices and NVIDIA Linux drivers below 580.65.06 are
prohibited by this CUDA 13 runtime contract.

## Agent workflow

1. Retain `models/DPA4-alloytongqi/model.pt` as the source identity; do not pass
   it to LAMMPS.
2. Inspect the candidate with `validate_apex_combo.py list-combos
   --runtime-profile dpa4-alloytongqi-t4`.
3. Stop while `recommend` and `generate_config.py create --runtime-profile
   dpa4-alloytongqi-t4` fail closed. After exact-image qualification, use the
   generated image-resident `.pt2` contract and audited wrapper commands only.
   The exact wrappers are `/usr/local/bin/dpa4-lmp` and
   `/usr/local/bin/dpa4-phonolammps`.

The skill contains no alternate DPA model or old-model downloader. APEX infers
a zero-based, contiguous element map from the structure; do not substitute
atomic numbers or model-internal indices.
