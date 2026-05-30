import base64
import io
import json
import os
import tempfile
import unittest
import zipfile
from unittest import mock

import apex.gui as gui_module
from apex.gui import (
    DEFAULT_REPORT_PORT,
    BLOCKED_INLINE_COMMANDS,
    RETRIEVE_RUNNING_MESSAGE,
    _advanced_report_args,
    _autodetect_interaction_rows,
    _build_param_payload,
    _build_submit_shell_command,
    _cleanup_reset_logs,
    _ensure_default_interaction_files,
    _extract_property_types,
    _extract_potcar_rows,
    _find_param_fallback_file,
    _is_retrieve_feedback,
    _is_param_fallback_filename,
    _parse_extra_elements,
    _interaction_editor_label,
    _interaction_table_columns_for_profile,
    _interaction_table_rows_from_template,
    _interaction_type_options_for_profile,
    _list_structure_path_options,
    _list_workdir_file_options,
    _load_account_state,
    _load_profile_param_template,
    _param_controls_from_text,
    _patch_param_payload,
    _parse_retrieve_progress_from_log,
    _parse_submit_payloads,
    _read_latest_workflow_id,
    _render_account_summary,
    _finalize_retrieve_status,
    _retrieve_state_is_active,
    _run_finalize_pipeline,
    _save_uploaded_files,
    _save_account_overwrite,
    _strip_parenthetical_suffix,
    _summarize_conf_progress,
    _summarize_step_progress,
    _workflow_progress_percent,
)


