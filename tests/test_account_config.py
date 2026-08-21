import json
import os
import tempfile
import unittest
from argparse import Namespace
from unittest.mock import patch

from apex.account import (
    BOHRIUM_WORKFLOWS_HOST,
    DEFAULT_BOHRIUM_CONFIG,
    account_from_args,
    merge_bohrium_defaults,
    prompt_for_account_fields,
)
from apex.config import Config
from apex.utils import load_config_file


class TestAccountConfig(unittest.TestCase):
    def test_config_default_lammps_image_is_cpu_safe_runtime(self):
        self.assertEqual(
            Config().lammps_image_name,
            "registry.dp.tech/dptech/dp/native/prod-397637/"
            "apex-flow:1.3.0.post",
        )

    def test_merge_bohrium_defaults_for_bohrium_config_file(self):
        with patch.dict(os.environ, {"APEX_ACCOUNT_FILE": "/tmp/does-not-exist.json"}):
            merged = merge_bohrium_defaults(
                {"scass_type": "c8_m31_1 * NVIDIA T4"},
                config_file="global_bohrium.json"
            )
        self.assertEqual(merged["dflow_host"], BOHRIUM_WORKFLOWS_HOST)
        self.assertEqual(merged["k8s_api_server"], BOHRIUM_WORKFLOWS_HOST)
        self.assertEqual(merged["batch_type"], "Bohrium")
        self.assertEqual(merged["context_type"], "Bohrium")
        self.assertEqual(
            merged["apex_image_name"],
            DEFAULT_BOHRIUM_CONFIG["apex_image_name"]
        )
        self.assertEqual(merged["scass_type"], "c8_m31_1 * NVIDIA T4")

    def test_json_overrides_account_defaults(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            account_file = os.path.join(tmpdir, "account.json")
            with open(account_file, "w", encoding="utf-8") as fp:
                json.dump({
                    "email": "saved@example.com",
                    "password": "saved_password",
                    "program_id": 1111,
                    "apex_image_name": "saved/apex:image"
                }, fp)

            with patch.dict(os.environ, {"APEX_ACCOUNT_FILE": account_file}):
                merged = merge_bohrium_defaults(
                    {
                        "email": "override@example.com",
                        "apex_image_name": "override/apex:image",
                        "scass_type": "c32_m64_cpu"
                    },
                    config_file="global_bohrium.json"
                )

        self.assertEqual(merged["email"], "override@example.com")
        self.assertEqual(merged["password"], "saved_password")
        self.assertEqual(merged["program_id"], 1111)
        self.assertEqual(merged["apex_image_name"], "override/apex:image")
        self.assertEqual(merged["scass_type"], "c32_m64_cpu")

    def test_do_not_inject_bohrium_defaults_for_local_context(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            account_file = os.path.join(tmpdir, "account.json")
            with open(account_file, "w", encoding="utf-8") as fp:
                json.dump({
                    "email": "saved@example.com",
                    "password": "saved_password",
                    "program_id": 1111
                }, fp)

            with patch.dict(os.environ, {"APEX_ACCOUNT_FILE": account_file}):
                merged = merge_bohrium_defaults(
                    {"context_type": "Local", "batch_type": "Shell"},
                    config_file="global_local_debug.json"
                )

        self.assertEqual(merged["context_type"], "Local")
        self.assertEqual(merged["batch_type"], "Shell")
        self.assertNotIn("dflow_host", merged)
        self.assertNotIn("email", merged)

    def test_load_config_file_uses_account_defaults(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            account_file = os.path.join(tmpdir, "account.json")
            config_file = os.path.join(tmpdir, "global_bohrium.json")
            with open(account_file, "w", encoding="utf-8") as fp:
                json.dump({
                    "email": "saved@example.com",
                    "password": "saved_password",
                    "program_id": 1111
                }, fp)
            with open(config_file, "w", encoding="utf-8") as fp:
                json.dump({"scass_type": "c8_m31_1 * NVIDIA T4"}, fp)

            with patch.dict(os.environ, {"APEX_ACCOUNT_FILE": account_file}):
                merged = load_config_file(config_file)

        self.assertEqual(merged["email"], "saved@example.com")
        self.assertEqual(merged["program_id"], 1111)
        self.assertEqual(merged["dflow_host"], BOHRIUM_WORKFLOWS_HOST)
        self.assertEqual(merged["scass_type"], "c8_m31_1 * NVIDIA T4")

    def test_ignore_broken_account_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            account_file = os.path.join(tmpdir, "account.json")
            with open(account_file, "w", encoding="utf-8") as fp:
                fp.write("{invalid-json")
            with patch.dict(os.environ, {"APEX_ACCOUNT_FILE": account_file}):
                merged = merge_bohrium_defaults(
                    {"scass_type": "c8_m31_1 * NVIDIA T4"},
                    config_file="global_bohrium.json"
                )
        self.assertEqual(merged["dflow_host"], BOHRIUM_WORKFLOWS_HOST)

    def test_access_key_auth_does_not_require_email_or_password(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            account_file = os.path.join(tmpdir, "account.json")
            with open(account_file, "w", encoding="utf-8") as fp:
                json.dump({"access_key": "secret-key", "program_id": 1111}, fp)
            with patch.dict(os.environ, {"APEX_ACCOUNT_FILE": account_file}):
                merged = merge_bohrium_defaults({}, config_file="global_bohrium.json")

        self.assertEqual(merged["access_key"], "secret-key")
        self.assertNotIn("email", merged)
        self.assertNotIn("password", merged)

    def test_access_key_uses_dflow_and_ticket_compatible_dispatcher_auth(self):
        config = Config(
            dflow_host=BOHRIUM_WORKFLOWS_HOST,
            context_type="Bohrium",
            batch_type="Bohrium",
            access_key="secret-key",
            program_id=1111,
            scass_type="c8_m31_1 * NVIDIA T4",
        )

        self.assertEqual(config.bohrium_config_dict["access_key"], "secret-key")
        self.assertEqual(config.bohrium_config_dict["app_key"], "")
        self.assertEqual(config.machine_dict["context_type"], "Bohrium")
        self.assertEqual(config.machine_dict["batch_type"], "Bohrium")
        self.assertEqual(
            config.machine_dict["remote_profile"],
            {
                "email": None,
                "password": None,
                "program_id": 1111,
                "input_data": {
                    "job_type": "container",
                    "platform": "ali",
                    "scass_type": "c8_m31_1 * NVIDIA T4",
                },
            },
        )

    def test_clear_login_targets(self):
        expected_remaining = {
            "all": set(),
            "access-key": {"email", "password"},
            "email": {"access_key"},
        }
        for clear_target, remaining in expected_remaining.items():
            with self.subTest(clear=clear_target), tempfile.TemporaryDirectory() as tmpdir:
                account_file = os.path.join(tmpdir, "account.json")
                with open(account_file, "w", encoding="utf-8") as fp:
                    json.dump(
                        {
                            "email": "saved@example.com",
                            "password": "saved-password",
                            "access_key": "saved-key",
                            "program_id": 1111,
                        },
                        fp,
                    )
                args = Namespace(
                    file=account_file,
                    reset=False,
                    show=False,
                    non_interactive=True,
                    clear=clear_target,
                    dflow_host=None,
                    k8s_api_server=None,
                    batch_type=None,
                    context_type=None,
                    email=None,
                    password=None,
                    access_key=None,
                    program_id=None,
                    apex_image_name=None,
                )

                account_from_args(args)
                with open(account_file, encoding="utf-8") as fp:
                    saved = json.load(fp)

            present = {key for key in ("email", "password", "access_key") if key in saved}
            self.assertEqual(remaining, present)
            self.assertEqual(1111, saved["program_id"])

    def test_interactive_email_choice_clears_access_key_and_keeps_blanks(self):
        current = {
            "access_key": "stale-key",
            "email": "saved@example.com",
            "password": "saved-password",
            "program_id": 1111,
        }
        with patch("apex.account.getpass", return_value="   "), patch(
            "builtins.input", side_effect=["1", "   ", "   "]
        ):
            updated = prompt_for_account_fields(current)

        self.assertIsNone(updated["access_key"])
        self.assertEqual("saved@example.com", updated["email"])
        self.assertEqual("saved-password", updated["password"])
        self.assertEqual(1111, updated["program_id"])

    def test_interactive_access_key_choice_keeps_blank_value(self):
        current = {"access_key": "saved-key", "program_id": 1111}
        with patch("apex.account.getpass", return_value="   "), patch(
            "builtins.input", side_effect=["2", "   "]
        ):
            updated = prompt_for_account_fields(current)

        self.assertEqual("saved-key", updated["access_key"])
        self.assertEqual(1111, updated["program_id"])
