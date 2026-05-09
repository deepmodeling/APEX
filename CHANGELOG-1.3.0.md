# APEX 1.3.0 Changelog

This release consolidates the 1.2.1 and 1.2.2 changes into a single changelog.

## 1.2.2 update

- Automatic lattice constant estimation from composition-weighted radii
- Automatic supercell generation from composition tolerance
- Supports B2, L12, L10 sublattice-aware systems
- Added RSS workflow support and example documentation
- Added GammaSurface workflow support and example documentation
- Added Finite Temperature Elasticity workflow support and example documentation
- Shape control:
  - near_cubic
  - xy_equal_z_free (for gamma surface / slab)
- Optional maximum atom budget for automatic supercells
- Automatic Break if relaxation step fail
- Retrive failed output file back(main-logs) from the latest failed steps (Prioritize the Relaxmake)

### GUI Updates

- Reworked `apex gui` Submit flow to focus on:
  - profile/template selection
  - `param.json` generation
  - `global.json` generation
  - background submit (`nohup apex submit param.json -c global.json > apex.log 2>&1 &`)
- Submit templates are now merged from profile-specific parts under `apex/default_config/<profile>/`:
  - `param_structure.json`
  - `param_interaction/param_interaction.json`
  - `param_relax.json`
  - `param_props.json`
- Updated interaction editing UX:
  - LAMMPS interaction type options no longer include `vasp` / `abacus`.
  - VASP/ABACUS use table-based interaction rows with dynamic add/remove.
  - ABACUS table includes a third `orb_file` column.
  - `interaction.incar` and INCAR/INPUT editor is shown in the right-side advanced panel for VASP/ABACUS.
- Updated Submit action buttons to `Reset` / `Apply` / `Submit`:
  - `Reset`: regenerate output JSON from current form state.
  - `Apply`: save user-edited contents.
  - `Submit`: run background submit.
- Added submit safety check:
  - if `apex.log` already exists, GUI asks for confirmation before resubmission.
- Added batched GUI submit for dflow calculation limits:
  - GUI now resolves the total matched conf count before submit.
  - when more than 100 confs are matched, GUI splits them into multiple batch parameter files with at most 100 confs per workflow.
  - those batch workflows are submitted in parallel from the GUI background wrapper.
  - GUI writes `.apex-submit-group.json` to record batch metadata and discovered workflow ids.
- Added multi-workflow progress tracking in GUI:
  - `Workflow ID(s)` now accepts multiple ids separated by commas.
  - submit progress aggregates workflow phase, remote progress, step counts, and conf counts across all listed workflows.
  - `Retrieve + Report` can reuse the same multi-id field to retrieve several workflows from one GUI session.
- Added GUI workflow grouping hints for easier identification:
  - each batch workflow gets a grouped GUI name with batch suffix such as `...-batch-001`.
  - each batch workflow is labeled with shared keys such as `apex_gui_group`, `apex_gui_workdir`, and `apex_gui_batch`.
- Added a reusable GUI workflow-id template file:
  - `apex/default_config/gui_submit_group.template.json`
  - users can copy/fill this template and paste its `workflow_ids` into the GUI `Workflow ID(s)` field for manual progress queries.
- Property checkbox behavior is now strict:
  - only checked properties are kept in generated `param.json`.
  - unchecked properties are not emitted.
- Simplified Manage tab:
  - now dedicated to tailing and refreshing `apex.log`.
- Added Account tab in GUI:
  - supports overwrite updates for `email` / `program_id` / `password`.
  - password is never displayed in plaintext (status only).
- Normalized default interaction templates by removing placeholder suffixes such as `(to be change)` from stored values.
- Added GUI developer documentation: `docs/gui_dev.md`.

## 1.2.1 Changelog

Release date: 2026-03-03

### New Features

- Added the `apex account` command to manage Bohrium account settings in one place (default path: `~/.apex/account.json`).
- Added both interactive and non-interactive account setup options:
  - `--email`
  - `--password`
  - `--program-id`
- Added account management utilities:
  - `apex account --show`
  - `apex account --reset`

