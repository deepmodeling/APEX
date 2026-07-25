# Installed Profile: Local Workstation

APEX and calculator executables run on this workstation in dflow debug mode.
No Bohrium account, ticket, cloud image, Kubernetes, or outer job is required.

## Preflight

Confirm the selected calculator command works directly in the current shell,
including required modules/environment variables and model/potential files.

## Configuration

Copy `data/global_local_debug.json` to the job as `global.json` and replace
`run_command` with the approved local calculator command.

## Submit

```bash
cd <job-dir>
apex submit -d param.json -c global.json -f <relax|props|joint> -n <workflow-name>
```

Do not add Bohrium fields to this configuration. Results and debug artifacts
remain on the local filesystem.
