import os

from dflow.python import upload_packages
upload_packages.append(__file__)


def recursive_search(directories, path='.'):
    # list all directions
    items = os.listdir(path)
    directories_in_path = [
        i for i in items if os.path.isdir(os.path.join(path, i)) and not i.startswith('.')
    ]

    # check if target work direction is found
    if set(directories) <= set(directories_in_path):
        return os.path.abspath(path)

    # recursive search in next direction
    if len(directories_in_path) == 1:
        return recursive_search(directories, os.path.join(path, directories_in_path[0]))

    # return False for failure
    return False
