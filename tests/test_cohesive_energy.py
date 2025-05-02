import glob
import os
import shutil
import sys
import unittest
import json

import dpdata
import numpy as np
import pandas as pd
from monty.serialization import loadfn, dumpfn
from pymatgen.io.vasp import Incar
import importlib.util
import sys

# 避免导入pyramid包中已移除的类
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
__package__ = "tests"

# 动态导入带有连字符的模块
spec = importlib.util.spec_from_file_location(
    "CohesiveEnergy", 
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "apex", "core", "property", "CohesiveEnergy-claude.py"))
)
CohesiveEnergy_module = importlib.util.module_from_spec(spec)
sys.modules["CohesiveEnergy"] = CohesiveEnergy_module
spec.loader.exec_module(CohesiveEnergy_module)
CohesiveEnergy = CohesiveEnergy_module.CohesiveEnergy

# 直接导入报告模块，避免通过apex包导入
from apex.reporter.property_report import CohesiveEnergyReport

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
__package__ = "tests"


class TestCohesiveEnergy(unittest.TestCase):
    def setUp(self):
        _jdata = {
            "structures": ["confs/std-fcc"],
            "interaction": {
                "type": "vasp",
                "incar": "vasp_input/INCAR.rlx",
                "potcar_prefix": ".",
                "potcars": {"Li": "vasp_input/POTCAR"},
            },
            "properties": [
                {
                    "type": "cohesive_energy",
                    "skip": False,
                    "lattice_param_start": 0.8,
                    "lattice_param_end": 1.2,
                    "lattice_param_step": 0.01,
                    "latt_abs": False,
                    "single_atom": True,
                    "single_atom_path": None,
                    "cal_setting": {
                        "relax_pos": True,
                        "relax_shape": True,
                        "relax_vol": False,
                        "overwrite_interaction": {
                            "type": "deepmd",
                            "model": "lammps_input/frozen_model.pb",
                            "type_map": {"Al": 0},
                        },
                    },
                }
            ],
        }

        self.equi_path = "confs/std-fcc/relaxation/relax_task"
        self.source_path = "equi/vasp"
        self.target_path = "confs/std-fcc/cohesive_energy_00"
        if not os.path.exists(self.equi_path):
            os.makedirs(self.equi_path)

        self.confs = _jdata["structures"]
        self.inter_param = _jdata["interaction"]
        self.prop_param = _jdata["properties"]

        self.cohesive_energy = CohesiveEnergy(_jdata["properties"][0])

        # 确保源文件目录存在
        os.makedirs(os.path.dirname(self.source_path), exist_ok=True)
        os.makedirs(self.source_path, exist_ok=True)
        
        # 创建一个简单的CONTCAR文件用于测试
        contcar_content = """Li bulk
1.0
        4.0000000000         0.0000000000         0.0000000000
        0.0000000000         4.0000000000         0.0000000000
        0.0000000000         0.0000000000         4.0000000000
   Li
   4
Direct
     0.000000000         0.000000000         0.000000000
     0.000000000         0.500000000         0.500000000
     0.500000000         0.000000000         0.500000000
     0.500000000         0.500000000         0.000000000
"""
        with open(os.path.join(self.source_path, "CONTCAR"), "w") as f:
            f.write(contcar_content)
        
        # 同样在equi_path中创建CONTCAR文件
        with open(os.path.join(self.equi_path, "CONTCAR"), "w") as f:
            f.write(contcar_content)

    def tearDown(self):
        if os.path.exists(self.equi_path):
            shutil.rmtree(self.equi_path)
        if os.path.exists(self.target_path):
            shutil.rmtree(self.target_path)

    def test_task_type(self):
        self.assertEqual("cohesive_energy", self.cohesive_energy.task_type())

    def test_task_param(self):
        self.assertEqual(self.prop_param[0], self.cohesive_energy.task_param())

    def test_make_confs_0(self):
        # 测试没有CONTCAR文件时的异常
        if not os.path.exists(os.path.join(self.equi_path, "CONTCAR")):
            with self.assertRaises(RuntimeError):
                self.cohesive_energy.make_confs(self.target_path, self.equi_path)
        
        # 复制CONTCAR文件
        shutil.copy(
            os.path.join(self.source_path, "CONTCAR"),
            os.path.join(self.equi_path, "CONTCAR"),
        )
        
        # 测试生成配置
        print(
            "gen cohesive energy from "
            + str(self.cohesive_energy.lattice_param_start)
            + " to "
            + str(self.cohesive_energy.lattice_param_end)
            + " by every "
            + str(self.cohesive_energy.lattice_param_step)
        )
        task_list = self.cohesive_energy.make_confs(self.target_path, self.equi_path)
        dfm_dirs = glob.glob(os.path.join(self.target_path, "task.*"))
        
        # 检查是否创建了单原子计算任务
        single_atom_dir = os.path.join(self.target_path, "single_atom")
        self.assertTrue(os.path.exists(single_atom_dir))
        self.assertTrue(os.path.isfile(os.path.join(single_atom_dir, "single_atom.json")))
        
        # 检查每个任务目录
        for ii in dfm_dirs:
            self.assertTrue(os.path.isfile(os.path.join(ii, "POSCAR")))
            cohesive_json_file = os.path.join(ii, "cohesive.json")
            self.assertTrue(os.path.isfile(cohesive_json_file))
            cohesive_json = loadfn(cohesive_json_file)
            self.assertIn("lattice", cohesive_json)
            self.assertIn("scale", cohesive_json)
            self.assertEqual(
                os.path.realpath(os.path.join(ii, "POSCAR.orig")),
                os.path.realpath(os.path.join(self.equi_path, "CONTCAR")),
            )

    def test_make_confs_1(self):
        # 测试reproduce模式
        self.cohesive_energy.reprod = True
        shutil.copy(
            os.path.join(self.source_path, "CONTCAR"),
            os.path.join(self.equi_path, "CONTCAR"),
        )
        # 在reproduce模式下，如果没有init_data_path，应该抛出异常
        with self.assertRaises(RuntimeError):
            self.cohesive_energy.make_confs(self.target_path, self.equi_path)


