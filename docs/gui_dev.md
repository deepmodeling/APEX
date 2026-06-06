# APEX GUI Dev Log

This document serves as a development log for anyone maintaining the `apex gui` frontend (Dash). It records implementation locations, data flows, and conventions for future extensions.

## 1. Code Locations

- Main entry & frontend implementation: `apex/gui.py`
- Account config read/write: `apex/account.py`
- Submit template directories: `apex/default_config/{lammps,vasp,abacus}/`

The current GUI is built with Dash. Both the frontend layout and backend callbacks live in a single Python file (the `ApexGuiApp` class).

## 2. Tab Structure

Four tabs are defined in `ApexGuiApp._build_layout()`:

1. `Submit`
2. `Log`
3. `Advanced`
4. `Account`

Corresponding builder functions:

- `_build_submit_tab`
- `_build_manage_tab`
- `_build_advanced_tab`
- `_build_account_tab`

## 3. Submit Page (Core)

### 3.1 Templates & Data Sources

The `Submit` page does not rely on hand-written JSON. Instead, it assembles parameters from profile templates:

- `param_structure.json`
- `param_interaction/param_interaction.json`
- `param_relax.json`
- `param_props.json`

Assembly function: `_load_profile_param_template(profile)`.

`global.json` source: `_load_profile_global(profile)`.

### 3.2 User Actions & Button Semantics

Buttons at the bottom of the Submit page:

- `Reset`: Regenerate the `param.json` editor content based on the current form state, and clear all generated logs.
- `Submit`: Writes the user's current edits to `global.json`/`param.json`, writes interaction-related files (e.g., `vasp_input/INCAR`), and executes in the background:
  `nohup apex submit param.json -c global.json > apex.log 2>&1 &`

The Submit page now distinguishes two types of uploads:

- `Upload Structure`: Structure files are saved to the current `Workdir/confs/` by default. The `confs/` directory is created automatically if it does not exist.
- `Upload File`: Regular files are saved directly to the current `Working Directory`.

Upload results are displayed in `Command Output`.

The `structures` field is no longer edited manually in `param.json`. Instead, a dropdown lets users select structure directories/paths from the current `Workdir`, preventing accidental entries like `.`.

### 3.3 Pre-Submit Log Conflict Check

When `Submit` is clicked, the GUI checks whether `apex.log` already exists in the current directory.

- If it exists: a `ConfirmDialog` pops up for secondary confirmation.
- The actual resubmission only proceeds after the user confirms.

### 3.4 Interaction Editing Rules

- `lammps`: Displays `interaction.type` and a file selector for `interaction.model`.
- `vasp`: `interaction.type` is no longer selected manually. The GUI automatically reads element order from `POSCAR` in the selected structure directory, then prioritizes searching for suffix-matching POTCAR files under `vasp_input/` in the `Workdir`. Missing elements are indicated with gray text: "Please submit the POTCAR for the corresponding element".
- `abacus`: `interaction.type` is no longer selected manually. The GUI automatically reads element order from `POSCAR` in the selected structure directory, then prioritizes searching for prefix-matching pseudopotential/orbital files under `abacus_input/` in the `Workdir`. Missing items are indicated with gray text.

In the right-side Advanced Settings:

- `vasp` uses `interaction.incar`
- `abacus` uses `interaction.input`

If the corresponding files do not exist, they are automatically created from the profile default template during `Submit`:

- `vasp_input/INCAR`
- `abacus_input/INPUT`

### 3.5 Properties Checkbox Behavior

In `param.json`, only checked properties are retained; unchecked items are not written to the output JSON.

## 4. Log Page

The `Log` page is used to view `apex.log`:

- Manual refresh button
- Auto-refresh every 3 seconds
- Displays the tail of the log

Log read function: `_read_log_tail()`.

## 5. Advanced Page

Supports executing command tail arguments (calls `python -m apex ...`).

- Example input: `submit param_joint.json -c global.json`
- `gui` is blocked to prevent nested Dash launches.
- `report` is allowed but will open on a separate port (`8070`) to avoid conflict with the GUI.

## 6. Account Page

### 6.1 Goal

Provides a visual overlay editor for `apex account` stored credentials within the GUI.

### 6.2 Current Fields

- `email`
- `program_id`
- `password` (overwrite only, no echo)

### 6.3 Security Policy

- The page never displays plaintext passwords; only shows "Set / Not Set".
- The password input field is cleared after saving.
- The underlying file is still written by `save_account_config()`.

### 6.4 Related Functions

In `apex/gui.py`:

- `_load_account_state`
- `_render_account_summary`
- `_save_account_overwrite`
- Callback: `_handle_account`

In `apex/account.py`:

- `load_account_config`
- `save_account_config`
- `mask_sensitive_config`

## 7. Key Callback Inventory (apex/gui.py)

- `_sync_profile_defaults`: Syncs default values and control states when the profile is switched.
- `_update_interaction_table`: Dynamic row add/remove and reset on profile switch.
- `_generate_param_editor`: Generates `param.json` text from the current UI state.
- `_handle_command`: Handles `Apply/Submit/Confirm/Advanced` actions.
- `_handle_structure_upload`: Uploads structure files to the current `Workdir/confs/`.
- `_handle_file_upload`: Uploads regular files to the current `Workdir`.
- `_handle_account`: Handles Account refresh and overwrite save.
- `_update_manage_log`: Updates the `apex.log` display.

## 8. APEX GUI Major Fix

### 8.1 Critical Bug Fixes

- **Fixed**: Submit no longer overwrites input parameters with templates.
- **Fixed**: Selecting properties no longer overwrites already-entered parameters with templates

### 8.2 New Features

- **Workdir, file upload, and structure upload** added.
- **Retrieve + Report**: Replaced blocking report with a new-port approach to open the report page (!).
  - `apex report` now opens on port `8070` instead of blocking the GUI.
- **Auto-retry on network disconnect** during retrieve to prevent workflow interruption.
- **Workflow progress stats**: Shows how many confs completed and how many failed.
- **Workflow "Last updated: xx time"** to confirm whether the query has stalled.
- **Retrieve progress bar** added .
- **Auto-query workflow progress** after entering a Workflow ID .
- **Reset function improved**: Now resets all generated logs .
- **Renamed `maximal` to `maxeval`** to align with LAMMPS parameters (!!).

### 8.3 UX Updates

- Updated web page text; removed suspected prompt leakage.
- **Removed Apply button**: Its functionality was redundant with Submit (!).

### 8.4 Unresolved Issues

- When using `apex submit -s`, files cannot be passed from relaxation to properties calculation.
- Calculation failure reasons are not properly output or debugged.

## 9. Extension Notes

1. If the Submit page continues to grow, consider splitting `apex/gui.py` into:
   - `apex/gui/layout.py`
   - `apex/gui/callbacks_submit.py`
   - `apex/gui/callbacks_account.py`
2. Add minimal unit tests for each callback, especially:
   - `properties` filtering logic
   - `apex.log` resubmission confirmation logic
   - Account password non-echo logic
3. When adding a new profile, be sure to also add the four param sub-templates under `default_config/<profile>/`.

## 10. Quick Debug

```bash
apex gui --no-browser
```

Default address: `http://127.0.0.1:8060/`

If you modify the GUI, it is recommended to at least run:

```bash
python -m py_compile apex/gui.py tests/test_gui_submit_builder.py
python -m unittest tests.test_gui_cli tests.test_gui_submit_builder -v
```
