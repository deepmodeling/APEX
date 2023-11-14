import glob
import logging
import json
import os
from pathlib import Path
from monty.json import MontyEncoder
from monty.serialization import loadfn
from apex.utils import (
    judge_flow,
    json2dict,
    update_dict,
    return_prop_list
)
from apex.database.MongoDB import MongoDBPlugin
from apex.config import Config


class ResultArchive:
    def __init__(
        self,
        work_dir
    ):
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
            inter = os.path.join(relax_task, 'inter.json')
            task = os.path.join(relax_task, 'task.json')
            result = os.path.join(relax_task, 'result.json')
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
                         "result": loadfn(result)}
            new_dict = {conf_key: {"relaxation": conf_dict}}
            update_dict(self.result_dict, new_dict)

    @json2dict
    def sync_props(self, props_param: dict):
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
                if not os.path.isfile(result):
                    logging.warning(
                        f"Property task is not complete, will skip result extraction from {prop_dir}"
                    )
                    continue
                logging.info(msg=f"extracting results from {prop_dir}")
                conf_key = os.path.relpath(ii, self.work_dir)
                result_dict = loadfn(result)
                param_dict = loadfn(param)
                prop_dict = {"parameter": param_dict, "result": result_dict}
                new_dict = {conf_key: {jj: prop_dict}}
                update_dict(self.result_dict, new_dict)


def archive(relax_param, props_param, config, work_dir, flow_type):
    print(f'Archive {work_dir}')
    store = ResultArchive(work_dir)
    if relax_param and flow_type != 'props':
        store.sync_relax(relax_param)
    if props_param and flow_type != 'relax':
        store.sync_props(props_param)

    if config.database_type == 'mongodb':
        data_id = str(store.work_dir_path)
        data_json_str = json.dumps(store.result_data, cls=MontyEncoder, indent=4)
        data_dict = json.loads(data_json_str)
        data_dict['_id'] = data_id
        mongo = MongoDBPlugin(
            name=data_id,
            database_name=config.mongodb_config_dict["database"],
            collection_name=config.mongodb_config_dict["collection"],
            host=config.mongodb_config_dict["host"],
            port=config.mongodb_config_dict["port"],
        )
        if config.archive_method == 'sync':
            mongo.sync(data_dict, data_id, depth=2)
        elif config.archive_method == 'record':
            mongo.record(data_dict, data_id)
        else:
            raise RuntimeError(f'unrecognized result archive method: {config.archive_method}')


def archive_result(parameter, config_file, work_dir, user_flow_type):
    print('-------Archive result Mode-------')
    try:
        config_dict = loadfn(config_file)
    except FileNotFoundError:
        raise FileNotFoundError(
            'Please prepare global.json under current work direction '
            'or use optional argument: -c to indicate a specific json file.'
        )
    config = Config(**config_dict)
    _, _, flow_type, relax_param, props_param = judge_flow(parameter, user_flow_type)

    work_dir_list = []
    for ii in work_dir:
        glob_list = glob.glob(os.path.abspath(ii))
        work_dir_list.extend(glob_list)
        work_dir_list.sort()
    for ii in work_dir_list:
        archive(relax_param, props_param, config, ii, flow_type)
