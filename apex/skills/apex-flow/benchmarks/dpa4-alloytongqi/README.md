# DPA4 alloytongqi small-cell compatibility benchmark

This directory is a reproducible, fail-closed runtime benchmark for the exact
bundled training checkpoint:

- model: `../../models/DPA4-alloytongqi/model.pt`
- SHA-256: `c84b268cc6191afc72bd2d5c001cbe526a0d2e04ebf6dbd7df021306e9abe9ad`
- benchmark ID: `dpa4-alloytongqi-small-cell-gpu-compat-v2`

The `.pt` checkpoint is **never** passed to LAMMPS. The current DPA4 LAMMPS
backend accepts a device-specific AOTI `.pt2` runtime artifact; CPU and T4 GPU
legs therefore require independently frozen `.pt2` files with distinct hashes.

It tests checkpoint inspection, the DeepMD LAMMPS plugin, short LAMMPS `run 0` and NVE
MD, CPU–GPU numerical parity, and the APEX-shaped phonoLAMMPS path. It does not
claim that a tiny two-atom cell is scientifically converged, and it does not by
itself prove a complete dflow/Bohrium APEX workflow.

`manifest.example.json` is intentionally `untested`. Do not change it to
`passed`; create a new evidence workspace and let `build_manifest.py` derive the
status from files produced by actual execution.

## Evidence states

- `untested`: no runtime command has produced evidence.
- `inconclusive`: a command may have run, but image digest, package version,
  GPU-use proof, or another required identity field is absent.
- `failed`: an executed command failed, emitted a fatal signature, produced no
  parseable finite observables or complete finite `FORCE_CONSTANTS`, or exceeded
  a parity gate.
- `passed`: all 12 LAMMPS runs, all six CPU–GPU comparisons, and the Ti T4 GPU
  phonoLAMMPS smoke passed with a full image digest, one consistent CPU `.pt2`,
  one consistent T4 `.pt2`, distinct runtime hashes, and complete evidence.

Only `passed` is positive compatibility evidence. Never promote an
`inconclusive` run based on a successful shell exit alone.

## What is generated

`generate_cases.py` writes independent POSCAR, LAMMPS data, and input files;
it does not depend on `tests/confs/std-fcc` or any other test fixture.

| Case | Atoms | Purpose |
| --- | ---: | --- |
| `Ti_hcp` | 2 | single-element hcp model load; 2×2×2 phonoLAMMPS smoke |
| `V_bcc` | 2 | single-element bcc model load |
| `TiV_B2` | 2 | mixed Ti/V type mapping in the B2 prototype |

The NVE input uses 20 steps at 1 fs only as a deterministic execution smoke.
It is not a thermodynamic or stability benchmark.

Every generated DPA4 LAMMPS input contains `atom_modify map yes` immediately
after `atom_style atomic` and before `read_data`. This is required by the
single-rank periodic DPA4 graph path; removing or moving it invalidates the
benchmark.

The generated inputs deliberately contain no `plugin load` command. The exact
b95 `BUILD_PY_IF=ON` build installs `libdeepmd_lmpplugin.so`; the candidate
image must expose its containing directory through `LAMMPS_PLUGIN_PATH` so
LAMMPS auto-loads it. The runner rejects a missing/invalid path or a path that
does not contain that exact plugin filename.

## Required runtime

Run these scripts **inside the candidate image**, using that image's Python and
executables. The image must provide:

- DeepMD-kit with DPA4 support;
- LAMMPS plus `libdeepmd_lmpplugin.so` discoverable through
  `LAMMPS_PLUGIN_PATH` auto-loading;
- `phonolammps`, `phonopy`, Python `lammps`, `dpdata`, PyTorch, and NumPy;
- `nvidia-smi` on GPU runs.

The scripts never install packages. They record package metadata for
`deepmd-kit`, `phonolammps`, `phonopy`, `lammps`, `dpdata`, `torch`, and
`numpy`; a missing version keeps the result non-passing.

