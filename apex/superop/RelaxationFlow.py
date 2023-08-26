import os
from pathlib import (
    Path,
)
from typing import (
    List,
    Optional,
    Set,
    Type,
)
from dflow import (
    InputArtifact,
    InputParameter,
    Inputs,
    OutputArtifact,
    OutputParameter,
    Outputs,
    Step,
    Steps,
    Workflow,
    argo_len,
    argo_range,
    argo_sequence,
    download_artifact,
    upload_artifact,
)
from dflow.python import (
    OP,
    OPIO,
    Artifact,
    OPIOSign,
    PythonOPTemplate,
    Slices,
)
from dflow.plugins.dispatcher import DispatcherExecutor
from apex import LOCAL_PATH


class RelaxationFlow(Steps):

    def __init__(
        self,
        name: str,
        make_op: Type[OP],
        lmp_run_op: Type[OP],
        vasp_run_op: Type[OP],
        abacus_run_op: Type[OP],
    ):

