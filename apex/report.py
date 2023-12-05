import glob
import os
import logging
from monty.serialization import loadfn
from apex.config import Config
from apex.utils import load_config_file, is_json_file, simplify_paths
from apex.reporter.DashReportApp import DashReportApp


def simplify_dataset(orig_dataset: dict) -> dict:
    orig_work_path_list = [k for k in orig_dataset.keys()]
    simplified_path_dict = simplify_paths(orig_work_path_list)
    simplified_dataset = {simplified_path_dict[k]: v for k, v in orig_dataset.items()}
    return simplified_dataset


def report_local(input_path_list):
    path_list = []
    for ii in input_path_list:
        glob_list = glob.glob(os.path.abspath(ii))
        path_list.extend(glob_list)
        path_list.sort()

    if not path_list:
        raise RuntimeError('Invalid work path indicated. No path has been found!')

    file_path_list = []
    for jj in path_list:
        if os.path.isfile(jj) and is_json_file(jj):
            file_path_list.append(jj)
        elif os.path.isdir(jj) and os.path.isfile(os.path.join(jj, 'all_result.json')):
            file_path_list.append(os.path.join(jj, 'all_result.json'))
        else:
            raise FileNotFoundError(f'Invalid work path or json file path provided: {jj}')

    if not file_path_list:
        raise FileNotFoundError(
            'all_result.json not exist or not under work path indicated. Please double check the input.'
        )
    all_data_dict = {}
    for kk in file_path_list:
        data_dict = loadfn(kk)
        try:
            workdir_id = data_dict.pop('work_path')
        except KeyError:
            logging.warning(msg=f'Invalid json for result archive, will skip: {kk}')
            continue
        else:
            all_data_dict[workdir_id] = data_dict

    # simplify the work path key for all datasets
    simplified_dataset = simplify_dataset(all_data_dict)
    DashReportApp(datasets=simplified_dataset).run(debug=True)


def report_database():
    pass


def report_result(config_file, path_list):
    print('-------Report Visualization Mode-------')
    config_dict = load_config_file(config_file)
    config = Config(**config_dict)

    if config.database_type == 'local':
        report_local(path_list)
    else:
        pass

    print('Complete!')
