import logging
import os
import datetime
from dflow import (
    Workflow,
    download_artifact
)
from apex.config import Config
from apex.utils import load_config_file


def retrieve_results(
        workflow_id: str,
        work_dir: os.PathLike,
        config_dict: dict,
):
    # config dflow_config and s3_config
    wf_config = Config(**config_dict)
    wf_config.config_dflow(wf_config.dflow_config_dict)
    wf_config.config_bohrium(wf_config.bohrium_config_dict)
    wf_config.config_s3(wf_config.dflow_s3_config_dict)

    # try to retrieve the latest record from .workflow.log if no workflow_id is provided
    if not workflow_id:
        logging.info(msg='No workflow_id is provided, will retrieve the latest workflow')
        workflow_log = os.path.join(work_dir, '.workflow.log')
        assert os.path.isfile(workflow_log), \
            'No workflow_id is provided and no .workflow.log file found in work_dir'
        with open(workflow_log, 'r') as f:
            try:
                last_record = f.readlines()[-1]
            except IndexError:
                raise RuntimeError('No workflow_id is provided and .workflow.log file is empty!')
        workflow_id = last_record[-1].split('\t')[0]
        modified_record = last_record.split('\t')
        modified_record[1] = 'retrieve'
        modified_record[2] = datetime.datetime.now().isoformat()
        with open(workflow_log, 'a') as f:
            f.write('\t'.join(modified_record))

    assert workflow_id, 'No workflow ID for operation!'
    wf = Workflow(id=workflow_id)
    all_keys = wf.query_keys_of_steps()
    wf_info = wf.query()
    download_keys = [key for key in all_keys if key.split('-')[0] == 'propertycal' or key == 'relaxationcal']
    task_left = len(download_keys)
    print(f'Retrieving {task_left} workflow results {workflow_id} to {work_dir}')

    for key in download_keys:
        step = wf_info.get_step(key=key)[0]
        task_left -= 1
        if step['phase'] == 'Succeeded':
            logging.info(f"Retrieving {key}...({task_left} more left)")
            download_artifact(
                artifact=step.outputs.artifacts['retrieve_path'],
                path=work_dir
            )
        else:
            logging.warning(f"Step {key} with status: {step['phase']} will be skipping...({task_left} more left)")


def retrieve_from_args(workflow_id, work_dir, config_file):
    print('-------Retrieve Results-------')
    retrieve_results(
        workflow_id=workflow_id,
        work_dir=work_dir,
        config_dict=load_config_file(config_file)
    )
    print('Completed!')

