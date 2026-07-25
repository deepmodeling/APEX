import glob
import os
import shutil
import sys
import tempfile
import unittest

from monty.serialization import dumpfn, loadfn

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
__package__ = "tests"

from apex.core.property.FiniteTlatt import FiniteTlatt
from apex.core.calculator.Lammps import Lammps
from apex.core.calculator.VASP import VASP


class TestFiniteTlatt(unittest.TestCase):
    def setUp(self):
        base = {
            "structures": ["confs/hcp-Ti"],
            "interaction": {
                "type": "meam_spline",
                "model": "lammps_input/Ti.meam.spline",
                "type_map": {"Ti": 0}
            },
            "properties": [
                {
                    "type": "finite_t_latt",
                    "supercell_size":  [2, 2, 2],
                    "cal_setting":{
                        "temperature": [400, 600],
                        "equi_step": 4000,
                        "N_every": 100,
                        "N_repeat": 5,
                        "N_freq": 1000,
                        "ave_step": 4000
                    }
                }
            ],
        }

        self.equi_path = "confs/hcp-Ti/relaxation/relax_task"
        self.source_path = "equi/lammps"
        self.target_path = "confs/hcp-Ti/FiniteTlatt_00"

        if not os.path.exists(self.equi_path):
            os.makedirs(self.equi_path)

        self.confs = base["structures"]
        self.inter_param = base["interaction"]
        self.prop_param = base["properties"]

        self.finite = FiniteTlatt(self.prop_param[0])
        self.lammps = Lammps(
            self.inter_param, os.path.join(self.source_path, "hcp-Ti-CONTCAR")
        )

    def tearDown(self):
        if os.path.exists(os.path.abspath(os.path.join(self.equi_path, ".."))):
            shutil.rmtree(os.path.abspath(os.path.join(self.equi_path, "..")))
        if os.path.exists(self.equi_path):
            shutil.rmtree(self.equi_path)
        if os.path.exists(self.target_path):
            shutil.rmtree(self.target_path)

    def test_task_type(self):
        self.assertEqual("finite_t_latt", self.finite.task_type())

    def test_task_param(self):
        self.assertEqual(self.prop_param[0], self.finite.task_param())
        self.assertEqual("static", self.finite.task_param()["cal_type"])

    def test_make_potential_files(self):
        cwd = os.getcwd()
        abs_equi_path = os.path.abspath(self.equi_path)
        self.lammps.make_potential_files(abs_equi_path)
        self.assertTrue(os.path.islink(os.path.join(self.equi_path, "Ti.meam.spline")))
        self.assertTrue(os.path.isfile(os.path.join(self.equi_path, "inter.json")))
        ret = loadfn(os.path.join(self.equi_path, "inter.json"))
        self.assertEqual(self.inter_param, ret)
        os.chdir(cwd)

    def test_make_confs(self):
        if not os.path.exists(os.path.join(self.equi_path, "CONTCAR")):
            with self.assertRaises(RuntimeError):
                self.finite.make_confs(self.target_path, self.equi_path)
        shutil.copy(
            os.path.join(self.source_path, "hcp-Ti-CONTCAR"),
            os.path.join(self.equi_path, "CONTCAR"),
        )

        task_list = self.finite.make_confs(self.target_path, self.equi_path)
        self.assertEqual(len(task_list), 2)
        dfm_dirs = glob.glob(os.path.join(self.target_path, "task.*"))
        num = 0
        dfm_dirs.sort()

        for ii in dfm_dirs:
            self.assertTrue(os.path.isfile(os.path.join(ii, "POSCAR")))
            self.assertFalse(os.path.exists(os.path.join(ii, "POSCAR.tmp")))
            FiniteTlatt_json_file = os.path.join(ii, "FiniteTlatt.json")
            self.assertTrue(os.path.isfile(FiniteTlatt_json_file))
            variable_FiniteTlatt_file = os.path.join(ii, "variable_FiniteTlatt.in")
            self.assertTrue(os.path.isfile(variable_FiniteTlatt_file))
            with open(variable_FiniteTlatt_file, 'r') as file:
                lines = file.readlines()
                temp = lines[1].strip()
            self.assertEqual(temp, "variable temperature equal %.2f" % self.prop_param[0]["cal_setting"]["temperature"][num])
            num += 1

    def test_forward_common_files(self):
        fc_files = ["in.lammps", "variable_FiniteTlatt.in", "Ti.meam.spline"]
        self.assertEqual(self.lammps.forward_common_files(self.prop_param[0]["type"]), fc_files)

    def test_backward_files(self):
        backward_files = [
            "log.lammps",
            "outlog",
            "apex_task_status.json",
            ".debug.log",
            ".debug.stdout",
            ".debug.stderr",
            "dump.relax",
            "average_box.txt",
        ]
        self.assertEqual(self.lammps.backward_files(self.prop_param[0]["type"]), backward_files)


