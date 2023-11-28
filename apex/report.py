import glob
import os
import logging
from monty.serialization import loadfn
from apex.config import Config
from apex.utils import load_config_file, is_json_file
from apex.reporter.DashReportApp import DashReportApp


def report_local(input_path_list):
    path_list = []
    for ii in input_path_list:
        glob_list = glob.glob(os.path.abspath(ii))
        path_list.extend(glob_list)
        path_list.sort()

    file_path_list = []
    for jj in path_list:
        if os.path.isfile(jj) and is_json_file(jj):
            file_path_list.append(jj)
        elif os.path.isdir(jj) and os.path.isfile(os.path.join(jj, 'all_result.json')):
            file_path_list.append(os.path.join(jj, 'all_result.json'))
        else:
            raise FileNotFoundError(f'Invalid work path or json file path provided: {jj}')

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

    DashReportApp(datasets=all_data_dict).run()


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
