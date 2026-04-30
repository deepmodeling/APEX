#!/usr/bin/env python3

from __future__ import annotations

import argparse
import glob
import os
import re
import shutil
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Iterable, List

import numpy as np
from monty.serialization import loadfn

from apex.core.property.factory import make_property_instance
from apex.utils import handle_prop_suffix


def _natural_key(path: str):
    return [int(text) if text.isdigit() else text for text in re.split(r"(\d+)", path)]


def _resolve_path(base_dir: Path, path_text: str) -> str:
    path = Path(path_text)
    if path.is_absolute():
        return str(path)
    return str((base_dir / path).resolve())


def _resolve_interaction_paths(base_dir: Path, interaction: dict) -> dict:
    resolved = deepcopy(interaction or {})
    for key in ("model", "incar", "potcar_prefix"):
        value = resolved.get(key)
        if isinstance(value, str) and value:
            resolved[key] = _resolve_path(base_dir, value)
    for key in ("potcars", "orb_files"):
        mapping = resolved.get(key)
        if isinstance(mapping, dict):
            resolved[key] = {
                item_key: _resolve_path(base_dir, item_value)
                if isinstance(item_value, str) and item_value
                else item_value
                for item_key, item_value in mapping.items()
            }
    return resolved


def _resolve_structure_path(base_dir: Path, structure_glob: str) -> str:
    matches = sorted(glob.glob(str((base_dir / structure_glob).resolve())), key=_natural_key)
    if not matches:
        raise FileNotFoundError(f"No structure matched: {structure_glob}")
    return matches[0]


def _prepare_equilibrium_dir(structure_dir: str, temp_root: Path, label: str) -> str:
    src_dir = Path(structure_dir)
    equi_dir = temp_root / label / "relaxation" / "relax_task"
    equi_dir.mkdir(parents=True, exist_ok=True)

    candidates = [src_dir / "CONTCAR", src_dir / "POSCAR", src_dir / "STRU"]
    source_file = next((path for path in candidates if path.is_file()), None)
    if source_file is None:
        raise FileNotFoundError(
            f"Cannot find CONTCAR/POSCAR/STRU under {structure_dir}"
        )

    target_file = equi_dir / "CONTCAR"
    shutil.copy2(source_file, target_file)
    return str(equi_dir)


def _parse_gif_size(size_text: str):
    try:
        width_text, height_text = size_text.lower().split("x", 1)
        width_px = int(width_text)
        height_px = int(height_text)
    except Exception as exc:
        raise ValueError("--gif-size must be like WIDTHxHEIGHT, e.g. 1100x1100") from exc
    if width_px <= 0 or height_px <= 0:
        raise ValueError("--gif-size dimensions must be positive")
    return width_px, height_px


def _structure_bounds(atoms, radii_scale: float):
    try:
        from ase.data import covalent_radii
    except Exception as exc:
        raise RuntimeError("GIF export requires ASE covalent radii data") from exc

    xy = atoms.get_positions()[:, :2]
    radii = covalent_radii[atoms.get_atomic_numbers()] * radii_scale
    low = (xy - radii[:, None]).min(axis=0)
    high = (xy + radii[:, None]).max(axis=0)
    x_min, y_min = low
    x_max, y_max = high
    return x_min, x_max, y_min, y_max


def _view_size(
    bounds_list,
    *,
    canvas_aspect: float,
    padding: float,
):
    max_dx = 1e-6
    max_dy = 1e-6
    for x_min, x_max, y_min, y_max in bounds_list:
        max_dx = max(max_dx, x_max - x_min)
        max_dy = max(max_dy, y_max - y_min)

    padded_dx = max_dx * (1.0 + 2.0 * max(padding, 0.0))
    padded_dy = max_dy * (1.0 + 2.0 * max(padding, 0.0))

    if padded_dx / padded_dy < canvas_aspect:
        view_dy = padded_dy
        view_dx = view_dy * canvas_aspect
    else:
        view_dx = padded_dx
        view_dy = view_dx / canvas_aspect

    return view_dx, view_dy


