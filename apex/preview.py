#!/usr/bin/env python3

from __future__ import annotations

import argparse
import glob
import os
import re
import shutil
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Iterable, List, Optional

import numpy as np
from monty.serialization import loadfn

from apex.core.common_prop import make_property_instance
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


def _find_structure_file(structure_dir: str) -> Path:
    src_dir = Path(structure_dir)
    candidates = [src_dir / "CONTCAR", src_dir / "POSCAR", src_dir / "STRU"]
    source_file = next((path for path in candidates if path.is_file()), None)
    if source_file is None:
        raise FileNotFoundError(
            f"Cannot find CONTCAR/POSCAR/STRU under {structure_dir}"
        )
    return source_file


def _prepare_equilibrium_dir(structure_dir: str, temp_root: Path, label: str) -> str:
    src_dir = Path(structure_dir)
    equi_dir = temp_root / label / "relaxation" / "relax_task"
    equi_dir.mkdir(parents=True, exist_ok=True)

    source_file = _find_structure_file(structure_dir)
    target_file = equi_dir / "CONTCAR"
    shutil.copy2(source_file, target_file)
    source_result = src_dir / "result.json"
    target_result = equi_dir / "result.json"
    if source_result.is_file():
        shutil.copy2(source_result, target_result)
    else:
        # Gamma.make_confs requires the baseline result to exist, while preview
        # only generates geometry and never runs property post-processing.
        target_result.write_text('{"preview_only": true}\n')
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

    # Keep the complete projected unit cell in the viewport.  In particular,
    # a Gamma slab's vacuum is represented by empty cell volume; atom-only
    # bounds crop that volume even when plot_atoms(show_unit_cell=1) is used.
    cell = np.asarray(atoms.cell.array, dtype=float)
    if cell.shape == (3, 3) and np.isfinite(cell).all() and np.any(cell):
        cell_corners = np.asarray(
            [
                [i, j, k]
                for i in (0.0, 1.0)
                for j in (0.0, 1.0)
                for k in (0.0, 1.0)
            ],
            dtype=float,
        ) @ cell
        low = np.minimum(low, cell_corners[:, :2].min(axis=0))
        high = np.maximum(high, cell_corners[:, :2].max(axis=0))

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


class _ProgressBar:
    def __init__(self, label: str, total: int, *, width: int = 30):
        self.label = label
        self.total = max(int(total), 0)
        self.width = max(int(width), 1)
        self.current = 0
        self._stream = sys.stderr
        self._enabled = self.total > 0

    def __enter__(self):
        self.update(0)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._enabled:
            if exc_type is None and self.current < self.total:
                self.current = self.total
                self._write()
            self._stream.write("\n")
            self._stream.flush()

    def update(self, step: int = 1):
        if not self._enabled:
            return
        self.current = min(self.total, self.current + step)
        self._write()

    def _write(self):
        fraction = self.current / self.total if self.total else 1.0
        filled = int(round(self.width * fraction))
        bar = "#" * filled + "-" * (self.width - filled)
        percent = int(round(100 * fraction))
        self._stream.write(
            f"\r{self.label} [{bar}] {self.current}/{self.total} ({percent:3d}%)"
        )
        self._stream.flush()


def _write_gif(
    frames,
    output_gif: str,
    fps: int = 8,
    size_text: str = "1100x1100",
    dpi: int = 140,
    padding: float = 0.30,
    xshift: float = 0.0,
    yshift: float = 0.0,
    progress_label: Optional[str] = None,
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
    progress = _ProgressBar(progress_label, len(frames)) if progress_label else None
    progress_context = progress if progress is not None else _nullcontext()
    with progress_context as progress_bar:
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
            if progress_bar is not None:
                progress_bar.update()

    duration = 1.0 / max(fps, 1)
    imageio.mimsave(output_gif, images, duration=duration)


class _nullcontext:
    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, tb):
        return False


def _load_frames(poscar_files: Iterable[str]):
    from ase.io import read

    frames = []
    for path in poscar_files:
        frames.append(read(path, format="vasp"))
    return frames


