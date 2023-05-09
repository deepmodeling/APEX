import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from apex.calculator.lib.vasp import *


def setUpModule():
    os.chdir(os.path.abspath(os.path.dirname(__file__)))
