# DPA4 alloytongqi

- File: `model.pt`
- Model type: DPA4 (`dpa4_ener` fitting)
- Task form: single/default task (`model_branch_alias=[]`)
- Branch provenance: `alloytongqi`, supplied by the user; not embedded in the checkpoint
- Size: 30,403,297 bytes
- SHA-256: `c84b268cc6191afc72bd2d5c001cbe526a0d2e04ebf6dbd7df021306e9abe9ad`
- APEX default/phonon/Grüneisen image: `registry.dp.tech/dptech/dp/native/prod-397637/deepmd-kit-phonolammps:3.1.3`
- Compatibility result: the old tag's Bohrium registry mirror, repo digest
  `sha256:43a27ca4a7bba7f774bbd56104d205a6a80cd9d65928f249f6109e9ef37b8402`,
  failed with `RuntimeError: Unknown model type: dpa4`; LAMMPS aborted while
  initializing `pair_style deepmd`.

Keep `model.pt` as the source identity; never pass it directly to LAMMPS under
the DPA4 T4 profile. That profile is currently locked by image placeholders and
`pre_snapshot_only` qualification. Pre-snapshot checks passed on one rank/one
GPU at `c4_m15_1 * NVIDIA T4`, but this is not exact-image acceptance; c8/c16
T4 and non-T4 GPUs remain unverified. After an immutable digest rerun, generate
the hashed image-resident `.pt2` interaction with `--runtime-profile
dpa4-alloytongqi-t4`; its generated commands use absolute
`/usr/local/bin/dpa4-lmp` and `/usr/local/bin/dpa4-phonolammps` wrappers. The configured `registry.dp.tech` endpoint was
pull-denied; the legacy digest above identifies the tested artifact from
`registry.bohrium.dp.tech`.
