import unittest
import tempfile
import os
import json
from unittest.mock import patch

import apex.submit as submit_module
from apex.submit import (
    LAMMPS_PHONON_IMAGE,
    _select_run_image,
    _with_lammps_retry_env,
    submit_workflow,
    validate_submit_paths,
    auto_fill_type_map_from_poscar,
    pack_upload_dir,
)


class TestSubmitPathValidation(unittest.TestCase):
    def test_lammps_phonon_forces_validated_image(self):
        selected = _select_run_image(
            "lammps",
            {"properties": [{"type": "phonon"}]},
            "registry.dp.tech/dptech/deepmd-kit:3.1.3",
        )
        self.assertEqual(selected, LAMMPS_PHONON_IMAGE)

    def test_lammps_gruneisen_forces_validated_image(self):
        selected = _select_run_image(
            "lammps",
            {"properties": [{"type": "gruneisen"}]},
            "registry.dp.tech/dptech/deepmd-kit:3.1.3",
        )
        self.assertEqual(selected, LAMMPS_PHONON_IMAGE)

    def test_non_phonon_keeps_configured_lammps_image(self):
        configured = "registry.example/custom-lammps:latest"
        selected = _select_run_image(
            "lammps",
            {"properties": [{"type": "eos"}]},
            configured,
        )
        self.assertEqual(selected, configured)

    def test_with_lammps_retry_env_handles_empty_existing_and_new_commands(self):
        class DummyConfig:
            lammps_header_retry_attempts = 4
            lammps_header_retry_delay = 0.25
            lammps_transient_retry_attempts = 2

        self.assertEqual(_with_lammps_retry_env("", DummyConfig()), "")
        existing = "APEX_LAMMPS_HEADER_RETRY=9 lmp -in in.lammps"
        self.assertEqual(_with_lammps_retry_env(existing, DummyConfig()), existing)
        wrapped = _with_lammps_retry_env("lmp -in in.lammps", DummyConfig())
        self.assertTrue(wrapped.startswith("APEX_LAMMPS_HEADER_RETRY=4 "))
        self.assertIn("APEX_LAMMPS_HEADER_RETRY_DELAY=0.25", wrapped)
        self.assertIn("APEX_LAMMPS_TRANSIENT_RETRY=2", wrapped)
        self.assertTrue(wrapped.endswith("lmp -in in.lammps"))

    def test_submit_workflow_wraps_lammps_run_command(self):
        captured = {}

        class DummyConfig:
            remote_root = None
            dispatcher_config_dict = {}
            dflow_config_dict = {}
            bohrium_config_dict = {}
            dflow_s3_config_dict = {}
            database_type = "local"
            submit_only = False
            flow_name = None
            lammps_header_retry_attempts = 4
            lammps_header_retry_delay = 0.25
            lammps_transient_retry_attempts = 2

            def __init__(self, **_kwargs):
                self.basic_config_dict = {
                    "apex_image_name": "apex-image",
                    "lammps_image_name": "",
                    "run_image_name": "run-image",
                    "lammps_run_command": "",
                    "run_command": "lmp -in in.lammps",
                    "phonolammps_run_command": "",
                    "group_size": 1,
                    "pool_size": 1,
                    "upload_python_packages": [],
                }

            @staticmethod
            def config_dflow(_config):
                return None

            @staticmethod
            def config_bohrium(_config):
                return None

            @staticmethod
            def config_s3(_config):
                return None

            def get_executor(self, _config):
                return None

        class DummyFlow:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        with tempfile.TemporaryDirectory() as work_dir, \
                patch.object(submit_module, "validate_submit_paths"), \
                patch.object(submit_module, "Config", DummyConfig), \
                patch.object(
                    submit_module,
                    "judge_flow",
                    return_value=(object(), "lammps", "props", None, {"properties": []}),
                ), \
                patch.object(submit_module, "FlowGenerator", DummyFlow), \
                patch.object(submit_module, "submit") as mocked_submit:
            submit_workflow([{}], {}, [work_dir], "props", submit_only=True)

        self.assertIn("APEX_LAMMPS_HEADER_RETRY=4", captured["run_command"])
        self.assertTrue(captured["run_command"].endswith("lmp -in in.lammps"))
        mocked_submit.assert_called_once()

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
            with open(os.path.join(relax_result, "apex_task_status.json"), "w", encoding="utf-8") as fp:
                json.dump({"state": "succeeded", "exit_code": 0}, fp)

            prop_task = os.path.join(conf_dir, "eos_00", "task.000000")
            os.makedirs(prop_task, exist_ok=True)
            with open(os.path.join(prop_task, "apex_task_status.json"), "w", encoding="utf-8") as fp:
                json.dump({"state": "succeeded", "exit_code": 0}, fp)

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

    def test_pack_joint_stages_poscar_when_relaxation_req_calc_false(self):
        with tempfile.TemporaryDirectory() as work_dir, \
                tempfile.TemporaryDirectory() as upload_dir:
            conf_dir = os.path.join(work_dir, "confs", "std-001")
            os.makedirs(conf_dir, exist_ok=True)
            with open(os.path.join(conf_dir, "POSCAR"), "w", encoding="utf-8") as fp:
                fp.write("raw-poscar\n")

            relax_param = {
                "structures": ["confs/std-*"],
                "interaction": {"type": "lammps"},
                "relaxation": {"req_calc": False},
            }
            prop_param = {
                "structures": ["confs/std-*"],
                "interaction": {"type": "lammps"},
                "properties": [{"type": "eos", "req_calc": True}],
            }

            pack_upload_dir(
                work_dir=work_dir,
                upload_dir=upload_dir,
                relax_param=relax_param,
                prop_param=prop_param,
                flow_type="joint",
                exclude_upload_files=[],
            )

            staged_contcar = os.path.join(
                upload_dir,
                "confs",
                "std-001",
                "relaxation",
                "relax_task",
                "CONTCAR",
            )
            self.assertTrue(os.path.isfile(staged_contcar))
            with open(staged_contcar, "r", encoding="utf-8") as fp:
                self.assertEqual(fp.read(), "raw-poscar\n")
            self.assertEqual(prop_param["pre_relaxed_structures"], ["confs/std-001"])

    def test_pack_joint_requires_poscar_when_relaxation_req_calc_false(self):
        with tempfile.TemporaryDirectory() as work_dir, \
                tempfile.TemporaryDirectory() as upload_dir:
            os.makedirs(os.path.join(work_dir, "confs", "std-001"), exist_ok=True)
            relax_param = {
                "structures": ["confs/std-*"],
                "interaction": {"type": "lammps"},
                "relaxation": {"req_calc": False},
            }
            prop_param = {
                "structures": ["confs/std-*"],
                "interaction": {"type": "lammps"},
                "properties": [{"type": "eos", "req_calc": True}],
            }

            with self.assertRaisesRegex(RuntimeError, "requires POSCAR"):
                pack_upload_dir(
                    work_dir=work_dir,
                    upload_dir=upload_dir,
                    relax_param=relax_param,
                    prop_param=prop_param,
                    flow_type="joint",
                    exclude_upload_files=[],
                )
