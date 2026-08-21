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
│  1. pip install --upgrade --no-cache-dir apex-flow             │
│  2. apex submit param.json -c global.json -n "<wf-name>"      │
│  → Connects to workflows.deepmodeling.com                      │
│  → Waits for completion and retrieves results automatically    │
│  → upload_packages sends new APEX code to all inner steps      │
└──────────────────────────┬──────────────────────────────────────┘
                           │ dflow orchestration
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  Inner containers (managed by dflow on Bohrium)                 │
│  - LAMMPS image: dpa4-phonolammps:0.0.2                       │
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
3. For a new task, run `scripts/generate_config.py create ...`; never hand-write
   `param.json` or `global.json`.
4. For an existing generated task, `cd` into its directory and run
   `python <skill-root>/scripts/generate_config.py refresh-global --global global.json`.
   This refreshes the ticket and integer project IDs atomically without changing
   `param.json`.
5. Verify that `global.json` contains a non-empty `bohrium_config.ticket`.

> ⚠️ **Ticket 有效期约 1 周，会过期！** Use `refresh-global` when preparing
> an existing generated task for a new submission. Do not refresh the
> ticket in `run.sh`: the outer APEX container may not receive
> `BOHRIUM_ACCESS_KEY`, and ad-hoc response parsing there has no reliable error
> handling.

> **WARNING**: The log may show `WARNING:root:Missing Bohrium account fields: email, password.` — this is **non-fatal**; ticket-based auth works without email/password.

## Outer Job run.sh Template

