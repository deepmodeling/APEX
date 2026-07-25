# Local Agent Submission Reference

This skill is installed on the machine that runs the APEX client. The selected
mode is recorded in `execution-profile.md`; read it before preparing a job.

## Mode Boundary

| Profile | Authentication | Submit client | Calculator execution |
|---|---|---|---|
| Bohrium cloud | `apex account` | Local Agent machine | Bohrium/dflow containers |
| local | None | Local Agent machine | Same workstation |
| local cluster | Scheduler/user login | Cluster login node | Slurm/PBS compute nodes |

Do not require `BOHRIUM_ACCESS_KEY` or a ticket in this local edition. Those are
used by the separate `apex skill --zip` Cloud/MatMaster edition, where an outer
container cannot read the user's local account file.

## Common Preparation

1. Confirm execution profile, calculator backend, structure, and property
   parameters.
2. Copy all required model/potential/input files into locations visible from
   the selected execution environment.
3. Build `param.json` from `reference/properties.md` and calculator references.
4. Start from the selected profile's audited global template under `data/`.
5. Validate:

   ```bash
   python <skill-root>/scripts/validate_inputs.py \
     --param param.json --global global.json
   ```

6. Submit without `-s` for automatic monitoring, retrieval, and local
   `all_result.json` generation.

## Results

Prefer property-level files for Agent answers:

```text
confs/<structure>/<property>_00/result.json
```

Use `scripts/parse_results.py --work-dir <job-dir> --format summary` when many
results must be summarized. `apex archive` is optional and is appropriate when
`all_result.json` is missing, database archival is requested, or a subsequent
`apex report` needs consolidated data.

## Failure Handling

- Preserve the exact workflow ID for Bohrium mode.
- For local/cluster debug mode, inspect the local dflow debug directory and
  `dpdispatcher.log`.
- Do not retry with changed inputs in place without revalidating.
- A zero exit code is not sufficient: verify expected `result.json` files.
