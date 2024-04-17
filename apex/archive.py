import glob
import logging
import json
import os
from typing import List
from pathlib import Path
from monty.json import MontyEncoder
from monty.serialization import loadfn, dumpfn
from apex.utils import (
    judge_flow,
    json2dict,
    update_dict,
    return_prop_list,
    load_config_file,
    generate_random_string
)
from apex.database.DatabaseFactory import DatabaseFactory
from apex.config import Config


class ResultStorage:
    def __init__(self, work_dir):
        self.work_dir = Path(work_dir).absolute()
        self.result_dict = {'work_path': str(self.work_dir)}

    @property
    def result_data(self):
        return self.result_dict

    @property
    def work_dir_path(self):
        return str(self.work_dir)

    @json2dict
    def sync_relax(self, relax_param: dict):
        # sync results from relaxation task
        confs = relax_param["structures"]
        interaction = relax_param["interaction"]
        conf_dirs = []
        for conf in confs:
            conf_dirs.extend(glob.glob(str(self.work_dir / conf)))
        conf_dirs.sort()
        for ii in conf_dirs:
            relax_task = os.path.join(ii, 'relaxation/relax_task')
            inter,task,structure,result=[os.path.join(relax_task,ii) for ii in 
                ['inter.json','task.json','structure.json','result.json']]
            if not (
                os.path.isfile(inter)
                and os.path.isfile(task)
                and os.path.isfile(result)
            ):
                logging.warning(
                    f"relaxation result path is not complete, will skip result extraction from {relax_task}"
                )
                continue
            logging.info(msg=f"extract results from {relax_task}")
            conf_key = os.path.relpath(ii, self.work_dir)
            conf_dict = {"interaction": loadfn(inter),
                         "parameter": loadfn(task),
                         "structure_info": loadfn(structure),
                         "result": loadfn(result)}
            new_dict = {conf_key: {"relaxation": conf_dict}}
            update_dict(self.result_dict, new_dict)

    @json2dict
    def sync_props(self, props_param: dict, archive_tasks: bool = False):
        # sync results from property test
        confs = props_param["structures"]
        interaction = props_param["interaction"]
        properties = props_param["properties"]
        prop_list = return_prop_list(properties)
        conf_dirs = []
        for conf in confs:
            conf_dirs.extend(glob.glob(str(self.work_dir / conf)))
        conf_dirs.sort()
        for ii in conf_dirs:
            for jj in prop_list:
                prop_dir = os.path.join(ii, jj)
                result = os.path.join(prop_dir, 'result.json')
                param = os.path.join(prop_dir, 'param.json')
                task_list_path = os.path.join(prop_dir, 'task_list.json')
                if not os.path.isfile(result):
                    logging.warning(
                        f"Property post-process is not complete, will skip result extraction from {prop_dir}"
                    )
                    continue
                logging.info(msg=f"extract results from {prop_dir}")
                conf_key = os.path.relpath(ii, self.work_dir)
                result_dict = loadfn(result)
                try:
                    param_dict = loadfn(param)
                except FileNotFoundError:
                    logging.warning(f'{param} file not found')
                    param_dict = None
                prop_dict = {"parameter": param_dict, "result": result_dict}
                # extract running details of each task
                if archive_tasks:
                    logging.debug(msg='Archive running details of tasks...')
                    logging.warning(
                        msg='You are trying to archive detailed running log of each task into database,'
                            'which may exceed the limitation of database allowance.'
                            'Please consider spliting the data or only archiving details of the most important property.'
                    )
                    try:
                        task_list = loadfn(task_list_path)
                        result_task_path = [os.path.join(prop_dir, task, 'result_task.json') for task in task_list]
                    except FileNotFoundError:
                        logging.warning(f'{task_list_path} file not found, will track all tasks listed {prop_dir}')
                        result_task_path = glob.glob(os.path.join(prop_dir, 'task.*', 'result_task.json'))
                    task_result_list = [loadfn(kk) for kk in sorted(result_task_path)]
                    prop_dict["tasks"] = task_result_list

                new_dict = {conf_key: {jj: prop_dict}}
                update_dict(self.result_dict, new_dict)


def connect_database(config):
    # connect to database
    if config.database_type == 'mongodb':
        logging.info(msg='Use database type: MongoDB')
        database = DatabaseFactory.create_database(
            'mongodb',
            'mongodb',
            config.mongodb_database,
            config.mongodb_collection,
            **config.mongodb_config_dict
        )
    elif config.database_type == 'dynamodb':
        logging.info(msg='Use database type: DynamoDB')
        database = DatabaseFactory.create_database(
            'dynamodb',
            'dynamodb',
            config.dynamodb_table_name,
            **config.dynamodb_config_dict
        )
    else:
        raise RuntimeError(f'unsupported database type: {config.database_type}')
    return database


