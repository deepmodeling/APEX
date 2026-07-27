"""APEX-owned VASP execution OP.

The upstream fpop RunVasp OP only stages the four conventional VASP input
files.  APEX finite-temperature properties also generate ``run_command`` and
multiple ``INCAR.<stage>`` files, so those tasks need the complete prepared
input set in a writable working directory.
"""

import datetime
import json
import logging
import re
import shutil
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from dflow.python import FatalError, OP, OPIO, TransientError, upload_packages
from dflow.utils import set_directory
from fpop.vasp import RunVasp

upload_packages.append(__file__)


class RunVASP(RunVasp):
    """Run VASP while preserving APEX staged-calculation semantics."""

    _MANDATORY_INPUTS = ("POSCAR", "INCAR", "POTCAR", "KPOINTS")
    _STAGE_PLAN = "apex_vasp_stage_plan.json"
    _STAGE_STATUS = "apex_vasp_stage_status.json"
    _FAILURE_STATUS = "apex_vasp_failure.json"
    _EVIDENCE_FILES = (
        "outlog",
        "OUTCAR",
        "OSZICAR",
        "CONTCAR",
        "XDATCAR",
        "POSCAR",
        "INCAR",
        "KPOINTS",
        "run_command",
        "task.json",
        "FiniteTlatt.json",
        "Annealing.json",
        _STAGE_PLAN,
        _STAGE_STATUS,
        _FAILURE_STATUS,
    )
    _EVIDENCE_GLOBS = (
        "OUTCAR.*",
        "OSZICAR.*",
        "CONTCAR.*",
        "XDATCAR.*",
        "INCAR.*",
    )
    _IONIC_POSITION = re.compile(
        r"(?m)^\s*POSITION\s+TOTAL-FORCE(?:\s|$)", re.IGNORECASE
    )
    _IONIC_ITERATION = re.compile(
        r"(?m)^-*\s*Iteration\s+(\d+)\s*\(", re.IGNORECASE
    )
    _OSZICAR_MD_STEP = re.compile(r"(?m)^\s*(\d+)\s+T=")
    _FOOTER_MARKERS = {
        "general_timing": re.compile(
            r"General timing and accounting", re.IGNORECASE
        ),
        "total_cpu_time": re.compile(
            r"Total CPU time used", re.IGNORECASE
        ),
        "elapsed_time": re.compile(r"Elapsed time", re.IGNORECASE),
    }
    _FOOTER_TAIL_LINES = 256

    @staticmethod
    def _copy_path(source: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            if destination.exists():
                shutil.rmtree(destination)
            shutil.copytree(source, destination, symlinks=False)
        else:
            # Follow task-input symlinks so INCAR and POSCAR become writable.
            shutil.copy2(source, destination, follow_symlinks=True)

    @classmethod
    def _prepared_inputs(
        cls,
        task_path: Path,
        backward_dir_name: str,
        log_name: str,
    ) -> Iterable[Path]:
        excluded = {
            backward_dir_name,
            log_name,
            cls._FAILURE_STATUS,
            cls._STAGE_STATUS,
        }
        output_prefixes = ("OUTCAR", "OSZICAR", "CONTCAR", "XDATCAR")
        for source in task_path.iterdir():
            # Never let stale completion evidence from an earlier attempt make
            # a partial rerun appear successful.
            if source.name in excluded:
                continue
            if source.name in output_prefixes or source.name.startswith(
                tuple(f"{prefix}." for prefix in output_prefixes)
            ):
                continue
            yield source

    @staticmethod
    def _incar_nsw(path: Path) -> Optional[int]:
        if not path.is_file():
            return None
        matches = re.findall(
            r"(?im)^\s*NSW\s*=\s*([+-]?\d+)", path.read_text(errors="replace")
        )
        return int(matches[-1]) if matches else None

    @classmethod
    def _oszicar_steps(cls, path: Path) -> int:
        if not path.is_file():
            return 0
        text = path.read_text(errors="replace")
        matches = [int(value) for value in cls._OSZICAR_MD_STEP.findall(text)]
        return max(matches, default=0)

    @classmethod
    def _inspect_outcar_text(
        cls,
        text: str,
        *,
        expected_ionic_steps: Optional[int],
        require_exact_steps: bool,
        oszicar_path: Optional[Path] = None,
    ) -> Dict:
        positions = len(cls._IONIC_POSITION.findall(text))
        iterations = [
            int(value) for value in cls._IONIC_ITERATION.findall(text)
        ]
        footer_tail = "\n".join(
            text.splitlines()[-cls._FOOTER_TAIL_LINES:]
        )
        footer_markers = {
            name: bool(pattern.search(footer_tail))
            for name, pattern in cls._FOOTER_MARKERS.items()
        }
        footer_complete = all(footer_markers.values())
        failure_reasons = []
        if not footer_complete:
            missing = [
                name for name, present in footer_markers.items() if not present
            ]
            failure_reasons.append(
                "missing_footer_markers:" + ",".join(missing)
            )
        if require_exact_steps:
            if expected_ionic_steps is None:
                failure_reasons.append("missing_expected_ionic_steps")
            elif positions != expected_ionic_steps:
                failure_reasons.append(
                    "ionic_step_count_mismatch:"
                    f"expected={expected_ionic_steps},observed={positions}"
                )
        return {
            "expected_ionic_steps": expected_ionic_steps,
            "observed_ionic_steps": positions,
            "observed_iteration_max": max(iterations, default=0),
            "observed_oszicar_steps": (
                cls._oszicar_steps(oszicar_path)
                if oszicar_path is not None
                else 0
            ),
            "footer_complete": footer_complete,
            "footer_markers": footer_markers,
            "footer_tail_lines_checked": cls._FOOTER_TAIL_LINES,
            "finished": not failure_reasons,
            "failure_reasons": failure_reasons,
        }

    @classmethod
    def _inspect_outcar(
        cls,
        path: Path,
        *,
        expected_ionic_steps: Optional[int],
        require_exact_steps: bool,
        oszicar_path: Optional[Path] = None,
    ) -> Dict:
        if not path.is_file():
            return {
                "expected_ionic_steps": expected_ionic_steps,
                "observed_ionic_steps": 0,
                "observed_iteration_max": 0,
                "observed_oszicar_steps": (
                    cls._oszicar_steps(oszicar_path)
                    if oszicar_path is not None
                    else 0
                ),
                "footer_complete": False,
                "footer_markers": {
                    name: False for name in cls._FOOTER_MARKERS
                },
                "finished": False,
                "failure_reasons": ["missing_outcar"],
            }
        return cls._inspect_outcar_text(
            path.read_text(errors="replace"),
            expected_ionic_steps=expected_ionic_steps,
            require_exact_steps=require_exact_steps,
            oszicar_path=oszicar_path,
        )

    @staticmethod
    def _task_type() -> str:
        task_json = Path("task.json")
        if task_json.is_file():
            try:
                payload = json.loads(task_json.read_text())
            except (OSError, ValueError):
                payload = {}
            if isinstance(payload, dict):
                return str(payload.get("type", ""))
        if Path("FiniteTlatt.json").is_file():
            return "finite_t_latt"
        if Path("Annealing.json").is_file():
            return "annealing"
        return ""

    @staticmethod
    def _annealing_stage_names() -> List[str]:
        command_path = Path("run_command")
        if not command_path.is_file():
            return []
        stages = []
        for line in command_path.read_text(errors="replace").splitlines():
            if "OUTCAR.apex" not in line:
                continue
            match = re.search(r"APEX_STAGE\s+([A-Za-z0-9_.-]+)", line)
            if match and match.group(1) not in stages:
                stages.append(match.group(1))
        return stages

    @classmethod
    def _load_stage_plan(cls) -> List[Dict]:
        path = Path(cls._STAGE_PLAN)
        if not path.is_file():
            return []
        try:
            payload = json.loads(path.read_text())
        except (OSError, ValueError):
            return []
        stages = payload.get("stages", []) if isinstance(payload, dict) else []
        return [stage for stage in stages if isinstance(stage, dict)]

    @classmethod
    def _fallback_finite_t_latt_plan(cls) -> List[Dict]:
        command = (
            Path("run_command").read_text(errors="replace")
            if Path("run_command").is_file()
            else ""
        )
        stages = []
        if "OUTCAR.nvt" in command:
            stages.append({
                "name": "nvt",
                "incar": "INCAR.nvt",
                "outcar": "OUTCAR.nvt",
                "oszicar": "OSZICAR.nvt",
            })
        stages.extend([
            {
                "name": "equi",
                "incar": "INCAR.equi",
                "outcar": "OUTCAR.equi",
                "oszicar": "OSZICAR.equi",
            },
            {
                "name": "production",
                "incar": "INCAR.production",
                "outcar": "OUTCAR",
                "oszicar": "OSZICAR",
            },
        ])
        return stages

    @classmethod
    def _fallback_annealing_plan(cls) -> List[Dict]:
        return [
            {
                "name": name,
                "incar": f"INCAR.{name}",
                "outcar": "OUTCAR",
                "oszicar": "OSZICAR",
                "section": name,
            }
            for name in cls._annealing_stage_names()
        ]

    @staticmethod
    def _stage_sections(text: str) -> Dict[str, str]:
        markers = list(
            re.finditer(r"(?m)^APEX_STAGE\s+([A-Za-z0-9_.-]+)\s*$", text)
        )
        sections = {}
        for index, marker in enumerate(markers):
            start = marker.end()
            end = (
                markers[index + 1].start()
                if index + 1 < len(markers)
                else len(text)
            )
            sections[marker.group(1)] = text[start:end]
        return sections

    @classmethod
    def _validate_stages(cls) -> Dict:
        task_type = cls._task_type()
        status = {
            "schema": "apex.vasp.stage-status/v1",
            "task_type": task_type or "single_stage",
            "checked_at": datetime.datetime.now(
                datetime.timezone.utc
            ).isoformat(),
            "stages": [],
        }

        if task_type in {"finite_t_latt", "annealing"}:
            plan = cls._load_stage_plan()
            if not plan:
                plan = (
                    cls._fallback_finite_t_latt_plan()
                    if task_type == "finite_t_latt"
                    else cls._fallback_annealing_plan()
                )
            if not plan:
                status["state"] = "failed"
                status["missing_or_incomplete_stages"] = ["stage_plan"]
                status["failure_reasons"] = ["missing_stage_plan"]
                return status

            section_cache = {}
            for stage in plan:
                name = str(stage.get("name", "unknown"))
                incar = Path(str(stage.get("incar", "INCAR")))
                outcar = Path(str(stage.get("outcar", "OUTCAR")))
                oszicar = Path(str(stage.get("oszicar", "OSZICAR")))
                expected = stage.get("expected_ionic_steps")
                if expected is None:
                    expected = cls._incar_nsw(incar)
                else:
                    expected = int(expected)

                section_name = stage.get("section")
                if section_name:
                    if outcar not in section_cache:
                        text = (
                            outcar.read_text(errors="replace")
                            if outcar.is_file()
                            else ""
                        )
                        section_cache[outcar] = cls._stage_sections(text)
                    section = section_cache[outcar].get(str(section_name))
                    if section is None:
                        inspection = cls._inspect_outcar(
                            Path("__missing_stage_section__"),
                            expected_ionic_steps=expected,
                            require_exact_steps=True,
                            oszicar_path=oszicar,
                        )
                        inspection["failure_reasons"] = [
                            f"missing_stage_section:{section_name}"
                        ]
                    else:
                        inspection = cls._inspect_outcar_text(
                            section,
                            expected_ionic_steps=expected,
                            require_exact_steps=True,
                            oszicar_path=oszicar,
                        )
                else:
                    inspection = cls._inspect_outcar(
                        outcar,
                        expected_ionic_steps=expected,
                        require_exact_steps=True,
                        oszicar_path=oszicar,
                    )
                inspection.update({
                    "name": name,
                    "incar": str(incar),
                    "output": str(outcar),
                    "oszicar": str(oszicar),
                })
                status["stages"].append(inspection)
        else:
            inspection = cls._inspect_outcar(
                Path("OUTCAR"),
                expected_ionic_steps=cls._incar_nsw(Path("INCAR")),
                require_exact_steps=False,
                oszicar_path=Path("OSZICAR"),
            )
            inspection.update({
                "name": "vasp",
                "incar": "INCAR",
                "output": "OUTCAR",
                "oszicar": "OSZICAR",
            })
            status["stages"] = [inspection]

        missing = [
            item["name"] for item in status["stages"] if not item["finished"]
        ]
        status["state"] = "failed" if missing else "succeeded"
        if missing:
            status["missing_or_incomplete_stages"] = missing
            status["failure_reasons"] = {
                item["name"]: item.get("failure_reasons", [])
                for item in status["stages"]
                if not item["finished"]
            }
        return status

    def check_run_success(self):
        """Override fpop's fragile last-line ``Voluntary`` check."""
        return self._validate_stages()["state"] == "succeeded"

    @classmethod
    def _save_stage_evidence(cls, backward_dir_name: str, status: Dict) -> None:
        status_path = Path(cls._STAGE_STATUS)
        status_path.write_text(json.dumps(status, indent=4) + "\n")
        backward_dir = Path(backward_dir_name)
        backward_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(status_path, backward_dir / status_path.name)
        for source in cls._evidence_sources():
            if source.name in {status_path.name, backward_dir.name}:
                continue
            if source.is_file():
                shutil.copy2(source, backward_dir / source.name)

    @classmethod
    def _evidence_sources(cls, log_name: Optional[str] = None) -> List[Path]:
        names = list(cls._EVIDENCE_FILES)
        if log_name and log_name not in names:
            names.append(log_name)
        sources = [Path(name) for name in names]
        for pattern in cls._EVIDENCE_GLOBS:
            sources.extend(Path(".").glob(pattern))
        unique = {}
        for source in sources:
            unique[source.name] = source
        return list(unique.values())

    @classmethod
    def _current_stage(cls, status: Dict) -> str:
        for stage in status.get("stages", []):
            if not stage.get("finished"):
                return str(stage.get("name", "unknown"))
        return "unknown"

    @classmethod
    def _save_failure_evidence(
        cls,
        destination: Path,
        error: Exception,
        log_name: str,
    ) -> None:
        destination.mkdir(parents=True, exist_ok=True)
        status = cls._validate_stages()
        status["state"] = "failed"
        status["current_or_next_stage"] = cls._current_stage(status)
        status["execution_error_type"] = type(error).__name__
        status["execution_error"] = str(error)
        status_path = Path(cls._STAGE_STATUS)
        status_path.write_text(json.dumps(status, indent=4) + "\n")

        failure = {
            "schema": "apex.vasp.failure/v1",
            "task_type": status.get("task_type"),
            "current_or_next_stage": status["current_or_next_stage"],
            "error_type": type(error).__name__,
            "error": str(error),
            "stage_status": status,
        }
        failure_path = Path(cls._FAILURE_STATUS)
        failure_path.write_text(json.dumps(failure, indent=4) + "\n")

        for source in cls._evidence_sources(log_name):
            if source.is_file():
                shutil.copy2(source, destination / source.name)

    @staticmethod
    def _dflow_tmp_roots() -> List[Path]:
        """Find PythonOP temporary roots from explicit and real cloud layouts."""
        roots = []
        cwd = Path.cwd().resolve()
        for candidate in (cwd, *cwd.parents):
            if (
                (candidate / "inputs" / "artifacts").is_dir()
                and (candidate / "outputs" / "artifacts").is_dir()
            ):
                roots.append(candidate)
        return roots

    def _failure_destinations(self, backward_dir_name: str) -> List[Path]:
        destinations = [Path(backward_dir_name)]
        tmp_roots = []
        explicit_tmp_root = getattr(self, "tmp_root", None)
        if explicit_tmp_root is not None:
            tmp_roots.append(Path(explicit_tmp_root).resolve())
        tmp_roots.extend(self._dflow_tmp_roots())

        # PythonOPTemplate pre-creates these directories. In production the OP
        # runs below ``<job-root>/tmp`` without setting ``self.tmp_root``, so
        # discover that ancestor from cwd and mirror evidence there before the
        # raised exception causes dflow to package output artifacts.
        seen_roots = set()
        for tmp_root in tmp_roots:
            if tmp_root in seen_roots:
                continue
            seen_roots.add(tmp_root)
            if not (
                (tmp_root / "inputs" / "artifacts").is_dir()
                and (tmp_root / "outputs" / "artifacts").is_dir()
            ):
                continue
            dflow_output = (
                tmp_root / "outputs" / "artifacts" / "backward_dir"
            )
            if dflow_output.resolve() not in {
                destination.resolve() for destination in destinations
            }:
                destinations.append(dflow_output)
        return destinations

    def run_task(
        self,
        backward_dir_name,
        log_name,
        backward_list: List[str],
        run_image_config: Optional[Dict] = None,
        optional_input: Optional[Dict] = None,
    ) -> str:
        try:
            backward_dir_name = super().run_task(
                backward_dir_name,
                log_name,
                backward_list,
                run_image_config,
                optional_input,
            )
        except TransientError as error:
            if "could not check the exact cause" not in str(error):
                raise
            status = self._validate_stages()
            details = []
            for stage in status.get("stages", []):
                if stage.get("finished"):
                    continue
                reasons = ",".join(stage.get("failure_reasons", []))
                details.append(f"{stage.get('name')}: {reasons}")
            raise TransientError(
                "APEX VASP completion validation failed; "
                + "; ".join(details)
            ) from error
        status = self._validate_stages()
        self._save_stage_evidence(backward_dir_name, status)
        if status["state"] != "succeeded":
            failed = ", ".join(status.get("missing_or_incomplete_stages", []))
            raise TransientError(
                "APEX VASP staged run is incomplete; missing or unfinished "
                f"stage(s): {failed}"
            )
        return backward_dir_name

    @OP.exec_sign_check
    def execute(self, op_in: OPIO) -> OPIO:
        task_path = Path(op_in["task_path"]).resolve()
        if not task_path.is_dir():
            raise FatalError(f"cannot find VASP task directory {task_path}")
        for name in self._MANDATORY_INPUTS:
            source = task_path / name
            if not source.exists():
                raise FatalError(f"cannot find VASP input file {source}")

        task_name = op_in["task_name"]
        backward_dir_name = op_in["backward_dir_name"]
        log_name = op_in["log_name"]
        work_dir = Path(task_name)

        with set_directory(work_dir, mkdir=True):
            for source in self._prepared_inputs(
                task_path, backward_dir_name, log_name
            ):
                self._copy_path(source, Path(source.name))

            optional_artifact = op_in.get("optional_artifact") or {}
            for name, artifact_path in optional_artifact.items():
                source = Path(artifact_path)
                if not source.exists():
                    fallback = task_path / name
                    if fallback.exists():
                        source = fallback
                    else:
                        logging.warning(
                            "Optional VASP artifact %s does not exist", source
                        )
                        continue
                self._copy_path(source, Path(name))

            try:
                backward_dir_name = self.run_task(
                    backward_dir_name,
                    log_name,
                    op_in["backward_list"],
                    op_in["run_image_config"],
                    op_in["optional_input"],
                )
            except Exception as error:
                for destination in self._failure_destinations(
                    backward_dir_name
                ):
                    self._save_failure_evidence(
                        destination, error, log_name
                    )
                raise

        return OPIO({"backward_dir": work_dir / backward_dir_name})
