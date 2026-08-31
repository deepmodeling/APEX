import logging
import os
import shlex
from dpdispatcher import (
    Machine,
    Resources,
    Submission,
    Task
)
from dflow.python import upload_packages
from apex.core.lib.vasp_runtime import (
    build_kpoint_aware_vasp_command,
    is_switchable_vasp_command,
)
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
        task_command = command
        # execute injected run command
        injected_run_command = os.path.join(work_path, ii, "run_command")
        has_injected_run_command = os.path.isfile(injected_run_command)
        kpoints_path = os.path.join(work_path, ii, "KPOINTS")
        if (
            os.path.isfile(kpoints_path)
            and is_switchable_vasp_command(command)
        ):
            task_command = build_kpoint_aware_vasp_command(
                command,
                staged_run_command=has_injected_run_command,
            )
        elif has_injected_run_command:
            logging.info(msg=f"execute injected run_command file in {injected_run_command}")
            task_command = (
                f"APEX_RUN_COMMAND={shlex.quote(command)} bash run_command"
            )
        task = Task(
            command=task_command,
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
