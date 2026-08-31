# Pre-snapshot evidence boundary

The benchmark manifest with SHA-256
`aa8984b51655150c8fbac3340a1c40ef9ea58f1ad24735b8cc3aaa1803eadb97`
records a successful run on the source Bohrium node only.

Its image reference and digest are synthetic pre-snapshot markers. They are
not a registry identity and must never unlock APEX submission or a Skill
recommendation.

That run used benchmark v1 and predates the current v2 complete
`FORCE_CONSTANTS` parser: it proved
the file was non-empty, but did not record all `N^2` ordered 3x3 blocks and
finite values in the manifest. The old manifest therefore does not satisfy the
current verifier even as source-node qualification evidence.

Two snapshot submissions through `lbg node tosnap` were rejected by the
Bohrium API with `record not found`: one used node ID `1508668`, and one used
the listed machine ID `1494489`. A filtered image readback found no matching
image record. Sensitive root metadata was restored after the failed attempt.
The snapshot and publication gates therefore remain open.

Promotion requires all of the following against the published immutable
`ref@sha256:digest`:

1. rerun the packaged 12 LAMMPS legs, six CPU/GPU parity checks, and the
   phonoLAMMPS smoke test;
2. rerun the latest-APEX make, RunLAMMPS, and post-processing path;
3. record the exact image identity, `c4_m15_1 * NVIDIA T4`, one visible T4,
   one MPI rank, wrapper hashes, PT2 hashes, and result evidence;
4. change the Skill profile from `pre_snapshot_only` to
   `post_snapshot_passed` only after all checks pass.
