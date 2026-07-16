# APEX Workflow Control Reference

## Key Command

```bash
apex submit param.json -c global.json -n "<workflow-name>"
```

| Flag | Meaning |
|------|---------|
| `-c global.json` | Workflow/machine configuration |
| `-f joint\|props\|relax` | Flow type: `joint` (relax+props), `props` (props only), `relax` (relax only) |
| `-s` | Submit-only (non-blocking); the outer job exits after submission |
| `-n "<name>"` | Workflow name — **MUST be RFC 1123 lowercase** (see `submission.md`) |

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
cell type and set parameters accordingly. APEX accepts primitive cells,
conventional cells, and user-provided supercells; a conventional cell is not
required.

1. **Read the STRU/POSCAR** — check atom count and lattice vectors
2. **Classify the input** — primitive, conventional, or an already-expanded supercell
3. **Preserve the input cell** — do not standardize or reduce it unless the user asks
4. **If it is already a supercell, ask whether to expand it again**
5. **If no further expansion is requested**, set applicable bulk-property
   `supercell` / `supercell_size` values to `[1,1,1]`; do not reapply unit-cell defaults
6. **Set or omit PRIMITIVE_AXES based on cell type** (see `calculators.md`)
7. **Verify consistency** — ensure param.json parameters match the actual structure

### Example Check Logic

- FCC 1 atom + non-orthogonal vectors → primitive → NO `PRIMITIVE_AXES`
- FCC 4 atoms + cubic vectors → conventional → SET `PRIMITIVE_AXES`
- User-provided 108-atom FCC supercell + no further expansion → preserve it and
  set applicable `supercell` / `supercell_size` to `[1,1,1]`
- When in doubt → OMIT `PRIMITIVE_AXES` (safer default)

> ⚠️ Getting this wrong causes phonopy `RuntimeError: Remapping of atoms by TrimmedCell failed` at Post step — after all expensive DFT calculations have already completed.

For surface, gamma, and decohesive properties, slab thickness and in-plane
replication define the requested geometry. Confirm those values separately;
do not silently replace them with `[1,1,1]`.
