# Repository Guidelines

## Project Structure & Module Organization
- `apex/`: CLI and core library (`main.py`, `flow.py`, `submit.py`, `report.py`); domain logic in `apex/core/{property,calculator,template,lib}`.
- `tests/`: unit tests (`test_*.py`) plus fixtures in `tests/{confs,vasp_input,lammps_input,...}`.
- `examples/`: runnable LAMMPS/VASP/ABACUS demos (see `examples/lammps/apex_lammps_tutorial.md`).
- `docs/`: images and aux docs.

## Build, Test, and Development Commands
- Setup (Python 3.10+): `python -m venv .venv && source .venv/bin/activate && pip install -U pip && pip install -e .`
- CLI quick check: `apex --help`
- Local debug run: `apex submit -d param_joint.json -c global_local_debug.json`
- Tests (match CI):
  - From repo root: `cd tests; export SKIP_UT_WITH_DFLOW=0; export DFLOW_DEBUG=1; coverage run -m unittest -v -f; coverage report`
  - Minimal: `python -m unittest -v`

## Coding Style & Naming Conventions
- Follow PEP 8; 4-space indentation; keep lines reasonably short; add type hints where practical.
- Names: `snake_case` for modules/functions/vars; `CapWords` for classes; tests named `test_*.py` with `unittest.TestCase` classes.
- JSON configs use `param_*.json` and `global_*.json`; keep relative paths inside the working directory.

## Testing Guidelines
- Framework: `unittest` with coverage; test data lives under `tests/` and should be hermetic (no network).
- Env commonly used in tests/CI: `DFLOW_DEBUG=1`, `SKIP_UT_WITH_DFLOW=0`.
- Prefer small, fast tests around `apex/core/property/*.py` and CLI behaviors (`apex main` subcommands).

## Commit & Pull Request Guidelines
- History shows short, descriptive subjects (e.g., "Fix JSON formatting", "Update tutorial", "Merge pull request"). Keep subjects imperative and <=72 chars; add a body for rationale when needed; link issues.
- PRs: include what/why, any user-facing changes, and example commands. Update docs/examples if behavior changes.
- CI must be green: GitHub Actions runs unit tests with coverage on Python 3.10.

## Security & Configuration Tips
- Never commit credentials in `global_*.json`; use placeholders or env injection. Exclude local work dirs and large artifacts from commits. For quick validation, prefer `apex submit -d` local debug runs.