def _unit_vector(vector, *, label: str):
    vector = np.asarray(vector, dtype=float)
    norm = float(np.linalg.norm(vector))
    if norm <= 1.0e-12:
        raise RuntimeError(f"Cannot define preview direction from zero {label}")
    return vector / norm


def _screen_transform(first_axis, second_axis):
    screen_x = _unit_vector(first_axis, label="screen x axis")
    second_axis = np.asarray(second_axis, dtype=float)
    screen_y = _unit_vector(
        second_axis - np.dot(second_axis, screen_x) * screen_x,
        label="screen y axis",
    )
    view_normal = _unit_vector(
        np.cross(screen_x, screen_y),
        label="view normal",
    )
    return np.vstack([screen_x, screen_y, view_normal])


def _transform_frames(frames, transform):
    transformed = []
    transform = np.asarray(transform, dtype=float)
    for source in frames:
        atoms = source.copy()
        atoms.positions = atoms.positions @ transform.T
        atoms.set_cell(atoms.cell.array @ transform.T, scale_atoms=False)
        transformed.append(atoms)
    return transformed


def _observed_gamma_slip(frames):
    if not frames:
        raise RuntimeError(
            "Gamma view projection requires at least one displacement frame"
        )
    first = frames[0]
    if len(frames) < 2:
        return _unit_vector(
            first.cell.array[0],
            label="generated Gamma slip axis",
        )
    first_scaled = first.get_scaled_positions(wrap=False)
    for candidate in frames[1:]:
        if len(first) != len(candidate):
            raise RuntimeError("Gamma preview frames have inconsistent atom counts")
        raw_fractional_delta = (
            candidate.get_scaled_positions(wrap=False) - first_scaled
        )
        minimum_image_delta = (
            raw_fractional_delta - np.round(raw_fractional_delta)
        )
        for fractional_delta in (minimum_image_delta, raw_fractional_delta):
            cartesian_delta = fractional_delta @ first.cell.array
            moving = cartesian_delta[
                np.linalg.norm(cartesian_delta, axis=1) > 1.0e-8
            ]
            if len(moving):
                mean_slip = np.mean(moving, axis=0)
                if np.linalg.norm(mean_slip) > 1.0e-12:
                    return _unit_vector(
                        mean_slip,
                        label="observed Gamma slip",
                    )

    # A one-step scan can end at a periodically equivalent structure whose
    # coordinates have already been wrapped. Gamma orients the generated slab
    # a axis along the slip direction, so it remains an unambiguous fallback.
    return _unit_vector(
        first.cell.array[0],
        label="generated Gamma slip axis",
    )


def _gamma_slip_axis(frames, *, use_cell_axis: bool = False):
    if not frames:
        raise RuntimeError(
            "Gamma view projection requires at least one displacement frame"
        )
    if use_cell_axis:
        # GammaSurface traverses two independent displacement directions, so
        # frame-to-frame motion is not a stable definition of its primary slip
        # axis.  Its generated slab a axis is the resolved x slip direction.
        return _unit_vector(
            frames[0].cell.array[0],
            label="generated Gamma slip axis",
        )
    return _observed_gamma_slip(frames)


def _slip_plane_transform(frames, *, use_cell_axis: bool = False):
    slip = _gamma_slip_axis(frames, use_cell_axis=use_cell_axis)
    cell = np.asarray(frames[0].cell.array, dtype=float)
    normal = _unit_vector(
        np.cross(cell[0], cell[1]),
        label="generated slip-plane normal",
    )
    if np.dot(normal, cell[2]) < 0:
        normal *= -1
    normal = _unit_vector(
        normal - np.dot(normal, slip) * slip,
        label="orthogonalized slip-plane normal",
    )
    in_plane_y = _unit_vector(
        np.cross(normal, slip),
        label="second slip-plane axis",
    )
    return np.vstack([slip, in_plane_y, normal])


