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
│  Outer Bohrium Job (APEX image, c16_m32_cpu)                   │
│  1. pip install apex-flow (latest, no version pin)             │
│  2. apex submit param.json -c global.json -n "<wf-name>"      │
│  → Connects to workflows.deepmodeling.com                      │
│  → Submits dflow workflow and waits for completion             │
│  → upload_packages sends new APEX code to all inner steps      │
└──────────────────────────┬──────────────────────────────────────┘
                           │ dflow orchestration
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  Inner containers (managed by dflow on Bohrium)                 │
│  - LAMMPS image: registry.dp.tech/dptech/deepmd-kit:3.1.3      │
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

The ticket goes into `global.json` as `bohrium_config.ticket`. Environment variable: `BOHRIUM_ACCESS_KEY`.

> ⚠️ **Ticket 有效期约 1 周，会过期！** 每次提交 APEX 任务时都必须重新调用上述 API 获取新 ticket，不要复用之前缓存的 ticket。`run.sh` 中应在 `apex submit` 之前执行 ticket 刷新逻辑。

### Recommended run.sh Ticket Refresh Template

```bash
# Refresh ticket before submission (tickets expire in ~1 week)
NEW_TICKET=$(python3 -c "
import requests, json, os
key = os.environ.get('BOHRIUM_ACCESS_KEY', '')
r = requests.get(f'https://openapi.dp.tech/openapi/v1/ticket/get?accessKey={key}', headers={'x-app-key': ''})
print(json.loads(r.text)['data']['ticket'])
")
python3 -c "
import json
with open('global.json') as f: d = json.load(f)
d.setdefault('bohrium_config', {})['ticket'] = '$NEW_TICKET'
with open('global.json', 'w') as f: json.dump(d, f, indent=4)
"
echo "Ticket refreshed: ${NEW_TICKET:0:8}..."
```

> **WARNING**: The log may show `WARNING:root:Missing Bohrium account fields: email, password.` — this is **non-fatal**; ticket-based auth works without email/password.

## Outer Job run.sh Template

The outer Bohrium job always uses this pattern. **Do NOT pin apex-flow version** — this ensures the latest code (with bug fixes) is used and propagated to inner steps via `upload_packages`.

```bash
#!/bin/bash
set -eo pipefail

# Always install latest apex-flow (DO NOT pin version)
pip install apex-flow 2>&1 | tail -3
python3 -c "import apex; print(f'APEX version: {apex.__version__}')"

set +eo pipefail
apex submit param.json -c global.json -n "<workflow-name>" 2>&1 | tee apex_submit.log
EXIT_CODE=${PIPESTATUS[0]}
set -eo pipefail

if [ $EXIT_CODE -eq 0 ]; then
    echo "=== APEX succeeded ==="
    find . -name "result.json" -exec echo "Found: {}" \; -exec cat {} \;
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
- **单次执行，不重试** — 失败即退出，由用户决定是否重新提交
- `-n` flag sets workflow name (NOT `-w` which is work directory)
- No `-s` flag → outer job waits for dflow completion and retrieves results

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
| **Outer job (submission client)** | `registry.dp.tech/dptech/dp/native/prod-397637/apex:1.3.0` | Lightweight; just runs `apex submit` |
| **LAMMPS calculator** | `registry.dp.tech/dptech/deepmd-kit:3.1.3` | Used by dflow for LAMMPS tasks |
| **ABACUS calculator** | (same APEX image has ABACUS) | Or user-specified |
| **VASP calculator** | User must provide | Commercial; confirm with user |

> ⚠️ **Do NOT use `deepmd-kit:3.1.1`** — it has a known segfault bug when handling triclinic cells (non-orthogonal boxes). Use `3.1.3` or later.

| Backend | scass_type (inner containers) | Notes |
|---------|-------------------------------|-------|
| LAMMPS (DeePMD/MACE/NEP) | `c8_m31_1 * NVIDIA T4` | GPU beneficial |
| LAMMPS (EAM/MEAM/SNAP) | `c16_m32_cpu` | CPU sufficient |
| ABACUS | `c16_m32_cpu` | CPU |
| VASP | User specifies | User's license |

> **IMPORTANT**: The outer Bohrium job only needs a minimal machine (e.g. `c2_m4_cpu`) since it just submits to dflow. The heavy compute is in the inner containers specified by `scass_type` in `global.json`.

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
    "apex_image_name": "registry.dp.tech/dptech/dp/native/prod-397637/apex:1.3.0",
    "lammps_image_name": "registry.dp.tech/dptech/deepmd-kit:3.1.3",
    "lammps_run_command": "lmp -in in.lammps",
    "scass_type": "c16_m32_cpu",
    "group_size": 1,
    "pool_size": 1
}
```

> For GPU potentials (DeePMD, MACE, NEP), change `scass_type` to `"c8_m31_1 * NVIDIA T4"`.
> Before submitting, run `scripts/validate_apex_combo.py check` on the chosen image × scass_type.

## Submission Workflow (Complete Lifecycle)

The workflow uses a **blocking** outer job (NO `-s` flag) so results come back automatically:

1. **Prepare inputs locally** (Bash): run `scripts/generate_config.py` to generate `global.json` + `param.json` + copy structure/model files into a job directory.

2. **Submit outer Bohrium job** (blocking mode):
   ```python
   Bohrium(action="submit",
     input_dir="<job_dir>",
     image="registry.dp.tech/dptech/dp/native/prod-397637/apex:1.3.0",
     machine="c2_m4_cpu",
     cmd='apex submit param.json -c global.json -f joint -n "cu-fcc-elastic" > log 2>&1')
   ```
   **Without `-s`**, `apex submit` blocks until the dflow workflow finishes, then auto-retrieves results into the working directory. The outer Bohrium job stays running the entire time (typically 10–30 min for simple systems).

3. **When outer job finishes**: MatMaster is automatically notified.
   - **If Finished**: Download results → parse `confs/*/elastic_00/result.json` (or equivalent property results) → summarize and present to user.
   - **If Failed**: Download logs → read `log` file and any `dpdispatcher.log` → analyze failure cause → report to user with fix suggestions.

4. **Result parsing**: After download, read the property result files:
   - Elastic: `confs/<structure>/elastic_00/result.json` → contains `elastic_tensor`, `B`, `G`, `E`, `u`
   - EOS: `confs/<structure>/eos_00/result.json` → contains `volume`, `energy`, fitted EOS params
   - Surface: `confs/<structure>/surface_00/result.json` → surface energies per miller index
   - Other properties follow the same pattern: `confs/<structure>/<prop_type>_00/result.json`

5. **Present results**: Summarize in a table with physical units (GPa for elastic, J/m² for surface, eV for formation energies). Compare with literature values when available.

> **IMPORTANT**: Do NOT use the `-s` flag for the standard workflow. The `-s` (submit-only) flag makes the outer job exit immediately after submission, which means MatMaster cannot track completion or auto-retrieve results. Only use `-s` for advanced users who want to manage monitoring themselves.

> **Timeout**: For large systems or many properties, the outer Bohrium job may run for hours. Set an appropriate Bohrium job timeout if needed. If the outer job times out, use `apex retrieve -i <workflow-id> -c global.json` in a separate job to fetch results.

## Expected Log Warnings (Non-Fatal)

- `WARNING:root:Missing Bohrium account fields: email, password.` — Normal with ticket auth
- `WARNING:root:Skip copying relaxation for confs/std-fcc: .../relaxation not found.` — Normal for first-time joint workflow (relaxation hasn't produced output yet when props phase starts scanning)