This v2 qualification is intentionally limited to one selected NVIDIA T4
(compute capability 7.5) and one LAMMPS rank with deterministic thread settings (`OMP_NUM_THREADS=1`, DeepMD
intra/inter-op threads = 1). The runners reject a non-T4 selected GPU and an MPI
launcher that does not explicitly request exactly one rank. CPU and GPU parity
must use the same exact image digest and checkpoint hash but different
device-specific `.pt2` hashes. Run the CPU leg on the same T4 node/image; the
script hides GPUs with an empty `CUDA_VISIBLE_DEVICES` and fails if its process
is nevertheless observed on a GPU.

## Reproducible procedure

Start in this benchmark directory. Use a new workspace name for every image ×
GPU qualification; scripts refuse to overwrite evidence.

```bash
BENCH_ROOT="$PWD"
WORKSPACE="$BENCH_ROOT/workspace"
CHECKPOINT="$BENCH_ROOT/../../models/DPA4-alloytongqi/model.pt"
CPU_PT2="/absolute/path/from-cpu-freeze/model.cpu.pt2"
GPU_PT2="/absolute/path/from-this-t4-freeze/model.t4.pt2"
PLUGIN_DIR="/absolute/path/containing/libdeepmd_lmpplugin.so"
IMAGE_REF="registry.example.invalid/owner/dpa4-apex:replace-me"
IMAGE_DIGEST="sha256:replace-with-64-lowercase-hex-characters"

python generate_cases.py --output "$WORKSPACE/cases"
sha256sum "$CHECKPOINT" "$CPU_PT2" "$GPU_PT2"
dp --pt show "$CHECKPOINT" type-map descriptor fitting-net size
```

Generate both `.pt2` artifacts with the candidate image's DPA4 AOTI freeze
implementation: freeze the CPU artifact with GPUs hidden, then freeze the GPU
artifact on the exact T4 being qualified. Do not rename a `.pt` checkpoint to
`.pt2`, reuse the CPU artifact on GPU, or reuse an artifact frozen for another
GPU family. The runner copies and hashes the supplied `.pt2`; it does not infer
device compatibility from the suffix.

The checkpoint hash must equal the value at the top of this document. The
attribute-bearing `dp --pt show` command above must exit zero; a bare
`dp show <checkpoint>` is not this probe and may be rejected by the current
parser because it supplies no attributes. A tag alone
is not an image identity: provide the registry-resolved `sha256:` digest. If the
digest is missing, the scripts still preserve diagnostic output but set the
result to `inconclusive`.

Run the complete CPU/GPU × `run0`/`md` matrix:

```bash
for case in Ti_hcp V_bcc TiV_B2; do
  for device in cpu gpu; do
    if [ "$device" = cpu ]; then
      runtime_model="$CPU_PT2"
    else
      runtime_model="$GPU_PT2"
    fi
    for mode in run0 md; do
      python run_lammps_case.py \
        --case-dir "$WORKSPACE/cases/$case" \
        --checkpoint "$CHECKPOINT" \
        --runtime-model "$runtime_model" \
        --mode "$mode" \
        --device "$device" \
        --output "$WORKSPACE/runs/$case/$device/$mode" \
        --image-ref "$IMAGE_REF" \
        --image-digest "$IMAGE_DIGEST" \
        --lammps-plugin-path "$PLUGIN_DIR" \
        --command-json '["lmp"]'
    done
  done
done
```

For a launcher, pass a shell-free JSON array, for example
`--command-json '["mpirun","-n","1","lmp"]'`. Do not put a shell pipeline or
credential in the command array. Multi-rank launchers are rejected.

Compare both run modes for all cases:

```bash
for case in Ti_hcp V_bcc TiV_B2; do
  for mode in run0 md; do
    python compare_parity.py \
      --cpu "$WORKSPACE/runs/$case/cpu/$mode/result.json" \
      --gpu "$WORKSPACE/runs/$case/gpu/$mode/result.json" \
      --output "$WORKSPACE/parity/$case/$mode.json"
  done
done
```

Default absolute gates are:

| Observable | Gate |
| --- | ---: |
| energy per atom | ≤ `1e-4 eV/atom` |
| force component RMS | ≤ `1e-3 eV/Å` |
| maximum absolute force component | ≤ `5e-3 eV/Å` |
| maximum absolute stress component | ≤ `0.01 GPa` |

