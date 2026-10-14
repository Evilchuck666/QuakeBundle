"""Verify per-user shortcuts without installing games or touching the real desktop."""

import base64
import contextlib
import io
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import singleplayer
from quake_installer import shortcuts


class ShortcutTests(unittest.TestCase):
    def setUp(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.home = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="quake shortcuts ")))
        self.root = self.home / "Quake's é $game; 100%"
        self.root.mkdir()
        self.python = Path(sys.executable).absolute()
        stack.enter_context(patch.object(Path, "home", return_value=self.home))
        self.template = (Path(singleplayer.__file__).parent / "assets/links/vkQuake.desktop")
        self.stub = self.home / "Automator Application Stub"
        self.stub.write_bytes(b"native stub fixture")
        self.native_stub = shortcuts.AUTOMATOR_STUB
        stack.enter_context(patch.object(shortcuts, "AUTOMATOR_STUB", self.stub))

    def fetch_template(self, url, destination):
        self.assertEqual(url, shortcuts.DESKTOP_URL)
        shutil.copyfile(self.template, destination)

    def test_linux_customizes_template_and_updates_existing_shortcut(self):
        destination = self.home / ".local/share/applications/vkQuake.desktop"
        destination.parent.mkdir(parents=True)
        destination.write_text("old shortcut", encoding="utf-8")
        for _ in range(2):
            self.assertEqual(shortcuts.create_linux_shortcut(
                self.root, self.python, self.fetch_template), destination)
            content = destination.read_text(encoding="utf-8")
            self.assertIn("Name=" + shortcuts.SHORTCUT_NAME, content)
            self.assertIn(f"Path={self.root}\n", content)
            self.assertIn(f"Icon={self.root / 'quake.svg'}\n", content)
            self.assertIn(f"TryExec={self.python}\n", content)
            self.assertIn("Terminal=true\n", content)
            self.assertNotIn("/absolute/path/to/", content)
            self.assertEqual(list(destination.parent.iterdir()), [destination])
        if shutil.which("desktop-file-validate"):
            subprocess.run(["desktop-file-validate", str(destination)], check=True,
                           capture_output=True)

    def test_linux_failure_preserves_previous_shortcut(self):
        destination = self.home / ".local/share/applications/vkQuake.desktop"
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"previous shortcut")

        def fail(_url, path):
            path.write_bytes(b"partial download")
            raise OSError("download interrupted")

        def malformed(_url, path):
            path.write_text("[Desktop Entry]\nType=Application\n", encoding="utf-8")

        for fetch in (fail, malformed):
            with self.subTest(fetch=fetch), self.assertRaises(OSError):
                shortcuts.create_linux_shortcut(self.root, self.python, fetch)
            self.assertEqual(destination.read_bytes(), b"previous shortcut")
            self.assertEqual(list(destination.parent.iterdir()), [destination])

    @unittest.skipUnless(sys.platform.startswith("linux") and shutil.which("gio"),
                         "Requires Linux GIO desktop entry launching")
    def test_linux_exec_preserves_special_characters_when_launched(self):
        # Launch a harmless Python recorder through the real desktop entry parser.
        root = self.root / 'back\\slash "quotes" `ticks` %f'
        root.mkdir()
        output = root / "received"
        (root / "launcher.py").write_text(
            "from pathlib import Path\n"
            "Path(__file__).with_name('received').write_text(str(Path.cwd()), encoding='utf-8')\n",
            encoding="utf-8",
        )
        destination = shortcuts.create_linux_shortcut(root, self.python, self.fetch_template)
        # Disable the terminal only for this headless integration test.
        destination.write_text(destination.read_text(encoding="utf-8").replace(
            "Terminal=true", "Terminal=false"), encoding="utf-8")
        subprocess.run(["gio", "launch", str(destination)], check=True, capture_output=True)
        deadline = time.monotonic() + 5
        while not output.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(output.read_text(encoding="utf-8"), str(root))

    def test_windows_passes_paths_as_data_and_uses_reported_desktop(self):
        destination = self.home / "OneDrive/Desktop" / (shortcuts.SHORTCUT_NAME + ".lnk")
        destination.parent.mkdir(parents=True)

        def windows(command, **kwargs):
            self.assertEqual(command[:4], ["powershell.exe", "-NoProfile", "-NonInteractive",
                                            "-EncodedCommand"])
            script = base64.b64decode(command[4]).decode("utf-16le")
            self.assertNotIn(str(self.root), script)
            self.assertEqual(kwargs["env"]["QUAKE_SHORTCUT_ROOT"], str(self.root))
            self.assertIn("SpecialFolders.Item('Desktop')", script)
            self.assertIn("'launcher.exe'", script)
            self.assertIn("'quake.ico'", script)
            destination.write_bytes(b"native shortcut")
            return Mock(stdout=str(destination) + "\n")

        with patch.object(shortcuts.subprocess, "run", side_effect=windows):
            self.assertEqual(shortcuts.create_windows_shortcut(self.root), destination)

    def test_windows_reports_native_failure_or_missing_output(self):
        with patch.object(shortcuts.subprocess, "run", side_effect=subprocess.CalledProcessError(
            1, "powershell.exe", stderr="access denied"
        )), self.assertRaisesRegex(OSError, "access denied"):
            shortcuts.create_windows_shortcut(self.root)
        with patch.object(shortcuts.subprocess, "run", return_value=Mock(stdout="")), \
             self.assertRaisesRegex(OSError, "did not create"):
            shortcuts.create_windows_shortcut(self.root)

    @unittest.skipUnless(sys.platform == "win32", "Requires Windows Script Host")
    def test_native_windows_shortcut_target_icon_and_reinstallation(self):
        # Only redirect Desktop discovery; exercise the actual COM creation code.
        script = shortcuts.WINDOWS_SHORTCUT_SCRIPT.replace(
            "$shell.SpecialFolders.Item('Desktop')", "$env:QUAKE_TEST_DESKTOP")
        desktop = self.home / "OneDrive/Desktop"
        desktop.mkdir(parents=True)
        with patch.object(shortcuts, "WINDOWS_SHORTCUT_SCRIPT", script), \
             patch.dict(os.environ, QUAKE_TEST_DESKTOP=str(desktop)):
            for _ in range(2):
                destination = shortcuts.create_windows_shortcut(self.root)
                self.assertEqual(destination.parent, desktop)
                self.assertEqual(list(desktop.iterdir()), [destination])
        inspect = """
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($env:QUAKE_TEST_LINK)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$link | Select-Object TargetPath,WorkingDirectory,IconLocation | ConvertTo-Json -Compress
"""
        encoded = base64.b64encode(inspect.encode("utf-16le")).decode("ascii")
        result = shortcuts.run_native(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            env=dict(os.environ, QUAKE_TEST_LINK=str(destination)),
        )
        link = json.loads(result.stdout)
        self.assertEqual(Path(link["TargetPath"]), self.root / "launcher.exe")
        self.assertEqual(Path(link["WorkingDirectory"]), self.root)
        self.assertEqual(link["IconLocation"], str(self.root / "quake.ico") + ",0")

    def test_macos_builds_automator_app_and_replaces_old_version(self):
        destination = self.home / "Applications" / (shortcuts.SHORTCUT_NAME + ".app")
        destination.mkdir(parents=True)
        (destination / "old").write_bytes(b"previous application")

        def macos(command, **_kwargs):
            staged = Path(command[-2])
            self.assertEqual(command[:4], ["/usr/bin/osascript", "-l", "JavaScript", "-e"])
            self.assertEqual(command[-1], str(self.root / "ezquake.png"))
            self.assertTrue((destination / "old").is_file())
            with (staged / "Contents/Info.plist").open("rb") as source:
                info = plistlib.load(source)
            self.assertEqual(info["CFBundleName"], shortcuts.SHORTCUT_NAME)
            self.assertEqual((staged / "Contents/MacOS" / info["CFBundleExecutable"]).read_bytes(),
                             self.stub.read_bytes())
            with (staged / "Contents/document.wflow").open("rb") as source:
                workflow = plistlib.load(source)
            self.assertEqual(workflow["workflowMetaData"]["workflowTypeIdentifier"],
                             "com.apple.Automator.application")
            action = workflow["actions"][0]["action"]
            self.assertEqual(action["BundleIdentifier"], "com.apple.RunShellScript")
            # Execute the shell action against a harmless launcher to test quoting.
            if os.name != "nt":
                (self.root / "launcher.py").write_text(
                    "from pathlib import Path\n"
                    "assert Path.cwd() == Path(__file__).parent\n", encoding="utf-8")
                subprocess.run(["/bin/sh", "-c", action["ActionParameters"]["source"]],
                               check=True)
            return Mock(stdout="")

        # Patch the wrapper so the shell action can still run a real subprocess.
        with patch.object(shortcuts, "run_native", side_effect=macos):
            self.assertEqual(shortcuts.create_macos_shortcut(self.root, self.python), destination)
        self.assertFalse((destination / "old").exists())
        self.assertEqual(list(destination.parent.iterdir()), [destination])

    def test_macos_failure_preserves_previous_application(self):
        destination = self.home / "Applications" / (shortcuts.SHORTCUT_NAME + ".app")
        destination.mkdir(parents=True)
        (destination / "old").write_bytes(b"previous application")
        with patch.object(shortcuts, "run_native", side_effect=OSError("invalid icon")), \
             self.assertRaisesRegex(OSError, "invalid icon"):
            shortcuts.create_macos_shortcut(self.root, self.python)
        self.assertEqual((destination / "old").read_bytes(), b"previous application")
        self.assertEqual(list(destination.parent.iterdir()), [destination])

    def test_macos_publish_failure_restores_previous_application(self):
        destination = self.home / "Applications" / (shortcuts.SHORTCUT_NAME + ".app")
        destination.mkdir(parents=True)
        (destination / "old").write_bytes(b"previous application")
        original_rename = Path.rename

        def rename(source, target):
            if source.name == destination.name and source.parent != destination.parent:
                raise OSError("publish failed")
            return original_rename(source, target)

        with patch.object(shortcuts, "run_native"), \
             patch.object(Path, "rename", rename), \
             self.assertRaisesRegex(OSError, "publish failed"):
            shortcuts.create_macos_shortcut(self.root, self.python)
        self.assertEqual((destination / "old").read_bytes(), b"previous application")
        self.assertEqual(list(destination.parent.iterdir()), [destination])

    @unittest.skipUnless(sys.platform == "darwin", "Requires Automator and AppKit")
    def test_native_macos_workflow_and_png_icon(self):
        source = Path(singleplayer.__file__).parent / "assets/icons/ezquake.png"
        shutil.copyfile(source, self.root / "ezquake.png")
        with patch.object(shortcuts, "AUTOMATOR_STUB", self.native_stub):
            for _ in range(2):
                destination = shortcuts.create_macos_shortcut(self.root, self.python)
                self.assertTrue((destination / "Contents/document.wflow").is_file())
                self.assertEqual(list(destination.parent.iterdir()), [destination])

    def test_installer_dispatches_to_current_platform(self):
        for system, helper in (("Linux", "create_linux_shortcut"),
                               ("Windows", "create_windows_shortcut"),
                               ("Darwin", "create_macos_shortcut")):
            with self.subTest(system=system), \
                 patch.object(singleplayer.platform, "system", return_value=system), \
                 patch.object(singleplayer, "INSTALL_SINGLE_PLAYER_DIR", self.root), \
                 patch.object(singleplayer, helper) as create, \
                 contextlib.redirect_stdout(io.StringIO()):
                singleplayer.install_vkquake_shortcut()
                expected = [self.root.resolve()]
                if system != "Windows":
                    expected.append(self.python)
                if system == "Linux":
                    expected.append(singleplayer.fetch)
                create.assert_called_once_with(*expected)