class TestGuiSubmitBuilder(unittest.TestCase):
    def test_build_param_payload_sets_interaction_and_req_calc(self):
        payload = _build_param_payload(
            profile="lammps",
            selected_structures=["confs/std-fcc"],
            with_relax=False,
            selected_properties=["elastic", "eos"],
            interaction_type="eam_alloy",
            interaction_model="my_model.eam",
            element_slots=["Al", "Ni", "", ""],
        )

        self.assertNotIn("relaxation", payload)
        self.assertEqual(payload["structures"], ["confs/std-fcc"])
        self.assertEqual(payload["interaction"]["type"], "eam_alloy")
        self.assertEqual(payload["interaction"]["model"], "my_model.eam")
        self.assertEqual(payload["interaction"]["type_map"], "auto")

        req_calc = {
            item["type"]: item.get("req_calc")
            for item in payload.get("properties", [])
            if isinstance(item, dict) and "type" in item
        }
        self.assertTrue(req_calc.get("elastic"))
        self.assertTrue(req_calc.get("eos"))
        if "vacancy" in req_calc:
            self.assertFalse(req_calc["vacancy"])

    def test_build_param_payload_default_element_and_model(self):
        payload = _build_param_payload(
            profile="lammps",
            selected_structures=["confs/std-bcc"],
            with_relax=True,
            selected_properties=[],
            interaction_type="deepmd",
            interaction_model="",
            element_slots=["", "", "", ""],
        )

        self.assertIn("relaxation", payload)
        self.assertEqual(payload["interaction"]["type"], "deepmd")
        self.assertNotIn("model", payload["interaction"])
        self.assertEqual(payload["interaction"]["type_map"], "auto")

    def test_patch_param_payload_updates_structures_without_resetting_manual_edits(self):
        template = _load_profile_param_template("lammps")
        current = {
            "structures": ["old/conf"],
            "relaxation": {"cal_setting": {"custom_relax": True}},
            "properties": [{"type": "eos", "req_calc": True, "custom": 123}],
            "interaction": {"type": "eam_alloy", "model": "manual.eam", "type_map": "auto"},
            "manual_top_level": {"keep": True},
        }

        payload = _patch_param_payload(
            current_text=json.dumps(current),
            triggered_id="submit-structures",
            profile="lammps",
            template=template,
            relax_check=["relax"],
            properties_check=["eos"],
            structures_value=["RSS_HEA/conf_*"],
            interaction_type="eam_alloy",
            interaction_model="new.eam",
            interaction_incar="",
            interaction_rows=[],
        )

        self.assertEqual(payload["structures"], ["RSS_HEA/conf_*"])
        self.assertEqual(payload["properties"], current["properties"])
        self.assertEqual(payload["interaction"], current["interaction"])
        self.assertEqual(payload["manual_top_level"], {"keep": True})

    def test_patch_param_payload_updates_properties_without_resetting_interaction(self):
        template = _load_profile_param_template("lammps")
        current = {
            "structures": ["confs/std-bcc"],
            "properties": [{"type": "eos", "req_calc": False, "custom": 123}],
            "interaction": {"type": "eam_alloy", "model": "manual.eam", "type_map": "auto"},
        }

        payload = _patch_param_payload(
            current_text=json.dumps(current),
            triggered_id="submit-properties-check",
            profile="lammps",
            template=template,
            relax_check=[],
            properties_check=["eos"],
            structures_value=["ignored/conf"],
            interaction_type="deepmd",
            interaction_model="ignored.pb",
            interaction_incar="",
            interaction_rows=[],
        )

        self.assertEqual(payload["structures"], ["confs/std-bcc"])
        self.assertEqual(payload["interaction"], current["interaction"])
        self.assertEqual(payload["properties"][0]["type"], "eos")
        self.assertTrue(payload["properties"][0]["req_calc"])
        self.assertEqual(payload["properties"][0]["custom"], 123)

    def test_patch_param_payload_updates_interaction_without_resetting_other_blocks(self):
        template = _load_profile_param_template("lammps")
        current = {
            "structures": ["confs/std-bcc"],
            "relaxation": {"cal_setting": {"custom_relax": True}},
            "properties": [{"type": "elastic", "req_calc": True, "custom": "keep"}],
            "interaction": {"type": "eam_alloy", "model": "old.eam", "type_map": "auto"},
        }

        payload = _patch_param_payload(
            current_text=json.dumps(current),
            triggered_id="submit-interaction-model",
            profile="lammps",
            template=template,
            relax_check=["relax"],
            properties_check=["eos"],
            structures_value=["ignored/conf"],
            interaction_type="deepmd",
            interaction_model="model.pb",
            interaction_incar="",
            interaction_rows=[],
        )

        self.assertEqual(payload["structures"], current["structures"])
        self.assertEqual(payload["relaxation"], current["relaxation"])
        self.assertEqual(payload["properties"], current["properties"])
        self.assertEqual(payload["interaction"]["type"], "deepmd")
        self.assertEqual(payload["interaction"]["model"], "model.pb")
        self.assertEqual(payload["interaction"]["type_map"], "auto")

    def test_parse_extra_elements(self):
        parsed = _parse_extra_elements("Cu, Ni Fe;Cr\nMn")
        self.assertEqual(parsed, ["Cu", "Ni", "Fe", "Cr", "Mn"])

    def test_list_workdir_file_options_recurses_and_keeps_current(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "models"), exist_ok=True)
            with open(os.path.join(tmpdir, "models", "Ni.eam.alloy"), "w", encoding="utf-8") as f:
                f.write("eam")
            options = _list_workdir_file_options(tmpdir, "missing.pb")
            values = [item["value"] for item in options]
            self.assertIn("models/Ni.eam.alloy", values)
            self.assertEqual(values[0], "missing.pb")

    def test_gui_submit_command_uses_background_runner_module(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            meta_path = os.path.join(tmpdir, ".apex-submit-group.json")
            shell_cmd, display_cmd = _build_submit_shell_command(meta_path, tmpdir)

        self.assertIn("apex.gui_background submit-group", shell_cmd)
        self.assertIn("apex.gui_background submit-group", display_cmd)
        self.assertIn(".apex-submit-group.json", shell_cmd)
        self.assertIn(".apex-submit.status", shell_cmd)
        self.assertNotIn("python -c", shell_cmd)
        self.assertNotIn("python -c", display_cmd)

    def test_list_structure_path_options_returns_structure_dirs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "confs", "std-bcc"), exist_ok=True)
            with open(os.path.join(tmpdir, "confs", "std-bcc", "POSCAR"), "w", encoding="utf-8") as f:
                f.write("POSCAR")
            options = _list_structure_path_options(tmpdir, ["confs/std-*"])
            values = [item["value"] for item in options]
            self.assertIn("confs/std-bcc", values)
            self.assertEqual(values[0], "confs/std-*")

    def test_list_structure_path_options_adds_numbered_wildcard_group(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            for name in ["conf_001", "conf_002"]:
                conf_dir = os.path.join(tmpdir, "RSS_HEA", name)
                os.makedirs(conf_dir, exist_ok=True)
                with open(os.path.join(conf_dir, "POSCAR"), "w", encoding="utf-8") as f:
                    f.write("POSCAR")
            single_dir = os.path.join(tmpdir, "other", "case_001")
            os.makedirs(single_dir, exist_ok=True)
            with open(os.path.join(single_dir, "POSCAR"), "w", encoding="utf-8") as f:
                f.write("POSCAR")

            options = _list_structure_path_options(tmpdir)
            values = [item["value"] for item in options]

            self.assertIn("RSS_HEA/conf_*", values)
            self.assertIn("RSS_HEA/conf_001", values)
            self.assertIn("RSS_HEA/conf_002", values)
            self.assertNotIn("other/case_*", values)

    def test_build_param_payload_ignores_manual_elements_for_auto_type_map(self):
        payload = _build_param_payload(
            profile="lammps",
            selected_structures=["confs/std-hcp"],
            with_relax=True,
            selected_properties=[],
            interaction_type="eam_alloy",
            interaction_model="x",
            element_slots=["Al", "Ni", "Al", "Cu", "Ni"],
        )
        self.assertEqual(payload["interaction"]["type_map"], "auto")

    def test_profile_templates_have_different_property_options(self):
        lammps = _extract_property_types(_load_profile_param_template("lammps"))
        vasp = _extract_property_types(_load_profile_param_template("vasp"))
        abacus = _extract_property_types(_load_profile_param_template("abacus"))

        self.assertIn("cohesive", lammps)
        self.assertNotIn("cohesive", vasp)
        self.assertIn("phonon", vasp)
        self.assertIn("phonon", abacus)
        self.assertNotIn("phonon", lammps)
        self.assertIn("gamma_surface", lammps)
        self.assertIn("gamma_surface", vasp)
        self.assertIn("gamma_surface", abacus)

    def test_lammps_interaction_types_exclude_vasp_abacus(self):
        options = [item["value"] for item in _interaction_type_options_for_profile("lammps", "meam")]
        self.assertNotIn("vasp", options)
        self.assertNotIn("abacus", options)
        self.assertIn("meam", options)

    def test_profile_template_merges_param_interaction(self):
        vasp_template = _load_profile_param_template("vasp")
        self.assertEqual(vasp_template["interaction"]["type"], "vasp")
        self.assertIn("potcars", vasp_template["interaction"])
        self.assertIn("incar", vasp_template["interaction"])
        pot_rows = _extract_potcar_rows(vasp_template)
        self.assertTrue(pot_rows and "(to be change)" not in pot_rows[0][1])

    def test_abacus_interaction_supports_orb_files(self):
        abacus_template = _load_profile_param_template("abacus")
        rows = _interaction_table_rows_from_template("abacus", abacus_template)
        payload = _build_param_payload(
            profile="abacus",
            selected_structures=["confs/fcc-Al"],
            with_relax=True,
            selected_properties=[],
            interaction_type="abacus",
            interaction_model="",
            element_slots=[],
            interaction_incar="abacus_input/INPUT(defaule value)",
            interaction_rows=rows,
            base_template=abacus_template,
        )
        self.assertIn("input", payload["interaction"])
        self.assertNotIn("incar", payload["interaction"])
        self.assertIn("orb_files", payload["interaction"])
        self.assertIn("potcars", payload["interaction"])

    def test_autodetect_interaction_rows_for_vasp_uses_poscar_order_and_suffix(self):
        vasp_template = _load_profile_param_template("vasp")
        poscar_text = "Test\n1.0\n1 0 0\n0 1 0\n0 0 1\nMo Al\n1 1\nDirect\n0 0 0\n0.5 0.5 0.5\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "confs", "std-bcc"), exist_ok=True)
            os.makedirs(os.path.join(tmpdir, "vasp_input"), exist_ok=True)
            with open(os.path.join(tmpdir, "confs", "std-bcc", "POSCAR"), "w", encoding="utf-8") as f:
                f.write(poscar_text)
            with open(os.path.join(tmpdir, "vasp_input", "POTCAR.Mo"), "w", encoding="utf-8") as f:
                f.write("Mo")
            rows = _autodetect_interaction_rows("vasp", tmpdir, ["confs/std-bcc"], vasp_template)
            self.assertEqual(rows[0]["element"], "Mo")
            self.assertEqual(rows[0]["potcar"], "POTCAR.Mo")
            self.assertEqual(rows[1]["element"], "Al")
            self.assertIn("请提交对应元素的POTCAR", rows[1]["potcar"])

    def test_autodetect_interaction_rows_for_abacus_uses_prefix(self):
        abacus_template = _load_profile_param_template("abacus")
        poscar_text = "Test\n1.0\n1 0 0\n0 1 0\n0 0 1\nAl H\n1 1\nDirect\n0 0 0\n0.5 0.5 0.5\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "confs", "std-bcc"), exist_ok=True)
            os.makedirs(os.path.join(tmpdir, "abacus_input"), exist_ok=True)
            with open(os.path.join(tmpdir, "confs", "std-bcc", "POSCAR"), "w", encoding="utf-8") as f:
                f.write(poscar_text)
            with open(os.path.join(tmpdir, "abacus_input", "Al_ONCV_PBE.upf"), "w", encoding="utf-8") as f:
                f.write("Al")
            with open(os.path.join(tmpdir, "abacus_input", "Al_gga_9au.orb"), "w", encoding="utf-8") as f:
                f.write("Al")
            rows = _autodetect_interaction_rows("abacus", tmpdir, ["confs/std-bcc"], abacus_template)
            self.assertEqual(rows[0]["element"], "Al")
            self.assertEqual(rows[0]["potcar"], "Al_ONCV_PBE.upf")
            self.assertEqual(rows[0]["orb_file"], "Al_gga_9au.orb")
            self.assertEqual(rows[1]["element"], "H")
            self.assertIn("请提交对应元素的POTCAR", rows[1]["potcar"])
            self.assertIn("请提交对应元素的ORB", rows[1]["orb_file"])

    def test_interaction_table_columns_profile_specific(self):
        lammps_cols = _interaction_table_columns_for_profile("lammps")
        abacus_cols = _interaction_table_columns_for_profile("abacus")
        self.assertEqual(len(lammps_cols), 2)
        self.assertEqual(len(abacus_cols), 3)

    def test_strip_parenthetical_suffix(self):
        cleaned = _strip_parenthetical_suffix("POTCAR.Mo (to be change)")
        self.assertEqual(cleaned, "POTCAR.Mo")

    def test_ensure_default_interaction_files_for_vasp(self):
        payload = {
            "interaction": {
                "type": "vasp",
                "incar": "vasp_input/INCAR (use default value)",
                "potcars": {"Mo": "POTCAR.Mo (to be change)"},
            }
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                created = _ensure_default_interaction_files("vasp", payload)
                self.assertIn("vasp_input/INCAR", created)
                self.assertTrue(os.path.isfile("vasp_input/INCAR"))
            finally:
                os.chdir(cwd)

    def test_ensure_default_interaction_files_writes_editor_content(self):
        payload = {
            "interaction": {
                "type": "abacus",
                "input": "abacus_input/INPUT(defaule value)",
            }
        }
        custom_content = "INPUT_PARAMETERS\ncustom_key custom_value\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                created = _ensure_default_interaction_files("abacus", payload, incar_content=custom_content)
                self.assertIn("abacus_input/INPUT", created)
                with open("abacus_input/INPUT", "r", encoding="utf-8") as f:
                    content = f.read()
                self.assertEqual(content, custom_content)
            finally:
                os.chdir(cwd)

    def test_interaction_editor_label(self):
        self.assertEqual(_interaction_editor_label("vasp"), "INCAR 编辑区")
        self.assertEqual(_interaction_editor_label("abacus"), "INPUT 编辑区")

    def test_account_overwrite_hides_password_in_summary(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            account_path = os.path.join(tmpdir, "account.json")
            feedback, account_state = _save_account_overwrite(
                email="user@example.com",
                password="secret-password",
                program_id_text="1234",
                account_path=account_path,
            )
            self.assertTrue(feedback["ok"])
            self.assertEqual(account_state["email"], "user@example.com")
            self.assertEqual(account_state["program_id"], "1234")
            self.assertTrue(account_state["password_set"])

            summary = _render_account_summary(account_state)
            self.assertIn("Email: user@example.com", summary)
            self.assertIn("Program ID: 1234", summary)
            self.assertIn("Password: 已设置", summary)
            self.assertNotIn("secret-password", summary)

            with open(account_path, "r", encoding="utf-8") as f:
                on_disk = json.load(f)
            self.assertEqual(on_disk["password"], "secret-password")

    def test_account_overwrite_requires_integer_program_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            account_path = os.path.join(tmpdir, "account.json")
            feedback, account_state = _save_account_overwrite(
                email="user@example.com",
                password="",
                program_id_text="12ab",
                account_path=account_path,
            )
            self.assertFalse(feedback["ok"])
            self.assertIn("program_id", feedback["message"])
            self.assertEqual(account_state["email"], "")
            self.assertEqual(account_state["program_id"], "")

    def test_load_account_state_from_missing_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            account_path = os.path.join(tmpdir, "account.json")
            state = _load_account_state(account_path)
            self.assertEqual(state["email"], "")
            self.assertEqual(state["program_id"], "")
            self.assertFalse(state["password_set"])

    def test_read_latest_workflow_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, ".workflow.log"), "w", encoding="utf-8") as f:
                f.write("wf-old\tsubmit\t2026-01-01T00:00:00\t/tmp/old\n")
                f.write("wf-new\tretrieve\t2026-01-01T00:00:01\t/tmp/new\twf-uid-new\n")
            self.assertEqual(_read_latest_workflow_id(tmpdir), "wf-new")

    def test_advanced_report_is_allowed_and_gets_separate_port(self):
        self.assertNotIn("report", BLOCKED_INLINE_COMMANDS)
        args = _advanced_report_args(["report", "-c", "global.json", "-w", "."])

        self.assertIn("--no-browser", args)
        self.assertEqual(args[-2:], ["--port", str(DEFAULT_REPORT_PORT)])

    def test_advanced_report_keeps_explicit_port(self):
        args = _advanced_report_args(["report", "-c", "global.json", "--port", "8090"])

        self.assertIn("--no-browser", args)
        self.assertEqual(args.count("--port"), 1)
        self.assertIn("8090", args)

    def test_finalize_pipeline_retrieves_then_runs_local_archive_and_report(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            commands = []

            def fake_run_apex_command(arguments, cwd=None):
                commands.append(arguments)
                self.assertEqual(cwd, tmpdir)
                if arguments[0] == "retrieve":
                    return {"ok": True, "message": "retrieved"}
                return {"ok": False, "message": "unexpected command"}

            def fake_run_archive_and_report_pipeline(workdir, global_file, param_file):
                self.assertEqual(workdir, tmpdir)
                self.assertEqual(global_file, "global.json")
                self.assertEqual(param_file, "param.json")
                return {"ok": True, "message": "Local archive + report completed. all_result.json: test. report started"}

            with mock.patch.object(gui_module, "_run_apex_command", side_effect=fake_run_apex_command), \
                    mock.patch.object(gui_module, "_run_archive_and_report_pipeline", side_effect=fake_run_archive_and_report_pipeline):
                feedback = _run_finalize_pipeline(
                    workdir=tmpdir,
                    workflow_id="wf-new",
                    global_file="global.json",
                )

            self.assertTrue(feedback["ok"])
            self.assertEqual(commands, [["retrieve", "-i", "wf-new", "-w", tmpdir, "-c", "global.json"]])
            self.assertIn("Retrieve + local archive + report completed", feedback["message"])

    def test_finalize_retrieve_status_reports_running_message(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state = {
                "workdir": tmpdir,
                "global_file": "global.json",
                "retrieve": {
                    "status": "running",
                    "workdir": tmpdir,
                    "global_file": "global.json",
                    "status_file": os.path.join(tmpdir, ".apex-retrieve.status"),
                    "log_file": os.path.join(tmpdir, "apex-retrieve.log"),
                },
            }

            value, label, animated, text, next_state, feedback = _finalize_retrieve_status(state)

            self.assertEqual(value, 5)
            self.assertEqual(label, "Retrieving")
            self.assertTrue(animated)
            self.assertEqual(text, RETRIEVE_RUNNING_MESSAGE)
            self.assertEqual(next_state["status"], "running")
            self.assertIs(feedback, gui_module.dash.no_update)

    def test_retrieve_state_is_active_until_status_file_has_result(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            status_file = os.path.join(tmpdir, ".apex-retrieve.status")
            state = {
                "status": "running",
                "status_file": status_file,
            }

            self.assertTrue(_retrieve_state_is_active(state))
            with open(status_file, "w", encoding="utf-8") as f:
                f.write("")
            self.assertTrue(_retrieve_state_is_active(state))
            with open(status_file, "w", encoding="utf-8") as f:
                f.write("0")
            self.assertFalse(_retrieve_state_is_active(state))

    def test_finalize_retrieve_status_auto_detects_log_progress(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "apex-retrieve.log")
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(
                    "Retrieving 971 workflow results guitest-joint-pzcq4 to /tmp/work\n"
                    "Retrieving result 123/971: propertycal-rss-hea-conf-084-eos-00\n"
                )
            state = {
                "workdir": tmpdir,
                "workflow_id": "guitest-joint-pzcq4",
            }

            value, label, animated, text, next_state, feedback = _finalize_retrieve_status(state)

            self.assertEqual(value, 13)
            self.assertEqual(label, "13%")
            self.assertTrue(animated)
            self.assertIn("123/971", text)
            self.assertEqual(next_state, state)
            self.assertIs(feedback, gui_module.dash.no_update)

    def test_finalize_retrieve_status_auto_detects_long_log_progress(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "apex-retrieve.log")
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("Retrieving 971 workflow results guitest-joint-pzcq4 to /tmp/work\n")
                for index in range(300):
                    f.write(f"noise line {index}\n")
                f.write("Retrieving result 123/971: propertycal-rss-hea-conf-084-eos-00\n")
            state = {
                "workdir": tmpdir,
                "workflow_id": "guitest-joint-pzcq4",
            }

            value, label, animated, text, next_state, feedback = _finalize_retrieve_status(state)

            self.assertEqual(value, 13)
            self.assertEqual(label, "13%")
            self.assertTrue(animated)
            self.assertIn("123/971", text)
            self.assertEqual(next_state, state)
            self.assertIs(feedback, gui_module.dash.no_update)

    def test_finalize_retrieve_status_ignores_mismatched_log_progress(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "apex-retrieve.log")
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(
                    "Retrieving 971 workflow results guitest-joint-pzcq4 to /tmp/work\n"
                    "Retrieving result 123/971: propertycal-rss-hea-conf-084-eos-00\n"
                )
            state = {
                "workdir": tmpdir,
                "workflow_id": "guitest-joint-other",
            }

            value, label, animated, text, next_state, feedback = _finalize_retrieve_status(state)

            self.assertEqual(value, 0)
            self.assertEqual(label, "0%")
            self.assertFalse(animated)
            self.assertEqual(text, "Retrieve 未运行")
            self.assertEqual(next_state, {})
            self.assertIs(feedback, gui_module.dash.no_update)

    def test_finalize_retrieve_status_starts_report_after_success(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            status_file = os.path.join(tmpdir, ".apex-retrieve.status")
            with open(status_file, "w", encoding="utf-8") as f:
                f.write("0")
            state = {
                "workdir": tmpdir,
                "global_file": "global.json",
                "retrieve": {
                    "status": "running",
                    "workdir": tmpdir,
                    "global_file": "global.json",
                    "param_file": "param.json",
                    "pending_report": True,
                    "status_file": status_file,
                    "log_file": os.path.join(tmpdir, "apex-retrieve.log"),
                    "command": "apex retrieve",
                },
            }

            with mock.patch.object(
                gui_module,
                "_run_archive_and_report_pipeline",
                return_value={"ok": True, "message": "Local archive + report completed. all_result.json: test. report started"},
            ):
                value, label, animated, text, next_state, feedback = _finalize_retrieve_status(state)

            self.assertEqual(value, 100)
            self.assertEqual(label, "100%")
            self.assertFalse(animated)
            self.assertEqual(text, ["Retrieve finished; report started."])
            self.assertEqual(next_state.get("status"), "done")
            self.assertEqual(next_state.get("workdir"), tmpdir)
            self.assertEqual(next_state.get("completed_text"), ["Retrieve finished; report started."])
            self.assertIn("Retrieve + local archive + report completed", feedback["message"])

    def test_finalize_retrieve_status_marks_done_without_report_when_not_requested(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            status_file = os.path.join(tmpdir, ".apex-retrieve.status")
            with open(status_file, "w", encoding="utf-8") as f:
                f.write("0")
            state = {
                "workdir": tmpdir,
                "global_file": "global.json",
                "retrieve": {
                    "status": "running",
                    "workdir": tmpdir,
                    "global_file": "global.json",
                    "param_file": "param.json",
                    "pending_report": False,
                    "status_file": status_file,
                    "log_file": os.path.join(tmpdir, "apex-retrieve.log"),
                },
            }

            with mock.patch.object(gui_module, "_run_archive_and_report_pipeline") as mocked_pipeline:
                value, label, animated, text, next_state, feedback = _finalize_retrieve_status(state)

            self.assertEqual(value, 100)
            self.assertEqual(label, "Done")
            self.assertFalse(animated)
            self.assertEqual(text, "Retrieve finished.")
            self.assertEqual(next_state.get("status"), "done")
            self.assertIs(feedback, gui_module.dash.no_update)
            mocked_pipeline.assert_not_called()

    def test_submit_status_file_is_not_treated_as_retrieve_feedback(self):
        self.assertFalse(
            _is_retrieve_feedback(
                {
                    "operation": "submit",
                    "status_file": "/tmp/.apex-submit.status",
                }
            )
        )
        self.assertFalse(_is_retrieve_feedback({"status_file": "/tmp/.apex-submit.status"}))
        self.assertTrue(
            _is_retrieve_feedback(
                {
                    "operation": "retrieve",
                    "status_file": "/tmp/.apex-retrieve.status",
                }
            )
        )

    def test_parse_retrieve_progress_from_log(self):
        progress = _parse_retrieve_progress_from_log(
            "Retrieving 4 workflow results wf-001 to /tmp/work\n"
            "Retrieving result 1/4: relaxcal-conf-001\n"
            "Retrieving result 2/4: propertycal-conf-001-eos-00\n"
        )

        self.assertEqual(progress, (50, "50%", f"{RETRIEVE_RUNNING_MESSAGE} 2/4: propertycal-conf-001-eos-00"))

    def test_parse_submit_payloads_rejects_dot_in_structures(self):
        global_text = json.dumps({})
        param_text = json.dumps({"structures": ["."], "interaction": {"type": "eam_alloy"}})
        _global_payload, _param_payload, feedback = _parse_submit_payloads(global_text, param_text)
        self.assertIn("dflow does not allow '.' in `structures`", feedback["message"])
        self.assertIn("parameter[0].structures[0] = .", feedback["message"])

    def test_cleanup_reset_logs_removes_requested_files_only(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            for name in ["dpdispatcher.log", ".workflow.log", "apex.log", "keep.log"]:
                with open(os.path.join(tmpdir, name), "w", encoding="utf-8") as f:
                    f.write("x")
            removed = _cleanup_reset_logs(tmpdir)
            self.assertEqual(removed, ["dpdispatcher.log", ".workflow.log", "apex.log"])
            self.assertFalse(os.path.exists(os.path.join(tmpdir, "dpdispatcher.log")))
            self.assertFalse(os.path.exists(os.path.join(tmpdir, ".workflow.log")))
            self.assertFalse(os.path.exists(os.path.join(tmpdir, "apex.log")))
            self.assertTrue(os.path.exists(os.path.join(tmpdir, "keep.log")))

    def test_save_uploaded_files_writes_to_workdir(self):
        payload = base64.b64encode(b"MODEL DATA\n").decode("ascii")
        with tempfile.TemporaryDirectory() as tmpdir:
            saved = _save_uploaded_files([f"data:application/octet-stream;base64,{payload}"], ["model.pb"], tmpdir)
            self.assertEqual(saved, ["confs/model.pb"])
            with open(os.path.join(tmpdir, "confs", "model.pb"), "rb") as f:
                self.assertEqual(f.read(), b"MODEL DATA\n")

    def test_save_uploaded_files_to_workdir_root(self):
        payload = base64.b64encode(b"INPUT\n").decode("ascii")
        with tempfile.TemporaryDirectory() as tmpdir:
            saved = _save_uploaded_files(
                [f"data:text/plain;base64,{payload}"],
                ["global.json"],
                tmpdir,
                target_subdir="",
            )
            self.assertEqual(saved, ["global.json"])
            with open(os.path.join(tmpdir, "global.json"), "rb") as f:
                self.assertEqual(f.read(), b"INPUT\n")

    def test_find_param_fallback_prefers_uploaded_param_star_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "param_old.json"), "w", encoding="utf-8") as f:
                f.write('{"old": true}\n')
            with open(os.path.join(tmpdir, "param_joint.json"), "w", encoding="utf-8") as f:
                f.write('{"joint": true}\n')

            param_file, param_text = _find_param_fallback_file(
                tmpdir,
                preferred_files=["param_joint.json"],
            )

            self.assertEqual(param_file, "param_joint.json")
            self.assertEqual(param_text, '{"joint": true}\n')

    def test_find_param_fallback_accepts_any_json_with_param_in_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "param.json"), "w", encoding="utf-8") as f:
                f.write("{}")

            self.assertEqual(_find_param_fallback_file(tmpdir), ("param.json", "{}"))
            self.assertTrue(_is_param_fallback_filename("param.json"))
            self.assertTrue(_is_param_fallback_filename("param_joint.json"))
            self.assertTrue(_is_param_fallback_filename("my_param.json"))
            self.assertFalse(_is_param_fallback_filename("global.json"))

    def test_param_controls_from_text_syncs_form_defaults(self):
        payload = {
            "structures": ["RSS_HEA/conf_*"],
            "relaxation": {"cal_setting": {}},
            "properties": [
                {"type": "eos", "req_calc": True},
                {"type": "elastic", "req_calc": False},
                {"type": "phonon"},
            ],
            "interaction": {
                "type": "deepmd",
                "model": ["frozen_model.pb", "other.pb"],
                "type_map": "auto",
            },
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            controls = _param_controls_from_text(json.dumps(payload), "lammps", tmpdir)

        self.assertEqual(controls["structures_value"], ["RSS_HEA/conf_*"])
        self.assertEqual(controls["relax_value"], ["relax"])
        self.assertEqual(
            [item["value"] for item in controls["property_options"]],
            ["eos", "elastic", "phonon"],
        )
        self.assertEqual(controls["property_value"], ["eos", "phonon"])
        self.assertEqual(controls["interaction_type"], "deepmd")
        self.assertEqual(controls["interaction_model"], "frozen_model.pb, other.pb")

    def test_save_uploaded_files_creates_confs_and_nested_paths(self):
        payload = base64.b64encode(b"POSCAR\n").decode("ascii")
        with tempfile.TemporaryDirectory() as tmpdir:
            saved = _save_uploaded_files(
                [f"data:text/plain;base64,{payload}"],
                ["std-bcc/POSCAR"],
                tmpdir,
            )
            self.assertEqual(saved, ["confs/std-bcc/POSCAR"])
            with open(os.path.join(tmpdir, "confs", "std-bcc", "POSCAR"), "rb") as f:
                self.assertEqual(f.read(), b"POSCAR\n")

    def test_save_uploaded_files_rejects_path_filenames(self):
        payload = base64.b64encode(b"bad").decode("ascii")
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(ValueError):
                _save_uploaded_files(f"data:text/plain;base64,{payload}", "../bad.txt", tmpdir)

    def test_save_uploaded_files_extracts_zip_folder(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, mode="w") as zf:
            zf.writestr("my-folder/POSCAR", "POSCAR\n")
            zf.writestr("my-folder/sub/INPUT", "INPUT\n")
        payload = base64.b64encode(stream.getvalue()).decode("ascii")

        with tempfile.TemporaryDirectory() as tmpdir:
            saved = _save_uploaded_files(
                [f"data:application/zip;base64,{payload}"],
                ["my-folder.zip"],
                tmpdir,
            )
            self.assertIn("confs/my-folder/POSCAR", saved)
            self.assertIn("confs/my-folder/sub/INPUT", saved)
            with open(os.path.join(tmpdir, "confs", "my-folder", "POSCAR"), "rb") as f:
                self.assertEqual(f.read(), b"POSCAR\n")

    def test_save_uploaded_files_rejects_unsafe_zip_member(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, mode="w") as zf:
            zf.writestr("../escape.txt", "bad")
        payload = base64.b64encode(stream.getvalue()).decode("ascii")

        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(ValueError):
                _save_uploaded_files(
                    [f"data:application/zip;base64,{payload}"],
                    ["bad.zip"],
                    tmpdir,
                )

    def test_summarize_step_progress(self):
        steps = [
            {"type": "Pod", "phase": "Pending"},
            {"type": "Pod", "phase": "Running"},
            {"type": "Pod", "phase": "Succeeded"},
            {"type": "Pod", "phase": "Skipped"},
            {"type": "StepGroup", "phase": "Running"},
        ]
        summary = _summarize_step_progress(steps)
        self.assertEqual(summary["total"], 4)
        self.assertEqual(summary["running"], 2)
        self.assertEqual(summary["finished"], 2)

    def test_workflow_progress_percent_from_argo_progress(self):
        self.assertEqual(_workflow_progress_percent("850/1942"), 44)
        self.assertEqual(_workflow_progress_percent("Progress: 1 / 4"), 25)
        self.assertEqual(_workflow_progress_percent(""), 0)

    def test_summarize_conf_progress_groups_property_tasks_by_conf(self):
        steps = [
            {
                "key": "relaxcal-rss-hea-conf-001",
                "phase": "Succeeded",
                "inputs": {"parameters": {"flow_id": {"value": "RSS_HEA/conf_001"}}},
            },
            {
                "key": "propertycal-rss-hea-conf-001-eos-00",
                "phase": "Succeeded",
                "inputs": {
                    "parameters": {
                        "path_to_prop": {"value": "RSS_HEA/conf_001/eos_00"},
                    }
                },
            },
            {
                "key": "relaxcal-rss-hea-conf-002",
                "phase": "Succeeded",
                "inputs": {"parameters": {"flow_id": {"value": "RSS_HEA/conf_002"}}},
            },
            {
                "key": "propertycal-rss-hea-conf-002-eos-00",
                "phase": "Running",
                "inputs": {
                    "parameters": {
                        "path_to_prop": {"value": "RSS_HEA/conf_002/eos_00"},
                    }
                },
            },
        ]

        summary = _summarize_conf_progress(steps)

        self.assertEqual(summary["conf_total"], 2)
        self.assertEqual(summary["conf_finished"], 1)
        self.assertEqual(summary["conf_running"], 1)
        self.assertEqual(summary["conf_failed"], 0)


if __name__ == "__main__":
    unittest.main()
