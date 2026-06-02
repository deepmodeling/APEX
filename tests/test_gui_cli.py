import sys
import unittest
from unittest.mock import patch

from apex.main import parse_args


class TestGuiCliParser(unittest.TestCase):
    def test_gui_defaults(self):
        with patch.object(sys, "argv", ["apex", "gui"]):
            _, args = parse_args()

        self.assertEqual(args.cmd, "gui")
        self.assertEqual(args.host, "127.0.0.1")
        self.assertEqual(args.port, 8060)
        self.assertFalse(args.no_browser)

    def test_gui_custom_options(self):
        argv = ["apex", "gui", "-H", "0.0.0.0", "-p", "9001", "--no-browser"]
        with patch.object(sys, "argv", argv):
            _, args = parse_args()

        self.assertEqual(args.cmd, "gui")
        self.assertEqual(args.host, "0.0.0.0")
        self.assertEqual(args.port, 9001)
        self.assertTrue(args.no_browser)

    def test_report_no_browser_option(self):
        with patch.object(sys, "argv", ["apex", "report", "--no-browser"]):
            _, args = parse_args()

        self.assertEqual(args.cmd, "report")
        self.assertTrue(args.no_browser)

    def test_report_custom_host_port(self):
        with patch.object(sys, "argv", ["apex", "report", "-H", "0.0.0.0", "-p", "8071"]):
            _, args = parse_args()

        self.assertEqual(args.cmd, "report")
        self.assertEqual(args.host, "0.0.0.0")
        self.assertEqual(args.port, 8071)