class TestCohesiveEnergyReport(unittest.TestCase):
    def setUp(self):
        # 创建测试数据
        self.res_data = {
            "3.5": {"total_energy": -3.0, "cohesive_energy": -5.0},
            "3.6": {"total_energy": -3.2, "cohesive_energy": -5.2},
            "3.7": {"total_energy": -3.5, "cohesive_energy": -5.5},
            "3.8": {"total_energy": -3.8, "cohesive_energy": -5.8},
            "3.9": {"total_energy": -3.6, "cohesive_energy": -5.6},
            "4.0": {"total_energy": -3.4, "cohesive_energy": -5.4},
        }
        
        # 转换为CohesiveEnergyReport期望的格式
        self.formatted_data = {}
        for k, v in self.res_data.items():
            # CohesiveEnergyReport期望直接使用cohesive_energy值
            # 而CohesiveEnergy._compute_lower返回的是字典格式
            if isinstance(v, dict) and "cohesive_energy" in v:
                self.formatted_data[k] = v["cohesive_energy"]
            else:
                # 如果已经是简单值，直接使用
                self.formatted_data[k] = v

    def test_plotly_graph(self):
        # 测试绘图功能
        traces, layout = CohesiveEnergyReport.plotly_graph(self.formatted_data, "test_material")
        
        # 验证返回的traces和layout
        self.assertEqual(len(traces), 2)  # 应该有两个trace：数据线和零能量参考线
        self.assertEqual(traces[0].name, "test_material")
        self.assertEqual(traces[0].mode, "lines+markers")
        
        # 验证x轴数据是标度化的晶格参数
        lattice_values = [float(k) for k in self.formatted_data.keys()]
        a0 = lattice_values[0]
        scaled_lattice = [a/a0 for a in lattice_values]
        self.assertEqual(list(traces[0].x), scaled_lattice)
        
        # 验证y轴数据是内聚能
        cohesive_values = list(self.formatted_data.values())
        self.assertEqual(list(traces[0].y), cohesive_values)
        
        # 验证layout
        self.assertEqual(layout.title.text, "Cohesive Energy")
        self.assertIn("Scaled lattice parameter", layout.xaxis.title.text)
        self.assertIn("Cohesive energy", layout.yaxis.title.text)

    def test_dash_table(self):
        # 测试表格功能
        table, df = CohesiveEnergyReport.dash_table(self.formatted_data)
        
        # 验证返回的表格和数据框
        self.assertEqual(len(df), len(self.formatted_data))
        self.assertEqual(len(df.columns), 3)  # 应该有三列：晶格常数、标度化晶格参数和内聚能
        
        # 验证列名
        self.assertIn("Lattice Constant (Å)", df.columns)
        self.assertIn("Scaled Lattice Parameter (a/a0)", df.columns)
        self.assertIn("Cohesive Energy (eV/atom)", df.columns)
        
        # 验证数据
        lattice_values = [float(k) for k in self.formatted_data.keys()]
        cohesive_values = list(self.formatted_data.values())
        a0 = lattice_values[0]
        scaled_lattice = [a/a0 for a in lattice_values]
        
        # 验证表格数据（注意：round_format函数会对数值进行格式化）
        self.assertEqual(len(table.data), len(self.formatted_data))