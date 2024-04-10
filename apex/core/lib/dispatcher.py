import logging
import os
from dpdispatcher import (
    Machine,
    Resources,
    Submission,
    Task
)
from dflow.python import upload_packages
upload_packages.append(__file__)


def make_submission(
    mdata_machine,
    mdata_resources,
    commands,
    work_path,
    run_tasks,
    group_size,
    forward_common_files,
    forward_files,
    backward_files,
    outlog,
    errlog,
):

    #if mdata_machine["local_root"] != "./":
    #    raise RuntimeError(f"local_root must be './' in config JSON file.")

    abs_local_root = os.path.abspath("./")

    abs_mdata_machine = mdata_machine.copy()
    abs_mdata_machine["local_root"] = abs_local_root

    machine = Machine.load_from_dict(abs_mdata_machine)
    resources = Resources.load_from_dict(mdata_resources)

    command = "&&".join(commands)

    task_list = []
    for ii in run_tasks:
        # execute injected run command
        injected_run_command = os.path.join(work_path, ii, "run_command")
        if os.path.isfile(injected_run_command):
            logging.info(msg=f"execute injected run_command file in {injected_run_command}")
            with open(injected_run_command, "r") as f:
                command = f.read()
        task = Task(
            command=command,
            task_work_path=ii,
            forward_files=forward_files,
            backward_files=backward_files,
            outlog=outlog,
            errlog=errlog,
        )
        task_list.append(task)
    submission = Submission(
        work_base=work_path,
        machine=machine,
        resources=resources,
        task_list=task_list,
        forward_common_files=[],
        backward_common_files=[],
    )
    return submission

