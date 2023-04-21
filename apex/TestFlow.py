import time
from abc import ABC, abstractmethod
from monty.serialization import loadfn

from dflow import download_artifact, Workflow

from apex.lib.utils import identify_task


class TestFlow(ABC):
    def __init__(self, args):
        # identify type of flow and input parameter file
        num_args = len(args.files)
        if num_args == 1:
            self.do_relax = False
            task_type = identify_task(args.files[0])
            if task_type == 'relax':
                self.relax_param = args.files[0]
                self.props_param = None
            elif task_type == 'props':
                self.relax_param = None
                self.props_param = args.files[0]
        elif num_args == 2:
            self.do_relax = True
            file1_type = identify_task(args.files[0])
            file2_type = identify_task(args.files[1])
            if not file1_type == file2_type:
                if file1_type == 'relax':
                    self.relax_param = args.files[0]
                    self.props_param = args.files[1]
                else:
                    self.relax_param = args.files[1]
                    self.props_param = args.files[0]
            else:
                raise RuntimeError('Same type of input json files')
        else:
            raise ValueError('A maximum of two input arguments is allowed')

        if self.do_relax:
            self.flow_type = 'joint'
        elif not self.props_param:
            self.flow_type = 'relax'
        else:
            self.flow_type = 'props'

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
    def assertion(wf, task_type):
        while wf.query_status() in ["Pending", "Running"]:
            time.sleep(4)
        assert (wf.query_status() == 'Succeeded')
        step = wf.query_step(name=f"{task_type}post")[0]
        download_artifact(step.outputs.artifacts["output_post"])

    def generate_flow(self):
        if self.flow_type == 'relax':
            wf = Workflow(name='relaxation')
            wf.add(self.relaxmake)
            wf.add(self.relaxcal)
            wf.add(self.relaxpost)
            wf.submit()
            self.assertion(wf, 'Relax')

        elif self.flow_type == 'props':
            wf = Workflow(name='properties')
            wf.add(self.propsmake)
            wf.add(self.propscal)
            wf.add(self.propspost)
            wf.submit()
            self.assertion(wf, 'Props')

        elif self.flow_type == 'joint':
            wf = Workflow(name='relax-props')
            wf.add(self.relaxmake)
            wf.add(self.relaxcal)
            wf.add(self.relaxpost)
            wf.add(self.propsmake)
            wf.add(self.propscal)
            wf.add(self.propspost)
            wf.submit()
            self.assertion(wf, 'Props')



