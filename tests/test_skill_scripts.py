import importlib.util
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from apex.skills import get_skill_root


def _load_script(name):
    path = get_skill_root() / "scripts" / name
    spec = importlib.util.spec_from_file_location(
        f"apex_skill_test_{name.replace('.', '_')}", path
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class TestFiniteTemperatureTemplates(unittest.TestCase):
    def test_backend_truth_table_and_vasp_defaults(self):
        skill_root = get_skill_root()
        templates = json.loads(
            (skill_root / "data" / "default_templates.json").read_text()
        )
        properties = templates["properties"]
        self.assertEqual(
            {"lammps": True, "abacus": False, "vasp": True},
            properties["finite_t_latt"],
        )
        self.assertEqual(
            {"lammps": True, "abacus": False, "vasp": True},
            properties["annealing"],
        )
        self.assertEqual(
            {"lammps": True, "abacus": False, "vasp": False},
            properties["finite_t_elastic"],
        )

        vasp_props = json.loads(
            (
                skill_root.parents[2]
                / "apex"
                / "default_config"
                / "vasp"
                / "param_props.json"
            ).read_text()
        )["properties"]
        finite_latt = next(
            prop for prop in vasp_props if prop["type"] == "finite_t_latt"
        )
        self.assertEqual(
            [300, 500, 700, 900, 1100, 1300, 1500],
            finite_latt["cal_setting"]["temperature"],
        )
        self.assertEqual(5000, finite_latt["cal_setting"]["equi_step"])
        self.assertEqual(10000, finite_latt["cal_setting"]["ave_step"])
        coexistence = next(
            prop
            for prop in vasp_props
            if prop["type"] == "annealing"
            and prop.get("protocol") == "coexistence"
        )
        self.assertEqual(5000, coexistence["cal_setting"]["equi_step"])
        self.assertEqual(10000, coexistence["cal_setting"]["production_step"])


class TestGenerateConfigHelpers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gen = _load_script("generate_config.py")

    def test_get_bohrium_ticket_success(self):
        ticket = "12345678-1234-1234-1234-123456789abc"
        with patch.object(
            self.gen, "urlopen", return_value=_Response(
                {"code": 0, "data": {"ticket": ticket}}
            )
        ) as mocked:
            self.assertEqual(self.gen.get_bohrium_ticket("secret"), ticket)
        request = mocked.call_args.args[0]
        self.assertIn("accessKey=secret", request.full_url)
        self.assertIn(
            f"expiration={self.gen.TICKET_EXPIRE_HOURS}", request.full_url
        )

    def test_get_bohrium_ticket_rejects_transport_and_api_errors(self):
        with patch.object(self.gen, "urlopen", side_effect=URLError("offline")):
            with self.assertRaisesRegex(RuntimeError, "Failed to get ticket"):
                self.gen.get_bohrium_ticket("secret")

        for payload, message in (
            ({"code": 401, "message": "denied"}, "code=401"),
            ({"code": 0, "data": {"ticket": "short"}}, "Invalid ticket"),
            ({"code": 0, "data": {}}, "Invalid ticket"),
        ):
            with self.subTest(payload=payload):
                with patch.object(
                    self.gen, "urlopen", return_value=_Response(payload)
                ):
                    with self.assertRaisesRegex(RuntimeError, message):
                        self.gen.get_bohrium_ticket("secret")

    def test_sanitize_workflow_name(self):
        self.assertEqual(
            self.gen.sanitize_workflow_name("  Mo_BCC Surface!  "),
            "mo-bcc-surface",
        )
        self.assertEqual(self.gen.sanitize_workflow_name("***"), "apex-workflow")
        sanitized = self.gen.sanitize_workflow_name("A" * 70 + "-")
        self.assertEqual(len(sanitized), 63)
        self.assertFalse(sanitized.endswith("-"))

    def test_resolve_project_id_rejects_non_integer_env(self):
        with patch.dict(os.environ, {"BOHRIUM_PROJECT_ID": "not-an-int"}):
            with self.assertRaisesRegex(RuntimeError, "must be an integer"):
                self.gen.resolve_project_id()

    def test_build_global_json_selects_backend_resources(self):
        cases = (
            ("lammps", "deepmd", self.gen.SCASS_TYPES["lammps_gpu"], "lmp"),
            ("lammps", "eam_alloy", self.gen.SCASS_TYPES["lammps_cpu"], "lmp"),
            ("abacus", None, self.gen.SCASS_TYPES["abacus"], "abacus"),
            ("vasp", None, self.gen.SCASS_TYPES["vasp"], "/opt/vasp.5.4.4/bin/vasp_std"),
            ("other", None, self.gen.SCASS_TYPES["lammps_cpu"], "lmp"),
        )
        with patch.object(
            self.gen, "get_bohrium_ticket", return_value="t" * 36
        ), patch.object(self.gen, "_validate_image_scass") as validate:
            for backend, potential, expected_scass, command in cases:
                with self.subTest(backend=backend, potential=potential):
                    config = self.gen.build_global_json(
                        backend, potential, access_key="key", project_id=42
                    )
                    self.assertEqual(config["program_id"], 42)
                    self.assertEqual(config["scass_type"], expected_scass)
                    self.assertIn(command, config["lammps_run_command"])
                    if backend == "lammps":
                        expected_image = (
                            self.gen.LAMMPS_GPU_IMAGE
                            if potential in self.gen.GPU_POTENTIALS
                            else self.gen.LAMMPS_CPU_IMAGE
                        )
                        self.assertEqual(
                            config["lammps_image_name"], expected_image
                        )
            self.assertEqual(validate.call_count, len(cases))

    def test_build_global_json_backend_fields_and_overrides(self):
        with patch.object(
            self.gen, "get_bohrium_ticket", return_value="t" * 36
        ), patch.object(self.gen, "_validate_image_scass"):
            abacus = self.gen.build_global_json(
                "abacus",
                access_key="key",
                project_id=42,
                scass_type="custom",
                run_command="custom-abacus",
            )
            vasp = self.gen.build_global_json(
                "vasp", access_key="key", project_id=42
            )
            vasp_with_image = self.gen.build_global_json(
                "vasp",
                access_key="key",
                project_id=42,
                vasp_image="registry.example/private/vasp:licensed",
            )
        self.assertEqual(abacus["scass_type"], "custom")
        self.assertEqual(abacus["abacus_run_command"], "custom-abacus")
        self.assertIn("abacus_image_name", abacus)
        self.assertIn("setvars.sh", vasp["vasp_run_command"])
        self.assertIn("ulimit -s unlimited", vasp["vasp_run_command"])
        self.assertIn("/opt/vasp.5.4.4/bin/vasp_std", vasp["vasp_run_command"])
        self.assertIn("mpirun -n 32", vasp["vasp_run_command"])
        self.assertNotIn("vasp_image_name", vasp)
        self.assertEqual(
            vasp_with_image["vasp_image_name"],
            "registry.example/private/vasp:licensed",
        )

    def test_build_global_json_sandbox_routes_lammps_images(self):
        gpu = self.gen.build_global_json_sandbox(
            "lammps", "deepmd", access_key="key", project_id=42
        )
        cpu = self.gen.build_global_json_sandbox(
            "lammps", "eam_alloy", access_key="key", project_id=42
        )

        self.assertEqual(gpu["lammps_image_name"], self.gen.LAMMPS_GPU_IMAGE)
        self.assertEqual(gpu["image_address"], self.gen.LAMMPS_GPU_IMAGE)
        self.assertEqual(
            gpu["machine_type"],
            self.gen.SANDBOX_MACHINE_TYPES["lammps_gpu"],
        )
        self.assertEqual(cpu["lammps_image_name"], self.gen.LAMMPS_CPU_IMAGE)
        self.assertEqual(cpu["image_address"], self.gen.LAMMPS_CPU_IMAGE)
        self.assertEqual(
            cpu["machine_type"],
            self.gen.SANDBOX_MACHINE_TYPES["lammps_cpu"],
        )

    def test_explicit_lammps_image_supports_cpu_deepmd(self):
        image = (
            "registry.dp.tech/dptech/dp/native/prod-397637/"
            "deepmd-kit-phonolammps:3.1.3"
        )
        config = self.gen.build_global_json_sandbox(
            "lammps",
            "deepmd",
            access_key="key",
            project_id=42,
            machine_type="c8_m32_cpu",
            lammps_image=image,
        )
        self.assertEqual(config["lammps_image_name"], image)
        self.assertEqual(config["image_address"], image)

    def test_load_confirmed_property_configs_filters_in_requested_order(self):
        payload = {
            "properties": [
                {"type": "eos", "vol_step": 0.02},
                {"type": "phonon", "supercell_size": [3, 3, 3]},
            ]
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "confirmed.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            configs = self.gen.load_confirmed_property_configs(
                str(path), ["phonon", "eos"]
            )
        self.assertEqual([item["type"] for item in configs], ["phonon", "eos"])
        self.assertEqual(configs[0]["supercell_size"], [3, 3, 3])

    def test_build_param_json_uses_confirmed_property_configs_exactly(self):
        confirmed = [{"type": "eos", "vol_step": 0.02}]
        param = self.gen.build_param_json(
            "confs/input",
            {"type": "deepmd", "model": "model.pth"},
            ["eos"],
            property_configs=confirmed,
        )
        self.assertEqual(param["properties"], confirmed)

    def test_build_global_json_requires_access_key_and_propagates_combo_error(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "BOHRIUM_ACCESS_KEY"):
                self.gen.build_global_json("lammps", "deepmd", project_id=1)

        with patch.object(
            self.gen, "get_bohrium_ticket", return_value="t" * 36
        ), patch.object(
            self.gen,
            "_validate_image_scass",
            side_effect=RuntimeError("blocked"),
        ):
            with self.assertRaisesRegex(RuntimeError, "blocked"):
                self.gen.build_global_json(
                    "lammps", "deepmd", access_key="key", project_id=1
                )

    def test_validate_image_scass_accepts_and_rejects_combos(self):
        self.gen._validate_image_scass(
            "deepmd-kit:3.1.3", "c32_m64_cpu"
        )
        with self.assertRaisesRegex(RuntimeError, "Blocked image"):
            self.gen._validate_image_scass(
                "deepmd-kit:3.1.2", "c32_m64_cpu"
            )

    def test_build_interaction_all_backends(self):
        with self.assertRaisesRegex(ValueError, "--potential"):
            self.gen.build_interaction("lammps", model="model.pb")
        with self.assertRaisesRegex(ValueError, "--model"):
            self.gen.build_interaction("lammps", potential="deepmd")

        abacus = self.gen.build_interaction(
            "abacus",
            potcars={"Al": "Al.upf"},
            orb_files={"Al": "Al.orb"},
        )
        self.assertEqual(abacus["incar"], "INPUT")
        self.assertEqual(abacus["potcars"]["Al"], "Al.upf")
        self.assertEqual(abacus["orb_files"]["Al"], "Al.orb")

        vasp = self.gen.build_interaction(
            "vasp", incar="CUSTOM", potcars={"Al": "Al"}
        )
        self.assertEqual(vasp["incar"], "CUSTOM")
        self.assertEqual(vasp["potcar_prefix"], ".")
        with self.assertRaisesRegex(ValueError, "Unknown backend"):
            self.gen.build_interaction("unknown")

    def test_stage_lammps_model_copies_and_returns_basename(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_dir = root / "source"
            output_dir = root / "job"
            source_dir.mkdir()
            output_dir.mkdir()
            source = source_dir / "model.pt2"
            source.write_bytes(b"dpa4")
            staged = self.gen.stage_lammps_model(str(source), output_dir)
            self.assertEqual(staged, "model.pt2")
            self.assertEqual((output_dir / staged).read_bytes(), b"dpa4")

    def test_build_param_json_flow_types_and_dft_overrides(self):
        lammps = {"type": "deepmd"}
        joint = self.gen.build_param_json(
            "confs/input", lammps, ["elastic", "unknown"], "joint"
        )
        self.assertIn("relaxation", joint)
        self.assertEqual(joint["properties"], [
            self.gen.PROPERTY_DEFAULTS["elastic"]
        ])

        relax = self.gen.build_param_json(
            "confs/input",
            lammps,
            [],
            "relax",
            relaxation_settings={"etol": 1},
        )
        self.assertEqual(relax["relaxation"]["cal_setting"], {"etol": 1})
        self.assertNotIn("properties", relax)
        self.assertEqual(
            self.gen.PROPERTY_DEFAULTS["gruneisen"]["MESH"],
            [20, 20, 20],
        )

        dft = self.gen.build_param_json(
            "confs/input", {"type": "vasp"}, ["phonon"], "props"
        )
        self.assertNotIn("relaxation", dft)
        self.assertEqual(dft["properties"][0]["BAND_POINTS"], 21)

    def test_gamma_overrides_preserve_defaults_and_route_by_property(self):
        interaction = {"type": "deepmd"}
        baseline = self.gen.build_param_json(
            "confs/input",
            interaction,
            ["gamma", "gamma_surface"],
            flow_type="props",
        )
        self.assertEqual(
            baseline["properties"][0],
            self.gen.PROPERTY_DEFAULTS["gamma"],
        )
        self.assertEqual(
            baseline["properties"][1],
            self.gen.PROPERTY_DEFAULTS["gamma_surface"],
        )

        args = Namespace(
            gamma_plane_miller=[1.0, 1.0, 0.0],
            gamma_slip_direction=[1.0, -1.0, 0.0],
            gamma_supercell_size=[2.0, 3.0, 4.5],
            gamma_min_slab_height=7.5,
            gamma_max_atoms=80,
            gamma_min_distance=0.4,
            gamma_n_steps=5,
            gamma_n_steps_x=3,
            gamma_n_steps_y=4,
            gamma_closed_loop=True,
        )
        overrides = self.gen.gamma_overrides_from_args(args)
        self.assertEqual(
            self.gen.validate_gamma_cli_options(
                ["gamma", "gamma_surface"], overrides
            ),
            [],
        )
        configured = self.gen.build_param_json(
            "confs/input",
            interaction,
            ["gamma", "gamma_surface"],
            flow_type="props",
            gamma_overrides=overrides,
        )
        line, surface = configured["properties"]
        self.assertEqual(line["supercell_size"], [2, 3, 4.5])
        self.assertEqual(line["n_steps"], 5)
        self.assertNotIn("n_steps_x", line)
        self.assertEqual(surface["n_steps_x"], 3)
        self.assertEqual(surface["n_steps_y"], 4)
        self.assertTrue(surface["closed_loop"])
        self.assertNotIn("n_steps", surface)

    def test_gamma_cli_options_reject_invalid_values_and_scope(self):
        cases = (
            (["elastic"], {"max_atoms": 10}, "--gamma-*"),
            (["gamma_surface"], {"n_steps": 2}, "--gamma-n-steps"),
            (["gamma"], {"n_steps_x": 2}, "--gamma-n-steps-x"),
            (["gamma"], {"plane_miller": [1, 1]}, "3 or 4"),
            (["gamma"], {"slip_direction": [1, 1, 1, 1, 1]}, "3 or 4"),
            (
                ["gamma"],
                {
                    "plane_miller": [1, 1, 1],
                    "slip_direction": [1, 0, 0, 0],
                },
                "dimensions differ",
            ),
            (["gamma"], {"supercell_size": [0, 1, 2]}, "component 1"),
            (["gamma"], {"supercell_size": [1, 1.5, 2]}, "component 2"),
            (["gamma"], {"supercell_size": [1, 1, 0]}, "plane count"),
            (["gamma"], {"min_slab_height": 0}, "positive"),
            (["gamma"], {"max_atoms": 0}, "positive integer"),
            (["gamma"], {"min_distance": -0.1}, "non-negative"),
            (["gamma"], {"n_steps": 0}, "positive integer"),
            (["gamma_surface"], {"n_steps_x": 0}, "positive integer"),
            (["gamma_surface"], {"n_steps_y": 0}, "positive integer"),
            (["gamma"], {"closed_loop": True}, "gamma_surface"),
        )
        for properties, overrides, message in cases:
            with self.subTest(properties=properties, overrides=overrides):
                errors = self.gen.validate_gamma_cli_options(
                    properties, overrides
                )
                self.assertTrue(
                    any(message in error for error in errors), errors
                )

    def test_validate_config_and_parse_str_map(self):
        self.assertTrue(
            self.gen.validate_config("vasp", None, ["finite_t_elastic"])
        )
        self.assertTrue(
            self.gen.validate_config("lammps", "unknown", ["elastic"])
        )
        self.assertEqual(
            self.gen.validate_config("lammps", "deepmd", ["elastic"]), []
        )
        self.assertIsNone(self.gen.parse_str_map(""))
        self.assertEqual(
            self.gen.parse_str_map("Al:Al.upf, Cu : Cu.upf,invalid"),
            {"Al": "Al.upf", "Cu": "Cu.upf"},
        )

    def test_main_generates_complete_job_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            structure = root / "Al structure.vasp"
            model = root / "model.pb"
            output = root / "job"
            structure.write_text("structure", encoding="utf-8")
            model.write_text("model", encoding="utf-8")
            argv = [
                "generate_config.py",
                "create",
                "--structure", str(structure),
                "--backend", "lammps",
                "--potential", "deepmd",
                "--model", str(model),
                "--properties", "elastic", "phonon",
                "--workflow-name", "Al Test!",
                "--output-dir", str(output),
                "--project-id", "7",
                "--access-key", "key",
            ]
            stdout = io.StringIO()
            with patch.object(sys, "argv", argv), patch.object(
                self.gen, "get_bohrium_ticket", return_value="t" * 36
            ), patch("sys.stdout", stdout):
                self.gen.main()

            global_config = json.loads(
                (output / "global.json").read_text(encoding="utf-8")
            )
            param = json.loads(
                (output / "param.json").read_text(encoding="utf-8")
            )
            submit = output / "submit.sh"
            self.assertEqual(global_config["program_id"], 7)
            self.assertEqual(
                [prop["type"] for prop in param["properties"]],
                ["elastic", "phonon"],
            )
            self.assertEqual(
                (output / "confs" / "input" / "POSCAR").read_text(),
                "structure",
            )
            self.assertEqual((output / "model.pb").read_text(), "model")
            self.assertIn('-n "al-test"', submit.read_text())
            self.assertTrue(submit.stat().st_mode & stat.S_IXUSR)
            self.assertIn("Workflow name sanitized", stdout.getvalue())

    def test_main_rejects_invalid_config_before_ticket_request(self):
        argv = [
            "generate_config.py",
            "create",
            "--structure", "POSCAR",
            "--backend", "vasp",
            "--properties", "finite_t_elastic",
        ]
        with patch.object(sys, "argv", argv), patch.object(
            self.gen, "get_bohrium_ticket"
        ) as ticket, self.assertRaises(SystemExit) as raised:
            self.gen.main()
        self.assertEqual(raised.exception.code, 1)
        ticket.assert_not_called()

    def test_refresh_global_subcommand_preserves_param_and_fixes_id_types(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            global_path = root / "global.json"
            param_path = root / "param.json"
            param_text = '{"properties": [{"type": "elastic"}]}\n'
            param_path.write_text(param_text, encoding="utf-8")
            global_path.write_text(json.dumps({
                "program_id": "old",
                "bohrium_config": {
                    "project_id": "old",
                    "ticket": "expired",
                },
                "machine": {
                    "remote_profile": {"program_id": "old"},
                },
            }), encoding="utf-8")

            argv = [
                "generate_config.py",
                "refresh-global",
                "--global", str(global_path),
                "--project-id", "42",
                "--access-key", "key",
            ]
            stdout = io.StringIO()
            with patch.object(sys, "argv", argv), patch.object(
                self.gen, "get_bohrium_ticket", return_value="t" * 36
            ), patch("sys.stdout", stdout):
                self.gen.main()

            config = json.loads(global_path.read_text(encoding="utf-8"))
            self.assertIs(type(config["program_id"]), int)
            self.assertIs(
                type(config["bohrium_config"]["project_id"]), int
            )
            self.assertIs(
                type(config["machine"]["remote_profile"]["program_id"]), int
            )
            self.assertEqual(config["bohrium_config"]["ticket"], "t" * 36)
            self.assertEqual(param_path.read_text(encoding="utf-8"), param_text)
            self.assertIn("Hard type check passed", stdout.getvalue())


class TestListBohriumImages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_script("list_bohrium_images.py")

    def test_filter_images_by_keyword_and_urls(self):
        records = [
            {
                "id": 1,
                "name": "my-vasp:5.4.4",
                "description": "licensed VASP",
                "url": "registry.dp.tech/user/my-vasp:5.4.4",
            },
            {
                "id": 2,
                "name": "lammps:latest",
                "url": "registry.dp.tech/user/lammps:latest",
            },
        ]
        payload = self.mod.filter_images(records, "vasp", max_results=20)
        self.assertEqual(payload["total_found"], 1)
        self.assertEqual(payload["returned"], 1)
        self.assertTrue(payload["images"][0]["private"])
        self.assertEqual(
            self.mod.image_urls(payload),
            ["registry.dp.tech/user/my-vasp:5.4.4"],
        )

    def test_main_require_exits_when_empty(self):
        with patch.object(self.mod, "fetch_private_images", return_value=[]), patch.dict(
            os.environ, {"BOHRIUM_ACCESS_KEY": "ak"}, clear=False
        ):
            code = self.mod.main(["--keyword", "vasp", "--require"])
        self.assertEqual(code, 1)

    def test_main_prints_matching_urls(self):
        records = [
            {
                "id": 9,
                "name": "priv-vasp:1",
                "url": "registry.dp.tech/acct/priv-vasp:1",
            }
        ]
        with patch.object(
            self.mod, "fetch_private_images", return_value=records
        ), patch.dict(os.environ, {"BOHRIUM_ACCESS_KEY": "ak"}, clear=False), patch(
            "sys.stdout", new_callable=io.StringIO
        ) as stdout:
            code = self.mod.main(["--keyword", "vasp", "--urls-only"])
        self.assertEqual(code, 0)
        self.assertIn("registry.dp.tech/acct/priv-vasp:1", stdout.getvalue())


class TestValidateInputs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validator = _load_script("validate_inputs.py")
        cls.gen = _load_script("generate_config.py")

    def test_validate_global(self):
        errors, warnings = self.validator.validate_global({})
        self.assertTrue(
            any("Missing local execution configuration" in error for error in errors)
        )
        self.assertTrue(
            any("Missing 'run_command'" in error for error in errors)
        )
        self.assertFalse(warnings)

        errors, warnings = self.validator.validate_global(
            {"machine": {}, "run_command": "run"}
        )
        self.assertTrue(
            any("machine.batch_type" in error for error in errors)
        )
        self.assertFalse(warnings)

        self.assertEqual(
            self.validator.validate_global(
                {
                    "machine": {"batch_type": "Bohrium"},
                    "resources": {},
                    "run_command": "run",
                }
            ),
            ([], []),
        )

    def test_validate_global_execution_profiles(self):
        data_root = get_skill_root() / "data"
        bohrium_direct = json.loads(
            (data_root / "global_bohrium_direct.json").read_text()
        )
        errors, warnings = self.validator.validate_global(bohrium_direct)
        self.assertFalse(errors)
        self.assertTrue(any("apex account --show" in warning for warning in warnings))

        local_debug = json.loads(
            (data_root / "global_local_debug.json").read_text()
        )
        self.assertEqual(
            self.validator.validate_global(local_debug),
            ([], []),
        )

        cluster_template = json.loads(
            (data_root / "global_local_cluster_slurm.json").read_text()
        )
        errors, _ = self.validator.validate_global(cluster_template)
        self.assertTrue(any("placeholders" in error for error in errors))

        local_cluster = {
            "context_type": "Local",
            "run_command": "srun lmp -in in.lammps",
            "machine": {
                "batch_type": "Slurm",
                "context_type": "Local",
            },
            "resources": {
                "number_node": 1,
                "custom_flags": ["#SBATCH --partition=compute"],
            },
        }
        self.assertEqual(
            self.validator.validate_global(local_cluster),
            ([], []),
        )

        pbs_cluster = {
            "context_type": "Local",
            "run_command": "mpirun lmp -in in.lammps",
            "machine": {
                "batch_type": "PBS",
                "context_type": "Local",
            },
            "resources": {"number_node": 1},
        }
        self.assertEqual(
            self.validator.validate_global(pbs_cluster),
            ([], []),
        )

        local_cluster["run_command"] = "<calculator command>"
        local_cluster["resources"]["custom_flags"] = [
            "#SBATCH --partition=<partition>"
        ]
        errors, _ = self.validator.validate_global(local_cluster)
        self.assertTrue(any("placeholders" in error for error in errors))

    def test_validate_global_openapi_sandbox(self):
        config = {
            "dflow_host": "https://lbg-workflow-dflow.dp.tech",
            "k8s_api_server": "https://lbg-workflow-dflow.dp.tech",
            "dflow_config": {
                "host": "https://lbg-workflow-dflow.dp.tech",
                "k8s_api_server": "https://lbg-workflow-dflow.dp.tech",
                "namespace": "dflow",
                "token": "",
            },
            "batch_type": "OpenAPI",
            "context_type": "OpenAPI",
            "access_key": "test-key",
            "project_id": 42,
            "machine_type": "c8_m32_1 * NVIDIA 4090",
            "image_address": self.gen.LAMMPS_IMAGE,
            "dispatcher_image": "dispatcher:image",
            "bohrium_config": {
                "access_key": "test-key",
                "project_id": 42,
                "app_key": "agent",
            },
            "lammps_image_name": self.gen.LAMMPS_IMAGE,
            "lammps_run_command": "lmp -in in.lammps",
        }
        self.assertEqual(self.validator.validate_global(config), ([], []))
        config["bohrium_config"]["project_id"] = "42"
        errors, _ = self.validator.validate_global(config)
        self.assertTrue(any("bohrium_config.project_id" in e for e in errors))

    def test_validate_melting_point_property(self):
        prop = self.gen.PROPERTY_DEFAULTS["melting_point"]
        self.assertEqual(
            self.validator.validate_properties([prop], "deepmd"),
            ([], []),
        )
        errors, _ = self.validator.validate_properties([prop], "vasp")
        self.assertTrue(any("LAMMPS-only" in e for e in errors))

    def test_validate_current_global_requires_integer_matching_project_ids(self):
        config = {
            "dflow_host": "https://workflows.deepmodeling.com",
            "batch_type": "Bohrium",
            "context_type": "Bohrium",
            "program_id": "42",
            "bohrium_config": {
                "ticket": "ticket",
                "project_id": "42",
            },
            "machine": {
                "remote_profile": {"program_id": "42"},
            },
            "scass_type": "c8_m31_1 * NVIDIA T4",
            "lammps_run_command": "lmp -in in.lammps",
        }
        errors, warnings = self.validator.validate_global(config)
        self.assertTrue(any("'program_id'" in error for error in errors))
        self.assertTrue(
            any("'bohrium_config.project_id'" in error for error in errors)
        )
        self.assertTrue(
            any("'machine.remote_profile.program_id'" in error for error in errors)
        )
        self.assertFalse(warnings)

        config["program_id"] = 42
        config["bohrium_config"]["project_id"] = 43
        config["machine"]["remote_profile"]["program_id"] = 42
        errors, _ = self.validator.validate_global(config)
        self.assertTrue(any("must match" in error for error in errors))

        config["bohrium_config"]["project_id"] = 42
        self.assertEqual(self.validator.validate_global(config), ([], []))

    def test_validate_vasp_run_command_rejects_bare_mpirun(self):
        errors, _ = self.validator.validate_vasp_run_command(
            {"vasp_run_command": "mpirun -n 16 vasp_std"}
        )
        self.assertTrue(any("Bohrium template" in e for e in errors))

        errors, warnings = self.validator.validate_vasp_run_command(
            {
                "vasp_run_command": (
                    'bash -c "source /opt/intel/oneapi/setvars.sh && '
                    "ulimit -s unlimited && "
                    'mpirun -n 32 /opt/vasp.5.4.4/bin/vasp_std"'
                )
            }
        )
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    def test_validate_dft_kspacing_requires_vasp_kspacing(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            incar = base / "INCAR"
            incar.write_text("ENCUT = 400\n", encoding="utf-8")
            errors, _ = self.validator.validate_dft_kspacing(
                {},
                base,
                {"type": "vasp", "incar": "INCAR"},
            )
            self.assertTrue(any("KSPACING" in e for e in errors))

            incar.write_text(
                "ENCUT = 400\nKSPACING = 0.15\nKGAMMA = True\n",
                encoding="utf-8",
            )
            errors, warnings = self.validator.validate_dft_kspacing(
                {},
                base,
                {"type": "vasp", "incar": "INCAR"},
            )
            self.assertEqual(errors, [])
            self.assertEqual(warnings, [])

    def test_validate_gamma_guard_settings(self):
        base = {
            "type": "gamma",
            "plane_miller": [1, 1, 1],
            "slip_direction": [-1, 1, 0],
        }
        errors, warnings = self.validator.validate_properties(
            [base], "lammps"
        )
        self.assertFalse(errors)
        self.assertTrue(any("min_slab_height" in item for item in warnings))
        self.assertTrue(any("max_atoms" in item for item in warnings))

        invalid_cases = (
            ({"supercell_size": [0, 1, 2]}, "supercell_size[0]"),
            ({"supercell_size": [1, 1.5, 2]}, "supercell_size[1]"),
            ({"supercell_size": [1, 1, float("inf")]}, "positive finite"),
            ({"min_slab_height": 0}, "min_slab_height"),
            ({"max_atoms": True}, "max_atoms"),
            ({"min_distance": -0.1}, "min_distance"),
            ({"n_steps": 0}, "n_steps"),
        )
        for update, message in invalid_cases:
            with self.subTest(update=update):
                prop = dict(base)
                prop.update(update)
                errors, _ = self.validator.validate_properties(
                    [prop], "lammps"
                )
                self.assertTrue(
                    any(message in item for item in errors), errors
                )

    def test_gamma_preflight_reports_slab_and_enforces_atom_limit(self):
        from pymatgen.core import Lattice, Structure

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conf = root / "conf"
            conf.mkdir()
            structure = Structure(
                Lattice.cubic(4.0),
                ["Al", "Al", "Al", "Al"],
                [
                    [0, 0, 0],
                    [0, 0.5, 0.5],
                    [0.5, 0, 0.5],
                    [0.5, 0.5, 0],
                ],
            )
            structure.to(filename=conf / "POSCAR", fmt="poscar")
            (root / "INCAR").write_text(
                "KSPACING = 100\nKGAMMA = .TRUE.\n"
                "NCORE = 2\nKPAR = 1\n",
                encoding="utf-8",
            )
            prop = {
                "type": "gamma",
                "plane_miller": [1, 1, 1],
                "slip_direction": [-1, 1, 0],
                "supercell_size": [1, 1, 2],
                "min_slab_height": 1.0,
                "max_atoms": 100,
                "min_distance": 0.1,
                "n_steps": 2,
            }
            param = {
                "structures": ["conf"],
                "interaction": {"type": "vasp", "incar": "INCAR"},
                "properties": [prop],
            }
            reports, errors, warnings = (
                self.validator.preflight_gamma_structures(param, root)
            )
            self.assertFalse(errors)
            self.assertFalse(warnings)
            self.assertEqual(len(reports), 1)
            self.assertEqual(reports[0]["parent_atom_count"], 4)
            self.assertLessEqual(reports[0]["atom_count"], 100)
            self.assertGreaterEqual(reports[0]["slab_height"], 1.0)
            self.assertEqual(reports[0]["expected_task_count"], 3)
            self.assertEqual(
                reports[0]["kpoints"],
                {"style": "Gamma", "grid": [1, 1, 1]},
            )

            param["properties"][0]["max_atoms"] = 1
            reports, errors, _ = self.validator.preflight_gamma_structures(
                param, root
            )
            self.assertFalse(reports)
            self.assertTrue(any("exceeding max_atoms=1" in item for item in errors))

    def test_validate_vasp_parallel_and_vasp_gam_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            incar = root / "INCAR"
            command = (
                'bash -c "source /opt/intel/oneapi/setvars.sh && '
                "ulimit -s unlimited && mpirun -n 8 "
                '/opt/vasp.5.4.4/bin/vasp_gam"'
            )
            global_config = {
                "vasp_run_command": command,
                "scass_type": "c8_m16_cpu",
            }
            param = {
                "interaction": {"type": "vasp", "incar": "INCAR"},
                "properties": [{"type": "gamma"}],
            }
            gamma_report = {
                "label": "synthetic Gamma slab",
                "kpoints": {"style": "Gamma", "grid": [1, 1, 1]},
            }

            incar.write_text(
                "NCORE = 2\nKPAR = 1\n", encoding="utf-8"
            )
            errors, warnings = self.validator.validate_vasp_parallel_settings(
                param, global_config, root, [gamma_report]
            )
            self.assertEqual(errors, [])
            self.assertEqual(warnings, [])

            report = dict(gamma_report)
            report["kpoints"] = {"style": "Gamma", "grid": [2, 1, 1]}
            errors, _ = self.validator.validate_vasp_parallel_settings(
                param, global_config, root, [report]
            )
            self.assertEqual(errors, [])

            incar.write_text(
                "NCORE = 3\nKPAR = 2\n", encoding="utf-8"
            )
            errors, _ = self.validator.validate_vasp_parallel_settings(
                param, global_config, root, [gamma_report]
            )
            self.assertTrue(any("require KPAR=1" in item for item in errors))
            self.assertTrue(any("NCORE=3" in item for item in errors))

            global_config["scass_type"] = "c4_m8_cpu"
            errors, _ = self.validator.validate_vasp_parallel_settings(
                param, global_config, root, [gamma_report]
            )
            self.assertTrue(any("must match Bohrium CPU" in item for item in errors))

            global_config["scass_type"] = "c8_m16_cpu"
            incar.write_text("KPAR = 1\n", encoding="utf-8")
            errors, warnings = self.validator.validate_vasp_parallel_settings(
                param, global_config, root, [gamma_report]
            )
            self.assertFalse(errors)
            self.assertTrue(any("NCORE is not set" in item for item in warnings))

            incar.write_text(
                "NCORE = 2\nNPAR = 4\nKPAR = 1\n", encoding="utf-8"
            )
            errors, _ = self.validator.validate_vasp_parallel_settings(
                param, global_config, root, [gamma_report]
            )
            self.assertTrue(any("same time" in item for item in errors))

    def test_validate_vasp_gam_selection_uses_sampling_not_property_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conf = root / "confs" / "alloy"
            conf.mkdir(parents=True)
            (conf / "POSCAR").write_text(
                "TiV\n"
                "1.0\n"
                "10 0 0\n"
                "0 10 0\n"
                "0 0 10\n"
                "Ti V\n"
                "1 1\n"
                "Direct\n"
                "0 0 0\n"
                "0.5 0.5 0.5\n",
                encoding="utf-8",
            )
            incar = root / "INCAR"
            incar.write_text(
                "KSPACING = 1.0\nKGAMMA = True\nNCORE = 2\nKPAR = 2\n",
                encoding="utf-8",
            )
            param = {
                "structures": ["confs/alloy"],
                "interaction": {"type": "vasp", "incar": "INCAR"},
                "relaxation": {"req_calc": False},
                "properties": [
                    {
                        "type": "finite_t_latt",
                        "supercell_size": [1, 1, 1],
                    }
                ],
            }
            global_config = {
                "vasp_run_command": (
                    'bash -c "source /opt/intel/oneapi/setvars.sh && '
                    "ulimit -s unlimited && mpirun -n 8 "
                    '/opt/vasp.5.4.4/bin/vasp_std"'
                ),
                "scass_type": "c8_m16_cpu",
            }
            errors, _ = self.validator.validate_vasp_parallel_settings(
                param, global_config, root, []
            )
            self.assertTrue(any("require KPAR=1" in item for item in errors))
            self.assertFalse(
                any("non-Gamma properties" in item for item in errors)
            )

            incar.write_text(
                "KSPACING = 1.0\nKGAMMA = True\nNCORE = 2\nKPAR = 1\n",
                encoding="utf-8",
            )
            errors, warnings = (
                self.validator.validate_vasp_parallel_settings(
                    param, global_config, root, []
                )
            )
            self.assertEqual(errors, [])
            self.assertEqual(warnings, [])

    def test_validate_interaction(self):
        cases = (
            ({}, "Missing 'interaction.type'"),
            ({"type": "invalid"}, "Unknown interaction type"),
            ({"type": "deepmd"}, "requires 'model'"),
            ({"type": "deepmd", "model": "m"}, "requires 'type_map'"),
            ({"type": "abacus"}, "requires 'potcars'"),
            ({"type": "vasp"}, "requires 'potcars'"),
        )
        for interaction, message in cases:
            with self.subTest(interaction=interaction):
                errors, _ = self.validator.validate_interaction(interaction)
                self.assertTrue(any(message in error for error in errors))

        errors, warnings = self.validator.validate_interaction(
            {"type": "abacus", "potcars": {}}
        )
        self.assertFalse(errors)
        self.assertTrue(any("orb_files" in warning for warning in warnings))
        errors, warnings = self.validator.validate_interaction(
            {"type": "vasp", "potcars": {}}
        )
        self.assertTrue(any("potcar_prefix" in error for error in errors))
        self.assertFalse(warnings)

    def test_validate_property_required_fields(self):
        cases = (
            ({}, "missing 'type'"),
            ({"type": "bad"}, "unknown property"),
            ({"type": "eos"}, "EOS requires"),
            ({"type": "cohesive"}, "cohesive requires"),
            ({"type": "surface"}, "surface requires"),
            ({"type": "decohesive"}, "decohesive requires"),
            ({"type": "gruneisen"}, "volume_strains"),
            (
                {
                    "type": "finite_t_elastic",
                    "cal_setting": {"method": "other"},
                },
                "only supports",
            ),
        )
        for prop, message in cases:
            with self.subTest(prop=prop):
                errors, _ = self.validator.validate_properties(
                    [prop], "lammps"
                )
                self.assertTrue(any(message in error for error in errors))

        errors, _ = self.validator.validate_properties(
            [{"type": "finite_t_elastic"}], "vasp"
        )
        self.assertTrue(any("LAMMPS-only" in error for error in errors))
    def test_validate_gruneisen_and_gamma_geometry(self):
        errors, _ = self.validator.validate_properties(
            [{
                "type": "gruneisen",
                "volume_strains": [-0.01, 0.01],
                "temperatures": [300],
            }],
            "lammps",
        )
        self.assertTrue(any("include 0.0" in error for error in errors))
        self.assertTrue(any("≥3" in error for error in errors))

        errors, _ = self.validator.validate_properties(
            [{
                "type": "gruneisen",
                "volume_strains": [-0.01, 0.0, 0.01],
                "temperatures": [300],
                "MESH": [20, 0, 20],
            }],
            "lammps",
        )
        self.assertTrue(any("MESH" in error for error in errors))

        for prop, message in (
            ({"type": "gamma"}, "requires plane_miller"),
            ({
                "type": "gamma",
                "plane_miller": [1, 1, 1],
                "slip_direction": [1, 0],
            }, "dimensions differ"),
            ({
                "type": "gamma",
                "plane_miller": [1, 1, 1],
                "slip_direction": [1, 0, 0],
            }, "must lie"),
        ):
            with self.subTest(prop=prop):
                errors, _ = self.validator.validate_properties(
                    [prop], "lammps"
                )
                self.assertTrue(any(message in error for error in errors))

    def test_validate_gamma_surface_steps(self):
        errors, _ = self.validator.validate_properties(
            [{
                "type": "gamma_surface",
                "plane_miller": [1, 1, 1],
                "slip_direction": [-1, 1, 0],
                "n_steps_x": True,
                "n_steps_y": 0,
            }],
            "lammps",
        )
        self.assertTrue(any("n_steps_x" in error for error in errors))
        self.assertTrue(any("n_steps_y" in error for error in errors))

    def test_validate_structures(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "POSCAR").write_text("structure", encoding="utf-8")
            self.assertEqual(
                self.validator.validate_structures(
                    {"structures": ["POSCAR"]}, root
                ),
                ([], []),
            )
            errors, warnings = self.validator.validate_structures({}, root)
            self.assertTrue(errors)
            self.assertFalse(warnings)
            errors, warnings = self.validator.validate_structures(
                {"structures": ["missing"]}, root
            )
            self.assertFalse(errors)
            self.assertTrue(warnings)

    def _run_main(self, argv):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(sys, "argv", argv), patch(
            "sys.stdout", stdout
        ), patch("sys.stderr", stderr):
            try:
                self.validator.main()
            except SystemExit as exc:
                return exc.code, stdout.getvalue(), stderr.getvalue()
        return 0, stdout.getvalue(), stderr.getvalue()

    def test_main_success_and_strict_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "POSCAR").write_text("structure", encoding="utf-8")
            param = root / "param.json"
            global_json = root / "global.json"
            param.write_text(json.dumps({
                "structures": ["POSCAR"],
                "interaction": {
                    "type": "deepmd",
                    "model": "model.pb",
                    "type_map": "auto",
                },
                "properties": [{
                    "type": "elastic",
                }],
            }))
            global_json.write_text(json.dumps({
                "machine": {"batch_type": "Bohrium"},
                "resources": {},
                "run_command": "lmp",
            }))
            code, stdout, stderr = self._run_main([
                "validate_inputs.py", "--param", str(param),
                "--global", str(global_json),
            ])
            self.assertEqual(code, 0)
            self.assertIn("Validation PASSED", stdout)
            self.assertEqual(stderr, "")

            param_data = json.loads(param.read_text())
            param_data["structures"] = ["missing"]
            param.write_text(json.dumps(param_data))
            code, _, stderr = self._run_main([
                "validate_inputs.py", "--param", str(param), "--strict",
            ])
            self.assertEqual(code, 1)
            self.assertIn("strict mode", stderr)

    def test_main_reports_missing_and_invalid_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            missing = root / "missing.json"
            code, _, stderr = self._run_main([
                "validate_inputs.py", "--param", str(missing)
            ])
            self.assertEqual(code, 1)
            self.assertIn("param.json not found", stderr)

            param = root / "param.json"
            param.write_text(json.dumps({}))
            code, _, stderr = self._run_main([
                "validate_inputs.py", "--param", str(param),
                "--global", str(missing),
            ])
            self.assertEqual(code, 1)
            self.assertIn("global.json not found", stderr)
            self.assertIn("Missing 'interaction'", stderr)


class TestValidateComboAdditionalPaths(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.combo = _load_script("validate_apex_combo.py")

    def test_image_normalization(self):
        self.assertEqual(
            self.combo.normalize_image(
                "registry.dp.tech/dptech/deepmd-kit:3.1.3"
            ),
            "deepmd-kit:3.1.3",
        )
        self.assertEqual(self.combo.full_image(""), "")
        self.assertEqual(
            self.combo.full_image("deepmd-kit:3.1.3"),
            "registry.dp.tech/dptech/deepmd-kit:3.1.3",
        )
        full = "ghcr.io/example/image:tag"
        self.assertEqual(self.combo.full_image(full), full)

    def test_triclinic_and_multiple_block_reasons(self):
        ok, errors = self.combo.check_combo(
            "deepmd-kit:3.1.1",
            "c12_m46_1 * NVIDIA T4",
            triclinic=True,
        )
        self.assertFalse(ok)
        self.assertGreaterEqual(len(errors), 3)
        self.assertTrue(any("triclinic" in error for error in errors))

    def test_recommend_all_backends_and_unknown(self):
        self.assertEqual(
            self.combo.recommend("abacus", "gpu")["prefer"], "cpu"
        )
        self.assertIn(
            "user-provided VASP", self.combo.recommend("vasp")["image"]
        )
        with self.assertRaisesRegex(ValueError, "Unknown backend"):
            self.combo.recommend("unknown")

    def test_list_and_recommend_cli_text_and_json(self):
        for argv, expected in (
            (["list-combos"], "Blocked images:"),
            (["list-combos", "--format", "json"], '"recommended"'),
            (["recommend", "--backend", "abacus"], "image="),
            (
                ["recommend", "--backend", "vasp", "--format", "json"],
                '"backend": "vasp"',
            ),
        ):
            with self.subTest(argv=argv):
                stdout = io.StringIO()
                with patch("sys.stdout", stdout):
                    self.assertEqual(self.combo.main(argv), 0)
                self.assertIn(expected, stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
