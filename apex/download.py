import logging
from dflow import (
    Workflow,
    download_artifact
)
from apex.config import Config
from apex.utils import load_config_file


def download_results(
        workflow_id,
        destination,
        config_file,
):
    print('-------Retrieve Results-------')
    config_dict = load_config_file(config_file)
    # config dflow_config and s3_config
    wf_config = Config(**config_dict)
    wf_config.config_dflow(wf_config.dflow_config_dict)
    wf_config.config_bohrium(wf_config.bohrium_config_dict)
    wf_config.config_s3(wf_config.dflow_s3_config_dict)

    wf = Workflow(id=workflow_id)
    all_keys = wf.query_keys_of_steps()
    wf_info = wf.query()
    download_keys = [key for key in all_keys if key.split('-')[0] == 'propertycal' or key == 'relaxationcal']
    task_left = len(download_keys)
    print(f'Retrieving {task_left} workflow results {workflow_id} to {destination}')
    # use tqdm to show progress
    for key in download_keys:
        step = wf_info.get_step(key=key)[0]
        task_left -= 1
        if step['phase'] == 'Succeeded':
            logging.info(f"Retrieving {key}...({task_left} more left)")
            download_artifact(
                artifact=step.outputs.artifacts['retrieve_path'],
                path=destination
            )
        else:
            logging.warning(f"Step {key} with status: {step['phase']} will be skipping...({task_left} more left)")

    print('Completed!')
