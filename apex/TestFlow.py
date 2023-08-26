import time
from abc import ABC, abstractmethod
from dflow import download_artifact, Workflow


class TestFlow(ABC):
    """
    Constructor
    """
    def __init__(self, flow_type, relax_param, props_param):
        self.flow_type = flow_type
        self.relax_param = relax_param
        self.props_param = props_param

    @abstractmethod
    def init_steps(self):
        """
        Define workflow steps for apex.
        IMPORTANT: total six steps are required to be defined as attributes in this method,
        and should be named strictly by self.relaxmake; self.relaxcal; self.relaxpost;
        self.propsmake; self.propscal; self.propspost.
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
            self.assertion(wf, step_name='relaxation-flow', artifacts='retrieve_path')

        elif self.flow_type == 'props':
            wf = Workflow(name='properties')
            wf.add(self.property)
            wf.submit()
            self.assertion(wf, step_name='property-flow', artifacts='retrieve_path')

        elif self.flow_type == 'joint':
            wf = Workflow(name='relax-props')
            wf.add(self.relaxation)
            wf.add(self.property)
            wf.submit()
            self.assertion(wf, step_name='property-flow', artifacts='retrieve_path')



