# Installed Profile: Bohrium Cloud (Direct Local Submit)

The Agent and APEX client run on this machine. Calculations run in Bohrium
containers through dflow. Do not create an outer Bohrium submission job.

## Authentication

Check the saved account without exposing the password or AccessKey:

```bash
apex account --show
```

Configure either email/password or AccessKey authentication, together with a
program ID:

```bash
apex account
# or, non-interactively:
apex account --access-key YOUR_ACCESS_KEY --program-id YOUR_PROGRAM_ID
```

Interactive setup first asks whether to use email/password or AccessKey;
whitespace-only field input keeps the saved value. Clear saved login methods
without removing the rest of the cloud profile with:

```bash
apex account --clear             # email/password and AccessKey
apex account --clear access-key  # AccessKey only
apex account --clear email       # email/password only
```

The account is stored in `~/.apex/account.json` and merged into Bohrium
configuration by APEX. A saved AccessKey is passed to dflow, which exchanges
it for a short-lived ticket used by DPDispatcher's Bohrium context; APEX does
not write that ticket into `global.json`.
This profile does not require the `BOHRIUM_ACCESS_KEY` environment variable,
`generate_config.py refresh-global`, or an outer `c1_m2_cpu` job.

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
