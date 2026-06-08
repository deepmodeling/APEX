import json
import os
import tempfile
import unittest
from unittest.mock import patch

from apex.account import (
    BOHRIUM_WORKFLOWS_HOST,
    DEFAULT_BOHRIUM_CONFIG,
    merge_bohrium_defaults,
)
from apex.utils import load_config_file


class TestAccountConfig(unittest.TestCase):
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