```bash
#!/bin/bash
set -eo pipefail

python3 -m pip install --upgrade --no-cache-dir apex-flow 2>&1 | tail -5
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
- Must use `pip install --upgrade --no-cache-dir apex-flow` (no `==` pin).
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
| **Outer job (submission client)** | `registry.dp.tech/dptech/dp/native/prod-397637/apex-flow:1.3.0.post` | Lightweight; just runs `apex submit` |
| **LAMMPS calculator** | `registry.dp.tech/dptech/dp/native/prod-16664/dpa4-phonolammps:0.0.2` | Default; includes phonoLAMMPS |
| **ABACUS calculator** | (same APEX image has ABACUS) | Or user-specified |
| **VASP calculator** | User must provide after confirming license | Commercial; **never invent a default image** |

> ⚠️ **Do NOT combine `deepmd-kit:3.1.1` with any NVIDIA T4 machine**. It also has a known segfault bug when handling triclinic cells (non-orthogonal boxes), including on CPU. Use `3.1.3` or later.

> LAMMPS phonon and Grüneisen tasks are forced to use `registry.dp.tech/dptech/dp/native/prod-16664/dpa4-phonolammps:0.0.2`. It includes integrated USER-DEEPMD, phonoLAMMPS, CUDA 12.8, and sm89/sm120 LAMMPS dispatch. Do not add the legacy `plugin load libdeepmd_lmp.so` command.

| Backend | scass_type (inner containers) | Notes |
|---------|-------------------------------|-------|
| LAMMPS (DeePMD/MACE/NEP) | `c8_m32_1 * NVIDIA 4090` | GPU beneficial |
| LAMMPS (EAM/MEAM/SNAP) | `c16_m32_cpu` | CPU sufficient |
| ABACUS | `c16_m32_cpu` | CPU |
| VASP | `c32_m128_cpu` (default) | Align `mpirun -n <N>` with CPU count |

### VASP image resolution + `vasp_run_command` (license-gated)

**Image resolution (mandatory before VASP submit):**

1. Query the user's **private** Bohrium images with keyword `vasp`:
   - MatMaster tool: `Bohrium(action="list_images", keyword="vasp")`
     (*list the user's own private Docker images (filtered by keyword)*)
   - Or skill helper: `python scripts/list_bohrium_images.py --keyword vasp --require`
2. Else use a **user-known authorized** VASP image address.
3. If neither exists → **stop**. Do not invent `vasp:5.4.4-dflow` or any default.
4. Pass the approved image as `--vasp-image` to `generate_config.py create`.

**Run command:** do **not** use bare `mpirun -n 16 vasp_std`. Typical
Bohrium-safe command (adjust binary path to the user-approved image):

```text
bash -c "source /opt/intel/oneapi/setvars.sh && ulimit -s unlimited && mpirun -n 32 /opt/vasp.5.4.4/bin/vasp_std"
```

Must include: Intel `setvars.sh`, `ulimit -s unlimited`, absolute `vasp_std`,
and `-n` matching `scass_type`. See `reference/calculators.md` and Critical
Rules in `SKILL.md`.

### DFT k-spacing (REQUIRED)

- VASP: `INCAR` must set `KSPACING` (APEX auto-writes `KPOINTS`).
- ABACUS: `INPUT` must set `kspacing`, or `cal_setting.K_POINTS`.
- `validate_inputs.py` rejects missing spacing / bare VASP run commands.

> **IMPORTANT**: Use the minimal `c1_m2_cpu` machine for the outer Bohrium job since it only submits to dflow and waits. The heavy compute is in the inner containers specified by `scass_type` in `global.json`.

## Validated global.json Structure

`program_id` and `bohrium_config.project_id` **must** come from the environment
variable `BOHRIUM_PROJECT_ID` (or `--project-id`). Never hardcode a personal
project ID in examples or generated configs. **Do not write `global.json`
manually and do not copy the placeholders below into a real file.** Run
`scripts/generate_config.py create ...` or `scripts/generate_config.py
refresh-global --global global.json`; both convert the environment string to an
integer and write both ID fields as JSON numbers.

The following is a type-annotated shape, not valid JSON:

```text
{
    "dflow_host": "https://workflows.deepmodeling.com",
    "k8s_api_server": "https://workflows.deepmodeling.com",
    "batch_type": "Bohrium",
    "context_type": "Bohrium",
    "program_id": <BOHRIUM_PROJECT_ID as an unquoted integer>,
    "bohrium_config": {
        "ticket": "<UUID from API conversion — auto-filled by generate_config.py>",
        "project_id": <the same unquoted integer>
    },
    "apex_image_name": "registry.dp.tech/dptech/dp/native/prod-397637/apex-flow:1.3.0.post",
    "lammps_image_name": "registry.dp.tech/dptech/dp/native/prod-16664/dpa4-phonolammps:0.0.2",
    "lammps_run_command": "lmp -in in.lammps",
    "scass_type": "c16_m32_cpu",
    "group_size": 1,
    "pool_size": 1
}
```

Quoted digits such as `"program_id": "..."` are invalid for DPDispatcher even
though they look numeric. From the task directory, always run:

```bash
cd <job-dir>
python <skill-root>/scripts/validate_inputs.py \
  --param param.json --global global.json
```

Do not upload or submit unless validation reports `Validation PASSED` and both
project ID lines report `type=int`. Submit the newly validated directory as a
new outer Bohrium job; retrying an old outer job reuses its old input snapshot.

> For GPU potentials (DeePMD, MACE, NEP), change `scass_type` to `"c8_m32_1 * NVIDIA 4090"`.
> Before submitting, run `scripts/validate_apex_combo.py check` on the chosen image × scass_type.

## Agent-Managed Submission Workflow (Complete Lifecycle)

The default agent workflow omits `-s` so the outer job waits for the inner dflow
workflow and retrieves its results automatically:

1. **Prepare inputs locally** (Bash): run `scripts/generate_config.py create ...`
   to generate `global.json` + `param.json` + copy structure/model files into a
   job directory. Never hand-write these files. If credentials must be refreshed
   later, run `refresh-global --global global.json` from that job directory.

2. **Submit outer Bohrium job** (blocking mode):
   ```python
   Bohrium(action="submit",
     input_dir="<job_dir>",
     image="registry.dp.tech/dptech/dp/native/prod-397637/apex-flow:1.3.0.post",
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