def _resolved_gamma_view_context(prop_obj):
    from apex.core.lib.trans_tools import direction_miller_bravais_to_miller
    from apex.core.lib.trans_tools import plane_miller_bravais_to_miller

    parent = getattr(prop_obj, "conv_std_structure", None)
    plane = getattr(prop_obj, "plane_miller", None)
    slip_direction = getattr(prop_obj, "slip_direction", None)
    if parent is None or plane is None or slip_direction is None:
        raise RuntimeError(
            "Gamma view projection requires resolved structure and slip-system data"
        )

    if getattr(prop_obj, "structure_type", None) == "hcp":
        if len(plane) == 4:
            plane = plane_miller_bravais_to_miller(plane)
        if len(slip_direction) == 4:
            slip_direction = direction_miller_bravais_to_miller(slip_direction)

    plane = np.asarray(plane, dtype=float)
    slip_direction = np.asarray(slip_direction, dtype=float)
    if plane.shape != (3,) or slip_direction.shape != (3,):
        raise RuntimeError(
            "Gamma view projection requires three-index plane_miller and "
            "slip_direction values after crystallographic conversion"
        )
    return parent, plane, slip_direction


def _parent_bc_transform(
    parent,
    frames,
    plane,
    slip_direction,
    *,
    use_cell_axis: bool = False,
):
    plane = np.asarray(plane, dtype=float)
    slip_direction = np.asarray(slip_direction, dtype=float)

    slip_parent = slip_direction @ parent.lattice.matrix
    normal_parent = plane @ parent.lattice.reciprocal_lattice.matrix
    ex_parent = _unit_vector(slip_parent, label="parent slip direction")
    ez_parent = _unit_vector(
        normal_parent - np.dot(normal_parent, ex_parent) * ex_parent,
        label="parent slip-plane normal",
    )
    ey_parent = _unit_vector(
        np.cross(ez_parent, ex_parent),
        label="parent second in-plane axis",
    )

    ex_slab = _gamma_slip_axis(frames, use_cell_axis=use_cell_axis)
    cell = np.asarray(frames[0].cell.array, dtype=float)
    ez_slab = _unit_vector(
        np.cross(cell[0], cell[1]),
        label="generated slip-plane normal",
    )
    if np.dot(ez_slab, cell[2]) < 0:
        ez_slab *= -1
    ez_slab = _unit_vector(
        ez_slab - np.dot(ez_slab, ex_slab) * ex_slab,
        label="orthogonalized generated slip-plane normal",
    )
    ey_slab = _unit_vector(
        np.cross(ez_slab, ex_slab),
        label="generated second in-plane axis",
    )

    parent_basis = np.vstack([ex_parent, ey_parent, ez_parent])
    slab_basis = np.vstack([ex_slab, ey_slab, ez_slab])

    def map_parent_vector(vector):
        components = parent_basis @ np.asarray(vector, dtype=float)
        return components @ slab_basis

    parent_b_slab = map_parent_vector(parent.lattice.matrix[1])
    parent_c_slab = map_parent_vector(parent.lattice.matrix[2])
    return _screen_transform(parent_b_slab, parent_c_slab)


def _gamma_frames_for_view(
    frames,
    view: str,
    *,
    parent_structure,
    plane_miller,
    slip_direction,
    use_cell_axis: bool = False,
):
    if view == "default":
        return [frame.copy() for frame in frames]
    if view == "slip-plane":
        return _transform_frames(
            frames,
            _slip_plane_transform(frames, use_cell_axis=use_cell_axis),
        )
    if view == "parent-bc":
        return _transform_frames(
            frames,
            _parent_bc_transform(
                parent_structure,
                frames,
                plane_miller,
                slip_direction,
                use_cell_axis=use_cell_axis,
            ),
        )
    raise ValueError(f"Unknown Gamma preview view: {view}")


def _requested_gif_views(
    gif_view: str,
    property_type: Optional[str] = None,
) -> List[str]:
    if gif_view == "auto":
        if property_type in {"gamma", "gamma_surface"}:
            return ["slip-plane", "parent-bc"]
        return ["default"]
    if gif_view == "both":
        return ["slip-plane", "parent-bc"]
    if gif_view in {"default", "slip-plane", "parent-bc"}:
        return [gif_view]
    raise ValueError(f"Unknown GIF view: {gif_view}")


def _view_output_gif_path(output_gif: Path, view: str) -> Path:
    if view == "default":
        return output_gif
    suffix = view.replace("-", "_")
    return output_gif.with_name(f"{output_gif.stem}_{suffix}.gif")


