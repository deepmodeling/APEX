import sys
import unittest
from unittest.mock import patch

from apex.main import parse_args


class TestAccountCliParser(unittest.TestCase):
    def test_account_parser(self):
        argv = [
            "apex", "account",
            "--email", "demo@example.com",
            "--program-id", "1234",
            "--show"
        ]
        with patch.object(sys, "argv", argv):
            _, args = parse_args()

        self.assertEqual(args.cmd, "account")
        self.assertEqual(args.email, "demo@example.com")
        self.assertEqual(args.program_id, 1234)
        self.assertTrue(args.show)
