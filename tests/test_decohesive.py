import glob
import os
import shutil
import sys
import unittest

from monty.serialization import loadfn
from pymatgen.core import Structure
from pymatgen.core.surface import SlabGenerator
from pymatgen.io.vasp import Incar
from apex.core.property.Decohesive import Decohesive
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
__package__ = "tests"

from apex.reporter.property_report import DecohesiveReport

class TestDecohesive(unittest.TestCase):
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
                    "type": "decohesive",
                    "min_slab_size": 15,
                    "max_vacuum_size": 10,
                    "vacuum_size_step": 5,
                    "miller_index":   [0, 0, 1],
                    "cal_type":       "static"
                }
            ],
        }

        self.equi_path = "confs/std-fcc/relaxation/relax_task"
        self.source_path = "equi/vasp"
        self.target_path = "confs/std-fcc/decohesive_00"
        if not os.path.exists(self.equi_path):
            os.makedirs(self.equi_path)

        self.confs = _jdata["structures"]
        self.inter_param = _jdata["interaction"]
        self.prop_param = _jdata["properties"]

        self.decohesive = Decohesive(_jdata["properties"][0])

    def tearDown(self):
        if os.path.exists(self.equi_path):
            shutil.rmtree(self.equi_path)
        if os.path.exists(self.target_path):
            shutil.rmtree(self.target_path)

    def test_task_type(self):
        self.assertEqual("decohesive", self.decohesive.task_type())

    def test_task_param(self):
        self.assertEqual(self.prop_param[0], self.decohesive.task_param())

    def test_make_confs_0(self):
        if not os.path.exists(os.path.join(self.equi_path, "CONTCAR")):
            with self.assertRaises(RuntimeError):
                self.decohesive.make_confs(self.target_path, self.equi_path)
        shutil.copy(
            os.path.join(self.source_path, "CONTCAR"),
            os.path.join(self.equi_path, "CONTCAR"),
        )
        task_list = self.decohesive.make_confs(self.target_path, self.equi_path)
        self.assertEqual(len(task_list), 3)

        dfm_dirs = glob.glob(os.path.join(self.target_path, "task.*"))

        incar0 = Incar.from_file(os.path.join("vasp_input", "INCAR.rlx"))
        incar0["ISIF"] = 2
        incar0["NSW"] = 0

        self.assertEqual(
            os.path.realpath(os.path.join(self.equi_path, "CONTCAR")),
            os.path.realpath(os.path.join(self.target_path, "POSCAR")),
        )

        ref_st = Structure.from_file(os.path.join(self.target_path, "POSCAR"))
        dfm_dirs.sort()
        num = 0
        for ii in dfm_dirs:
            st_file = os.path.join(ii, "POSCAR")
            self.assertTrue(os.path.isfile(st_file))
            st1_file = os.path.join(ii, "POSCAR.tmp")
            self.assertTrue(os.path.isfile(st1_file))
            st1 = Structure.from_file(st1_file)
            decohesive_json_file = os.path.join(ii, "decohesive.json")
            decohesive_json = loadfn(decohesive_json_file)
            sl = self.__gen_slab_pmg(
                ref_st,
                tuple(decohesive_json["miller_index"]),
                self.prop_param[0]["min_slab_size"],
                self.prop_param[0]["vacuum_size_step"] * num,
            )
            num += 1
            # slb = sl.get_slab()
            st2 = Structure(sl.lattice, sl.species, sl.frac_coords)
            self.assertEqual(len(st1), len(st2))
    
    def __gen_slab_pmg(self, structure: Structure,
                       plane_miller, slab_size, vacuum_size) -> Structure:

        # Generate slab via Pymatgen
        slabGen = SlabGenerator(structure, miller_index=plane_miller,
                                min_slab_size=slab_size, min_vacuum_size=0,
                                center_slab=True, in_unit_planes=False,
                                lll_reduce=True, reorient_lattice=False,
                                primitive=False)
        slabs_pmg = slabGen.get_slabs(ftol=0.001)
        slab = [s for s in slabs_pmg if s.miller_index == plane_miller][0]
        # If a transform matrix is passed, reorient the slab
        order = zip(slab.frac_coords, slab.species)
        c_order = sorted(order, key=lambda x: x[0][2])
        sorted_frac_coords = []
        sorted_species = []
        for (frac_coord, species) in c_order:
            sorted_frac_coords.append(frac_coord)
            sorted_species.append(species)
        # add vacuum layer to the slab with height unit of angstrom
        a, b, c = slab.lattice.matrix
        slab_height = slab.lattice.matrix[2][2]
        if slab_height >= 0:
            self.is_flip = False
            elong_scale = 1 + (vacuum_size / slab_height)
        else:
            self.is_flip = True
            elong_scale = 1 + (-vacuum_size / slab_height)
        new_lattice = [a, b, elong_scale * c]
        new_frac_coords = []
        for ii in range(len(sorted_frac_coords)):
            coord = sorted_frac_coords[ii].copy()
            coord[2] = coord[2] / elong_scale
            new_frac_coords.append(coord)
        slab_new = Structure(lattice=np.array(new_lattice),
                         coords=new_frac_coords, species=sorted_species)

        return slab_new
    
class TestDecohesiveReport(unittest.TestCase):
    def setUp(self):
        self.res_data = {
            "0_task.000000": [0, 1, 2e9],
            "5_task.000000": [5, 6, 7e9],
            "10_task.000000": [10, 11, 12e9],
        }
        
        self.formatted_data_energy = {}
        self.formatted_data_stress = {}
        for k, v in self.res_data.items():
            if isinstance(v, list) and len(v) == 3:
                self.formatted_data_energy[k] = v[1]
                self.formatted_data_stress[k] = v[2]
    
    def test_plotly_graph(self):
        traces, layout = DecohesiveReport.plotly_graph(self.res_data, "test_material")
        
        self.assertEqual(len(traces), 2)
        self.assertEqual(traces[0].name, "test_material Decohesion Energy")
        self.assertEqual(traces[0].mode, "lines+markers")
        
        decohesive_energy = list(self.formatted_data_energy.values())
        self.assertEqual(list(traces[0].y), decohesive_energy)

        decohesive_stress = list(self.formatted_data_stress.values())
        self.assertEqual(list(traces[1].y), [s / 1e9 for s in decohesive_stress])
        
        self.assertEqual(layout.title.text, "Decohesion Energy and Stress")
        self.assertIn("Separation Distance (A)", layout.xaxis.title.text)
        self.assertIn("Decohesion Energy", layout.yaxis.title.text)
        self.assertIn("Decohesion Stress", layout.yaxis2.title.text)

    def test_dash_table(self):
        table, df = DecohesiveReport.dash_table(self.res_data)
        
        self.assertEqual(len(df), len(self.formatted_data_energy))
        self.assertEqual(len(df.columns), 3)
        
        self.assertIn("Separation Distance (A)", df.columns)
        self.assertIn("Decohesion Energy (J/m^2)", df.columns)
        self.assertIn("Decohesion Stress (GPa)", df.columns)

    
