import glob
import os
import logging
from typing import List
from monty.serialization import loadfn
from apex.config import Config
from apex.utils import load_config_file, is_json_file, simplify_paths
from apex.reporter.DashReportApp import DashReportApp


def tag_dataset(orig_dataset: dict) -> dict:
    orig_work_path_list = [k for k in orig_dataset.keys()]
    try:
        simplified_path_dict = simplify_paths(orig_work_path_list)
        simplified_dataset = {simplified_path_dict[k]: v for k, v in orig_dataset.items()}
    except KeyError:
        simplified_dataset = orig_dataset
    # replace data id with tag specified in the dataset if exists
    tagged_dataset = {}
    for k, v in simplified_dataset.items():
        if tag := v.pop('tag', None):
            tagged_dataset[tag] = v
        else:
            tagged_dataset[k] = v
    return tagged_dataset


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
            'all_result.json not exist or not under work path indicated. Please do result archive locally first.'
        )
    all_data_dict = {}
    for kk in file_path_list:
        data_dict = loadfn(kk)
        try:
            workdir_id = data_dict.pop('work_path')
            _ = data_dict.pop('archive_key')
        except KeyError:
            logging.warning(msg=f'Invalid json for result archive, will skip: {kk}')
            continue
        else:
            all_data_dict[workdir_id] = data_dict

    # simplify the work path key for all datasets
    simplified_dataset = tag_dataset(all_data_dict)
    DashReportApp(datasets=simplified_dataset).run(debug=True, use_reloader=True)


def report_result(config_dict: dict, path_list: List[os.PathLike]):
    config = Config(**config_dict)
    report_local(path_list)


def report_from_args(config_file, path_list):
    print('-------Report Visualization Mode-------')
    report_result(
        config_dict=load_config_file(config_file),
        path_list=path_list
    )
    print('Complete!')
