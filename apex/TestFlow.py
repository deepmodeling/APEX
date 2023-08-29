import os
import time
from abc import ABC, abstractmethod
from typing import Literal, Optional
from dflow import download_artifact, Workflow


class TestFlow(ABC):
    """
    Constructor
    """
    def __init__(
        self,
        flow_type: Literal['relax', 'props', 'joint'],
        relax_param: Optional[os.PathLike] = None,
        props_param: Optional[os.PathLike] = None
    ):
        self.flow_type = flow_type
        self.relax_param = relax_param
        self.props_param = props_param

    @abstractmethod
    def init_steps(self):
        """
        Define workflow steps for apex.
        IMPORTANT: two steps are required to be defined as attributes in this method,
        and should be named strictly by self.relaxation for relaxation workflow and
        self.property for property test workflow respectively.
        """
        pass

    @staticmethod
    def assertion(wf, step_name: str, artifacts: str):
        while wf.query_status() in ["Pending", "Running"]:
            time.sleep(4)
        assert (wf.query_status() == 'Succeeded')
        step = wf.query_step(name=step_name)[0]
        download_artifact(step.outputs.artifacts[artifacts])

    def generate_flow(self):
        if self.flow_type == 'relax':
            wf = Workflow(name='relaxation')
            wf.add(self.relaxation)
            wf.submit()
            self.assertion(wf, step_name='relaxation-cal', artifacts='retrieve_path')

        elif self.flow_type == 'props':
            wf = Workflow(name='property')
            wf.add(self.property)
            wf.submit()
            self.assertion(wf, step_name='property-cal', artifacts='retrieve_path')

        elif self.flow_type == 'joint':
            wf = Workflow(name='relax-prop')
            wf.add(self.relaxation)
            wf.add(self.property)
            wf.submit()
            self.assertion(wf, step_name='property-cal', artifacts='retrieve_path')



