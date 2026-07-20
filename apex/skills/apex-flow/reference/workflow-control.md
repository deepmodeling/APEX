# APEX Workflow Control Reference

## Key Command

```bash
apex submit param.json -c global.json -n "<workflow-name>"
```

| Flag | Meaning |
|------|---------|
| `-c global.json` | Workflow/machine configuration |
| `-f joint\|props\|relax` | Flow type: `joint` (relax+props), `props` (props only), `relax` (relax only) |
| `-n "<name>"` | Workflow name — **MUST be RFC 1123 lowercase** (see `submission.md`) |

Do not use `-s`; agent-managed submissions must wait for completion and automatic
result retrieval.

## Checking a Running Job (Required Format)

Do not decide that an existing job matches the user's request from its name.
Work from the outer job's submitted input directory and log.

### 1. Identify the inner workflow

1. Read the outer job log and capture the exact line:
   `Workflow has been submitted (ID: <workflow-id>, UID: <workflow-uid>)`.
2. Keep the outer Bohrium job ID, inner workflow ID, and inner workflow UID as
   three separate values.
3. From the submitted job directory, run:

   ```bash
   apex get -i <workflow-id> -c global.json
   apex getsteps <workflow-id> -c global.json
   ```

   `getsteps` takes the workflow ID as its positional argument. Do not write
   `apex getsteps -i <workflow-id>`.
4. Confirm that `apex get` reports `Running` or `Pending` before describing the
   workflow as running.

### 2. Count relaxation and property tasks

In the `getsteps` output, classify only the top-level task rows:

- keys beginning with `relaxcal-` are per-structure relaxation tasks;
- keys beginning with `propertycal-` are per-structure, per-property tasks;
- `Succeeded` counts as successful;
- `Failed` or `Error` counts as failed;
- `Pending` or `Running` counts as unfinished.

Report unexpected terminal phases separately instead of silently counting them
as successful. Verify:

```text
property_success + property_failed + property_unfinished = property_total
```

For one structure requesting all properties, `property_total` is 14. For
multiple structures, these are property-task counts, normally
`number of structures × number of requested properties`, less explicitly
skipped tasks.

The outer log text `relax N, props M` reports counts still being monitored:

- `relax 0` means no relaxation task remains; it does **not** mean relaxation
  has not started or has not finished.
- `props 1` means one property task remains unfinished.
- These two counters alone do not reveal how many tasks failed, because failed
  tasks are removed from the remaining list. Always use the top-level step
  phases to count successes and failures.

### 3. Give the live Argo location

For a Bohrium workflow that is confirmed `Running` or `Pending`, report:

```text
https://workflows.deepmodeling.com/workflows/argo/<workflow-id>
```

`argo` is the managed Bohrium workflow namespace. Use the workflow **ID/name**,
not the UID, in this live route. Never give an `/archived-workflows/...` URL for
a running task. Also print the workflow ID and UID as plain text so the user can
verify the target.

### 4. Verify the submitted material

Read `param.json`, resolve its `structures` entry inside the submitted job
directory, and inspect the actual submitted POSCAR/STRU. Verify formula, atom
count, lattice lengths, lattice angles, and—when needed—space group. A matching
outer job name such as `srtio3-*` is not evidence that the correct structure was
submitted.

For the SrTiO3 case, the expected input is:

```text
SrTiO3; cubic perovskite; 5 atoms; a = b = c = 3.9316 Å;
alpha = beta = gamma = 90 degrees
```

Report it as `SrTiO₃（立方钙钛矿，a=3.9316 Å，5 atoms）` only after the submitted
file passes these checks. If it does not, report the observed structure and do
not treat the existing job as a match.

### 5. User-facing status template

Use this exact field order:

```text
作业名称：<outer job name>
材料：<formula, structure, lattice parameter, atom count; verified from submitted file>
当前阶段：relax <N>, props <M>（剩余弛豫 <N>；剩余性质任务 <M>）
性质进度：成功 <S>，失败 <F>，未完成 <M>，总计 <T>
Argo workflow：https://workflows.deepmodeling.com/workflows/argo/<workflow-id>
Workflow ID：<workflow-id>
Workflow UID：<workflow-uid>
```

Example for the single-structure SrTiO3 all-properties run:

```text
作业名称：apex-srtio3-all-14props
材料：SrTiO₃（立方钙钛矿，a=3.9316 Å，5 atoms；已核对提交的 POSCAR）
当前阶段：relax 0, props 1（剩余弛豫 0；剩余性质任务 1）
性质进度：成功 <以 propertycal-* 的 Succeeded 数为准>，失败 <以 Failed/Error 数为准>，未完成 1，总计 14
Argo workflow：https://workflows.deepmodeling.com/workflows/argo/<workflow-id>
Workflow ID：<workflow-id>
Workflow UID：<workflow-uid>
```

Do not infer `成功 13，失败 0` merely from `props 1`; query the phases first.

## Stopping/Killing a Running APEX Workflow (CRITICAL)

When the user wants to stop/kill an APEX job, **you MUST terminate the inner dflow/Argo workflow FIRST**, then kill the outer Bohrium node. If you only kill the Bohrium node, the dflow workflow on `workflows.deepmodeling.com` continues running and consuming resources silently.