def archive2db(config, data: dict, data_id: str):
    database = connect_database(config)
    # archive results database
    if config.archive_method == 'sync':
        logging.debug(msg='Archive method: sync')
        database.sync(data, data_id, depth=2)
    elif config.archive_method == 'record':
        logging.debug(msg='Archive method: record')
        database.record(data, data_id)
    else:
        raise TypeError(
            f'Unrecognized archive method: {config.archive_method}. '
            f"Should select from 'sync' and 'record'."
        )


def archive_workdir(relax_param, props_param, config, work_dir, flow_type):
    print(f'=> Begin archiving {work_dir}')
    # extract results json
    store = ResultStorage(work_dir)
    if relax_param and flow_type != 'props':
        store.sync_relax(relax_param)
    if props_param and flow_type != 'relax':
        store.sync_props(props_param, config.archive_tasks)

    dump_file = os.path.join(store.work_dir_path, 'all_result.json')
    default_id = generate_random_string(10)
    if os.path.isfile(dump_file):
        logging.info(msg='all_result.json exists, and will be updated.')
        orig_data = loadfn(dump_file)
        try:
            default_id = orig_data['archive_key']
        except KeyError:
            store.result_data['archive_key'] = default_id
        update_dict(orig_data, store.result_data, depth=2)
        dumpfn(orig_data, dump_file, indent=4)
    else:
        store.result_data['archive_key'] = default_id
        dumpfn(store.result_data, dump_file, indent=4)

    # try to get documented key id from all_result.json
    # define archive key
    data_id = config.archive_key if config.archive_key else default_id

    if config.database_type != 'local':
        data_json_str = json.dumps(store.result_data, cls=MontyEncoder, indent=4)
        data_dict = json.loads(data_json_str)
        data_dict['_id'] = data_id

        archive2db(config, data_dict, data_id)


def archive2db_from_json(config, json_file):
    logging.info(msg=f'Archive from local json file: {json_file}')
    data_dict = loadfn(json_file)
    data_json_str = json.dumps(data_dict, cls=MontyEncoder, indent=4)
    data_dict = json.loads(data_json_str)
    # define archive key
    if config.archive_key:
        data_id = config.archive_key
    else:
        data_id = data_dict['archive_key']
    data_dict['_id'] = data_id

    archive2db(config, data_dict, data_id)


def archive_result(
        parameters: List[os.PathLike],
        config_dict: dict,
        work_dir: List[os.PathLike],
        indicated_flow_type: str,
        database_type,
        method,
        archive_tasks,
        is_result
):
    global_config = Config(**config_dict)
    # re-specify args
    if database_type:
        global_config.database_type = database_type
    if method:
        global_config.archive_method = method
    if archive_tasks:
        global_config.archive_tasks = archive_tasks

    if is_result:
        # archive local results json file
        json_file_list = []
        # Parameter here stands for json files that store test results and be archived directly
        for ii in parameters:
            glob_list = glob.glob(os.path.abspath(ii))
            json_file_list.extend(glob_list)
            json_file_list.sort()
        for ii in json_file_list:
            archive2db_from_json(global_config, ii)
    else:
        _, _, flow_type, relax_param, props_param = judge_flow(
            [loadfn(jj) for jj in parameters],
            indicated_flow_type
        )
        # archive work directories
        work_dir_list = []
        for ii in work_dir:
            glob_list = glob.glob(os.path.abspath(ii))
            work_dir_list.extend(glob_list)
            work_dir_list.sort()
        for ii in work_dir_list:
            archive_workdir(relax_param, props_param, global_config, ii, flow_type)


def archive_from_args(
        parameters: List[os.PathLike],
        config_file: os.PathLike,
        work_dirs: List[os.PathLike],
        indicated_flow_type: str,
        database_type,
        method,
        archive_tasks,
        is_result
):
    print('-------Archive result Mode-------')
    archive_result(
        parameters=parameters,
        config_dict=load_config_file(config_file),
        work_dir=work_dirs,
        indicated_flow_type=indicated_flow_type,
        database_type=database_type,
        method=method,
        archive_tasks=archive_tasks,
        is_result=is_result
    )
    print('Complete!')
