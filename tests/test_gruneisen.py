import glob
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
import json
from unittest.mock import patch

import dpdata
import yaml

from monty.serialization import dumpfn, loadfn

from apex.core.calculator.Lammps import Lammps
from apex.core.lib.mfp_eosfit import fit_birch_murnaghan
from apex.core.property.Gruneisen import Gruneisen


class TestGruneisen(unittest.TestCase):
    def setUp(self):
        tests_dir = Path(__file__).resolve().parent
        self.source_path = tests_dir / "equi" / "vasp" / "CONTCAR"
        self.abacus_source_path = tests_dir / "equi" / "abacus"
        self.tmpdir = tempfile.TemporaryDirectory(prefix="apex-gruneisen-test-")
        self.work_root = Path(self.tmpdir.name)
        self.equi_path = self.work_root / "relaxation" / "relax_task"
        self.abacus_equi_path = self.work_root / "relaxation_abacus" / "relax_task"
        self.target_path = self.work_root / "gruneisen_00"
        self.equi_path.mkdir(parents=True, exist_ok=True)
        self.abacus_equi_path.mkdir(parents=True, exist_ok=True)

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
        def fake_check_call(command, shell):
            self.assertTrue(shell)
            self.assertIn("phonopy -d", command)
            Path("SPOSCAR").write_text(Path("POSCAR").read_text())
            Path("phonopy_disp.yaml").write_text("displacements: []\n")

        with patch("apex.core.property.Gruneisen.subprocess.check_call", side_effect=fake_check_call):
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

    def test_make_confs_generates_vasp_displacement_volume_manifest_and_tasks(self):
        gruneisen = Gruneisen(
            {
                "type": "gruneisen",
                "volume_strains": [-0.01, 0.0, 0.01],
                "temperatures": [300],
                "approach": "displacement",
                "supercell_size": [2, 2, 2],
            },
            inter_param={"type": "vasp"},
        )
        shutil.copy(self.source_path, self.equi_path / "CONTCAR")

        def fake_check_call(command, shell):
            self.assertTrue(shell)
            self.assertIn("phonopy -d", command)
            Path("phonopy_disp.yaml").write_text("displacements: []\n")
            Path("POSCAR-001").write_text(Path("POSCAR").read_text())
            Path("POSCAR-002").write_text(Path("POSCAR").read_text())

        with patch("apex.core.property.Gruneisen.subprocess.check_call", side_effect=fake_check_call):
            task_list = gruneisen.make_confs(str(self.target_path), str(self.equi_path))

        task_dirs = sorted(self.target_path.glob("task.*"))
        self.assertEqual(len(task_list), 9)
        self.assertEqual(len(task_dirs), 9)
        manifest = loadfn(self.target_path / "vasp_gruneisen_tasks.json")
        self.assertEqual(len(manifest["volume_points"]), 3)
        self.assertEqual(manifest["volume_points"][0]["reference_task"], "task.000000")
        self.assertEqual(manifest["volume_points"][0]["displacement_tasks"], ["task.000001", "task.000002"])

        helper_dir = self.target_path / "volume.000000"
        self.assertTrue((helper_dir / "POSCAR").is_file())
        self.assertTrue((helper_dir / "POSCAR-unitcell").is_file())
        self.assertTrue((helper_dir / "phonopy_disp.yaml").is_file())
        self.assertTrue((helper_dir / "band.conf").is_file())

        reference_task = self.target_path / "task.000000"
        displacement_task = self.target_path / "task.000001"
        self.assertTrue((reference_task / "POSCAR").exists())
        self.assertTrue((reference_task / "POSCAR-unitcell").exists())
        self.assertEqual(loadfn(reference_task / "gruneisen_task.json")["role"], "reference")
        self.assertEqual(loadfn(displacement_task / "gruneisen_task.json")["role"], "displacement")

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

    def test_sign_only_compute_lower_from_vasp_displacement_manifest(self):
        gruneisen = Gruneisen(
            {
                "type": "gruneisen",
                "volume_strains": [-0.01, 0.0, 0.01],
                "temperatures": [100, 300],
                "alpha_mode": "sign_only",
                "approach": "displacement",
            },
            inter_param={"type": "vasp"},
        )
        work_dir = self.work_root / "gruneisen_vasp_displacement_compute"
        task_dirs = self._write_synthetic_vasp_displacement_gruneisen_layout(work_dir)
        calls = []

        def fake_check_call(command, shell):
            self.assertTrue(shell)
            calls.append((Path.cwd().name, command))
            if command.startswith("phonopy -f "):
                Path("FORCE_SETS").write_text("fake force sets\n")
            elif command.startswith("phonopy --dim=") and "--writefc" in command:
                Path("FORCE_CONSTANTS").write_text("fake force constants\n")
            elif command.startswith("phonopy --dim="):
                strain = loadfn("volume.json")["strain"]
                if strain < 0:
                    frequencies = [4.2, 8.4]
                elif strain > 0:
                    frequencies = [4.0, 8.0]
                else:
                    frequencies = [4.1, 8.2]
                Path("band.yaml").write_text("phonon: []\n")
                Path("mesh.yaml").write_text(
                    yaml.safe_dump(
                        {
                            "phonon": [
                                {
                                    "q-position": [0.0, 0.0, 0.0],
                                    "weight": 1,
                                    "band": [{"frequency": freq} for freq in frequencies],
                                }
                            ]
                        },
                        sort_keys=False,
                    )
                )
            else:
                raise AssertionError(f"unexpected command: {command}")

        def fake_run(command, stdout, stderr, text):
            self.assertEqual(command, ["phonopy-bandplot", "--gnuplot", "band.yaml"])
            stdout.write("0 0\n")
            return subprocess.CompletedProcess(command, 1, stderr="bandplot warning")

        with patch("apex.core.property.Gruneisen.subprocess.check_call", side_effect=fake_check_call), \
                patch("apex.core.property.Gruneisen.subprocess.run", side_effect=fake_run):
            result, ptr = gruneisen._compute_lower(str(work_dir / "result.json"), task_dirs, [])

        self.assertEqual(result["thermal_expansion"]["sign"], ["positive", "positive"])
        self.assertEqual(result["gruneisen"]["qpoint_count"], 1)
        self.assertEqual(len([cmd for _, cmd in calls if cmd.startswith("phonopy -f ")]), 3)
        self.assertTrue((work_dir / "volume.000000" / "mesh.yaml").is_file())
        self.assertTrue((work_dir / "volume.000001" / "band.dat").is_file())
        self.assertIn("Temperature(K)  SumGammaCv  Sign", ptr)

    def test_make_confs_generates_abacus_volume_manifest_and_tasks(self):
        gruneisen = Gruneisen(
            {
                "type": "gruneisen",
                "volume_strains": [-0.01, 0.0, 0.01],
                "temperatures": [300],
                "supercell_size": [2, 2, 2],
            },
            inter_param={
                "type": "abacus",
                "incar": "abacus_input/INPUT",
                "potcar_prefix": "abacus_input",
                "potcars": {"Al": "Al_ONCV_PBE-1.0.upf"},
                "orb_files": {"Al": "Al_gga_9au_100Ry_4s4p1d.orb"},
            },
        )
        self._prepare_abacus_relax_fixture()

        def fake_check_call(command, shell):
            self.assertTrue(shell)
            self.assertEqual(command, "phonopy setting.conf --abacus -d")
            source = Path("STRU").read_text()
            Path("phonopy_disp.yaml").write_text("displacements: []\n")
            Path("STRU-001").write_text(source)
            Path("STRU-002").write_text(source)

        with patch("apex.core.property.Gruneisen.subprocess.check_call", side_effect=fake_check_call):
            task_list = gruneisen.make_confs(str(self.target_path), str(self.abacus_equi_path))

        task_dirs = sorted(self.target_path.glob("task.*"))
        self.assertEqual(len(task_list), 9)
        self.assertEqual(len(task_dirs), 9)
        manifest = loadfn(self.target_path / "abacus_gruneisen_tasks.json")
        self.assertEqual(len(manifest["volume_points"]), 3)
        self.assertEqual(manifest["volume_points"][0]["reference_task"], "task.000000")
        self.assertEqual(manifest["volume_points"][0]["displacement_tasks"], ["task.000001", "task.000002"])
        self.assertEqual(manifest["volume_points"][1]["reference_task"], "task.000003")
        self.assertTrue((self.target_path / "band_path.json").is_file())

        helper_dir = self.target_path / "volume.000000"
        self.assertTrue((helper_dir / "STRU").is_file())
        self.assertTrue((helper_dir / "POSCAR").is_file())
        self.assertTrue((helper_dir / "volume.json").is_file())
        self.assertTrue((helper_dir / "phonopy_disp.yaml").is_file())
        self.assertTrue((helper_dir / "band.conf").is_file())

        reference_task = self.target_path / "task.000000"
        displacement_task = self.target_path / "task.000001"
        self.assertTrue((reference_task / "STRU").exists())
        self.assertTrue((reference_task / "POSCAR").is_file())
        self.assertTrue((reference_task / "gruneisen_task.json").is_file())
        self.assertEqual(loadfn(reference_task / "gruneisen_task.json")["role"], "reference")
        self.assertEqual(loadfn(displacement_task / "gruneisen_task.json")["role"], "displacement")

    def test_sign_only_compute_lower_from_abacus_manifest(self):
        gruneisen = Gruneisen(
            {
                "type": "gruneisen",
                "volume_strains": [-0.01, 0.0, 0.01],
                "temperatures": [100, 300],
                "alpha_mode": "sign_only",
            },
            inter_param={"type": "abacus"},
        )
        work_dir = self.work_root / "gruneisen_abacus_compute"
        task_dirs = self._write_synthetic_abacus_gruneisen_layout(work_dir)
        calls = []

        def fake_check_call(command, shell):
            self.assertTrue(shell)
            calls.append((Path.cwd().name, command))
            if command.startswith("phonopy -f "):
                Path("FORCE_SETS").write_text("fake force sets\n")
            elif command == "phonopy phonopy_disp.yaml --writefc":
                Path("FORCE_CONSTANTS").write_text("fake force constants\n")
            elif command == "phonopy band.conf":
                strain = loadfn("volume.json")["strain"]
                if strain < 0:
                    frequencies = [4.2, 8.4]
                elif strain > 0:
                    frequencies = [4.0, 8.0]
                else:
                    frequencies = [4.1, 8.2]
                Path("band.yaml").write_text("phonon: []\n")
                Path("mesh.yaml").write_text(
                    yaml.safe_dump(
                        {
                            "phonon": [
                                {
                                    "q-position": [0.0, 0.0, 0.0],
                                    "weight": 1,
                                    "band": [{"frequency": freq} for freq in frequencies],
                                }
                            ]
                        },
                        sort_keys=False,
                    )
                )
            else:
                raise AssertionError(f"unexpected command: {command}")

        def fake_run(command, stdout, stderr, text):
            self.assertEqual(command, ["phonopy-bandplot", "--gnuplot", "band.yaml"])
            stdout.write("0 0\n")
            return subprocess.CompletedProcess(command, 1, stderr="bandplot warning")

        with patch("apex.core.property.Gruneisen.subprocess.check_call", side_effect=fake_check_call), \
                patch("apex.core.property.Gruneisen.subprocess.run", side_effect=fake_run):
            result, ptr = gruneisen._compute_lower(str(work_dir / "result.json"), task_dirs, [])

        self.assertEqual(result["thermal_expansion"]["alpha_mode"], "sign_only")
        self.assertEqual(result["thermal_expansion"]["sign"], ["positive", "positive"])
        self.assertEqual(result["gruneisen"]["qpoint_count"], 1)
        self.assertEqual(result["gruneisen"]["mode_count"], 2)
        self.assertEqual(len([cmd for _, cmd in calls if cmd.startswith("phonopy -f ")]), 3)
        self.assertEqual(
            len([cmd for _, cmd in calls if cmd == "phonopy phonopy_disp.yaml --writefc"]),
            3,
        )
        self.assertEqual(len([cmd for _, cmd in calls if cmd == "phonopy band.conf"]), 3)
        self.assertTrue((work_dir / "volume.000000" / "mesh.yaml").is_file())
        self.assertTrue((work_dir / "volume.000001" / "band.dat").is_file())
        self.assertTrue("Temperature(K)  SumGammaCv  Sign" in ptr)

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
            [
                "log.lammps",
                "outlog",
                "apex_task_status.json",
                ".debug.log",
                ".debug.stdout",
                ".debug.stderr",
                "dump.relax",
                "FORCE_CONSTANTS",
                "mesh.yaml",
                "band.yaml",
                "phonopy.yaml",
            ],
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

    def test_full_compute_lower_fits_bulk_modulus_and_alpha(self):
        gruneisen = Gruneisen(
            {
                "type": "gruneisen",
                "volume_strains": [-0.01, 0.0, 0.01],
                "temperatures": [100, 300],
                "alpha_mode": "full",
            }
        )
        work_dir = self.work_root / "gruneisen_full"
        task_dirs, result_paths = self._write_synthetic_gruneisen_tasks(
            work_dir,
            [
                (-0.01, 99.0, [[4.2, 8.4]], -4.0 + 0.01 * (24.75 - 25.0) ** 2),
                (0.0, 100.0, [[4.1, 8.2]], -4.0),
                (0.01, 101.0, [[4.0, 8.0]], -4.0 + 0.01 * (25.25 - 25.0) ** 2),
            ],
            weight=2,
        )

        output_file = work_dir / "result.json"
        result, ptr = gruneisen._compute_lower(str(output_file), task_dirs, result_paths)

        self.assertEqual(result["thermal_expansion"]["alpha_mode"], "full")
        self.assertGreater(result["bulk_modulus"]["K_T_GPa"], 0.0)
        self.assertEqual(result["bulk_modulus"]["fit_variant"], "fixed_bp")
        self.assertEqual(result["thermal_expansion"]["qpoint_weight_sum"], 2)
        self.assertEqual(len(result["thermal_expansion"]["alpha"]), 2)
        raw = result["thermal_expansion"]["sum_gamma_cv"][0]
        per_cell = result["thermal_expansion"]["sum_gamma_cv_per_cell"][0]
        self.assertAlmostEqual(per_cell, raw / 2.0)
        expected_alpha = per_cell / (
            result["thermal_expansion"]["reference_volume_for_alpha"]
            * result["bulk_modulus"]["K_T_eV_per_A3"]
        )
        self.assertAlmostEqual(result["thermal_expansion"]["alpha"][0], expected_alpha)
        self.assertIn("bulk modulus", ptr)
        self.assertIn("Alpha(K^-1)", ptr)

    def test_full_compute_lower_rejects_non_positive_bulk_modulus(self):
        gruneisen = Gruneisen(
            {
                "type": "gruneisen",
                "volume_strains": [-0.01, 0.0, 0.01],
                "temperatures": [300],
                "alpha_mode": "full",
            }
        )
        work_dir = self.work_root / "gruneisen_bad_bulk"
        task_dirs, result_paths = self._write_synthetic_gruneisen_tasks(
            work_dir,
            [
                (-0.01, 99.0, [[4.2, 8.4]], -4.0 - 0.01 * (24.75 - 25.0) ** 2),
                (0.0, 100.0, [[4.1, 8.2]], -4.0),
                (0.01, 101.0, [[4.0, 8.0]], -4.0 - 0.01 * (25.25 - 25.0) ** 2),
            ],
        )

        with self.assertRaisesRegex(ValueError, "positive fitted bulk modulus"):
            gruneisen._compute_lower(str(work_dir / "result.json"), task_dirs, result_paths)

    def test_full_compute_lower_requires_task_results(self):
        gruneisen = Gruneisen(
            {
                "type": "gruneisen",
                "volume_strains": [-0.01, 0.0, 0.01],
                "temperatures": [300],
                "alpha_mode": "full",
            }
        )
        work_dir = self.work_root / "gruneisen_missing_result"
        task_dirs, _ = self._write_synthetic_gruneisen_tasks(
            work_dir,
            [
                (-0.01, 99.0, [[4.2, 8.4]], -4.0),
                (0.0, 100.0, [[4.1, 8.2]], -4.0),
                (0.01, 101.0, [[4.0, 8.0]], -4.0),
            ],
        )

        with self.assertRaisesRegex(ValueError, "result_task.json"):
            gruneisen._compute_lower(str(work_dir / "result.json"), task_dirs, [])

    def test_primitive_compute_lower_requires_poscar_unitcell(self):
        gruneisen = Gruneisen(
            {
                "type": "gruneisen",
                "volume_strains": [-0.01, 0.0, 0.01],
                "temperatures": [300],
                "primitive": True,
            }
        )
        work_dir = self.work_root / "gruneisen_missing_unitcell"
        task_dirs, _ = self._write_synthetic_gruneisen_tasks(
            work_dir,
            [
                (-0.01, 99.0, [[4.2, 8.4]], -4.0),
                (0.0, 100.0, [[4.1, 8.2]], -4.0),
                (0.01, 101.0, [[4.0, 8.0]], -4.0),
            ],
        )

        with self.assertRaisesRegex(FileNotFoundError, "POSCAR-unitcell is required"):
            gruneisen._compute_lower(str(work_dir / "result.json"), task_dirs, [])

    def test_fit_birch_murnaghan_helper_is_side_effect_free(self):
        fit = fit_birch_murnaghan(
            [24.75, 25.0, 25.25],
            [
                -4.0 + 0.01 * (24.75 - 25.0) ** 2,
                -4.0,
                -4.0 + 0.01 * (25.25 - 25.0) ** 2,
            ],
            fixed_bp=4.0,
        )
        self.assertGreater(fit["K_T_GPa"], 0.0)
        self.assertEqual(fit["fit_variant"], "fixed_bp")
        self.assertEqual(fit["model"], "birch_murnaghan")

    def test_mode_heat_capacity_is_stable_for_large_x(self):
        cv = Gruneisen._mode_heat_capacity(frequency_thz=500.0, temperature=1.0)
        self.assertEqual(cv, 0.0)

    def _write_synthetic_gruneisen_tasks(self, work_dir, synthetic, weight=1):
        work_dir.mkdir(parents=True, exist_ok=True)
        task_dirs = []
        result_paths = []
        for task_id, (strain, volume, frequencies, energy_per_atom) in enumerate(synthetic):
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
                        "weight": weight,
                        "band": [{"frequency": freq} for freq in frequencies[0]],
                    }
                ]
            }
            (task_dir / "mesh.yaml").write_text(yaml.safe_dump(mesh, sort_keys=False))
            result_path = task_dir / "result_task.json"
            result_path.write_text(
                json.dumps(
                    {
                        "energies": [energy_per_atom * 4.0],
                        "atom_numbs": [4],
                    }
                )
            )
            task_dirs.append(str(task_dir))
            result_paths.append(str(result_path))
        return task_dirs, result_paths

    def _prepare_abacus_relax_fixture(self):
        out_dir = self.abacus_equi_path / "OUT.ABACUS"
        out_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(self.abacus_source_path / "INPUT", self.abacus_equi_path / "INPUT")
        shutil.copy(self.abacus_source_path / "STRU", self.abacus_equi_path / "STRU")
        shutil.copy(
            self.abacus_source_path / "running_cell-relax.log",
            out_dir / "running_cell-relax.log",
        )
        shutil.copy(self.abacus_source_path / "STRU_ION_D", out_dir / "STRU_ION_D")

    def _write_synthetic_abacus_gruneisen_layout(self, work_dir: Path):
        work_dir.mkdir(parents=True, exist_ok=True)
        manifest = {"volume_points": []}
        task_dirs = []
        source_stru = self.abacus_source_path / "STRU_ION_D"
        for volume_index, strain in enumerate([-0.01, 0.0, 0.01]):
            helper_dir = work_dir / f"volume.{volume_index:06d}"
            helper_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy(source_stru, helper_dir / "STRU")
            dpdata.System(str(helper_dir / "STRU"), fmt="stru").to("vasp/poscar", str(helper_dir / "POSCAR"))
            (helper_dir / "phonopy_disp.yaml").write_text("displacements: []\n")
            (helper_dir / "band.conf").write_text("MESH = 1 1 1\nFORCE_CONSTANTS = READ\n")
            (helper_dir / "volume.json").write_text(
                json.dumps(
                    {
                        "strain": strain,
                        "scale": 1.0,
                        "volume": 100.0 + strain * 100.0,
                        "volume_per_atom": 25.0 + strain * 25.0,
                        "reference_volume": 100.0,
                        "reference_volume_per_atom": 25.0,
                    }
                )
            )
            reference_task = work_dir / f"task.{volume_index * 3:06d}"
            reference_task.mkdir(parents=True, exist_ok=True)
            displacement_tasks = []
            for offset in [1, 2]:
                task_dir = work_dir / f"task.{volume_index * 3 + offset:06d}"
                out_dir = task_dir / "OUT.ABACUS"
                out_dir.mkdir(parents=True, exist_ok=True)
                (out_dir / "running_scf.log").write_text("SEE INFORMATION IN\n")
                task_dirs.append(str(task_dir))
                displacement_tasks.append(task_dir.name)
            task_dirs.append(str(reference_task))
            manifest["volume_points"].append(
                {
                    "volume_index": volume_index,
                    "helper_dir": helper_dir.name,
                    "reference_task": reference_task.name,
                    "displacement_tasks": displacement_tasks,
                    "strain": strain,
                }
            )
        dumpfn(manifest, work_dir / "abacus_gruneisen_tasks.json", indent=4)
        task_dirs.sort()
        return task_dirs

    def _write_synthetic_vasp_displacement_gruneisen_layout(self, work_dir: Path):
        work_dir.mkdir(parents=True, exist_ok=True)
        manifest = {"volume_points": []}
        task_dirs = []
        poscar_text = self.source_path.read_text()
        for volume_index, strain in enumerate([-0.01, 0.0, 0.01]):
            helper_dir = work_dir / f"volume.{volume_index:06d}"
            helper_dir.mkdir(parents=True, exist_ok=True)
            (helper_dir / "POSCAR-unitcell").write_text(poscar_text)
            (helper_dir / "phonopy_disp.yaml").write_text("displacements: []\n")
            (helper_dir / "band.conf").write_text("MESH = 1 1 1\nFORCE_CONSTANTS = READ\n")
            (helper_dir / "volume.json").write_text(
                json.dumps(
                    {
                        "strain": strain,
                        "scale": 1.0,
                        "volume": 100.0 + strain * 100.0,
                        "volume_per_atom": 25.0 + strain * 25.0,
                        "reference_volume": 100.0,
                        "reference_volume_per_atom": 25.0,
                    }
                )
            )
            reference_task = work_dir / f"task.{volume_index * 3:06d}"
            reference_task.mkdir(parents=True, exist_ok=True)
            displacement_tasks = []
            for offset in [1, 2]:
                task_dir = work_dir / f"task.{volume_index * 3 + offset:06d}"
                task_dir.mkdir(parents=True, exist_ok=True)
                (task_dir / "vasprun.xml").write_text("<modeling />\n")
                task_dirs.append(str(task_dir))
                displacement_tasks.append(task_dir.name)
            task_dirs.append(str(reference_task))
            manifest["volume_points"].append(
                {
                    "volume_index": volume_index,
                    "helper_dir": helper_dir.name,
                    "reference_task": reference_task.name,
                    "displacement_tasks": displacement_tasks,
                    "strain": strain,
                }
            )
        dumpfn(manifest, work_dir / "vasp_gruneisen_tasks.json", indent=4)
        task_dirs.sort()
        return task_dirs