def _arrange_gamma_surface_tasks(task_list: List[str]) -> List[str]:
    indexed_tasks = []
    for fallback_index, task_dir in enumerate(task_list):
        displacement_path = os.path.join(task_dir, "displacement.json")
        if not os.path.isfile(displacement_path):
            return task_list
        displacement = loadfn(displacement_path)
        try:
            idx_x = int(displacement["idx_x"])
            idx_y = int(displacement["idx_y"])
        except (KeyError, TypeError, ValueError):
            return task_list
        indexed_tasks.append((idx_x, idx_y, fallback_index, task_dir))

    def surface_slide_key(item):
        idx_x, idx_y, fallback_index, _ = item
        x_order = idx_x if idx_y % 2 == 0 else -idx_x
        return idx_y, x_order, fallback_index

    return [task_dir for _, _, _, task_dir in sorted(indexed_tasks, key=surface_slide_key)]


def _min_pair_distance(structure) -> float:
    dmat = structure.distance_matrix
    n = dmat.shape[0]
    if n < 2:
        return float("inf")
    iu = np.triu_indices(n, k=1)
    return float(dmat[iu].min())


def _warn_gamma_surface_overlaps(task_list: List[str], threshold: float = 0.2) -> None:
    from pymatgen.core import Structure

    for task_dir in task_list:
        poscar = os.path.join(task_dir, "POSCAR")
        if not os.path.isfile(poscar):
            continue
        structure = Structure.from_file(poscar)
        if _min_pair_distance(structure) < threshold:
            print(
                "Generated Gamma surface contains overlapping atoms.",
                file=sys.stderr,
            )
            return


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
    gif_view: str = "auto",
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
                make_kwargs = {}
                if prop.get("type") == "gamma_surface":
                    make_kwargs["require_relaxation_result"] = False
                task_list = prop_obj.make_confs(
                    str(work_dir), equi_dir, refine=do_refine, **make_kwargs
                )
                if prop.get("type") == "gamma_surface":
                    _warn_gamma_surface_overlaps(task_list)
                    task_list = _arrange_gamma_surface_tasks(task_list)

                poscar_files = [os.path.join(task_dir, "POSCAR") for task_dir in task_list]
                frames = _load_frames(poscar_files)
                property_type = prop.get("type")
                is_gamma_preview = property_type in {"gamma", "gamma_surface"}
                views = _requested_gif_views(gif_view, property_type)
                if not is_gamma_preview and views != ["default"]:
                    raise RuntimeError(
                        "--gif-view slip-plane, parent-bc, and both are "
                        "supported only for type=gamma or type=gamma_surface"
                    )
                parent_structure = None
                plane_miller = None
                slip_direction = None
                if is_gamma_preview and "parent-bc" in views:
                    (
                        parent_structure,
                        plane_miller,
                        slip_direction,
                    ) = _resolved_gamma_view_context(prop_obj)
                for view in views:
                    view_frames = (
                        _gamma_frames_for_view(
                            frames,
                            view,
                            parent_structure=parent_structure,
                            plane_miller=plane_miller,
                            slip_direction=slip_direction,
                            use_cell_axis=property_type == "gamma_surface",
                        )
                        if is_gamma_preview
                        else frames
                    )
                    view_output_gif = _view_output_gif_path(output_gif, view)
                    _write_gif(
                        view_frames,
                        str(view_output_gif),
                        fps=gif_fps,
                        dpi=gif_dpi,
                        padding=gif_padding,
                        xshift=gif_xshift,
                        yshift=gif_yshift,
                        progress_label=f"Loading {view} view...",
                    )
                    output_paths.append(str(view_output_gif))

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
                gif_view=getattr(args, "gif_view", "auto"),
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
    parser.add_argument(
        "--gif-view",
        choices=("auto", "default", "slip-plane", "parent-bc", "both"),
        default="auto",
        help=(
            "Gamma projection: auto writes both scientific views for gamma "
            "and gamma_surface; alternatively preserve the legacy Cartesian "
            "view, look normal to the slip plane, look normal to the parent "
            "bc plane, or explicitly write both views"
        ),
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
