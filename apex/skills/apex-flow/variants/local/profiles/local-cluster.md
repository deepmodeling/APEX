# Installed Profile: Local Slurm/PBS Cluster

The Agent runs APEX on a cluster login node. dflow uses local debug mode while
DPDispatcher submits calculator tasks to the site's scheduler.

## Required Questions

Before writing `global.json`, ask for:

- scheduler (`Slurm` or `PBS`);
- partition/queue and account/project flags;
- nodes, tasks/cores, GPUs, memory, and walltime;
- required modules or environment activation;
- calculator `run_command`;
- cluster-visible work/scratch paths.

Do not invent scheduler flags or credentials.

## Configuration

For Slurm, copy `data/global_local_cluster_slurm.json` to `global.json` and
replace every `<...>` placeholder. For PBS, translate scheduler directives to
the site's PBS syntax and set `machine.batch_type` to `PBS`.

The default assumes the Agent is already on the login node and therefore uses
`Local` context. If APEX runs on a workstation and connects remotely, use
DPDispatcher `SSHContext` instead and obtain hostname, username, port, and
remote root from the user.

## Submit

```bash
cd <job-dir>
apex submit -d param.json -c global.json -f <relax|props|joint> -n <workflow-name>
```

Monitor scheduler jobs and inspect `dpdispatcher.log` on failure. No Bohrium
account or ticket is used.
