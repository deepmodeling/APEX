import sys
import tempfile
import unittest
from pathlib import Path


BENCHMARK_DIR = (
    Path(__file__).resolve().parents[1]
    / "apex"
    / "skills"
    / "apex-flow"
    / "benchmarks"
    / "dpa4-alloytongqi"
)
sys.path.insert(0, str(BENCHMARK_DIR))

from run_phonolammps_smoke import (  # noqa: E402
    _poscar_atom_count,
    validate_force_constants,
)


def _force_constants_text(atom_count: int = 2) -> str:
    lines = [str(atom_count)]
    for first in range(1, atom_count + 1):
        for second in range(1, atom_count + 1):
            lines.extend(
                (
                    "",
                    f"{first} {second}",
                    "1.0 0.0 0.0",
                    "0.0 1.0 0.0",
                    "0.0 0.0 1.0",
                )
            )
    return "\n".join(lines) + "\n"


class TestDPA4ForceConstantsValidation(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def _write(self, text: str) -> Path:
        path = self.root / "FORCE_CONSTANTS"
        path.write_text(text, encoding="utf-8")
        return path

    def test_complete_finite_force_constants_pass(self):
        result = validate_force_constants(self._write(_force_constants_text()), 2)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["atom_count"], 2)
        self.assertEqual(result["matrix_blocks"], 4)
        self.assertEqual(result["finite_values"], 36)
        self.assertIsNone(result["error"])

    def test_atom_count_mismatch_fails(self):
        with self.assertRaisesRegex(ValueError, "atom count 2 != expected 3"):
            validate_force_constants(self._write(_force_constants_text()), 3)

    def test_truncated_matrix_fails(self):
        text = _force_constants_text().rsplit("0.0 0.0 1.0", 1)[0]
        with self.assertRaisesRegex(ValueError, "truncated in matrix block"):
            validate_force_constants(self._write(text), 2)

    def test_non_finite_matrix_fails(self):
        text = _force_constants_text().replace("1.0 0.0 0.0", "nan 0.0 0.0", 1)
        with self.assertRaisesRegex(ValueError, "non-finite value"):
            validate_force_constants(self._write(text), 2)

    def test_trailing_data_fails(self):
        with self.assertRaisesRegex(ValueError, "trailing non-empty data"):
            validate_force_constants(
                self._write(_force_constants_text() + "unexpected\n"), 2
            )

    def test_poscar_atom_count_vasp5(self):
        poscar = self.root / "POSCAR"
        poscar.write_text(
            "TiV\n1.0\n1 0 0\n0 1 0\n0 0 1\nTi V\n1 2\nDirect\n"
            "0 0 0\n0.5 0.5 0.5\n0.25 0.25 0.25\n",
            encoding="utf-8",
        )
        self.assertEqual(_poscar_atom_count(poscar), 3)


if __name__ == "__main__":
    unittest.main()
