import unittest
import tempfile
import os
import json

from apex.submit import (
    validate_submit_paths,
    auto_fill_type_map_from_poscar,
    pack_upload_dir,
)


class TestSubmitPathValidation(unittest.TestCase):
    def test_accept_paths_without_dot(self):
        params = [
            {
                "structures": ["confs/std-*"],
                "interaction": {"model": "models/Al_eam_alloy"},
            }
        ]
        validate_submit_paths(params)

    def test_reject_dot_in_structures(self):
        params = [
            {
                "structures": ["./confs/std-*"],
                "interaction": {"model": "models/Al_eam_alloy"},
            }
        ]
        with self.assertRaises(RuntimeError) as cm:
            validate_submit_paths(params)
        self.assertIn("parameter[0].structures[0]", str(cm.exception))

    def test_allow_dot_in_model_string(self):
        params = [
            {
                "structures": ["confs/std-*"],
                "interaction": {"model": "Al.eam.alloy"},
            }
        ]
        validate_submit_paths(params)

    def test_allow_dot_in_model_list(self):
        params = [
            {
                "structures": ["confs/std-*"],
                "interaction": {"model": ["Al_eam_alloy", "frozen_model.pb"]},
            }
        ]
        validate_submit_paths(params)

    def test_auto_fill_type_map_from_poscar(self):
        with tempfile.TemporaryDirectory() as tmp:
            structure_dir = os.path.join(tmp, "B2_HEA")
            os.makedirs(structure_dir, exist_ok=True)
            poscar_path = os.path.join(structure_dir, "POSCAR")
            with open(poscar_path, "w", encoding="utf-8") as fp:
                fp.write(
                    "Test\n"
                    "1.0\n"
                    "1 0 0\n"
                    "0 1 0\n"
                    "0 0 1\n"
                    "Al Co Cr Fe Mn Ni\n"
                    "1 1 1 1 1 1\n"
                    "Direct\n"
                    "0 0 0\n"
                    "0.1 0.1 0.1\n"
                    "0.2 0.2 0.2\n"
                    "0.3 0.3 0.3\n"
                    "0.4 0.4 0.4\n"
                    "0.5 0.5 0.5\n"
                )

            param_path = os.path.join(tmp, "param_props_gammasurface.json")
            payload = {
                "structures": ["B2_HEA"],
                "interaction": {
                    "type": "deepmd",
                    "model": "../frozen_model.pb",
                    "type_map": "auto",
                },
            }
            with open(param_path, "w", encoding="utf-8") as fp:
                json.dump(payload, fp, indent=4)

            changed = auto_fill_type_map_from_poscar(payload, param_path)
            self.assertTrue(changed)
            self.assertEqual(
                payload["interaction"]["type_map"],
                {"Al": 0, "Co": 1, "Cr": 2, "Fe": 3, "Mn": 4, "Ni": 5},
            )

            with open(param_path, "r", encoding="utf-8") as fp:
                persisted = json.load(fp)
            self.assertEqual(
                persisted["interaction"]["type_map"],
                {"Al": 0, "Co": 1, "Cr": 2, "Fe": 3, "Mn": 4, "Ni": 5},
            )

    def test_auto_fill_type_map_from_rss_conf_subdir(self):
        with tempfile.TemporaryDirectory() as tmp:
            structure_dir = os.path.join(tmp, "B2_HEA", "conf_001")
            os.makedirs(structure_dir, exist_ok=True)
            poscar_path = os.path.join(structure_dir, "POSCAR")
            with open(poscar_path, "w", encoding="utf-8") as fp:
                fp.write(
                    "Test\n"
                    "1.0\n"
                    "1 0 0\n"
                    "0 1 0\n"
                    "0 0 1\n"
                    "Al Co Cr Fe Mn Ni\n"
                    "1 1 1 1 1 1\n"
                    "Direct\n"
                    "0 0 0\n"
                    "0.1 0.1 0.1\n"
                    "0.2 0.2 0.2\n"
                    "0.3 0.3 0.3\n"
                    "0.4 0.4 0.4\n"
                    "0.5 0.5 0.5\n"
                )

            param_path = os.path.join(tmp, "param_props_gammasurface.json")
            payload = {
                "structures": ["B2_HEA"],
                "interaction": {
                    "type": "deepmd",
                    "model": "../frozen_model.pb",
                    "type_map": "auto",
                },
            }
            with open(param_path, "w", encoding="utf-8") as fp:
                json.dump(payload, fp, indent=4)

            changed = auto_fill_type_map_from_poscar(payload, param_path)
            self.assertTrue(changed)
            self.assertEqual(
                payload["interaction"]["type_map"],
                {"Al": 0, "Co": 1, "Cr": 2, "Fe": 3, "Mn": 4, "Ni": 5},
            )

    def test_pack_joint_rejects_all_finished_relax_and_properties(self):
        with tempfile.TemporaryDirectory() as work_dir, \
                tempfile.TemporaryDirectory() as upload_dir:
            conf_dir = os.path.join(work_dir, "confs", "std-001")
            os.makedirs(conf_dir, exist_ok=True)
            with open(os.path.join(conf_dir, "POSCAR"), "w", encoding="utf-8") as fp:
                fp.write("test\n")

            relax_result = os.path.join(conf_dir, "relaxation", "relax_task")
            os.makedirs(relax_result, exist_ok=True)
            with open(os.path.join(relax_result, "result.json"), "w", encoding="utf-8") as fp:
                fp.write("{}")

            prop_result = os.path.join(conf_dir, "eos_00")
            os.makedirs(prop_result, exist_ok=True)
            with open(os.path.join(prop_result, "result.json"), "w", encoding="utf-8") as fp:
                fp.write("{}")
            with open(os.path.join(prop_result, "result.out"), "w", encoding="utf-8") as fp:
                fp.write("done\n")

            relax_param = {
                "structures": ["confs/std-*"],
                "interaction": {
                    "type": "lammps",
                    "rerun_finished": False,
                },
            }
            prop_param = {
                "structures": ["confs/std-*"],
                "interaction": {"type": "lammps"},
                "properties": [
                    {
                        "type": "eos",
                        "skip": False,
                        "rerun_finished": False,
                    }
                ],
            }

            with self.assertRaisesRegex(
                    RuntimeError,
                    "All requested joint relaxation and property tasks are already finished",
            ):
                pack_upload_dir(
                    work_dir=work_dir,
                    upload_dir=upload_dir,
                    relax_param=relax_param,
                    prop_param=prop_param,
                    flow_type="joint",
                    exclude_upload_files=[],
                )

    def test_pack_upload_dir_reports_unmatched_structure_patterns(self):
        with tempfile.TemporaryDirectory() as work_dir, \
                tempfile.TemporaryDirectory() as upload_dir:
            relax_param = {
                "structures": ["confs/missing-*"],
                "interaction": {"type": "lammps"},
            }

            with self.assertRaisesRegex(RuntimeError, "No structures matched"):
                pack_upload_dir(
                    work_dir=work_dir,
                    upload_dir=upload_dir,
                    relax_param=relax_param,
                    prop_param=None,
                    flow_type="relax",
                    exclude_upload_files=[],
                )