def _centered_bbox(
    bounds,
    *,
    view_size,
    xshift: float,
    yshift: float,
):
    x_min, x_max, y_min, y_max = bounds
    dx = max(x_max - x_min, 1e-6)
    dy = max(y_max - y_min, 1e-6)
    view_dx, view_dy = view_size
    center_x = 0.5 * (x_min + x_max) + xshift * dx
    center_y = 0.5 * (y_min + y_max) + yshift * dy
    return (
        center_x - 0.5 * view_dx,
        center_x + 0.5 * view_dx,
        center_y - 0.5 * view_dy,
        center_y + 0.5 * view_dy,
    )


def _write_gif(
    frames,
    output_gif: str,
    fps: int = 8,
    size_text: str = "1100x1100",
    dpi: int = 140,
    padding: float = 0.30,
    xshift: float = 0.0,
    yshift: float = 0.0,
):
    try:
        import imageio.v2 as imageio
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from ase.visualize.plot import plot_atoms
    except Exception as exc:
        raise RuntimeError(
            "GIF export requires imageio and matplotlib in the current environment"
        ) from exc

    os.makedirs(os.path.dirname(os.path.abspath(output_gif)), exist_ok=True)

    width_px, height_px = _parse_gif_size(size_text)
    figsize = (width_px / max(dpi, 1), height_px / max(dpi, 1))
    radii_scale = 0.35
    frame_bounds = [_structure_bounds(atoms, radii_scale) for atoms in frames]
    view_size = _view_size(
        frame_bounds,
        canvas_aspect=width_px / height_px,
        padding=padding,
    )

    images = []
    for i, (atoms, bounds) in enumerate(zip(frames, frame_bounds)):
        x_min, x_max, y_min, y_max = _centered_bbox(
            bounds,
            view_size=view_size,
            xshift=xshift,
            yshift=yshift,
        )
        fig = plt.figure(figsize=figsize, dpi=dpi)
        ax = fig.add_axes([0.0, 0.0, 1.0, 1.0])
        plot_atoms(
            atoms,
            ax,
            radii=radii_scale,
            rotation="0x,0y,0z",
            show_unit_cell=1,
            bbox=(x_min, y_min, x_max, y_max),
        )
        ax.set_aspect("equal", adjustable="box")
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_axis_off()
        fig.text(0.02, 0.02, f"Frame {i:03d}", color="0.25", fontsize=10)
        fig.canvas.draw()
        rgba = np.asarray(fig.canvas.buffer_rgba())
        images.append(rgba[:, :, :3].copy())
        plt.close(fig)

    duration = 1.0 / max(fps, 1)
    imageio.mimsave(output_gif, images, duration=duration)


def _load_frames(poscar_files: Iterable[str]):
    from ase.io import read

    frames = []
    for path in poscar_files:
        frames.append(read(path, format="vasp"))
    return frames


def _derive_output_gif_path(parameter_path: Path, structures_count: int, prop_label: str) -> Path:
    if structures_count == 1 and prop_label == "":
        return parameter_path.with_suffix(".gif")
    suffix_parts = [parameter_path.stem]
    if prop_label:
        suffix_parts.append(prop_label)
    return parameter_path.with_name("_".join(suffix_parts) + ".gif")


def _expand_parameter_inputs(parameter_inputs: Iterable[str]) -> List[str]:
    expanded: List[str] = []
    for raw_input in parameter_inputs:
        candidate = Path(raw_input)
        if candidate.is_file():
            expanded.append(str(candidate.resolve()))
            continue

        matches = sorted(glob.glob(raw_input, recursive=True), key=_natural_key)
        if not matches:
            matches = sorted(glob.glob(f"**/{raw_input}", recursive=True), key=_natural_key)
        if not matches:
            raise FileNotFoundError(f"No parameter file matched: {raw_input}")
        expanded.extend(str(Path(match).resolve()) for match in matches)

    return expanded


