"""Small helpers for stage-concatenated VASP trajectory output."""

from __future__ import annotations

import re

from dflow.python import upload_packages


upload_packages.append(__file__)


_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?"
_STAGE = re.compile(r"^\s*APEX_STAGE\s+(\S+)\s*$")


def split_apex_stage_sections(text):
    """Group text by APEX_STAGE, preserving repeated sections in file order."""
    sections = {}
    stage = "trajectory"
    chunks = []

    def flush():
        if chunks:
            sections.setdefault(stage, []).append("".join(chunks))

    for line in text.splitlines(keepends=True):
        match = _STAGE.match(line)
        if match:
            flush()
            stage = match.group(1)
            chunks = []
        else:
            chunks.append(line)
    flush()
    return {name: "".join(parts) for name, parts in sections.items()}


def parse_outcar_stage_geometry(text):
    """Parse independent cell and volume samples from each OUTCAR stage."""
    geometry = {}
    for stage, section in split_apex_stage_sections(text).items():
        cells = []
        lines = section.splitlines()
        for index, line in enumerate(lines):
            if "direct lattice vectors" not in line.lower():
                continue
            try:
                cell = [
                    [float(value) for value in re.findall(_NUMBER, lines[index + row])[:3]]
                    for row in (1, 2, 3)
                ]
            except (IndexError, ValueError):
                continue
            if all(len(vector) == 3 for vector in cell):
                cells.append(cell)
        volumes = [
            float(value)
            for value in re.findall(
                rf"volume of cell\s*:\s*({_NUMBER})", section, flags=re.IGNORECASE
            )
        ]
        geometry[stage] = {"cells": cells, "volumes": volumes}
    return geometry


def tail_align_stage_geometry(frames, geometry):
    """Attach only available tail-aligned OUTCAR geometry to stage frames."""
    for stage, stage_frames in frames.items():
        samples = geometry.get(stage, {})
        for key, frame_key in (("cells", "cell"), ("volumes", "outcar_volume")):
            values = samples.get(key, [])
            count = min(len(stage_frames), len(values))
            if not count:
                continue
            for frame, value in zip(stage_frames[-count:], values[-count:]):
                frame[frame_key] = value
