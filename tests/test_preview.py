import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr

from pymatgen.core import Lattice, Structure

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
__package__ = "tests"

from apex import preview as preview_mod  # noqa: E402
from apex.preview import _min_pair_distance, _warn_gamma_surface_overlaps  # noqa: E402


class TestPreviewHelpers(unittest.TestCase):
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
