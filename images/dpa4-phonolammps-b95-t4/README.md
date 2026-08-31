# DPA4 b95 + phonoLAMMPS T4 snapshot runtime

This directory records the runtime contract for the DPA4 `alloytongqi`
LAMMPS image. The deliverable image is produced by a **Bohrium filesystem
snapshot** after the exact runtime, model copies, and wrappers below have been
verified. It is not produced from `docs/Dockerfile.deepmd-phonolammps`, which
remains the old DeepMD 3.1.3 runtime.

The final registry reference, immutable digest, and Bohrium snapshot ID have
not been provided. They are therefore `null` in
`runtime-manifest.template.json`. Do not describe a tag, digest, snapshot, or
latest-APEX compatibility as final until publication and readback supply those
values and the exact digest passes the benchmark.

## Frozen build identity

| Component | Exact identity |
| --- | --- |
| Formal Python environment | `/opt/dpa4-phonolammps-3.2.0b0-py310` |
| DeepMD-kit | `3.2.0b0.post0+b95f21e9`, commit `b95f21e998f9c294f4d467cf1422d38fa6b5e80a` |
| PyTorch backend | `2.11.0+cu130`, commit `70d99e998b4955e0049d13a98d77ae1b14db1f45` |
| LAMMPS | `22 Jul 2025 Update 2`, tag `stable_22Jul2025_update2`, commit `a33449868448baf3c73d8eacdb2d329b13361696` |
| phonoLAMMPS | `0.10.1`; upstream version commit `c590fd77efdf5e196ea3c7b5245b168eec8e332f` |
| phono stack | phonopy `4.3.1`, dynaphopy `1.18.0` |

The phonoLAMMPS wheel has no `direct_url.json` and no upstream `0.10.1` tag.
The listed commit is the upstream commit that introduces version `0.10.1`;
the installed distribution does not independently bind its bytes to that
commit. The manifest preserves this provenance limitation instead of claiming
stronger evidence.

“Native CUDA OFF” means the DeepMD native core is the CPU variant
(`DP_VARIANT=cpu`); it does **not** mean the image is CPU-only. DPA4 GPU
inference uses the PyTorch backend built with CUDA 13.0 and the T4-specific
AOTI `.pt2`. The native DeepMD core does not link CUDA, while its PyTorch
backend does.

## Final model layout

Install and hash the three immutable model files before taking the snapshot:

| Role | Final path | SHA-256 |
| --- | --- | --- |
| Checkpoint / identity | `/opt/dpa4-runtime/models/DPA4-alloytongqi/model.pt` | `c84b268cc6191afc72bd2d5c001cbe526a0d2e04ebf6dbd7df021306e9abe9ad` |
| CPU diagnostic/parity only | `/opt/dpa4-runtime/models/DPA4-alloytongqi/alloytongqi.cpu-x86_64.pt2` | `d24525ed454c181354397d46ea62b6376b4b79e1df66a160e89160dbb2284dc9` |
| Production T4 runtime | `/opt/dpa4-runtime/models/DPA4-alloytongqi/alloytongqi.t4-sm75.pt2` | `2614db9463f5864d80a78fec037aeae26930df2004bb9f1148a69b83c25b3daf` |

The checkpoint is not a LAMMPS runtime model. The CPU `.pt2` is not a
production fallback. The APEX DPA4 production contract references only the T4
`.pt2` path.

On the build host, the current source files are:

```text
/opt/dpa4-phonolammps-3.2.0b0-py310/share/models/DPA4-alloytongqi/model.pt
/opt/dpa4-artifacts/cpu-x86_64/alloytongqi.pt2
/opt/dpa4-artifacts/t4-sm75/alloytongqi.pt2
```

From this directory, stage the snapshot payload with:

```bash
MODEL_DIR=/opt/dpa4-runtime/models/DPA4-alloytongqi
install -d -m 0755 "$MODEL_DIR" /opt/dpa4-runtime /usr/local/bin
install -m 0644 /opt/dpa4-phonolammps-3.2.0b0-py310/share/models/DPA4-alloytongqi/model.pt "$MODEL_DIR/model.pt"
install -m 0644 /opt/dpa4-artifacts/cpu-x86_64/alloytongqi.pt2 "$MODEL_DIR/alloytongqi.cpu-x86_64.pt2"
install -m 0644 /opt/dpa4-artifacts/t4-sm75/alloytongqi.pt2 "$MODEL_DIR/alloytongqi.t4-sm75.pt2"
install -m 0755 dpa4-lmp dpa4-phonolammps dpa4-python3 /usr/local/bin/
install -m 0755 dpa4-python3 /root/.bohrium/python3
install -m 0644 runtime-manifest.template.json /opt/dpa4-runtime/
```

Then verify all three model byte sizes and hashes against
`runtime-manifest.template.json`. Its
`models.final_paths_verified` field is `true` because the three final paths
were re-hashed on the snapshot source filesystem. This proves only the source
filesystem layout; it does not prove that a registry image contains those
bytes.

## Runtime wrappers

Install `dpa4-lmp`, `dpa4-phonolammps`, and `dpa4-python3` as executable files
in `/usr/local/bin`. Install the same `dpa4-python3` bytes at
`/root/.bohrium/python3`, which is the first `python3` on the snapshot image's
PATH. This is required because dflow's LAMMPS Python OP launches with the
literal command `python3`; without the shim it would resolve to the inherited
DeepMD 3.1.3 environment. All wrappers preserve every user argument with
`"$@"` and execute the exact formal-environment entry point. They set:

- formal-venv `PATH` and all required `LD_LIBRARY_PATH` prefixes, including
  the venv `lib` that supplies `libmpi.so.12`;
- `LAMMPS_PLUGIN_PATH` for `libdeepmd_lmpplugin.so` auto-loading;
- `DP_BACKEND_PLUGIN_PATH` for the PyTorch/PT-export backend;
- `DP_COMPILE_INFER`, `DP_AMP_INFER`, `DP_TRITON_INFER`,
  `DP_CUTE_INFER`, and `DP_TF32_INFER` to `0`;
- OpenMP and both DeepMD thread counts to `1`.

Generated DPA4 LAMMPS inputs must still place `atom_modify map yes` before
`read_data`. Do not add an explicit legacy plugin-load directive.

## Snapshot and promotion gate

Before asking Bohrium to snapshot the build host:

1. Copy the three models and install all three wrappers, including the exact
   `/root/.bohrium/python3` shim.
2. Re-hash every model and `critical_artifacts` entry in the manifest.
3. Confirm `dpa4-lmp` auto-loads `libdeepmd_lmpplugin.so` and exposes the
   `deepmd` pair style.
4. Confirm `dpa4-phonolammps --version` and the Python LAMMPS API both load;
   testing only `lmp -h` does not exercise the MPI loader path.
5. Run the small-cell benchmark in
   `apex/skills/apex-flow/benchmarks/dpa4-alloytongqi/` and retain its complete
   evidence workspace.
6. Take the Bohrium snapshot, publish it, read back the immutable registry
   digest, and then create a filled runtime manifest alongside this template.
7. Re-run the benchmark from the published digest before marking the runtime
   compatible.

The qualified production envelope is exactly one LAMMPS rank on one NVIDIA
Tesla T4 (compute capability 7.5), with the recorded baseline flags and T4
`.pt2`. Multi-rank execution, multiple GPUs, other GPU families, other flags,
and mutable/unidentified images are unqualified. The existing APEX default
image and the forced phonon/Gruneisen image remain the old image unless a
separate, evidence-backed routing change is made.
