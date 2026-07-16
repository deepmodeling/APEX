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
        self.assertIn("models/DPA2", text)
        self.assertIn("models/DPA_alloy", text)
        root = get_skill_root()
        self.assertTrue((root / "models" / "README.md").is_file())
        self.assertTrue((root / "models" / "DPA2" / "DPA2.pb").is_file())
        self.assertTrue((root / "models" / "DPA_alloy" / "DPA_alloy.pb").is_file())
        self.assertTrue((root / "scripts" / "fetch_models.py").is_file())

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

    def test_skill_documents_safe_submission_defaults(self):
        root = get_skill_root()
        skill = (root / "SKILL.md").read_text(encoding="utf-8")
        submission = (root / "reference" / "submission.md").read_text(
            encoding="utf-8"
        )
        structure = (root / "reference" / "workflow-control.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("never refresh it in `run.sh`", skill)
        self.assertIn("Do not read BOHRIUM_ACCESS_KEY", submission)
        self.assertNotIn("Recommended run.sh Ticket Refresh Template", submission)
        self.assertIn("use a model bundled with this skill", skill)
        self.assertIn('"type_map": "auto"', skill)
        self.assertIn("already a supercell", structure)
        self.assertIn("`supercell` / `supercell_size` to `[1,1,1]`", structure)

    def test_skill_zip_flag(self):
        import tempfile
        import zipfile

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "apex-skill.zip"
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                skill_from_args(SimpleNamespace(zip=True, output=str(out)))
            self.assertTrue(out.is_file())
            printed = buf.getvalue()
            self.assertIn(str(out), printed)
            self.assertNotIn("Local DPA checkpoints missing", printed)
            with zipfile.ZipFile(out) as zf:
                names = zf.namelist()
            self.assertTrue(any(n == f"{SKILL_NAME}/SKILL.md" for n in names))
            self.assertTrue(
                any(n.startswith(f"{SKILL_NAME}/scripts/") for n in names)
            )
            self.assertTrue(any(n.endswith("DPA2.pb") for n in names))
            self.assertTrue(any(n.endswith("DPA_alloy.pb") for n in names))
            self.assertFalse(any(n.endswith(".pt") for n in names))

    def test_skill_agent_prompt(self):
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            skill_from_args(SimpleNamespace(zip=False, output=None))
        out = buf.getvalue()
        self.assertIn("Agent prompt", out)
        self.assertIn(SKILL_NAME, out)
        self.assertIn("apex skill --zip", out)
        self.assertIn("MatMaster", out)


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

    def test_lammps_interaction_defaults_to_auto_type_map(self):
        interaction = self.gen.build_interaction(
            backend="lammps",
            potential="deepmd",
            model="DPA2.pb",
        )
        self.assertEqual(interaction["type_map"], "auto")

    def test_generate_config_has_no_type_map_cli_option(self):
        source = (
            get_skill_root() / "scripts" / "generate_config.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn('add_argument("--type-map"', source)


class TestGammaSurfaceSkillConfig(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gen = _load_script("generate_config.py")
        cls.validator = _load_script("validate_inputs.py")

    def test_generated_gamma_surface_preserves_legacy_default(self):
        self.assertIs(
            self.gen.PROPERTY_DEFAULTS["gamma_surface"]["closed_loop"],
            False,
        )

    def test_closed_loop_requires_boolean_and_no_custom_lengths(self):
        properties = [
            {
                "type": "gamma_surface",
                "plane_miller": [1, 1, 1],
                "slip_direction": [-1, 1, 0],
                "closed_loop": "true",
            },
            {
                "type": "gamma_surface",
                "plane_miller": [1, 1, 1],
                "slip_direction": [-1, 1, 0],
                "closed_loop": True,
                "slip_length": 1,
            },
        ]

        errors, _ = self.validator.validate_properties(properties, "lammps")

        self.assertTrue(
            any("closed_loop must be a boolean" in error for error in errors)
        )
        self.assertTrue(
            any("cannot be combined with slip_length" in error for error in errors)
        )


if __name__ == "__main__":
    unittest.main()
