import argparse
import json
import logging
import os
import textwrap
from typing import (
    List,
    Optional,
)

from dflow import (
    Step,
    Steps,
    Workflow,
    download_artifact,
    upload_artifact,
)
from apex import (
    __version__,
)

def parse_args():
    parser = argparse.ArgumentParser(
        description="APEX: A scientific workflow for Alloy Properties EXplorer using simulations",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    parser.add_argument('files', type=str, nargs='+',
                        help='Input indicating json files')
    parser.add_argument('-r', "--relax", help="Submit relaxation workflow",
                        action="store_true")
    parser.add_argument('-p', "--props", help="Submit core test workflow",
                        action="store_true")
    parser.add_argument('-j', "--joint", help="Submit relaxation followed by core test joint workflow",
                        action="store_true")
    parser.add_argument('-mr', "--make_relax", help="Run make relaxation step locally",
                        action="store_true")
    parser.add_argument('-pr', "--post_relax", help="Run post relaxation step locally",
                        action="store_true")
    parser.add_argument('-mp', "--make_props", help="Run make properties step locally",
                        action="store_true")
    parser.add_argument('-pp', "--post_props", help="Run post properties step locally",
                        action="store_true")
    args = parser.parse_args()

    # check args
    provided_args = sum([args.relax, args.props, args.joint,
                         args.make_relax, args.post_relax,
                         args.make_props, args.post_props])
    if provided_args > 1:
        parser.error("Only one optional argument is allowed.")

    return args

def main():
    logging.basicConfig(level=logging.INFO)