### Configuration Improvements

- For Bohrium workflows, the following shared settings are auto-injected when they are missing in `-c` JSON:
  - `dflow_host = https://workflows.deepmodeling.com`
  - `k8s_api_server = https://workflows.deepmodeling.com`
  - `batch_type = Bohrium`
  - `context_type = Bohrium`
  - `apex_image_name = registry.dp.tech/dptech/prod-11045/apex-dependency:1.2.0`
- Configuration priority is now:
  1. Values in the `-c` JSON file
  2. Values in `~/.apex/account.json`
  3. Built-in Bohrium defaults

### Examples and Documentation

- Simplified Bohrium `global_bohrium.json` examples to avoid storing credentials in project directories.
- Updated README with `apex account` usage and default-injection behavior.
- Updated all property examples to use `req_calc` instead of `skip` for property on/off control.
- Documented property selection defaults:
  - Property block absent from `properties`: not calculated.
  - Property block present without `req_calc`: calculated by default.
  - Property block with `req_calc: false`: not calculated.

### Internal Architecture Updates (2026-03-16)

- Refactored `apex/core/property/` to a property-first source layout.
- Runtime dispatch remains backend-driven:
  - `factory.make_property_instance(...)` selects the calculator backend from `interaction.type`.
  - Backend registries then resolve the property implementation from `parameters["type"]`.
- Shared property logic now lives in canonical paths such as:
  - `apex/core/property/<PropertyName>/logic.py`
- Backend-bound wrappers now live under each property package:
  - `apex/core/property/<PropertyName>/vasp/`
  - `apex/core/property/<PropertyName>/abacus/`
  - `apex/core/property/<PropertyName>/lammps/`
- Added shared infrastructure for the new layout:
  - `apex/core/property/base.py`
  - `apex/core/property/_interaction_helpers.py`
  - `apex/core/property/_registries.py`
- Updated `apex/core/property/README.md` to document the canonical layout, import rules, and migration conventions for new properties.

### LAMMPS Property Backend Refactor (2026-03-17)

- Expanded the property-first backend model for LAMMPS-backed properties based on:
  - `apex/core/property/LAMMPS_refactor.md`
  - `apex/core/property/FiniteTlatt_refactor.md`
- Moved property-specific LAMMPS input ownership into property-local backends for:
  - `FiniteTlatt`
  - `Phonon`
  - `Gamma`
  - `Elastic`
- Added property-local template/rendering assets under `property/<Prop>/lammps/`, including:
  - `in.lammps`
  - `input.py`
  - `variables.py` where needed
- Removed property-specific template branching from `calculator/lib/lammps_utils.py` where those templates are now owned by property backends.
- Updated `apex/core/calculator/Lammps.py` to dispatch through property-local hooks for:
  - input rendering
  - forward/backward file manifests
  - runtime policy
- Kept `EOS` on generic input generation but moved its non-default task-local runtime policy and transfer-file rules into property-local hooks.
- Introduced a thin generic LAMMPS backend binding helper for properties that still use generic calculator behavior:
  - `Cohesive`
  - `Decohesive`
  - `Interstitial`
  - `Surface`
  - `Vacancy`
- Added structured LAMMPS backend summaries discoverable from the property factory via:
  - `get_lammps_backend_summary(...)`
- Updated the property factory and backend registries so LAMMPS backend modules remain discoverable under the property-first layout.
- Added focused tests for the new LAMMPS backend architecture, including:
  - renderer/dispatch coverage
  - runtime policy and transfer-file behavior
  - backend summary discovery

### Submit Validation Update (2026-03-06)

- Added pre-submit validation for `apex submit` to reject `structures` entries containing `.` (for dflow compatibility).
- The command now fails fast with a clear error message and lists the offending `structures` entries.
- Kept `interaction.model` compatible with `.` in file names/paths (for example, `Al.eam.alloy` is allowed).

### Compatibility

- Backward compatible: if account or shared Bohrium fields are explicitly set in `global_bohrium.json`, those values are still used.
- Backward compatible for property controls: legacy `skip` is still accepted when `req_calc` is not provided.