def preview_parameter_file(
    parameter_file: str,
    *,
    gif_fps: int = 8,
    gif_dpi: int = 140,
    gif_padding: float = 0.30,
    gif_xshift: float = 0.0,
    gif_yshift: float = 0.0,
) -> List[str]:
    parameter_path = Path(parameter_file).resolve()
    payload = loadfn(str(parameter_path))
    if not isinstance(payload, dict):
        raise RuntimeError(f"{parameter_file} is not a JSON object")

    structures = payload.get("structures", [])
    if not isinstance(structures, list) or not structures:
        raise RuntimeError(f"{parameter_file} does not define any structures")

    properties = payload.get("properties", [])
    if not isinstance(properties, list) or not properties:
        raise RuntimeError(f"{parameter_file} does not define any properties")

    interaction = payload.get("interaction", {})
    if not isinstance(interaction, dict):
        raise RuntimeError(f"{parameter_file} has an invalid interaction block")

    resolved_interaction = _resolve_interaction_paths(parameter_path.parent, interaction)
    output_paths: List[str] = []

    with tempfile.TemporaryDirectory(prefix="apex_preview_") as temp_root_text:
        temp_root = Path(temp_root_text)

        runnable_properties = []
        for prop in properties:
            if not isinstance(prop, dict):
                continue
            do_refine, suffix = handle_prop_suffix(prop)
            if not suffix and not prop.get("reproduce", False):
                continue
            runnable_properties.append((prop, suffix or "", do_refine))

        if not runnable_properties:
            raise RuntimeError(f"{parameter_file} has no runnable properties")

        for structure_index, structure_glob in enumerate(structures):
            structure_dir = _resolve_structure_path(parameter_path.parent, structure_glob)

            for prop_index, (prop, suffix, do_refine) in enumerate(runnable_properties):
                prop_obj = make_property_instance(
                    {**deepcopy(prop), "type": prop["type"]},
                    resolved_interaction,
                )
                prop_label = ""
                if len(runnable_properties) > 1 or len(structures) > 1:
                    prop_label = prop["type"]
                    if suffix:
                        prop_label = f"{prop_label}_{suffix}"
                    if len(structures) > 1:
                        prop_label = f"{Path(structure_dir).name}_{prop_label}"

                output_gif = _derive_output_gif_path(
                    parameter_path,
                    len(structures),
                    prop_label,
                )

                work_dir = temp_root / f"work_{structure_index}_{prop_index}"
                work_dir.mkdir(parents=True, exist_ok=True)
                equi_dir = _prepare_equilibrium_dir(
                    structure_dir,
                    temp_root,
                    f"structure_{structure_index}_{prop_index}",
                )
                task_list = prop_obj.make_confs(str(work_dir), equi_dir, refine=do_refine)
                prop_obj.post_process(task_list)

                poscar_files = [os.path.join(task_dir, "POSCAR") for task_dir in task_list]
                frames = _load_frames(poscar_files)
                _write_gif(
                    frames,
                    str(output_gif),
                    fps=gif_fps,
                    dpi=gif_dpi,
                    padding=gif_padding,
                    xshift=gif_xshift,
                    yshift=gif_yshift,
                )
                output_paths.append(str(output_gif))

    return output_paths


def preview_from_args(args: argparse.Namespace) -> List[str]:
    outputs: List[str] = []
    for parameter_file in _expand_parameter_inputs(args.parameters):
        outputs.extend(
            preview_parameter_file(
                parameter_file,
                gif_fps=args.gif_fps,
                gif_dpi=args.gif_dpi,
                gif_padding=args.gif_padding,
                gif_xshift=args.gif_xshift,
                gif_yshift=args.gif_yshift,
            )
        )
    return outputs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate preview GIFs from APEX param_props JSON files."
    )
    parser.add_argument(
        "parameters",
        nargs="+",
        help="param_props JSON files, e.g. param_props_gamma*.json",
    )
    parser.add_argument("--gif-fps", type=int, default=8, help="GIF frames per second")
    parser.add_argument("--gif-dpi", type=int, default=140, help="GIF rendering DPI")
    parser.add_argument(
        "--gif-padding",
        type=float,
        default=0.30,
        help="Relative padding ratio around the detected atom bounds",
    )
    parser.add_argument(
        "--gif-xshift",
        type=float,
        default=0.0,
        help="Shift the rendered viewport horizontally by a fraction of the data span",
    )
    parser.add_argument(
        "--gif-yshift",
        type=float,
        default=0.0,
        help="Shift the rendered viewport vertically by a fraction of the data span; positive values move the structure downward",
    )
    return parser


def main(argv: List[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    outputs = preview_from_args(args)
    for path in outputs:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
