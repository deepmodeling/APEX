import importlib.util
import io
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from apex.skills import SKILL_NAME, get_skill_root
from apex.skill import skill_from_args


def _load_script(name: str):
    root = get_skill_root() / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), root)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class TestApexSkill(unittest.TestCase):
    def test_bundled_skill_exists(self):
        root = get_skill_root()
        self.assertEqual(root.name, SKILL_NAME)
        self.assertTrue((root / "SKILL.md").is_file())
        text = (root / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn(f"name: {SKILL_NAME}", text)
        self.assertIn("BOHRIUM_PROJECT_ID", text)
        self.assertIn("validate_apex_combo.py", text)

    def test_no_hardcoded_project_id_in_skill_docs(self):
        root = get_skill_root()
        for path in [root / "SKILL.md", root / "reference" / "submission.md"]:
            text = path.read_text(encoding="utf-8")
            # Allow mentioning 13529 only as a negative example in SKILL.md
            if path.name == "SKILL.md":
                self.assertNotRegex(text, r'"program_id":\s*13529')
                self.assertNotRegex(text, r'"project_id":\s*13529')
            else:
                self.assertNotIn("13529", text)

    def test_skill_path_flag(self):
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            skill_from_args(SimpleNamespace(path=True))
        self.assertEqual(buf.getvalue().strip(), str(get_skill_root()))

    def test_skill_agent_prompt(self):
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            skill_from_args(SimpleNamespace(path=False))
        out = buf.getvalue()
        self.assertIn("Agent prompt", out)
        self.assertIn(SKILL_NAME, out)
        self.assertIn(str(get_skill_root()), out)


class TestValidateApexCombo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.combo = _load_script("validate_apex_combo.py")

    def test_blocked_image(self):
        ok, errors = self.combo.check_combo(
            "registry.dp.tech/dptech/deepmd-kit:3.1.2",
            "c32_m64_cpu",
        )
        self.assertFalse(ok)
        self.assertTrue(any("3.1.2" in e for e in errors))

    def test_blocked_scass(self):
        ok, errors = self.combo.check_combo(
            "deepmd-kit:3.1.3",
            "c4_m16_cpu",
        )
        self.assertFalse(ok)
        self.assertTrue(any("c4_m16_cpu" in e for e in errors))

    def test_ok_combo(self):
        ok, errors = self.combo.check_combo(
            "registry.dp.tech/dptech/deepmd-kit:3.1.3",
            "c8_m31_1 * NVIDIA T4",
        )
        self.assertTrue(ok)
        self.assertEqual(errors, [])

    def test_recommend_lammps_gpu(self):
        rec = self.combo.recommend("lammps", "gpu")
        self.assertIn("3.1.3", rec["image"])
        self.assertIn("T4", rec["scass_type"])

    def test_cli_check_exit_codes(self):
        self.assertEqual(
            self.combo.main(
                [
                    "check",
                    "--image",
                    "deepmd-kit:3.1.3",
                    "--scass",
                    "c32_m64_cpu",
                    "--format",
                    "json",
                ]
            ),
            0,
        )
        self.assertEqual(
            self.combo.main(
                [
                    "check",
                    "--image",
                    "deepmd-kit:3.1.0",
                    "--scass",
                    "c32_m64_cpu",
                ]
            ),
            1,
        )


class TestGenerateConfigProjectId(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gen = _load_script("generate_config.py")

    def test_requires_env_or_arg(self):
        env = {k: v for k, v in os.environ.items() if k != "BOHRIUM_PROJECT_ID"}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(RuntimeError):
                self.gen.resolve_project_id(None)

    def test_reads_env(self):
        with patch.dict(os.environ, {"BOHRIUM_PROJECT_ID": "4242"}):
            self.assertEqual(self.gen.resolve_project_id(None), 4242)

    def test_arg_overrides_env(self):
        with patch.dict(os.environ, {"BOHRIUM_PROJECT_ID": "4242"}):
            self.assertEqual(self.gen.resolve_project_id(99), 99)

    def test_no_default_constant(self):
        self.assertFalse(hasattr(self.gen, "DEFAULT_PROJECT_ID"))


if __name__ == "__main__":
    unittest.main()
