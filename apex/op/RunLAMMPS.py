import os, subprocess, logging
from pathlib import Path
from dflow.python import (
    OP,
    OPIO,
    OPIOSign,
    Artifact,
    upload_packages
)

upload_packages.append(__file__)


class RunLAMMPS(OP):
    """
    class for LAMMPS calculation
    """
    def __init__(self, infomode=1):
        self.infomode = infomode

    @classmethod
    def get_input_sign(cls):
        return OPIOSign({
            'input_lammps': Artifact(Path),
            'run_command': str
        })

    @classmethod
    def get_output_sign(cls):
        return OPIOSign({
            'backward_dir': Artifact(Path, sub_path=False)
        })

    @OP.exec_sign_check
    def execute(self, op_in: OPIO) -> OPIO:
        cwd = os.getcwd()
        os.chdir(op_in["input_lammps"])
        if os.path.exists("run_command"):
            with open("run_command", 'r') as f:
                cmd = f.read()
        else:
            cmd = op_in["run_command"]
        exit_code = subprocess.call(cmd, shell=True)
        if exit_code == 0:
            logging.info("Call Lammps command successfully!")
        else:
            logging.warning(f"Call Lammps command failed with exit code: {exit_code}")

        os.chdir(cwd)
        op_out = OPIO({
            "backward_dir": op_in["input_lammps"]
        })
        return op_out
