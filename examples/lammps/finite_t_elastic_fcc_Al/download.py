from pathlib import Path
from dflow import Workflow, download_artifact, path_object_of_artifact
from apex.main import config_dflow

config_dflow("global.json")
wf = Workflow(id="finite-t-elastic-fcc-al-joint-vsbrn")

step = wf.query_step(key="confs/std-fcc-finite_t_elastic-00--post")[0]
artifact = step.inputs.artifacts["input_post"]

paths = path_object_of_artifact(artifact)

# 遍历 task.000001 到 task.000025
for task_num in range(1, 26):
    TASK = f"task.{task_num:06d}"
    
    task_prefixes = sorted({
        "/".join(p.split("/")[:i + 1])
        for p in paths
        for i, part in enumerate(p.split("/"))
        if part == TASK
    })
    
    if not task_prefixes:
        print(f"Cannot find {TASK} in artifact, skipping...")
        continue
    
    task_prefix = task_prefixes[0]
    print(f"downloading sub_path: {task_prefix}")
    
    out = Path(f"downloaded-{TASK}")
    out.mkdir(exist_ok=True)
    
    download_artifact(
        artifact=artifact,
        sub_path=task_prefix,
        path=out,
        skip_exists=True,
    )
    
    print(f"downloaded to: {out.resolve()}")
