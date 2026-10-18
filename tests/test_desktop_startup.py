"""Startup errors must remain visible without Qt or a Windows console."""

import io
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import quakebundle


class StartupTests(unittest.TestCase):
    def test_source_failure_points_to_standalone_package_without_pip(self):
        output = io.StringIO()
        with (
            patch.object(
                quakebundle, "main", side_effect=ModuleNotFoundError("PySide6")
            ),
            patch.object(sys, "stderr", output),
            patch.object(sys, "frozen", False, create=True),
        ):
            self.assertEqual(quakebundle.run(), 1)
        self.assertIn("ModuleNotFoundError: PySide6", output.getvalue())
        self.assertIn("standalone package", output.getvalue())
        self.assertIn("QuakeBundle.exe", output.getvalue())
        self.assertNotIn("pip install", output.getvalue())

    def test_pythonw_reports_failure_with_native_dialog_without_qt(self):
        dialog = Mock()
        windows = SimpleNamespace(user32=SimpleNamespace(MessageBoxW=dialog))
        with (
            patch.object(
                quakebundle, "main", side_effect=ImportError("Qt DLL missing")
            ),
            patch.object(sys, "stderr", None),
            patch.object(sys, "platform", "win32"),
            patch.object(sys, "executable", "C:/Python/pythonw.exe"),
            patch.object(sys, "frozen", False, create=True),
            patch("ctypes.windll", windows, create=True),
        ):
            self.assertEqual(quakebundle.run(), 1)
        dialog.assert_called_once()
        message = dialog.call_args.args[1]
        self.assertIn("Qt DLL missing", message)
        self.assertIn("QuakeBundle.exe", message)
        self.assertNotIn("pip install", message)

    def test_frozen_failure_does_not_suggest_pip(self):
        output = io.StringIO()
        with (
            patch.object(quakebundle, "main", side_effect=ImportError("missing DLL")),
            patch.object(sys, "stderr", output),
            patch.object(sys, "frozen", True, create=True),
        ):
            self.assertEqual(quakebundle.run(), 1)
        self.assertIn("missing DLL", output.getvalue())
        self.assertNotIn("pip install", output.getvalue())

    def test_help_keeps_successful_exit(self):
        with (
            patch.object(sys, "stdout", io.StringIO()),
            self.assertRaises(SystemExit) as result,
        ):
            quakebundle.run(["--help"])
        self.assertEqual(result.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
