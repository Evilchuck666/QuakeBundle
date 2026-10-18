"""Reject incomplete build environments before producing a broken executable."""

import runpy
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch


class AnalysisReached(Exception):
    """Stop before running the real PyInstaller build in unit tests."""


class BuildSpecTests(unittest.TestCase):
    def execute_spec(self, platform, broken=None):
        root = Path(__file__).resolve().parents[1]
        hooks = ModuleType("PyInstaller.utils.hooks")
        hooks.collect_all = Mock(return_value=([], [], []))
        analysis = Mock(side_effect=AnalysisReached)

        def import_dependency(name):
            if name == broken:
                raise ImportError("synthetic missing module or DLL")
            return Mock()

        with (
            patch.dict(sys.modules, {"PyInstaller.utils.hooks": hooks}),
            patch.object(sys, "platform", platform),
            patch("importlib.import_module", side_effect=import_dependency) as imports,
        ):
            if broken:
                with self.assertRaises(SystemExit) as error:
                    runpy.run_path(
                        str(root / "QuakeBundle.spec"),
                        init_globals={
                            "SPECPATH": str(root),
                            "Analysis": analysis,
                        },
                    )
                self.assertIn(broken, str(error.exception))
                self.assertIn("requirements-build.txt", str(error.exception))
                self.assertIn(sys.executable, str(error.exception))
                analysis.assert_not_called()
                hooks.collect_all.assert_not_called()
            else:
                with self.assertRaises(AnalysisReached):
                    runpy.run_path(
                        str(root / "QuakeBundle.spec"),
                        init_globals={
                            "SPECPATH": str(root),
                            "Analysis": analysis,
                        },
                    )
                analysis.assert_called_once()
            return [call.args[0] for call in imports.call_args_list]

    def test_missing_qt_stops_before_analysis(self):
        self.execute_spec("win32", "PySide6.QtCore")

    def test_missing_windows_console_support_stops_before_analysis(self):
        self.execute_spec("win32", "winpty")

    def test_complete_windows_environment_reaches_analysis(self):
        self.assertIn("winpty", self.execute_spec("win32"))

    def test_linux_does_not_require_windows_dependency(self):
        self.assertNotIn("winpty", self.execute_spec("linux"))

    def test_all_platforms_embed_binaries_and_resources_in_the_executable(self):
        root = Path(__file__).resolve().parents[1]
        hooks = ModuleType("PyInstaller.utils.hooks")
        hooks.collect_all = Mock(return_value=([], [], []))
        analysis = SimpleNamespace(
            pure=[], scripts=[], binaries=["Qt"], datas=["assets"]
        )
        for platform in ("linux", "win32", "darwin"):
            exe = Mock()
            with (
                self.subTest(platform=platform),
                patch.dict(sys.modules, {"PyInstaller.utils.hooks": hooks}),
                patch.object(sys, "platform", platform),
                patch("importlib.import_module"),
            ):
                # COLLECT and BUNDLE are deliberately unavailable: the output
                # must not require a separate directory or application bundle.
                runpy.run_path(
                    str(root / "QuakeBundle.spec"),
                    init_globals={
                        "SPECPATH": str(root),
                        "Analysis": Mock(return_value=analysis),
                        "PYZ": Mock(return_value="bytecode"),
                        "EXE": exe,
                    },
                )
            exe.assert_called_once()
            self.assertIn(analysis.binaries, exe.call_args.args)
            self.assertIn(analysis.datas, exe.call_args.args)
            self.assertFalse(exe.call_args.kwargs.get("exclude_binaries", False))