LAMMPS `metal` pressure is recorded in bar and converted to GPa by the parity
script. The scripts reject missing or non-finite energy, stress, or force data.

Run the phonoLAMMPS smoke on the Ti hcp case. This benchmark keeps `read_data`,
relies on b95 `LAMMPS_PLUGIN_PATH` auto-loading, truncates the ordinary input
after `pair_coeff`, passes a POSCAR, uses `--dim 2 2 2`, and expands
`PRIMITIVE_AXES=P` to the identity matrix. It does not change APEX's legacy
phonon input path for the old image:

```bash
python run_phonolammps_smoke.py \
  --case-dir "$WORKSPACE/cases/Ti_hcp" \
  --checkpoint "$CHECKPOINT" \
  --runtime-model "$GPU_PT2" \
  --device gpu \
  --output "$WORKSPACE/phonolammps/Ti_hcp/gpu" \
  --image-ref "$IMAGE_REF" \
  --image-digest "$IMAGE_DIGEST" \
  --lammps-plugin-path "$PLUGIN_DIR" \
  --command-json '["phonolammps"]'
```

The smoke passes only when the command exits zero, no fatal signature is found,
GPU use is directly observed, and `FORCE_CONSTANTS` parses completely: its atom
count must equal the POSCAR atom count multiplied by the requested supercell,
all `N^2` indexed 3×3 blocks must be present in order, every matrix value must
be finite, and no trailing non-empty data is allowed.

Finally aggregate and verify all evidence:

```bash
python build_manifest.py \
  --workspace "$WORKSPACE" \
  --checkpoint "$CHECKPOINT" \
  --image-ref "$IMAGE_REF" \
  --image-digest "$IMAGE_DIGEST" \
  --output "$WORKSPACE/manifest.json"

python verify_manifest.py \
  "$WORKSPACE/manifest.json" \
  --root "$WORKSPACE"
```

`verify_manifest.py` re-hashes every declared workspace artifact and enforces
the pass matrix. It uses only Python's standard library. `manifest.schema.json`
is the machine-readable Draft 2020-12 schema; schema validation is an optional
additional check, not a substitute for hash verification.

## Recorded evidence

Each LAMMPS and phonoLAMMPS result records:

- exact training-checkpoint `model.pt` SHA-256 and size;
- exact device-specific runtime `.pt2` SHA-256, byte size, requested device,
  and every run record that used it;
- image reference and immutable digest;
- GPU name, UUID, memory, compute capability, NVIDIA driver, and CUDA version;
- effective `LAMMPS_PLUGIN_PATH` plus the exact
  `libdeepmd_lmpplugin.so` SHA-256 and size;
- relevant package versions and resolved executable paths;
- command, selected thread/device environment, start/end time, timeout and exit
  code;
- attribute-bearing `dp --pt show <checkpoint> type-map descriptor fitting-net
  size` exit code and complete stdout/stderr before LAMMPS is allowed to start;
- complete stdout/stderr files and their hashes;
- child/descendant GPU-process samples from `nvidia-smi`;
- finite energy/stress/forces or a fully parsed, dimension-consistent, finite
  `FORCE_CONSTANTS`;
- hashes and byte sizes for all generated inputs and outputs.

Short runs can finish before `nvidia-smi` observes their process. That is not a
pass: it is `inconclusive`. Increase the workload in a new, versioned benchmark
rather than editing an existing evidence directory or manually changing the
status.

## GPU recommendation and prohibition boundary

Use a passed manifest as one piece of evidence for the exact image digest,
checkpoint hash, CPU/T4 `.pt2` hashes, T4 compute capability, driver, CUDA,
single-rank command, and thread tuple. Do not generalize this v1 benchmark to
another GPU or mutable image tag. A single transient
failure is not enough to prohibit a GPU combination: reproduce a deterministic
hard failure in two fresh workspaces and preserve both manifests. Missing or
mixed evidence is `untested`/`inconclusive`, not “compatible” and not
“forbidden.”

Before calling the image ready for latest APEX, also run a real APEX make → run
→ post workflow with that exact digest. This benchmark verifies the underlying
runtime and APEX-shaped phonoLAMMPS command, not cloud orchestration, scheduling,
result retrieval, or scientific convergence.
