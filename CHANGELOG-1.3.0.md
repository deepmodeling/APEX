# APEX 1.3.0 Changelog

## Highlights

APEX 1.3.0 introduces new structure-generation workflows, new property workflows, improved automatic supercell construction, stronger failure-handling behavior, and a substantially reworked GUI submission/retrieval workflow.

Major additions include:

- Composition-aware automatic lattice constant estimation
- Automatic supercell generation from composition tolerance
- Sublattice-aware RSS generation for B2, L1_2, and L1_0 systems
- RSS workflow support and example documentation
- GammaSurface workflow support and example documentation
- Finite-temperature elasticity workflow support and example documentation
- Shape-controlled automatic supercell generation
- GUI-side batched submission for large configuration sets
- Multi-workflow progress tracking and retrieval in `apex gui`
- Improved failed-step output retrieval and early-break behavior

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

### GammaSurface Workflow

- Added GammaSurface workflow support.
- Added GammaSurface example documentation.
- Added shape-control support for slab-like gamma-surface supercells.
- Added apex preview for preview the movement of GammaSurface

### Finite-Temperature Elasticity Workflow

- Added finite-temperature elasticity workflow support.
- Added finite-temperature elasticity example documentation.

### Failure Handling and Retrieval

- Added automatic break behavior when the relaxation step fails.
- Added retrieval support for failed output by using --debug files and main logs from the latest failed steps.
- Support retrieve archive workflow by apex retrieve

---

## User Experience
- Auto detect interaction type from poscar
- Will print out the reason from the last failed step
- Apex Account for saving bohrium identity for better usage