# APEX Submission Reference

## Execution Architecture (Two-Layer Model)

APEX uses a **two-layer submission architecture**:

1. **Outer layer — Bohrium job**: A thin submission client running the APEX image. Its sole purpose is to call `apex submit` which connects to the dflow orchestration server.
2. **Inner layer — dflow/Argo orchestration**: The actual LAMMPS/ABACUS/VASP calculations run in **separate containers** orchestrated by `workflows.deepmodeling.com`. dflow handles task parallelism, retries, and artifact collection.

```
┌─────────────────────────────────────────────────────────────────┐
│  MatMaster (local)                                              │
│  1. Generate param.json + global.json + structure files         │
│  2. Submit outer Bohrium job                                    │
└──────────────────────────┬──────────────────────────────────────┘
                           │ Bohrium submit
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  Outer Bohrium Job (APEX image, c1_m2_cpu)                     │
│  1. pip install apex-flow (latest, no version pin)             │
│  2. apex submit param.json -c global.json -n "<wf-name>"      │
│  → Connects to workflows.deepmodeling.com                      │
│  → Waits for completion and retrieves results automatically    │
│  → upload_packages sends new APEX code to all inner steps      │
└──────────────────────────┬──────────────────────────────────────┘
                           │ dflow orchestration
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  Inner containers (managed by dflow on Bohrium)                 │
│  - LAMMPS image: deepmd-kit-phonolammps:3.1.3                  │
│  - ABACUS image: registry.dp.tech/dptech/abacus:3.2.3          │
│  - VASP image: (user-provided)                                  │
│  - Machine type per task: scass_type in global.json             │
│  - APEX code: received via upload_packages (always latest)      │
└─────────────────────────────────────────────────────────────────┘
```

## Authentication: access_key → ticket Conversion (CRITICAL)

APEX/dflow authentication requires a **ticket** (UUID), not the raw Bohrium access_key. The `generate_config.py` script handles this conversion automatically:

```
GET https://openapi.dp.tech/openapi/v1/ticket/get?accessKey=<ACCESS_KEY>
Header: x-app-key: (can be empty string)
Response: {"code": 0, "data": {"ticket": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"}}
```

The ticket goes into `global.json` as `bohrium_config.ticket`. Generate it before
the job directory is packaged:

1. Inspect the agent/local environment for `BOHRIUM_ACCESS_KEY`.
2. If the variable is missing, stop and ask the user to provide/configure it.
   `generate_config.py` cannot obtain a ticket without an access key.
3. If it is present, run `scripts/generate_config.py`; the script calls the ticket
   API, validates the response, and writes a fresh ticket to `global.json`.
4. Verify that `global.json` contains a non-empty `bohrium_config.ticket`.

> ⚠️ **Ticket 有效期约 1 周，会过期！** Generate a fresh `global.json` with
> `generate_config.py` when preparing each new submission. Do not refresh the
> ticket in `run.sh`: the outer APEX container may not receive
> `BOHRIUM_ACCESS_KEY`, and ad-hoc response parsing there has no reliable error
> handling.

> **WARNING**: The log may show `WARNING:root:Missing Bohrium account fields: email, password.` — this is **non-fatal**; ticket-based auth works without email/password.

## Outer Job run.sh Template

The outer Bohrium job always uses this pattern. **Do NOT pin apex-flow version** — this ensures the latest code (with bug fixes) is used and propagated to inner steps via `upload_packages`.

```bash
#!/bin/bash
set -eo pipefail

# Always install latest apex-flow (DO NOT pin version)
pip install apex-flow 2>&1 | tail -3
python3 -c "import apex; print(f'APEX version: {apex.__version__}')"

# Authentication is already stored in global.json by generate_config.py.
# Do not read BOHRIUM_ACCESS_KEY or refresh the ticket in this container.
set +eo pipefail
apex submit param.json -c global.json -n "<workflow-name>" 2>&1 | tee apex_submit.log
EXIT_CODE=${PIPESTATUS[0]}
set -eo pipefail

if [ $EXIT_CODE -eq 0 ]; then
    echo "=== APEX workflow completed and results retrieved ==="
    echo "Retain apex_submit.log and the workflow ID."
    exit 0
else
    echo "APEX failed (exit $EXIT_CODE)"
    tail -50 apex_submit.log 2>/dev/null || true
    exit 1
fi
```

**Key points:**
- `pip install apex-flow` (no `==` pin) → always gets latest from PyPI
- The installed APEX code is automatically sent to inner dflow steps via `upload_packages`
- `run.sh` does not access `BOHRIUM_ACCESS_KEY` or modify `global.json`
- **单次执行，不重试** — 失败即退出，由用户决定是否重新提交
- `-n` flag sets workflow name (NOT `-w` which is work directory)
- Do not use `-s` for agent-managed runs. The outer job must wait for dflow to
  finish so `apex submit` retrieves results automatically

## RFC 1123 Workflow Name Constraint (CRITICAL)

dflow validates workflow names against RFC 1123 subdomain regex. Names like `"Cu-FCC-elastic"` will **FAIL**. The name must:
- Be all lowercase
- Contain only `[a-z0-9-]`
- Not start/end with `-`
- Max 63 characters

**Always auto-lowercase** any user-provided name: `"Cu-FCC-elastic"` → `"cu-fcc-elastic"`.

## Bohrium Image & Machine Defaults

