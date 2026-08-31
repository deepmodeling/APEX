import unittest

from apex.core.structure import (
    normalize_parent_lattice_hint,
    resolve_parent_lattice_hint,
)


class TestParentLatticeHint(unittest.TestCase):
    def test_no_hint_preserves_detected_type(self):
        self.assertEqual(
            resolve_parent_lattice_hint("other"),
            ("other", "auto_detected"),
        )

    def test_hint_overrides_detected_type(self):
        self.assertEqual(
            resolve_parent_lattice_hint("other", "BCC"),
            ("bcc", "user_override"),
        )

    def test_normalization_and_validation(self):
        self.assertEqual(normalize_parent_lattice_hint(" HCP "), "hcp")
        with self.assertRaisesRegex(ValueError, "bcc, fcc, hcp"):
            normalize_parent_lattice_hint("diamond")


if __name__ == "__main__":
    unittest.main()
