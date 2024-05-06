#!/usr/bin/env python3

import logging
import os
import re
import shutil
from dflow.python import upload_packages
upload_packages.append(__file__)

iter_format = "%s"
task_format = "%s"
log_iter_head = "task type: " + iter_format + " task: " + task_format + " process: "


def round_format(nums, decimal_places):
    return [f"{round(num, decimal_places):.{decimal_places}f}"
             if isinstance(num, (int, float)) else num for num in nums]


def round_2d_format(nums, decimal_places):
    return [[f"{round(num, decimal_places):.{decimal_places}f}"
             if isinstance(num, (int, float)) else num for num in row]
            for row in nums]


def make_iter_name(iter_index):
    return "task type:" + (iter_format)


def create_path(path):
    path += "/"
    if os.path.isdir(path):
        dirname = os.path.dirname(path)
        counter = 0
        while True:
            bk_dirname = dirname + ".bk%03d" % counter
            if not os.path.isdir(bk_dirname):
                shutil.move(dirname, bk_dirname)
                break
            counter += 1
    os.makedirs(path)


def replace(file_name, pattern, subst):
    file_handel = open(file_name, "r")
    file_string = file_handel.read()
    file_handel.close()
    file_string = re.sub(pattern, subst, file_string)
    file_handel = open(file_name, "w")
    file_handel.write(file_string)
    file_handel.close()


def copy_file_list(file_list, from_path, to_path):
    for jj in file_list:
        if os.path.isfile(os.path.join(from_path, jj)):
            shutil.copy(os.path.join(from_path, jj), to_path)
        elif os.path.isdir(os.path.join(from_path, jj)):
            shutil.copytree(os.path.join(from_path, jj), os.path.join(to_path, jj))


def cmd_append_log(cmd, log_file):
    ret = cmd
    ret = ret + " 1> " + log_file
    ret = ret + " 2> " + log_file
    return ret


def log_iter(task, ii, jj):
    logging.info((log_iter_head + "%s") % (ii, jj, task))


def repeat_to_length(string_to_expand, length):
    ret = ""
    for ii in range(length):
        ret += string_to_expand
    return ret


def log_task(message):
    header = repeat_to_length(" ", len(log_iter_head % (0, 0)))
    logging.info(header + message)


def record_iter(record, confs, ii, jj):
    with open(record, "a") as frec:
        frec.write("%s %s %s\n" % (confs, ii, jj))
