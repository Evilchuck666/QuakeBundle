"""Offline platform checks shared by the two independently installed launchers."""

from __future__ import annotations

import contextlib
import ctypes
import importlib.util
import io
import os
import plistlib
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


class PlatformChecksMixin:
    """Shared checks for concrete unittest.TestCase subclasses, never run alone."""

    engine_name: str
    appimage: str
    windows_exe: str
    autoexec: str
    config_dir: str
    answers: str

    def _prepare_platform(self):
        """Create an isolated installation and register cleanup with the test case."""
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(
            self.stack.enter_context(
                tempfile.TemporaryDirectory(prefix="quake é ' $ ; & ")
            )
        ).resolve()
        self.source = ROOT / "assets" / "scripts" / self.engine_name / "launcher.py"
        spec = importlib.util.spec_from_file_location(
            self.engine_name + "_platform", self.source
        )
        self.launcher = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.launcher)
        self.stack.enter_context(
            patch.object(self.launcher, "__file__", str(self.root / "launcher.py"))
        )
        self.system = self.stack.enter_context(
            patch.object(self.launcher.platform, "system", return_value="Linux")
        )
        self.stack.enter_context(patch.dict(os.environ))
        self.stdout = self.stack.enter_context(
            contextlib.redirect_stdout(io.StringIO())
        )
        self.stderr = self.stack.enter_context(
            contextlib.redirect_stderr(io.StringIO())
        )

    def _executable(self, name):
        """Create a placeholder executable in the temporary installation."""
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake engine")
        path.chmod(0o755)
        return path

    def _bundle(self, name=None, executable="quake engine"):
        """Build a macOS bundle whose metadata names its internal executable."""
        app = self.root / (name or self.engine_name + ".app")
        binary = self._executable(str(app / "Contents" / "MacOS" / executable))
        (app / "Contents" / "Info.plist").write_bytes(
            plistlib.dumps({"CFBundleExecutable": executable})
        )
        return app, binary

    def test_selects_only_the_current_platform_engine(self):
        """Select the matching engine when all platform builds are present."""
        linux = self._executable(self.appimage)
        windows = self._executable(self.windows_exe)
        _, mac = self._bundle()
        self._executable("uninstall.exe")
        for system, expected in (
            ("Linux", linux),
            ("Windows", windows),
            ("Darwin", mac),
        ):
            with self.subTest(system=system):
                self.system.return_value = system
                self.assertEqual(self.launcher.resolve_engine(self.root), expected)

    def test_missing_engine_and_unsupported_system_fail_before_setup(self):
        """Reject missing engines and unsupported systems before changing configuration."""
        for system in ("Linux", "Windows", "Darwin", "FreeBSD"):
            with (
                self.subTest(system=system),
                patch.object(self.launcher, "ensure_setup") as setup,
            ):
                self.system.return_value = system
                self.assertEqual(self.launcher.main([]), 1)
                setup.assert_not_called()
        self.assertIn("--engine PATH", self.stderr.getvalue())
        self.assertIn("Unsupported operating system", self.stderr.getvalue())

    def test_multiple_bundles_require_an_explicit_choice(self):
        """Require an override when more than one application bundle matches."""
        self.system.return_value = "Darwin"
        self._bundle()
        app, binary = self._bundle(self.engine_name + "-alternate.app")
        with self.assertRaisesRegex(OSError, "Multiple.*--engine"):
            self.launcher.resolve_engine(self.root)
        self.assertEqual(self.launcher.resolve_engine(self.root, str(app)), binary)

    def test_override_is_relative_to_calling_directory_and_can_use_custom_names(self):
        """Resolve custom paths relative to the caller rather than the installation."""
        engine = self._executable("custom engine.exe")
        # Relative to the real cwd, not to the supplied installation directory.
        # Keep both paths on one volume on Windows CI hosts.
        previous = Path.cwd()
        try:
            os.chdir(self.root.parent)
            relative = str(Path(self.root.name) / engine.name)
            for system in ("Linux", "Windows", "Darwin"):
                with self.subTest(system=system):
                    self.system.return_value = system
                    self.assertEqual(
                        self.launcher.resolve_engine(self.root / "absent", relative),
                        engine,
                    )
        finally:
            os.chdir(previous)

    def test_invalid_overrides_report_actionable_errors(self):
        """Reject missing files, directories and incompatible executable paths."""
        with self.assertRaisesRegex(OSError, "not found"):
            self.launcher.resolve_engine(self.root, str(self.root / "absent"))
        with self.assertRaisesRegex(OSError, "not found"):
            self.launcher.resolve_engine(self.root, str(self.root))
        engine = self._executable("engine")
        with (
            patch.object(self.launcher.os, "access", return_value=False),
            self.assertRaisesRegex(OSError, "not executable"),
        ):
            self.launcher.resolve_engine(self.root, str(engine))
        self.system.return_value = "Windows"
        with self.assertRaisesRegex(OSError, "requires an .exe"):
            self.launcher.resolve_engine(self.root, str(engine))

    def test_invalid_bundles_fail_without_guessing_a_binary(self):
        """Reject incomplete or malformed bundle metadata and missing executables."""
        self.system.return_value = "Darwin"
        app, binary = self._bundle()
        metadata = app / "Contents" / "Info.plist"
        for info in (
            {},
            [],
            {"CFBundleExecutable": "../escape"},
            {"CFBundleExecutable": "/absolute"},
            {"CFBundleExecutable": 3},
        ):
            with self.subTest(info=info):
                metadata.write_bytes(plistlib.dumps(info))
                with self.assertRaisesRegex(OSError, "Invalid application bundle"):
                    self.launcher.resolve_engine(self.root)
        for content in (b"garbage", b"<?xml version='1.0'?><plist><dict>"):
            metadata.write_bytes(content)
            with self.assertRaisesRegex(OSError, "Invalid application bundle"):
                self.launcher.resolve_engine(self.root)
        metadata.unlink()
        with self.assertRaisesRegex(OSError, "Invalid application bundle"):
            self.launcher.resolve_engine(self.root)
        metadata.write_bytes(plistlib.dumps({"CFBundleExecutable": binary.name}))
        binary.unlink()
        with self.assertRaisesRegex(OSError, "not found"):
            self.launcher.resolve_engine(self.root)

    def test_engine_option_and_separator_preserve_literal_arguments(self):
        """Parse the engine override while preserving arguments after the separator."""
        path = str(self.root / "a game.exe")
        options = self.launcher.parse_launcher_args(
            ["--engine", path, "--mod", "a mod", "--", "--engine", "$(touch injected)"]
        )
        self.assertEqual(options.engine, path)
        self.assertEqual(
            options.game_args, ["-game", "a mod", "--engine", "$(touch injected)"]
        )
        for args in (["--engine"], ["--engine", "--wizard"], ["--engine", ""]):
            with self.assertRaisesRegex(ValueError, "--engine requires"):
                self.launcher.parse_launcher_args(args)
        self.assertEqual(self.launcher.main(["--engine"]), 2)
        self.assertEqual(self.launcher.main(["--help"]), 0)
        self.assertIn("--engine PATH", self.stdout.getvalue())

    def test_launch_passes_cwd_arguments_and_status_without_a_shell(self):
        """Preserve engine arguments, working directory and exit status on each platform."""
        engine = self._executable("custom engine.exe")
        game_args = ["-game", "a mod", "+name", "Ránger", "$(touch injected)"]
        for system in ("Linux", "Windows", "Darwin"):
            self.system.return_value = system
            with (
                self.subTest(system=system),
                patch.object(self.launcher, "prepare_shared_autoexec") as prepare,
                patch.object(self.launcher.os, "chdir") as chdir,
                patch.object(self.launcher.os, "execv") as execv,
                patch.object(subprocess, "run", return_value=Mock(returncode=7)) as run,
            ):
                expected = [str(engine), "-basedir", str(self.root)]
                if self.engine_name == "ezQuake":
                    expected += ["-nohome"]
                elif system != "Windows":
                    expected += ["-userdir", str(self.root)]
                expected += ["+exec", self.autoexec, *game_args]
                status = self.launcher.launch_engine(self.root, game_args, engine)
                prepare.assert_called_once_with(self.root)
                if system == "Windows":
                    self.assertEqual(status, 7)
                    run.assert_called_once_with(expected, cwd=self.root, check=False)
                    execv.assert_not_called()
                    chdir.assert_not_called()
                else:
                    execv.assert_called_once_with(str(engine), expected)
                    chdir.assert_called_once_with(self.root)
                    run.assert_not_called()

    def test_main_returns_engine_status_and_never_launches_after_setup_failure(self):
        """Start the engine only after successful setup and return its status."""
        engine = self._executable(self.windows_exe)
        self.system.return_value = "Windows"
        with (
            patch.object(self.launcher, "ensure_setup", return_value=0) as setup,
            patch.object(self.launcher, "launch_engine", return_value=7) as launch,
        ):
            self.assertEqual(self.launcher.main(["--wizard", "+map", "start"]), 7)
            setup.assert_called_once_with(force=True)
            launch.assert_called_once_with(self.root, ["+map", "start"], engine)
            launch.reset_mock()
            setup.return_value = 1
            self.assertEqual(self.launcher.main([]), 1)
            launch.assert_not_called()
            setup.side_effect = OSError("terminal unavailable")
            self.assertEqual(self.launcher.main([]), 1)
            launch.assert_not_called()
            self.assertIn("terminal unavailable", self.stderr.getvalue())

    def test_graphics_overrides_are_platform_specific(self):
        """Apply common mouse settings and restrict graphics overrides to Linux."""
        linux_only = (
            "SDL_VIDEO_X11_NET_WM_BYPASS_COMPOSITOR",
            "__GL_SYNC_TO_VBLANK",
            "__GL_MaxFramesAllowed",
        )
        for system in ("Linux", "Windows", "Darwin"):
            for populated in (False, True):
                with (
                    self.subTest(system=system, populated=populated),
                    patch.dict(os.environ, {}, clear=True),
                ):
                    self.system.return_value = system
                    if populated:
                        os.environ.update(dict.fromkeys(linux_only, "custom"))
                    self.launcher.configure_sdl_environment()
                    self.assertEqual(os.environ["SDL_MOUSE_RELATIVE_MODE_WARP"], "0")
                    if system != "Linux":
                        for key in linux_only:
                            self.assertEqual(
                                os.environ.get(key), "custom" if populated else None
                            )
                    else:
                        self.assertEqual(os.environ[linux_only[0]], "1")
                        if self.engine_name == "ezQuake":
                            self.assertEqual(os.environ[linux_only[1]], "0")
                            self.assertEqual(os.environ[linux_only[2]], "1")

    def test_existing_console_runs_setup_in_process_on_every_platform(self):
        """Reuse an interactive console without creating another terminal."""
        terminal = Mock()
        terminal.isatty.return_value = True
        for system in ("Linux", "Windows", "Darwin"):
            self.system.return_value = system
            with (
                self.subTest(system=system),
                patch.object(sys, "stdin", terminal),
                patch.object(sys, "stdout", terminal),
                patch.object(self.launcher, "setup_main", return_value=3) as setup,
                patch.object(subprocess, "run") as run,
            ):
                self.assertEqual(self.launcher.run_setup_wizard(automatic=True), 3)
                setup.assert_called_once_with(["--auto"])
                run.assert_not_called()

    def test_windows_pythonw_opens_console_python_and_waits_for_failure(self):
        """Use console Python for setup and propagate cancellation to the caller."""
        self.system.return_value = "Windows"
        console = self._executable("Python/python.exe")
        gui_python = console.with_name("pythonw.exe")
        with (
            patch.object(sys, "executable", str(gui_python)),
            patch.object(sys, "stdin", None),
            patch.object(sys, "stdout", None),
            patch.object(subprocess, "CREATE_NEW_CONSOLE", 16, create=True),
            patch.object(subprocess, "run", return_value=Mock(returncode=1)) as run,
        ):
            self.assertEqual(self.launcher.run_setup_wizard(True), 1)
            run.assert_called_once_with(
                [
                    str(console),
                    str(self.root / "launcher.py"),
                    "--setup-only",
                    "--auto",
                ],
                check=False,
                creationflags=16,
            )
            console.unlink()
            with self.assertRaisesRegex(OSError, "Console Python not found"):
                self.launcher.run_setup_wizard()

    def test_setup_without_streams_cancels_cleanly_and_ready_launch_can_proceed(self):
        """Handle absent streams during prompting and during an already configured launch."""
        with patch.object(sys, "stdin", None), patch.object(sys, "stdout", None):
            with self.assertRaises(EOFError):
                self.launcher.get_input("Name")
            with (
                patch.object(self.launcher, "prepare_shared_autoexec"),
                patch.object(self.launcher.os, "execv"),
                patch.object(self.launcher.os, "chdir"),
            ):
                self.assertEqual(
                    self.launcher.launch_engine(self.root, [], self.root / "engine"), 0
                )

    def test_pythonw_launch_errors_are_visible_without_standard_streams(self):
        """Display an error dialog when Windows GUI Python has no stderr."""
        self.system.return_value = "Windows"
        with (
            patch.object(sys, "stderr", None),
            patch.object(sys, "stdout", None),
            patch.object(ctypes, "windll", create=True) as dll,
        ):
            self.assertEqual(self.launcher.main([]), 1)
            args = dll.user32.MessageBoxW.call_args.args
            self.assertIn("No " + self.engine_name + " engine found", args[1])
            self.assertEqual(args[2], self.engine_name + " Launcher")

    def test_macos_dispatch_returns_the_terminal_result(self):
        """Pass setup arguments to Terminal and preserve its cancellation status."""
        self.system.return_value = "Darwin"
        with (
            patch.object(sys, "stdin", None),
            patch.object(
                self.launcher, "run_macos_setup", return_value=130
            ) as terminal,
        ):
            self.assertEqual(self.launcher.run_setup_wizard(True), 130)
            terminal.assert_called_once_with(
                [
                    sys.executable,
                    str(self.root / "launcher.py"),
                    "--setup-only",
                    "--auto",
                ]
            )

    @unittest.skipIf(os.name == "nt", "Execute the macOS shell wrapper on a POSIX host")
    def test_macos_shell_wrapper_preserves_paths_arguments_status_and_cleans_up(self):
        """Execute the shell wrapper to verify quoting, status and temporary cleanup."""
        child = self.root / "wizard.py"
        child.write_text(
            "import sys\nfrom pathlib import Path\n"
            "Path(sys.argv[1]).write_text(repr(sys.argv[3:]), encoding='utf-8')\n"
            "sys.exit(int(sys.argv[2]))\n",
            encoding="utf-8",
        )
        output = self.root / "received"
        arguments = ["a mod", "$(touch injected)", "'quoted'", "é"]
        real_run = subprocess.run
        temporary_paths = []

        def simulate_terminal(command, **kwargs):
            self.assertEqual(command[:2], ["/usr/bin/osascript", "-e"])
            shell_command = shlex.split(command[-2])
            temporary_paths.append(Path(command[-1]).parent)
            self.assertEqual(shell_command[0], "/bin/sh")
            result = real_run(shell_command, check=kwargs.pop("check"), **kwargs)
            self.assertEqual(result.returncode, 0, result.stderr)
            return result

        with patch.object(subprocess, "run", side_effect=simulate_terminal):
            for status in (0, 1, 7, 130):
                with self.subTest(status=status):
                    self.assertEqual(
                        self.launcher.run_macos_setup(
                            [
                                sys.executable,
                                str(child),
                                str(output),
                                str(status),
                                *arguments,
                            ]
                        ),
                        status,
                    )
                    self.assertEqual(
                        output.read_text(encoding="utf-8"), repr(arguments)
                    )
        self.assertTrue(all(not path.exists() for path in temporary_paths))
        self.assertFalse((Path.cwd() / "injected").exists())

    def test_macos_terminal_failure_or_missing_status_never_reports_success(self):
        """Reject terminal failure and completion without a setup result."""
        for returncode in (0, 1):
            with (
                self.subTest(returncode=returncode),
                patch.object(
                    subprocess,
                    "run",
                    return_value=Mock(returncode=returncode, stderr="terminal closed"),
                ),
                self.assertRaises(OSError),
            ):
                self.launcher.run_macos_setup([sys.executable, "missing.py"])

    def test_macos_rejects_invalid_status_and_removes_temporary_files(self):
        """Reject malformed exit statuses and clean up each temporary directory."""
        paths = []
        for value in ("", "not an exit code", "-1", "256"):

            def terminal(command, status=value, **_kwargs):
                path = Path(command[-1])
                path.write_text(status, encoding="utf-8")
                paths.append(path.parent)
                return Mock(returncode=0)

            with (
                self.subTest(value=value),
                patch.object(subprocess, "run", side_effect=terminal),
                self.assertRaisesRegex(OSError, "valid exit status"),
            ):
                self.launcher.run_macos_setup([sys.executable])
            self.assertTrue(all(not path.exists() for path in paths))

    def test_setup_only_cli_runs_natively_without_an_engine_or_terminal(self):
        """Configure and check an installation through the native Python CLI."""
        script = self.root / "launcher.py"
        script.write_bytes(self.source.read_bytes())
        configs = self.root / self.config_dir / "configs"
        configs.mkdir(parents=True)
        for name in ("autoexec.cfg", "game.cfg", "player.cfg"):
            (configs / name).write_text("// existing configuration\n", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(script), "--setup-only", "--auto"],
            input=self.answers,
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((configs / ".wizard-complete").is_file())
        result = subprocess.run(
            [sys.executable, str(script), "--setup-only", "--check"],
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


class EzQuakePlatformTests(PlatformChecksMixin, unittest.TestCase):
    """Exercise the standalone ezQuake launcher."""

    engine_name = "ezQuake"
    appimage = "ezQuake-x86_64.AppImage"
    windows_exe = "ezquake.exe"
    autoexec = "ezquake/configs/autoexec.cfg"
    config_dir = "ezquake"
    answers = "Ranger\nRng\nRed\n3\n"

    def setUp(self):
        """Prepare the shared platform fixtures for this test case."""
        self._prepare_platform()

    def test_windows_detects_only_packaged_engine_and_allows_explicit_overrides(self):
        """Detect ezquake.exe automatically and require overrides for other names."""
        self.system.return_value = "Windows"
        self._executable("ezquake-gl.exe")
        selected = self._executable("ezquake-glsl.exe")
        with self.assertRaisesRegex(OSError, "No ezQuake engine found.*--engine"):
            self.launcher.resolve_engine(self.root)
        self.assertEqual(
            self.launcher.resolve_engine(self.root, str(selected)), selected
        )
        engine = self._executable(self.windows_exe)
        self.assertEqual(self.launcher.resolve_engine(self.root), engine)
        self.assertEqual(
            self.launcher.resolve_engine(self.root, str(selected)), selected
        )


class VkQuakePlatformTests(PlatformChecksMixin, unittest.TestCase):
    """Exercise the standalone vkQuake launcher."""

    engine_name = "vkQuake"
    appimage = "vkQuake.AppImage"
    windows_exe = "vkQuake.exe"
    autoexec = "../id1/configs/autoexec.cfg"
    config_dir = "id1"
    answers = "Ranger\n3\n"

    def setUp(self):
        """Prepare the shared platform fixtures for this test case."""
        self._prepare_platform()