class TestFiniteTlattDFT(unittest.TestCase):
    POSCAR = """Si
1.0
2.0 0.0 0.0
0.0 2.0 0.0
0.0 0.0 2.0
Si
1
Direct
0.0 0.0 0.0
"""

    def test_vasp_cell_parser_and_defaults(self):
        defaults = FiniteTlatt(
            {"type": "finite_t_latt"}, {"type": "vasp"}
        ).task_param()["cal_setting"]
        self.assertEqual(5000, defaults["equi_step"])
        self.assertEqual(10000, defaults["ave_step"])
        self.assertEqual(1.0, defaults["timestep_fs"])
        self.assertEqual(
            [300, 500, 700, 900, 1100, 1300, 1500],
            defaults["temperature"],
        )

        with tempfile.TemporaryDirectory() as tmp:
            outcar = os.path.join(tmp, "OUTCAR")
            with open(outcar, "w") as fp:
                fp.write(
                    " direct lattice vectors                 reciprocal lattice vectors\n"
                    " 4 0 0  0 0 0\n 0 4 0  0 0 0\n 0 0 4  0 0 0\n"
                    " direct lattice vectors                 reciprocal lattice vectors\n"
                    " 6 0 0  0 0 0\n 0 6 0  0 0 0\n 0 0 6  0 0 0\n"
                )
            prop = FiniteTlatt(
                {"type": "finite_t_latt"}, {"type": "vasp"}
            )
            self.assertEqual((2.5, 2.5, 2.5), prop._average_box(tmp, [2, 2, 2]))

    def test_vasp_npt_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            poscar = os.path.join(tmp, "POSCAR")
            incar = os.path.join(tmp, "INCAR.base")
            with open(poscar, "w") as fp:
                fp.write(self.POSCAR)
            with open(incar, "w") as fp:
                fp.write("ENCUT=300\nKSPACING=0.5\nSMASS=0\n")
            dumpfn(
                {"temperature": 500, "supercell_size": [1, 1, 1]},
                os.path.join(tmp, "FiniteTlatt.json"),
            )
            task_param = {
                "type": "finite_t_latt",
                "cal_type": "static",
                "cal_setting": {
                    "equi_step": 10,
                    "ave_step": 20,
                    "timestep_fs": 1.0,
                    "langevin_gamma": 10.0,
                },
            }
            VASP(
                {
                    "type": "vasp",
                    "incar": incar,
                    "potcars": {"Si": "Si"},
                },
                poscar,
            ).make_input_file(tmp, "finite_t_latt", task_param)
            with open(os.path.join(tmp, "INCAR.equi")) as fp:
                equi = fp.read()
            self.assertIn("MDALGO = 3", equi)
            self.assertIn("ISIF = 3", equi)
            self.assertIn("POTIM = 1.0", equi)
            self.assertIn("LANGEVIN_GAMMA_L = 10.0", equi)
            self.assertIn("PMASS = 1000.0", equi)
            self.assertNotIn("SMASS", equi)
            self.assertEqual(["OUTCAR", "CONTCAR", "XDATCAR"], VASP(
                {"type": "vasp", "incar": incar, "potcars": {"Si": "Si"}},
                poscar,
            ).backward_files("finite_t_latt"))

    def test_cell_statistics_preserve_legacy_result_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = os.path.join(tmp, "task.000000")
            os.makedirs(task)
            with open(os.path.join(task, "OUTCAR"), "w") as fp:
                for length in (4.0, 6.0, 8.0, 10.0):
                    fp.write(
                        " direct lattice vectors reciprocal lattice vectors\n"
                        f" {length} 0 0 0 0 0\n"
                        f" 0 {length} 0 0 0 0\n"
                        f" 0 0 {length} 0 0 0\n"
                    )
            prop = FiniteTlatt(
                {
                    "type": "finite_t_latt",
                    "supercell_size": [2, 2, 2],
                    "cal_setting": {"temperature": [500]},
                },
                {"type": "vasp"},
            )
            result, _ = prop._compute_lower(
                os.path.join(tmp, "result.json"), [task], {}
            )
            self.assertEqual([3.5, 3.5, 3.5, 500], result["500"])
            stats = result["_metadata"]["temperatures"]["500"]
            self.assertEqual(4, stats["sample_count"])
            self.assertEqual([3.5, 3.5, 3.5], stats["lengths"]["mean"])
            self.assertEqual([90.0, 90.0, 90.0], stats["angles"]["mean"])
            self.assertEqual(4, stats["cell"]["sample_count"])
            self.assertGreater(stats["volume"]["std"], 0)

    def test_vasp_md_defaults_and_potcar_encut_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            poscar = os.path.join(tmp, "POSCAR")
            incar = os.path.join(tmp, "INCAR.base")
            with open(poscar, "w") as fp:
                fp.write(self.POSCAR)
            with open(incar, "w") as fp:
                fp.write("ENCUT=300\nKSPACING=0.5\n")
            with open(os.path.join(tmp, "POTCAR"), "w") as fp:
                fp.write("ENMAX = 250.0; ENMIN = 200.0\n")
            dumpfn({"temperature": 500}, os.path.join(tmp, "FiniteTlatt.json"))
            task_param = {
                "type": "finite_t_latt",
                "cal_type": "static",
                "cal_setting": {"equi_step": 1, "ave_step": 1},
            }
            calculator = VASP(
                {"type": "vasp", "incar": incar, "potcars": {"Si": "Si"}},
                poscar,
            )
            with self.assertRaisesRegex(ValueError, "1.3"):
                calculator.make_input_file(tmp, "finite_t_latt", task_param)
            task_param["cal_setting"]["encut"] = 325
            calculator.make_input_file(tmp, "finite_t_latt", task_param)
            text = open(os.path.join(tmp, "INCAR.production")).read()
            for expected in (
                "PREC = Accurate",
                "EDIFF = 1e-06",
                "ISMEAR = 1",
                "SIGMA = 0.2",
                "LASPH = True",
                "LREAL = Auto",
                "ALGO = Normal",
                "NBLOCK = 1",
            ):
                self.assertIn(expected, text)

    def test_abacus_backend_is_rejected_early(self):
        with self.assertRaisesRegex(NotImplementedError, "does not support.*ABACUS"):
            FiniteTlatt({"type": "finite_t_latt"}, {"type": "abacus"})

