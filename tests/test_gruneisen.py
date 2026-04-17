import glob
import os
import shutil
import tempfile
import unittest
from pathlib import Path
import json
from unittest.mock import patch

import yaml

from monty.serialization import loadfn

from apex.core.calculator.Lammps import Lammps
from apex.core.property.Gruneisen import Gruneisen


class TestGruneisen(unittest.TestCase):
    def setUp(self):
        tests_dir = Path(__file__).resolve().parent
        self.source_path = tests_dir / "equi" / "vasp" / "CONTCAR"
        self.tmpdir = tempfile.TemporaryDirectory(prefix="apex-gruneisen-test-")
        self.work_root = Path(self.tmpdir.name)
        self.equi_path = self.work_root / "relaxation" / "relax_task"
        self.target_path = self.work_root / "gruneisen_00"
        self.equi_path.mkdir(parents=True, exist_ok=True)

        self.prop_param = {
            "type": "gruneisen",
            "volume_strains": [-0.02, 0.0, 0.02],
            "temperatures": [10, 50, 100],
        }
        self.gruneisen = Gruneisen(dict(self.prop_param))

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_task_type(self):
        self.assertEqual("gruneisen", self.gruneisen.task_type())

    def test_task_param_defaults(self):
        task_param = self.gruneisen.task_param()
        self.assertEqual(task_param["alpha_mode"], "sign_only")
        self.assertEqual(task_param["bulk_modulus_source"], "eos_fit")
        self.assertEqual(task_param["eos_model"], "birch_murnaghan")
        self.assertEqual(task_param["BAND_POINTS"], 51)
        self.assertEqual(task_param["supercell_size"], [2, 2, 2])
        self.assertEqual(task_param["approach"], "linear")

    def test_validation_rejects_invalid_schema(self):
        with self.assertRaises(ValueError):
            Gruneisen(
                {
                    "type": "gruneisen",
                    "volume_strains": [-0.02, 0.02],
                    "temperatures": [10, 50],
                }
            )
        with self.assertRaises(ValueError):
            Gruneisen(
                {
                    "type": "gruneisen",
                    "volume_strains": [-0.02, 0.01, 0.02],
                    "temperatures": [10, 50],
                }
            )
        with self.assertRaises(ValueError):
            Gruneisen(
                {
                    "type": "gruneisen",
                    "volume_strains": [-0.02, 0.0, 0.02],
                    "temperatures": [0, 50],
                }
            )

    def test_make_confs_generates_volume_tasks(self):
        with self.assertRaises(RuntimeError):
            self.gruneisen.make_confs(str(self.target_path), str(self.equi_path))

        shutil.copy(self.source_path, self.equi_path / "CONTCAR")
        task_list = self.gruneisen.make_confs(str(self.target_path), str(self.equi_path))
        task_dirs = glob.glob(str(self.target_path / "task.*"))
        self.assertEqual(len(task_dirs), 3)
        self.assertEqual(len(task_list), 3)
        self.assertTrue((self.target_path / "volume_points.json").is_file())

        for task_dir, strain in zip(sorted(task_dirs), self.prop_param["volume_strains"]):
            self.assertTrue((Path(task_dir) / "POSCAR").is_file())
            self.assertTrue((Path(task_dir) / "POSCAR.orig").exists())
            self.assertTrue((Path(task_dir) / "POSCAR-unitcell").is_file())
            self.assertTrue((Path(task_dir) / "SPOSCAR").is_file())
            self.assertTrue((Path(task_dir) / "volume.json").is_file())
            volume_data = loadfn(Path(task_dir) / "volume.json")
            self.assertAlmostEqual(volume_data["strain"], strain)
            self.assertGreater(volume_data["volume"], 0.0)
            self.assertGreater(volume_data["volume_per_atom"], 0.0)
            self.assertTrue((Path(task_dir) / "band.conf").is_file())

        self.assertTrue((self.target_path / "band_path.json").is_file())

    def test_vasp_displacement_gruneisen_is_explicitly_unsupported(self):
        gruneisen = Gruneisen(
            {
                "type": "gruneisen",
                "volume_strains": [-0.01, 0.0, 0.01],
                "temperatures": [300],
                "approach": "displacement",
            },
            inter_param={"type": "vasp"},
        )
        shutil.copy(self.source_path, self.equi_path / "CONTCAR")

        with self.assertRaisesRegex(NotImplementedError, "approach='linear'"):
            gruneisen.make_confs(str(self.target_path), str(self.equi_path))

    def test_vasp_ensure_mesh_yaml_builds_force_constants_from_vasprun(self):
        gruneisen = Gruneisen(
            {
                "type": "gruneisen",
                "volume_strains": [-0.01, 0.0, 0.01],
                "temperatures": [300],
                "supercell_size": [1, 1, 1],
            },
            inter_param={"type": "vasp"},
        )
        task_dir = self.work_root / "vasp_mesh" / "task.000000"
        task_dir.mkdir(parents=True)
        (task_dir / "vasprun.xml").write_text("<modeling />\n")
        (task_dir / "band.conf").write_text("MESH = 1 1 1\n")
        (task_dir / "POSCAR-unitcell").write_text(self.source_path.read_text())
        calls = []

        def fake_check_call(command, shell):
            self.assertTrue(shell)
            calls.append(command)
            if command == "phonopy --fc vasprun.xml":
                (task_dir / "FORCE_CONSTANTS").write_text("fake force constants\n")
            elif "POSCAR-unitcell" in command:
                (task_dir / "mesh.yaml").write_text(
                    yaml.safe_dump(
                        {
                            "phonon": [
                                {
                                    "q-position": [0.0, 0.0, 0.0],
                                    "weight": 1,
                                    "band": [{"frequency": 1.0}],
                                }
                            ]
                        },
                        sort_keys=False,
                    )
                )
            else:
                raise AssertionError(f"unexpected command: {command}")

        cwd = os.getcwd()
        try:
            with patch("apex.core.property.Gruneisen.subprocess.check_call", side_effect=fake_check_call):
                gruneisen._ensure_mesh_yaml(str(task_dir))
        finally:
            os.chdir(cwd)

        self.assertTrue((task_dir / "FORCE_CONSTANTS").is_file())
        self.assertTrue((task_dir / "mesh.yaml").is_file())
        self.assertEqual(calls[0], "phonopy --fc vasprun.xml")
        self.assertIn("-c POSCAR-unitcell", calls[1])
        self.assertIn("--nomeshsym", calls[1])

    def test_post_process_prepares_phonon_run_inputs_for_lammps(self):
        deepmd_gruneisen = Gruneisen(
            {
                "type": "gruneisen",
                "volume_strains": [-0.02, 0.0, 0.02],
                "temperatures": [10, 50, 100],
                "supercell_size": [2, 2, 2],
                "lammps_run_command": "/root/.dp1s/bin/lmp -in in.lammps",
                "phonolammps_run_command": "phonolammps {input_file} -c {poscar} --dim {dim_x} {dim_y} {dim_z}",
            },
            inter_param={
                "type": "deepmd",
                "model": "frozen_model.pth",
                "type_map": {"Cu": 0, "O": 1},
                "deepmd_version": "3.1.1",
            },
        )
        task_dir = self.work_root / "gruneisen_post" / "task.000000"
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "in.lammps").write_text(
            "clear\npair_style deepmd frozen_model.pth\npair_coeff * * Cu O\nrun 0\n"
        )
        (task_dir / "conf.lmp").write_text("LAMMPS data\n")
        (task_dir / "POSCAR").write_text(self.source_path.read_text())

        deepmd_gruneisen.post_process([str(task_dir)])

        rewritten = (task_dir / "in.lammps").read_text()
        self.assertIn("plugin load libdeepmd_lmp.so", rewritten)
        self.assertIn("pair_style deepmd frozen_model.pth", rewritten)
        self.assertNotIn("run 0", rewritten)
        self.assertTrue((task_dir / "in.relax.lammps").is_file())
        self.assertTrue((task_dir / "type_map.json").is_file())
        self.assertTrue((task_dir / "convert_relax_dump_to_poscar.py").is_file())
        self.assertEqual((task_dir / "run_command").read_text(), "bash run_gruneisen_task.sh")
        run_script = (task_dir / "run_gruneisen_task.sh").read_text()
        self.assertIn("/root/.dp1s/bin/lmp -in in.relax.lammps", run_script)
        self.assertIn("python3 convert_relax_dump_to_poscar.py dump.relax POSCAR.relaxed type_map.json", run_script)
        self.assertIn("phonolammps in.lammps -c POSCAR --dim 2 2 2", run_script)
        self.assertIn("cp POSCAR.relaxed POSCAR", run_script)

    def test_lammps_backward_files_for_gruneisen(self):
        lammps = Lammps(
            {
                "type": "deepmd",
                "model": "lammps_input/frozen_model.pb",
                "deepmd_version": "1.1.0",
                "type_map": {"Al": 0},
            },
            str(self.source_path),
        )
        self.assertEqual(
            lammps.backward_files("gruneisen"),
            ["outlog", "FORCE_CONSTANTS", "mesh.yaml", "band.yaml", "phonopy.yaml"],
        )

    def test_sign_only_compute_lower_from_synthetic_mesh(self):
        gruneisen = Gruneisen(
            {
                "type": "gruneisen",
                "volume_strains": [-0.01, 0.0, 0.01],
                "temperatures": [100, 300],
                "alpha_mode": "sign_only",
            }
        )
        work_dir = self.work_root / "gruneisen_compute"
        work_dir.mkdir(parents=True, exist_ok=True)
        task_dirs = []
        synthetic = [
            (-0.01, 99.0, [[4.2, 8.4]]),
            (0.0, 100.0, [[4.1, 8.2]]),
            (0.01, 101.0, [[4.0, 8.0]]),
        ]
        for task_id, (strain, volume, frequencies) in enumerate(synthetic):
            task_dir = work_dir / f"task.{task_id:06d}"
            task_dir.mkdir(parents=True, exist_ok=True)
            (task_dir / "volume.json").write_text(
                json.dumps(
                    {
                        "strain": strain,
                        "scale": 1.0,
                        "volume": volume,
                        "volume_per_atom": volume / 4.0,
                        "reference_volume": 100.0,
                        "reference_volume_per_atom": 25.0,
                    }
                )
            )
            mesh = {
                "phonon": [
                    {
                        "q-position": [0.0, 0.0, 0.0],
                        "weight": 1,
                        "band": [{"frequency": freq} for freq in frequencies[0]],
                    }
                ]
            }
            (task_dir / "mesh.yaml").write_text(yaml.safe_dump(mesh, sort_keys=False))
            task_dirs.append(str(task_dir))

        output_file = work_dir / "result.json"
        result, ptr = gruneisen._compute_lower(str(output_file), task_dirs, [])
        self.assertEqual(result["thermal_expansion"]["alpha_mode"], "sign_only")
        self.assertEqual(result["thermal_expansion"]["sign"], ["positive", "positive"])
        self.assertEqual(result["gruneisen"]["qpoint_count"], 1)
        self.assertEqual(result["gruneisen"]["mode_count"], 2)
        self.assertEqual(result["bulk_modulus"], None)
        self.assertEqual(len(result["mode_gruneisen"]), 1)
        self.assertEqual(len(result["mode_gruneisen"][0]["gamma"]), 2)
        self.assertEqual(result["mode_gruneisen"][0]["weight"], 1)
        self.assertEqual(result["mode_gruneisen"][0]["omega_ref"], [4.1, 8.2])
        self.assertIn("100.0", result["mode_heat_capacity"][0]["cv"])
        self.assertIn("300.0", result["mode_heat_capacity"][0]["cv"])
        self.assertEqual(len(result["mode_heat_capacity"][0]["cv"]["100.0"]), 2)
        self.assertEqual(len(result["mode_contributions"][0]["gamma_cv"]["300.0"]), 2)
        self.assertEqual(len(result["contribution_summary"]), 2)
        self.assertGreater(result["contribution_summary"][0]["positive_sum"], 0.0)
        self.assertEqual(result["contribution_summary"][0]["negative_sum"], 0.0)
        self.assertAlmostEqual(
            result["contribution_summary"][0]["net_sum"],
            result["thermal_expansion"]["sum_gamma_cv"][0],
        )
        self.assertTrue("Temperature(K)  SumGammaCv  Sign" in ptr)
        self.assertTrue("# contribution summary" in ptr)

    def test_mode_heat_capacity_is_stable_for_large_x(self):
        cv = Gruneisen._mode_heat_capacity(frequency_thz=500.0, temperature=1.0)
        self.assertEqual(cv, 0.0)
