import unittest
import sys
import os
import glob
import shutil
from pathlib import Path
from dflow.python import (
    OP,
    OPIO,
    OPIOSign,
    Artifact,
    TransientError,
)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
__package__ = "tests"

from apex.fp_OPs import RelaxMakeFp, PropsMakeFp
from apex.LAMMPS_OPs import RelaxMakeLAMMPS, PropsMakeLAMMPS
from context import write_poscar


class TestMakeRelaxOPs(unittest.TestCase):
    def setUp(self) -> None:
        cwd = os.getcwd()
        self.path = cwd
        self.vasp_dir = cwd/Path('vasp_input')
        self.abacus_dir = cwd/Path('abacus_input')
        self.lammps_dir = cwd/Path('lammps_input')

        os.chdir(self.vasp_dir)
        shutil.copytree(os.path.join(cwd, 'confs/confs_example0/'), './confs/', dirs_exist_ok=True)
        self.vasp_confs = self.vasp_dir/'confs'
        os.chdir(cwd)

        os.chdir(self.abacus_dir)
        shutil.copytree(os.path.join(cwd, 'confs/confs_abacus_example0/'), './confs/', dirs_exist_ok=True)
        self.abacus_confs = self.abacus_dir/'confs'
        os.chdir(cwd)

        os.chdir(self.lammps_dir)
        shutil.copytree(os.path.join(cwd, 'confs/confs_example0/'), './confs/', dirs_exist_ok=True)
        self.lammps_confs = self.lammps_dir/'confs'
        os.chdir(cwd)

    def tearDown(self) -> None:
        shutil.rmtree(self.vasp_confs)
        shutil.rmtree(self.abacus_confs)
        shutil.rmtree(self.lammps_confs)

    def test_vasp_make_equi(self):
        op = RelaxMakeFp()
        out = op.execute(
            OPIO({
            'input': self.vasp_dir,
            'param': Path('param_joint.json')
        }))
        self.assertTrue(os.path.exists(self.vasp_dir/'confs'))
        self.assertTrue(os.path.exists(self.vasp_dir/'confs/std-bcc/relaxation/relax_task'))
        self.assertEqual(out['task_paths'], [self.vasp_dir/'confs/std-bcc/relaxation/relax_task'])


    def test_abacus_make_equi(self):
        op = RelaxMakeFp()
        out = op.execute(
            OPIO({
            'input': self.abacus_dir,
            'param': Path('param_joint.json')
        }))
        self.assertTrue(os.path.exists(self.abacus_dir/'confs'))
        self.assertTrue(os.path.exists(self.abacus_dir/'confs/fcc-Al/relaxation/relax_task'))
        self.assertEqual(out['task_paths'], [self.abacus_dir/'confs/fcc-Al/relaxation/relax_task'])


    def test_lammps_make_equi(self):
        op = RelaxMakeLAMMPS()
        out = op.execute(
            OPIO({
            'input': self.lammps_dir,
            'param': Path('param_joint.json')
        }))
        self.assertTrue(os.path.exists(self.lammps_dir/'confs'))
        self.assertTrue(os.path.exists(self.lammps_dir/'confs/std-bcc/relaxation/relax_task'))
        self.assertEqual(out['task_paths'], [self.lammps_dir/'confs/std-bcc/relaxation/relax_task'])


class TestMakePropsOPs(unittest.TestCase):
    def setUp(self) -> None:
        cwd = os.getcwd()
        self.path = cwd
        self.vasp_dir = cwd/Path('vasp_input')
        self.abacus_dir = cwd/Path('abacus_input')
        self.lammps_dir = cwd/Path('lammps_input')

        os.chdir(self.vasp_dir)
        shutil.copytree(os.path.join(cwd, 'confs/confs_example2/'), './confs/', dirs_exist_ok=True)
        self.vasp_confs = self.vasp_dir/'confs'
        os.chdir(cwd)

        os.chdir(self.abacus_dir)
        shutil.copytree(os.path.join(cwd, 'confs/confs_abacus_example2/'), './confs/', dirs_exist_ok=True)
        self.abacus_confs = self.abacus_dir/'confs'
        os.chdir(cwd)

        os.chdir(self.lammps_dir)
        shutil.copytree(os.path.join(cwd, 'confs/confs_example2/'), './confs/', dirs_exist_ok=True)
        self.lammps_confs = self.lammps_dir/'confs'
        os.chdir(cwd)

    def tearDown(self) -> None:
        shutil.rmtree(self.vasp_confs)
        shutil.rmtree(self.abacus_confs)
        shutil.rmtree(self.lammps_confs)

    def test_vasp_make_props(self):
        op = PropsMakeFp()
        out = op.execute(
            OPIO({
            'input': self.vasp_dir,
            'param': Path('param_joint.json')
        }))
        self.assertTrue(os.path.exists(self.vasp_dir/'confs'))
        self.assertTrue(os.path.exists(self.vasp_dir/'confs/std-bcc/eos_00'))
        self.assertEqual(len(out['task_paths']), 2)


    def test_abacus_make_props(self):
        op = PropsMakeFp()
        out = op.execute(
            OPIO({
            'input': self.abacus_dir,
            'param': Path('param_joint.json')
        }))
        self.assertTrue(os.path.exists(self.abacus_dir/'confs'))
        self.assertTrue(os.path.exists(self.abacus_dir/'confs/fcc-Al/eos_00'))
        self.assertEqual(len(out['task_paths']), 2)


    def test_lammps_make_props(self):
        op = PropsMakeLAMMPS()
        out = op.execute(
            OPIO({
            'input': self.lammps_dir,
            'param': Path('param_joint.json')
        }))
        self.assertTrue(os.path.exists(self.lammps_dir/'confs'))
        self.assertTrue(os.path.exists(self.lammps_dir/'confs/std-bcc/eos_00'))
        self.assertEqual(len(out['task_paths']), 2)



