import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np
from ase import Atoms
from monty.serialization import dumpfn
from pymatgen.core import Lattice, Structure

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
__package__ = "tests"

from apex import preview as preview_mod  # noqa: E402
from apex.preview import (  # noqa: E402
    _gamma_frames_for_view,
    _min_pair_distance,
    _prepare_equilibrium_dir,
    _requested_gif_views,
    _resolved_gamma_view_context,
    _slip_plane_transform,
    _structure_bounds,
    _view_output_gif_path,
    _warn_gamma_surface_overlaps,
)


class TestPreviewHelpers(unittest.TestCase):
    def test_requested_gif_views(self):
        self.assertEqual(
            _requested_gif_views("both"),
            ["slip-plane", "parent-bc"],
        )
        self.assertEqual(_requested_gif_views("default"), ["default"])
        self.assertEqual(
            _requested_gif_views("auto", "gamma"),
            ["slip-plane", "parent-bc"],
        )
        self.assertEqual(
            _requested_gif_views("auto", "gamma_surface"),
            ["slip-plane", "parent-bc"],
        )
        self.assertEqual(_requested_gif_views("auto", "vacancy"), ["default"])
        with self.assertRaisesRegex(ValueError, "Unknown GIF view"):
            _requested_gif_views("invalid")

    def test_preview_parser_defaults_gamma_views_to_auto(self):
        args = preview_mod.build_parser().parse_args(["param.json"])
        self.assertEqual(args.gif_view, "auto")

        from apex.main import parse_args

        with mock.patch.object(
            sys,
            "argv",
            ["apex", "preview", "param.json"],
        ):
            _, main_args = parse_args()
        self.assertEqual(main_args.gif_view, "auto")

    def test_main_preview_prints_generated_paths(self):
        import apex.main as main_mod

        with mock.patch.object(
            sys,
            "argv",
            ["apex", "preview", "param.json"],
        ), mock.patch.object(
            preview_mod,
            "preview_from_args",
            return_value=["first.gif", "second.gif"],
        ), mock.patch("builtins.print") as print_mock:
            main_mod.main()

        print_mock.assert_has_calls(
            [mock.call("first.gif"), mock.call("second.gif")]
        )

    def test_structure_bounds_include_projected_unit_cell(self):
        atoms = Atoms(
            "H",
            positions=[[2.0, 12.0, 0.0]],
            cell=[[4.0, 0.0, 0.0], [0.0, 0.0, 4.0], [0.0, 24.0, 0.0]],
            pbc=True,
        )

        x_min, x_max, y_min, y_max = _structure_bounds(atoms, 0.35)

        self.assertLessEqual(x_min, 0.0)
        self.assertGreaterEqual(x_max, 4.0)
        self.assertLessEqual(y_min, 0.0)
        self.assertGreaterEqual(y_max, 24.0)

    def test_view_output_paths_preserve_legacy_default(self):
        base = Path("/tmp/example.gif")
        self.assertEqual(_view_output_gif_path(base, "default"), base)
        self.assertEqual(
            _view_output_gif_path(base, "slip-plane"),
            Path("/tmp/example_slip_plane.gif"),
        )
        self.assertEqual(
            _view_output_gif_path(base, "parent-bc"),
            Path("/tmp/example_parent_bc.gif"),
        )

    def test_prepare_equilibrium_dir_adds_preview_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            source.mkdir()
            Structure(
                Lattice.cubic(4.0),
                ["H"],
                [[0.0, 0.0, 0.0]],
            ).to(filename=source / "POSCAR", fmt="poscar")
            prepared = Path(
                _prepare_equilibrium_dir(
                    str(source),
                    Path(tmp) / "preview",
                    "test",
                )
            )
            self.assertTrue((prepared / "CONTCAR").is_file())
            self.assertEqual(
                (prepared / "result.json").read_text(),
                '{"preview_only": true}\n',
            )

    def test_slip_plane_view_looks_along_generated_normal(self):
        cell = np.diag([10.0, 10.0, 10.0])
        first = Atoms(
            "HH",
            positions=[[1.0, 1.0, 1.0], [5.0, 5.0, 5.0]],
            cell=cell,
            pbc=True,
        )
        second = first.copy()
        second.positions[1, 0] += 0.5
        transform = _slip_plane_transform([first, second])
        np.testing.assert_allclose(transform[0], [1.0, 0.0, 0.0])
        np.testing.assert_allclose(transform[2], [0.0, 0.0, 1.0])

    def test_slip_plane_view_handles_full_period_endpoint(self):
        first = Atoms(
            "HH",
            scaled_positions=[[0.1, 0.1, 0.1], [0.5, 0.5, 0.5]],
            cell=np.diag([10.0, 10.0, 10.0]),
            pbc=True,
        )
        second = first.copy()
        second.set_scaled_positions(
            [[0.1, 0.1, 0.1], [1.5, 0.5, 0.5]]
        )

        transform = _slip_plane_transform([first, second])

        np.testing.assert_allclose(transform[0], [1.0, 0.0, 0.0])

    def test_resolved_gamma_view_context_uses_crystal_override(self):
        parent = Structure(
            Lattice.cubic(4.0),
            ["Mo", "Mo"],
            [[0.0, 0.0, 0.0], [0.5, 0.5, 0.5]],
        )
        prop_obj = SimpleNamespace(
            conv_std_structure=parent,
            structure_type="bcc",
            plane_miller=[1, 1, 0],
            slip_direction=[-1, 1, 1],
        )

        resolved_parent, plane, direction = _resolved_gamma_view_context(
            prop_obj
        )

        self.assertIs(resolved_parent, parent)
        np.testing.assert_allclose(plane, [1.0, 1.0, 0.0])
        np.testing.assert_allclose(direction, [-1.0, 1.0, 1.0])

    def test_resolved_gamma_view_context_converts_hcp_indices(self):
        parent = Structure(
            Lattice.hexagonal(3.0, 4.8),
            ["Ti", "Ti"],
            [[0.0, 0.0, 0.0], [1 / 3, 2 / 3, 0.5]],
        )
        prop_obj = SimpleNamespace(
            conv_std_structure=parent,
            structure_type="hcp",
            plane_miller=[0, 0, 0, 1],
            slip_direction=[2, -1, -1, 0],
        )

        _, plane, direction = _resolved_gamma_view_context(prop_obj)

        np.testing.assert_allclose(plane, [0.0, 0.0, 1.0])
        np.testing.assert_allclose(direction, [3.0, 0.0, 0.0])

    def test_parent_bc_view_looks_along_parent_bc_normal(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent_path = Path(tmp) / "POSCAR"
            Structure(
                Lattice.cubic(10.0),
                ["H", "H"],
                [[0.1, 0.1, 0.1], [0.5, 0.5, 0.5]],
            ).to(filename=parent_path, fmt="poscar")
            first = Atoms(
                "HH",
                positions=[[1.0, 1.0, 1.0], [5.0, 5.0, 5.0]],
                cell=np.diag([10.0, 10.0, 10.0]),
                pbc=True,
            )
            second = first.copy()
            second.positions[1, 0] += 0.5
            transformed = _gamma_frames_for_view(
                [first, second],
                "parent-bc",
                parent_structure=Structure.from_file(parent_path),
                plane_miller=[0, 1, 0],
                slip_direction=[1, 0, 0],
            )
            original_a = np.array([10.0, 0.0, 0.0])
            transformed_a = transformed[0].cell.array[0]
            self.assertAlmostEqual(transformed_a[0], 0.0)
            self.assertAlmostEqual(transformed_a[1], 0.0)
            self.assertAlmostEqual(abs(transformed_a[2]), np.linalg.norm(original_a))

    def test_gamma_surface_view_uses_primary_cell_axis(self):
        first = Atoms(
            "HH",
            positions=[[1.0, 1.0, 1.0], [5.0, 5.0, 5.0]],
            cell=np.diag([10.0, 10.0, 30.0]),
            pbc=True,
        )
        second = first.copy()
        # A two-dimensional surface traversal can move along the secondary
        # direction first; the primary view must remain tied to slab a.
        second.positions[1, 1] += 0.5

        transformed = _gamma_frames_for_view(
            [first, second],
            "slip-plane",
            parent_structure=None,
            plane_miller=None,
            slip_direction=None,
            use_cell_axis=True,
        )

        np.testing.assert_allclose(transformed[0].cell.array[0], [10.0, 0.0, 0.0])

    def test_gamma_family_preview_defaults_to_two_views_with_20a_vacuum(self):
        def normal_height(atoms):
            cell = np.asarray(atoms.cell.array, dtype=float)
            normal = np.cross(cell[0], cell[1])
            normal /= np.linalg.norm(normal)
            return abs(float(np.dot(cell[2], normal)))

        for property_type in ("gamma", "gamma_surface"):
            with self.subTest(
                property_type=property_type
            ), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                structure_dir = root / "bcc"
                structure_dir.mkdir()
                Structure(
                    Lattice.cubic(3.2),
                    ["Mo", "Mo"],
                    [[0.0, 0.0, 0.0], [0.5, 0.5, 0.5]],
                ).to(filename=structure_dir / "POSCAR", fmt="poscar")

                prop = {
                    "type": property_type,
                    "req_calc": True,
                    "plane_miller": [1, 1, 0],
                    "slip_direction": [1, -1, -1],
                    "supercell_size": [1, 1, 2],
                }
                if property_type == "gamma":
                    prop["n_steps"] = 1
                else:
                    prop.update(
                        {
                            "closed_loop": False,
                            "n_steps_x": 1,
                            "n_steps_y": 1,
                        }
                    )
                payload = {
                    "structures": ["bcc"],
                    "interaction": {"type": "vasp"},
                    "properties": [prop],
                }
                parameter_path = root / "param.json"
                dumpfn(payload, parameter_path)

                rendered = []

                def capture(frames, output_gif, **_kwargs):
                    rendered.append((Path(output_gif), frames))

                with mock.patch.object(
                    preview_mod,
                    "_write_gif",
                    side_effect=capture,
                ):
                    outputs = preview_mod.preview_parameter_file(
                        str(parameter_path)
                    )

                self.assertEqual(
                    [Path(path).name for path in outputs],
                    ["param_slip_plane.gif", "param_parent_bc.gif"],
                )
                self.assertEqual(len(rendered), 2)
                default_vacuum_height = normal_height(rendered[0][1][0])

                prop["vacuum_size"] = 0
                dumpfn(payload, parameter_path)
                zero_vacuum_rendered = []

                def capture_zero(frames, output_gif, **_kwargs):
                    zero_vacuum_rendered.append((Path(output_gif), frames))

                with mock.patch.object(
                    preview_mod,
                    "_write_gif",
                    side_effect=capture_zero,
                ):
                    preview_mod.preview_parameter_file(str(parameter_path))

                zero_vacuum_height = normal_height(
                    zero_vacuum_rendered[0][1][0]
                )
                self.assertAlmostEqual(
                    default_vacuum_height - zero_vacuum_height,
                    20.0,
                    places=6,
                )

    def test_min_pair_distance_detects_overlap(self):
        overlapping = Structure(
            Lattice.cubic(10.0),
            ["H", "H"],
            [[0.0, 0.0, 0.0], [0.01, 0.0, 0.0]],
            coords_are_cartesian=True,
        )
        separated = Structure(
            Lattice.cubic(10.0),
            ["H", "H"],
            [[0.0, 0.0, 0.0], [2.0, 0.0, 0.0]],
            coords_are_cartesian=True,
        )
        self.assertLess(_min_pair_distance(overlapping), 0.2)
        self.assertGreater(_min_pair_distance(separated), 0.2)

    def test_warn_gamma_surface_overlaps_prints_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            task0 = os.path.join(tmp, "task.000000")
            task1 = os.path.join(tmp, "task.000001")
            os.makedirs(task0)
            os.makedirs(task1)
            Structure(
                Lattice.cubic(10.0),
                ["H", "H"],
                [[0.0, 0.0, 0.0], [0.01, 0.0, 0.0]],
                coords_are_cartesian=True,
            ).to(filename=os.path.join(task0, "POSCAR"), fmt="poscar")
            Structure(
                Lattice.cubic(10.0),
                ["H", "H"],
                [[0.0, 0.0, 0.0], [2.0, 0.0, 0.0]],
                coords_are_cartesian=True,
            ).to(filename=os.path.join(task1, "POSCAR"), fmt="poscar")

            buf = io.StringIO()
            with redirect_stderr(buf):
                _warn_gamma_surface_overlaps([task0, task1])
            self.assertIn(
                "Generated Gamma surface contains overlapping atoms.",
                buf.getvalue(),
            )

    def test_warn_gamma_surface_overlaps_silent_when_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            task0 = os.path.join(tmp, "task.000000")
            os.makedirs(task0)
            Structure(
                Lattice.cubic(10.0),
                ["H", "H"],
                [[0.0, 0.0, 0.0], [2.0, 0.0, 0.0]],
                coords_are_cartesian=True,
            ).to(filename=os.path.join(task0, "POSCAR"), fmt="poscar")
            buf = io.StringIO()
            with redirect_stderr(buf):
                _warn_gamma_surface_overlaps([task0])
            self.assertEqual(buf.getvalue(), "")

    def test_preview_source_skips_post_process(self):
        with open(preview_mod.__file__, encoding="utf-8") as fp:
            source = fp.read()
        self.assertNotIn("prop_obj.post_process(task_list)", source)
        self.assertIn("_warn_gamma_surface_overlaps(task_list)", source)


if __name__ == "__main__":
    unittest.main()
