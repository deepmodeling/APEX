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
    DPA4_TEST_REF = "registry.example/apex/dpa4-runtime:tested"
    DPA4_TEST_DIGEST = "sha256:" + "a" * 64

    @classmethod
    def _published_dpa4_profile(cls):
        return {
            "published": True,
            "image": {
                "ref": cls.DPA4_TEST_REF,
                "digest": cls.DPA4_TEST_DIGEST,
            },
        }

    @staticmethod
    def _dpa4_interaction():
        return {
            "type": "deepmd",
            "deepmd_runtime": submit_module.DPA4_RUNTIME_KIND,
            "model_in_image": True,
            "model": submit_module.DPA4_RUNTIME_MODEL_PATH,
            "runtime_model_sha256": submit_module.DPA4_RUNTIME_MODEL_SHA256,
            "source_checkpoint": submit_module.DPA4_SOURCE_CHECKPOINT_PATH,
            "source_checkpoint_sha256": (
                submit_module.DPA4_SOURCE_CHECKPOINT_SHA256
            ),
            "type_map": "auto",
        }

    def test_lammps_phonon_image_matches_validated_phonolammps_runtime(self):
        self.assertEqual(
            LAMMPS_PHONON_IMAGE,
            "registry.dp.tech/dptech/dp/native/prod-16664/"
            "dpa4-phonolammps:0.0.2",
        )

    def test_lammps_phonon_forces_validated_image(self):
        selected = _select_run_image(
            "lammps",
            {
                "interaction": {"type": "deepmd"},
                "properties": [{"type": "phonon"}],
            },
            "registry.dp.tech/dptech/deepmd-kit:3.1.3",
        )
        self.assertEqual(selected, LAMMPS_PHONON_IMAGE)

    def test_lammps_gruneisen_forces_validated_image(self):
        selected = _select_run_image(
            "lammps",
            {
                "interaction": {"type": "deepmd"},
                "properties": [{"type": "gruneisen"}],
            },
            "registry.dp.tech/dptech/deepmd-kit:3.1.3",
        )
        self.assertEqual(selected, LAMMPS_PHONON_IMAGE)

    def test_cpu_lammps_phonon_keeps_configured_image(self):
        configured = (
            "registry.dp.tech/dptech/dp/native/prod-397637/"
            "apex-flow:1.3.0.post"
        )
        selected = _select_run_image(
            "lammps",
            {
                "interaction": {"type": "eam_alloy"},
                "properties": [{"type": "phonon"}],
            },
            configured,
        )
        self.assertEqual(selected, configured)

    def test_deepmd_cpu_phonon_does_not_force_gpu_image(self):
        configured = (
            "registry.dp.tech/dptech/dp/native/prod-397637/"
            "deepmd-kit-phonolammps:3.1.3"
        )
        selected = _select_run_image(
            "lammps",
            {
                "interaction": {"type": "deepmd"},
                "properties": [{"type": "phonon"}],
            },
            configured,
            "c8_m32_cpu",
        )
        self.assertEqual(selected, configured)

    def test_non_phonon_keeps_configured_lammps_image(self):
        configured = "registry.example/custom-lammps:latest"
        selected = _select_run_image(
            "lammps",
            {"properties": [{"type": "eos"}]},
            configured,
        )
        self.assertEqual(selected, configured)

    def test_dpa4_image_placeholders_fail_closed(self):
        with self.assertRaisesRegex(RuntimeError, "not published"):
            submit_module._dpa4_image_name()

        partial = self._dpa4_interaction()
        partial["deepmd_runtime"] = "legacy"
        props = {"interaction": partial, "properties": [{"type": "eos"}]}
        with tempfile.TemporaryDirectory() as work_dir, self.assertRaisesRegex(
            RuntimeError, "deepmd_runtime"
        ):
            submit_module._validate_lammps_runtime_contract(
                None, props, "props", [work_dir]
            )

    def test_dpa4_image_identity_comes_from_canonical_profile(self):
        profile = self._published_dpa4_profile()
        with patch.object(
            submit_module,
            "_load_dpa4_profile",
            return_value=profile,
        ) as loader:
            self.assertEqual(
                submit_module._dpa4_image_name(),
                f"{self.DPA4_TEST_REF}@{self.DPA4_TEST_DIGEST}",
            )
        loader.assert_called_once_with(require_published=True)

    def test_exact_dpa4_phonon_uses_candidate_not_legacy_override(self):
        exact_image = f"{self.DPA4_TEST_REF}@{self.DPA4_TEST_DIGEST}"
        with tempfile.TemporaryDirectory() as work_dir, patch.object(
            submit_module,
            "_load_dpa4_profile",
            return_value=self._published_dpa4_profile(),
        ):
            for prop_type in ("phonon", "gruneisen"):
                with self.subTest(prop_type=prop_type):
                    props = {
                        "interaction": self._dpa4_interaction(),
                        "properties": [{"type": prop_type}],
                    }
                    contract = submit_module._validate_lammps_runtime_contract(
                        None, props, "props", [work_dir]
                    )
                    selected = _select_run_image(
                        "lammps",
                        props,
                        LAMMPS_PHONON_IMAGE,
                        runtime_contract=contract,
                    )
                    self.assertEqual(
                        contract, submit_module.DPA4_RUNTIME_KIND
                    )
                    self.assertEqual(selected, exact_image)
                    self.assertNotEqual(selected, LAMMPS_PHONON_IMAGE)

    def test_dpa4_contract_rejects_wrong_hash_image_and_partial_intent(self):
        exact_image = f"{self.DPA4_TEST_REF}@{self.DPA4_TEST_DIGEST}"
        interaction = self._dpa4_interaction()
        interaction["runtime_model_sha256"] = "0" * 64
        props = {
            "interaction": interaction,
            "properties": [{"type": "eos"}],
        }
        with tempfile.TemporaryDirectory() as work_dir, patch.object(
            submit_module,
            "_load_dpa4_profile",
            return_value=self._published_dpa4_profile(),
        ):
            with self.assertRaisesRegex(RuntimeError, "runtime_model_sha256"):
                submit_module._validate_lammps_runtime_contract(
                    None, props, "props", [work_dir]
                )

            props["interaction"] = self._dpa4_interaction()
            props["interaction"]["type_map"] = {"Ti": 0, "V": 1}
            self.assertEqual(
                submit_module._validate_lammps_runtime_contract(
                    None, props, "props", [work_dir]
                ),
                submit_module.DPA4_RUNTIME_KIND,
            )

            props["interaction"]["type_map"] = {"Ti": 1, "V": 3}
            with self.assertRaisesRegex(RuntimeError, "contiguous"):
                submit_module._validate_lammps_runtime_contract(
                    None, props, "props", [work_dir]
                )

            props["interaction"] = self._dpa4_interaction()
            contract = submit_module._validate_lammps_runtime_contract(
                None, props, "props", [work_dir]
            )
            self.assertEqual(contract, submit_module.DPA4_RUNTIME_KIND)
            self.assertEqual(
                _select_run_image(
                    "lammps",
                    props,
                    "registry.example/wrong:tag",
                    runtime_contract=contract,
                ),
                exact_image,
            )

    def test_dpa4_contract_rejects_legacy_overwrite_and_joint_mix(self):
        props = {
            "interaction": self._dpa4_interaction(),
            "properties": [
                {"type": "eos"},
                {
                    "type": "elastic",
                    "cal_setting": {
                        "overwrite_interaction": {
                            "type": "deepmd",
                            "model": "legacy.pb",
                            "type_map": {"Ti": 0, "V": 1},
                        }
                    },
                },
            ],
        }
        with tempfile.TemporaryDirectory() as work_dir, patch.object(
            submit_module,
            "_load_dpa4_profile",
            return_value=self._published_dpa4_profile(),
        ):
            with self.assertRaisesRegex(RuntimeError, "cannot mix legacy"):
                submit_module._validate_lammps_runtime_contract(
                    None, props, "props", [work_dir]
                )

            relax = {
                "interaction": {
                    "type": "deepmd",
                    "model": "legacy.pb",
                    "type_map": {"Ti": 0, "V": 1},
                }
            }
            props["properties"] = [{"type": "eos"}]
            with self.assertRaisesRegex(RuntimeError, "cannot mix legacy"):
                submit_module._validate_lammps_runtime_contract(
                    relax, props, "joint", [work_dir]
                )

    def test_dpa4_overwrite_cannot_hide_under_vasp_base_calculator(self):
        props = {
            "interaction": {
                "type": "vasp",
                "potcars": {"Ti": "Ti"},
                "potcar_prefix": "/potcars",
            },
            "properties": [
                {
                    "type": "eos",
                    "cal_setting": {
                        "overwrite_interaction": self._dpa4_interaction()
                    },
                }
            ],
        }
        with tempfile.TemporaryDirectory() as work_dir, patch.object(
            submit_module,
            "_load_dpa4_profile",
            return_value=self._published_dpa4_profile(),
        ), patch.object(
            submit_module,
            "judge_flow",
            return_value=(object(), "vasp", "props", None, props),
        ), self.assertRaisesRegex(RuntimeError, "base calculator is 'vasp'"):
            submit_workflow([props], {}, [work_dir], "props")

    def test_dpa4_execution_profile_is_exact_t4_single_rank(self):
        def make_config(**overrides):
            values = {
                "context_type": "Bohrium",
                "batch_type": "Bohrium",
                "scass_type": submit_module.DPA4_SCASS_TYPE,
                "lammps_run_command": submit_module.DPA4_LAMMPS_RUN_COMMAND,
                "phonolammps_run_command": (
                    submit_module.DPA4_PHONOLAMMPS_RUN_COMMAND
                ),
                "group_size": submit_module.DPA4_GROUP_SIZE,
                "pool_size": submit_module.DPA4_POOL_SIZE,
            }
            values.update(overrides)
            return submit_module.Config(**values)

        phonon = {"properties": [{"type": "phonon"}]}
        submit_module._validate_dpa4_execution_config(make_config(), phonon)

        invalid_cases = {
            "wrong SKU": {"scass_type": "c8_m31_1 * NVIDIA T4"},
            "wrong job type": {"job_type": "not-container"},
            "wrong platform": {"platform": "not-ali"},
            "CPU command": {"lammps_run_command": "lmp -in in.lammps"},
            "missing phono wrapper": {"phonolammps_run_command": None},
            "grouped tasks": {"group_size": 2},
            "pooled tasks": {"pool_size": 2},
            "remote multi-rank wrapper": {
                "dispatcher_remote_command": [
                    "mpirun",
                    "-n",
                    "2",
                    "python3",
                ]
            },
            "dispatcher multi-rank wrapper": {
                "dispatcher_config": {
                    "command": ["mpirun", "-n", "2", "python3"]
                }
            },
            "dispatcher remote multi-rank wrapper": {
                "dispatcher_config": {
                    "remote_command": ["mpirun", "-n", "2", "python3"]
                }
            },
            "dispatcher JSON injection": {
                "dispatcher_config": {"json_file": "attacker.json"}
            },
            "resource override": {
                "resources": {"number_node": 2, "gpu_per_node": 1}
            },
            "task override": {"task": {"command": "mpirun -n 2 dpa4-lmp"}},
            "local context": {
                "context_type": "LocalContext",
                "batch_type": "Shell",
            },
        }
        for label, overrides in invalid_cases.items():
            with self.subTest(label=label), self.assertRaisesRegex(
                RuntimeError, "Invalid DPA4 execution profile"
            ):
                submit_module._validate_dpa4_execution_config(
                    make_config(**overrides), phonon
                )

        nested_override = {
            "machine_dict": {
                "context_type": "Bohrium",
                "batch_type": "Bohrium",
                "remote_profile": {
                    "input_data": {
                        "scass_type": "c16_m62_1 * NVIDIA T4"
                    }
                },
            }
        }
        with self.assertRaisesRegex(RuntimeError, "nested machine overrides"):
            submit_module._validate_dpa4_execution_config(
                make_config(dispatcher_config=nested_override), phonon
            )

        wrong_image_override = {
            "machine_dict": {
                "context_type": "Bohrium",
                "batch_type": "Bohrium",
                "remote_profile": {
                    "input_data": {
                        "scass_type": submit_module.DPA4_SCASS_TYPE,
                        "image_name": "registry.example/wrong:latest",
                    }
                },
            }
        }
        with patch.object(
            submit_module,
            "_load_dpa4_profile",
            return_value=self._published_dpa4_profile(),
        ), self.assertRaisesRegex(RuntimeError, "nested image overrides"):
            submit_module._validate_dpa4_execution_config(
                make_config(dispatcher_config=wrong_image_override), phonon
            )

    def test_dpa4_source_checkpoint_in_any_workdir_is_never_a_runtime(self):
        props = {
            "interaction": {
                "type": "deepmd",
                "model": "model.pt",
                "type_map": {"Ti": 0, "V": 1},
            },
            "properties": [{"type": "eos"}],
        }
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            with open(os.path.join(first, "model.pt"), "wb") as stream:
                stream.write(b"exact-checkpoint-test")
            digest = submit_module._sha256_file(
                submit_module.Path(first) / "model.pt"
            )
            with patch.object(
                submit_module, "DPA4_SOURCE_CHECKPOINT_SHA256", digest
            ):
                with self.assertRaisesRegex(RuntimeError, "never model.pt"):
                    submit_module._validate_lammps_runtime_contract(
                        None,
                        props,
                        "props",
                        [first, second],
                    )

    def test_staged_dpa4_pt2_in_any_workdir_is_rejected(self):
        props = {
            "interaction": {
                "type": "deepmd",
                "model": "runtime.pt2",
                "type_map": {"Ti": 0, "V": 1},
            },
            "properties": [{"type": "eos"}],
        }
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            runtime_path = submit_module.Path(second) / "runtime.pt2"
            runtime_path.write_bytes(b"exact-runtime-test")
            digest = submit_module._sha256_file(runtime_path)
            with patch.object(
                submit_module, "DPA4_RUNTIME_MODEL_SHA256", digest
            ):
                with self.assertRaisesRegex(RuntimeError, "image-resident path"):
                    submit_module._validate_lammps_runtime_contract(
                        None,
                        props,
                        "props",
                        [first, second],
                    )

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

    def test_dpa4_runtime_contract_accepts_cli_expanded_type_map(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            submit_module,
            "_load_dpa4_profile",
            return_value=self._published_dpa4_profile(),
        ):
            structure_dir = os.path.join(tmp, "TiV")
            os.makedirs(structure_dir)
            with open(
                os.path.join(structure_dir, "POSCAR"), "w", encoding="utf-8"
            ) as stream:
                stream.write(
                    "TiV\n1.0\n1 0 0\n0 1 0\n0 0 1\nTi V\n1 1\n"
                    "Direct\n0 0 0\n0.5 0.5 0.5\n"
                )
            payload = {
                "structures": ["TiV"],
                "interaction": self._dpa4_interaction(),
                "properties": [{"type": "eos"}],
            }
            param_path = os.path.join(tmp, "param.json")
            with open(param_path, "w", encoding="utf-8") as stream:
                json.dump(payload, stream)
            self.assertTrue(auto_fill_type_map_from_poscar(payload, param_path))
            self.assertEqual(payload["interaction"]["type_map"], {"Ti": 0, "V": 1})
            self.assertEqual(
                submit_module._validate_lammps_runtime_contract(
                    None, payload, "props", [tmp]
                ),
                submit_module.DPA4_RUNTIME_KIND,
            )

    def test_auto_fill_type_map_uses_all_structures_and_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            for directory, symbol in (("first", "Ti"), ("second", "V")):
                structure_dir = os.path.join(tmp, directory)
                os.makedirs(structure_dir)
                with open(
                    os.path.join(structure_dir, "POSCAR"),
                    "w",
                    encoding="utf-8",
                ) as stream:
                    stream.write(
                        f"{symbol}\n1.0\n1 0 0\n0 1 0\n0 0 1\n"
                        f"{symbol}\n1\nDirect\n0 0 0\n"
                    )

            overwrite = {
                "type": "deepmd",
                "model": "overwrite.pb",
                "type_map": "auto",
            }
            payload = {
                "structures": ["first", "second"],
                "interaction": {
                    "type": "deepmd",
                    "model": "base.pb",
                    "type_map": "auto",
                },
                "properties": [
                    {
                        "type": "eos",
                        "cal_setting": {
                            "overwrite_interaction": overwrite,
                        },
                    }
                ],
            }
            param_path = os.path.join(tmp, "param.json")
            with open(param_path, "w", encoding="utf-8") as stream:
                json.dump(payload, stream)

            self.assertTrue(auto_fill_type_map_from_poscar(payload, param_path))
            expected = {"Ti": 0, "V": 1}
            self.assertEqual(payload["interaction"]["type_map"], expected)
            self.assertEqual(overwrite["type_map"], expected)
            with open(param_path, "r", encoding="utf-8") as stream:
                persisted = json.load(stream)
            self.assertEqual(
                persisted["properties"][0]["cal_setting"][
                    "overwrite_interaction"
                ]["type_map"],
                expected,
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
