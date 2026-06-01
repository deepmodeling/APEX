# APEX 1.3.0 Changelog

## Highlights

APEX 1.3.0 introduces new structure-generation workflows, new property workflows, improved automatic supercell construction, stronger failure-handling behavior, and a substantially reworked GUI submission/retrieval workflow.

Major additions include:

- Composition-aware automatic lattice constant estimation
- Automatic supercell generation from composition tolerance
- Sublattice-aware RSS generation for B2, L1_2, and L1_0 systems
- RSS workflow support and example documentation
- GammaSurface workflow support and example documentation
- Grüneisen parameters and thermal expansion workflow support and example documentation
- Finite-temperature lattice workflow improvements
- Finite-temperature elasticity workflow support and example documentation
- Annealing workflow support for LAMMPS
- Shape-controlled automatic supercell generation
- `req_calc`-based relaxation/property selection for joint workflows
- GUI-side batched submission for large configuration sets
- Multi-workflow progress tracking and retrieval in `apex gui`
- Bohrium account defaults managed by `apex account`
- Improved failed-step output retrieval, diagnostics, retry behavior, and early-break handling

---

## Core Features

### Automatic Structure Construction

- Added automatic lattice constant estimation based on composition-weighted atomic radii.
- Added automatic supercell generation from composition tolerance.
- Added optional maximum atom budget for automatic supercell construction.
- Added shape-control modes for automatic supercell generation:
  - `near_cubic`
  - `xy_equal_z_free`, mainly for gamma-surface and slab-like workflows.
- Added support for sublattice-aware random solid solution generation for:
  - B2
  - L1_2
  - L1_0

### RSS Workflow

- Added RSS workflow support.
- Added RSS example documentation.
- Added support for prototype-aware and sublattice-aware RSS systems.
- Added parent-lattice helpers, automatic parent-lattice resolution, and Warren-Cowley SRO-driven sampling utilities.

### GammaSurface Workflow

- Added GammaSurface workflow support.
- Added GammaSurface example documentation.
- Added shape-control support for slab-like gamma-surface supercells.
- Added `apex preview` support for generating GIF previews of GammaSurface motion.

### Grüneisen Parameters and Thermal Expansion Workflow

- Added `gruneisen` workflow support based on phonon calculations at multiple symmetric volume strains.
- Added both `sign_only` and `full` output modes; `full` can fit Birch-Murnaghan EOS data to estimate thermal expansion.
- Extended LAMMPS/phonoLAMMPS and VASP paths for Grüneisen post-processing, and added dedicated tests and examples.

### Finite-Temperature Lattice Workflow

- Add finite-temperature lattice calculations with updated thermo output, temperature-aware reporting, pressure handling, and `c/a` post-processing.

### Finite-Temperature Elasticity Workflow

- Added finite-temperature elasticity workflow support.
- Added finite-temperature elasticity example documentation.
- Added finite-temperature elastic tensor fitting, stress post-processing, and derived elastic modulus output.

### Annealing Workflow

- Added LAMMPS annealing workflow support.
- Improved generated annealing input scripts for heating, cooling, RDF output, and holding stages.

### Workflow Selection and Submission

- Added `req_calc`-based workflow selection for relaxation and property calculations.
- Added support for skipping relaxation in joint workflows with `"relaxation": {"req_calc": false}` when property setup can use the structure-directory `POSCAR` directly.
- Added explicit disabling of individual properties with `"req_calc": false`.
- Added clearer submit-time path validation and workflow labeling support.
- Added skip-completed behavior for already successful relaxation and property tasks when rerun is not requested.

### Failure Handling and Retrieval

- Added automatic break behavior when the relaxation step fails in joint workflows.
- Added detailed failed-step diagnostics with last-step failure reasons, workflow IDs, and workflow UIDs.
- Added failed-output retrieval support using debug artifacts and main logs from the latest failed steps.
- Added archive-workflow retrieval support in `apex retrieve`, including UID-based fallback lookup when workflow-name lookup is insufficient.
- Added retry-aware artifact download behavior for transient failures while avoiding retries for permanent missing-artifact cases.
- Added per-task LAMMPS status records and debug artifacts, including:
  - `apex_task_status.json`
  - `.debug.log`
  - `.debug.stdout`
  - `.debug.stderr`

### Reporting

- Added richer report support for new and updated workflows, including finite-temperature lattice, finite-temperature elasticity, GammaSurface, and annealing outputs.
- Added static-report support for finite-temperature lattice tables/plots and annealing artifact summaries.
- Added report CLI controls for browser launch, host, and port.
- Improved Dash report startup behavior and reduced duplicate report-page launches.

---

## User Experience

- Added `apex gui`, a web-based interface for common APEX operations.
- Added GUI-side batched submission for large configuration sets.
- Added aggregated multi-workflow progress tracking and retrieval in `apex gui`.
- Added GUI support for structure upload, POSCAR-driven interaction auto-detection, VASP/ABACUS interaction-table editing, and profile-specific default templates.
- Added GUI-side account management and report/retrieve orchestration.
- Added `apex account` for saving Bohrium identity and cloud defaults for easier repeated submission.
- Added automatic Bohrium default injection while preserving user-provided config overrides.
- Added `apex preview` for visualizing GammaSurface movement before submission.
- Added automatic interaction-type detection from POSCAR content.
- Improved failed-step messages by printing the reason from the latest failed step.
- Added default configuration templates for LAMMPS, VASP, ABACUS, and GUI submit groups.

---

## Compatibility and Documentation

- Updated README coverage for RSS, GUI usage, Bohrium account defaults, Gamma line/surface, `req_calc`, and finite-temperature elasticity.
- Added runnable examples for RSS, GammaSurface, and finite-temperature elasticity.
- Added GUI developer documentation.
- Added `monty` to the package dependencies and constrained supported Python versions to `<3.13`.
- Added support for Phonopy v4 in terms of phonon calculation.