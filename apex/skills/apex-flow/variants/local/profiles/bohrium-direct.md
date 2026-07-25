# Installed Profile: Bohrium Cloud (Direct Local Submit)

The Agent and APEX client run on this machine. Calculations run in Bohrium
containers through dflow. Do not create an outer Bohrium submission job.

## Authentication

Check the saved account without exposing the password:

```bash
apex account --show
```

If email, password, or program ID is missing, ask the user to configure it:

```bash
apex account
```

The account is stored in `~/.apex/account.json` and merged into Bohrium
configuration by APEX. This profile does not use `BOHRIUM_ACCESS_KEY`,
`bohrium_config.ticket`, `generate_config.py refresh-global`, or an outer
`c1_m2_cpu` job.

## Configuration

Copy `data/global_bohrium_direct.json` to the job as `global.json`. Select the
calculator image/run command and `scass_type` for the approved backend. VASP
requires a user-authorized licensed image.

## Submit

```bash
cd <job-dir>
apex submit param.json -c global.json -f <relax|props|joint> -n <workflow-name>
```

Keep the process running for automatic monitoring, retrieval, and archival.
Preserve the printed dflow workflow ID.