| Role | Image | Notes |
|------|-------|-------|
| **Outer job (submission client)** | `registry.dp.tech/dptech/dp/native/prod-397637/apex-flow:1.3.0.post2` | Lightweight; just runs `apex submit` |
| **LAMMPS calculator** | `registry.dp.tech/dptech/dp/native/prod-397637/deepmd-kit-phonolammps:3.1.3` | Default; includes phonoLAMMPS |
| **ABACUS calculator** | (same APEX image has ABACUS) | Or user-specified |
| **VASP calculator** | User must provide | Commercial; confirm with user |

> ⚠️ **Do NOT combine `deepmd-kit:3.1.1` with any NVIDIA T4 machine**. It also has a known segfault bug when handling triclinic cells (non-orthogonal boxes), including on CPU. Use `3.1.3` or later.

> LAMMPS phonon and Grüneisen tasks are forced to use `registry.dp.tech/dptech/dp/native/prod-397637/deepmd-kit-phonolammps:3.1.3`, which includes the required phonoLAMMPS executable.

| Backend | scass_type (inner containers) | Notes |
|---------|-------------------------------|-------|
| LAMMPS (DeePMD/MACE/NEP) | `c8_m31_1 * NVIDIA T4` | GPU beneficial |
| LAMMPS (EAM/MEAM/SNAP) | `c16_m32_cpu` | CPU sufficient |
| ABACUS | `c16_m32_cpu` | CPU |
| VASP | User specifies | User's license |

> **IMPORTANT**: Use the minimal `c1_m2_cpu` machine for the outer Bohrium job since it only submits to dflow and waits. The heavy compute is in the inner containers specified by `scass_type` in `global.json`.

## Validated global.json Structure

`program_id` and `bohrium_config.project_id` **must** come from the environment
variable `BOHRIUM_PROJECT_ID` (or `--project-id`). Never hardcode a personal
project ID in examples or generated configs.

```json
{
    "dflow_host": "https://workflows.deepmodeling.com",
    "k8s_api_server": "https://workflows.deepmodeling.com",
    "batch_type": "Bohrium",
    "context_type": "Bohrium",
    "program_id": "<BOHRIUM_PROJECT_ID>",
    "bohrium_config": {
        "ticket": "<UUID from API conversion — auto-filled by generate_config.py>",
        "project_id": "<BOHRIUM_PROJECT_ID>"
    },
    "apex_image_name": "registry.dp.tech/dptech/dp/native/prod-397637/apex-flow:1.3.0.post2",
    "lammps_image_name": "registry.dp.tech/dptech/dp/native/prod-397637/deepmd-kit-phonolammps:3.1.3",
    "lammps_run_command": "lmp -in in.lammps",
    "scass_type": "c16_m32_cpu",
    "group_size": 1,
    "pool_size": 1
}
```

> For GPU potentials (DeePMD, MACE, NEP), change `scass_type` to `"c8_m31_1 * NVIDIA T4"`.
> Before submitting, run `scripts/validate_apex_combo.py check` on the chosen image × scass_type.

## Agent-Managed Submission Workflow (Complete Lifecycle)

The default agent workflow omits `-s` so the outer job waits for the inner dflow
workflow and retrieves its results automatically:

1. **Prepare inputs locally** (Bash): run `scripts/generate_config.py` to generate `global.json` + `param.json` + copy structure/model files into a job directory.

2. **Submit outer Bohrium job** (blocking mode):
   ```python
   Bohrium(action="submit",
     input_dir="<job_dir>",
     image="registry.dp.tech/dptech/dp/native/prod-397637/apex-flow:1.3.0.post2",
     machine="c1_m2_cpu",
     cmd='apex submit param.json -c global.json -f joint -n "cu-fcc-elastic" > log 2>&1')
   ```
   Parse and retain the workflow ID printed in the submission log. The outer
   Bohrium job remains active until the inner dflow workflow completes.

3. **Monitor the inner workflow by ID** using APEX workflow query commands while
   the outer job remains active.

4. **Verify automatic retrieval after the inner workflow finishes**:
   - **If Finished**: verify retrieved results, then parse `confs/*/elastic_00/result.json`
     (or equivalent property results) → summarize and present to user.
   - **If Failed**: read `log` and any
     `dpdispatcher.log` → analyze the failure and report suggested fixes.

5. **Result parsing**: After retrieval, read the property result files:
   - Elastic: `confs/<structure>/elastic_00/result.json` → contains `elastic_tensor`, `B`, `G`, `E`, `u`
   - EOS: `confs/<structure>/eos_00/result.json` → contains `volume`, `energy`, fitted EOS params
   - Surface: `confs/<structure>/surface_00/result.json` → surface energies per miller index
   - Other properties follow the same pattern: `confs/<structure>/<prop_type>_00/result.json`

6. **Present results**: Summarize in a table with physical units (GPa for elastic, J/m² for surface, eV for formation energies). Compare with literature values when available.

> **Agent responsibility**: Never add `-s` to the submission command. A
> successful outer job means the inner workflow completed and APEX performed
> automatic retrieval; verify the expected result files before reporting success.

## Expected Log Warnings (Non-Fatal)

- `WARNING:root:Missing Bohrium account fields: email, password.` — Normal with ticket auth
- `WARNING:root:Skip copying relaxation for confs/std-fcc: .../relaxation not found.` — Normal for first-time joint workflow (relaxation hasn't produced output yet when props phase starts scanning)