### Correct Kill Procedure

1. **Find the workflow ID** — from the outer job's log (look for `Workflow has been submitted (ID: <wf-id>, UID: <wf-uid>)`)
2. **Terminate the Argo workflow** — run inside the Bohrium node:
   ```bash
   apex terminate -i <workflow-id> -c global.json
   ```
3. **Then kill the Bohrium node** — via `Bohrium(action="kill", job_id=...)`

### Available APEX Workflow Control Commands

All commands require `-c global.json`:

| Command | Effect |
|---------|--------|
| `apex stop -i <wf-id>` | Gracefully stop (lets running steps finish, then stops) |
| `apex terminate -i <wf-id>` | Immediately terminate all running steps |
| `apex delete -i <wf-id>` | Delete the workflow record from Argo |
| `apex suspend -i <wf-id>` | Pause the workflow (can resume later) |
| `apex retrieve -i <wf-id>` | Retrieve results from a completed/stopped workflow |

> ⚠️ **Never just kill the Bohrium job without terminating the inner workflow.** The outer job is just a submission client; the actual compute happens in dflow containers that will keep running independently.

## Pre-Submission Structure Validation (MANDATORY)

Before generating param.json, **always inspect the input structure** to determine
cell type, atom count, and whether further expansion is needed. APEX accepts
primitive cells, conventional cells, and user-provided supercells; a
conventional cell is not required.

1. **Read the STRU/POSCAR** — atom count, species, lattice vectors / lengths
2. **Classify the input** — primitive, conventional, or an already-expanded supercell
3. **Preserve the input cell** — do not standardize or reduce it unless the user asks
4. **Decide whether the cell is large enough for the requested properties**
   (see size guidance below). Do **not** blindly apply default `supercell` /
   `supercell_size` values from templates.
5. **If already a supercell / already large enough**, ask whether to expand again
6. **If no further expansion is requested**, set applicable bulk-property
   `supercell` / `supercell_size` values to `[1,1,1]`; do not reapply unit-cell defaults
7. **Set or omit PRIMITIVE_AXES based on cell type** (see `calculators.md`)
8. **Verify consistency** — ensure param.json parameters match the actual structure

### Size guidance: when to recommend expansion

| Property family | Typical undersized input | Recommended total size (after expansion) |
| --- | --- | --- |
| vacancy / interstitial | 1–4 atom unit cell | ≳ conventional `[2,2,2]` equivalent |
| phonon / gruneisen / finite-T | unit cell or tiny supercell | ≳ `[3,3,3]` (smaller often fails) |
| elastic / EOS / cohesive | unit cell is usually OK | expand only if user wants finite-size check |
| surface / gamma / decohesive | very small in-plane cell | thicken with `min_slab_size`; confirm in-plane replication separately — **not** bulk `supercell` |

Rules of thumb:

- Primitive metal cell (1–2 atoms) for defect/phonon/finite-T → **recommend expand**.
- Conventional FCC/BCC (4 / 2 atoms) for phonon → still recommend `[3,3,3]` unless the user already supplied a large supercell.
- User supercell with tens–hundreds of atoms that already meets the table → prefer `[1,1,1]` after confirmation; do not expand twice.
- Expanding a tiny cell can also make slab construction more robust for tilted surfaces (e.g. bcc (110)), but it is **not** a substitute for correct APEX slab/handedness handling.

### Helper words (AskQuestion / confirmation)

Present a short structure summary, then ask in plain language. Example:

```
当前结构：Mo BCC 原胞，2 atoms，a≈3.16 Å（看起来是原胞，不是超胞）。

计划性质：decohesive (110)、phonon。
- decohesive 主要靠 slab 厚度 / 面内尺寸控制，不是 bulk supercell。
- phonon 建议总尺寸达到约 [3,3,3]；当前原子数偏少，建议先扩包。

请确认：
1) 保持原胞提交（phonon 可能不稳定）
2) 扩到 [2,2,2] / [3,3,3] 后再算
3) 你已有更大超胞，请提供新结构，并把 supercell 设为 [1,1,1]
```

If the input is already large:

```
当前结构：Cu FCC 超胞，108 atoms（已是约 3×3×3）。
缺陷/声子类参数将默认设为 supercell=[1,1,1]，避免二次扩包。
是否还要再扩一层？
```

### Example Check Logic

- FCC 1 atom + non-orthogonal vectors → primitive → NO `PRIMITIVE_AXES`; for phonon/defect recommend expand
- FCC 4 atoms + cubic vectors → conventional → SET `PRIMITIVE_AXES`; phonon still usually needs `[3,3,3]`
- User-provided 108-atom FCC supercell + no further expansion → preserve it and
  set applicable `supercell` / `supercell_size` to `[1,1,1]`
- When in doubt → OMIT `PRIMITIVE_AXES` (safer default) and ask about expansion

> ⚠️ Getting this wrong causes phonopy `RuntimeError: Remapping of atoms by TrimmedCell failed` at Post step — after all expensive DFT calculations have already completed.

For surface, gamma, and decohesive properties, slab thickness and in-plane
replication define the requested geometry. Confirm those values separately;
do not silently replace them with `[1,1,1]`.
