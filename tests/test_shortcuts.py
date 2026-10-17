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

import multiplayer
import singleplayer
from quake_installer import shortcuts


class ShortcutTests(unittest.TestCase):
    """Exercise shortcut creation, replacement, and recovery on each platform."""

    shortcut = shortcuts.VKQUAKE_SHORTCUT
    other_shortcut = shortcuts.EZQUAKE_SHORTCUT
    name = "Quake Single Player (vkQuake Port)"
    desktop = "vkQuake.desktop"
    icon = "vkQuake"
    bundle_id = "com.quakebundle.singleplayer"
    installer = singleplayer
    install_directory = "INSTALL_SINGLE_PLAYER_DIR"
    install_function = "install_vkquake_shortcut"

    def setUp(self):
        """Prepare an isolated home directory and native shortcut fixtures."""
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.home = Path(
            stack.enter_context(tempfile.TemporaryDirectory(prefix="quake shortcuts "))
        )
        self.root = self.home / "Quake's é $game; 100%"
        self.root.mkdir()
        self.python = Path(sys.executable).absolute()
        stack.enter_context(patch.object(Path, "home", return_value=self.home))
        self.template = Path(__file__).resolve().parents[1] / "assets/links" / self.desktop
        self.stub = self.home / "Automator Application Stub"
        self.stub.write_bytes(b"native stub fixture")
        self.native_stub = shortcuts.AUTOMATOR_STUB
        stack.enter_context(patch.object(shortcuts, "AUTOMATOR_STUB", self.stub))

    def fetch_template(self, url, destination):
        """Copy the local desktop template after checking the requested URL."""
        self.assertEqual(url, "https://raw.githubusercontent.com/Evilchuck666/QuakeBundle/main/"
                         f"assets/links/{self.desktop}")
        shutil.copyfile(self.template, destination)

    def test_linux_customizes_template_and_updates_existing_shortcut(self):
        """Customize the Linux desktop entry and replace it on reinstallation."""
        destination = self.home / ".local/share/applications" / self.desktop
        destination.parent.mkdir(parents=True)
        destination.write_text("old shortcut", encoding="utf-8")
        other = destination.with_name(f"{self.other_shortcut.engine}.desktop")
        other.write_bytes(b"other engine shortcut")
        for _ in range(2):
            self.assertEqual(shortcuts.create_linux_shortcut(
                self.root, self.python, self.fetch_template, self.shortcut), destination)
            content = destination.read_text(encoding="utf-8")
            self.assertIn("Name=" + self.name, content)
            self.assertIn(f"Path={self.root}\n", content)
            self.assertIn(f"Icon={self.root / f'{self.icon}.svg'}\n", content)
            self.assertIn(f"TryExec={self.python}\n", content)
            self.assertIn("Terminal=true\n", content)
            self.assertNotIn("/absolute/path/to/", content)
            self.assertEqual(other.read_bytes(), b"other engine shortcut")
            self.assertEqual(set(destination.parent.iterdir()), {destination, other})
        if shutil.which("desktop-file-validate"):
            subprocess.run(["desktop-file-validate", str(destination)], check=True,
                           capture_output=True)

    def test_linux_failure_preserves_previous_shortcut(self):
        """Preserve the existing desktop entry when fetching its template fails."""
        destination = self.home / ".local/share/applications" / self.desktop
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"previous shortcut")

        def fail(_url, path):
            path.write_bytes(b"partial download")
            raise OSError("download interrupted")

        def malformed(_url, path):
            path.write_text("[Desktop Entry]\nType=Application\n", encoding="utf-8")

        for fetch in (fail, malformed):
            with self.subTest(fetch=fetch), self.assertRaises(OSError):
                shortcuts.create_linux_shortcut(self.root, self.python, fetch, self.shortcut)
            self.assertEqual(destination.read_bytes(), b"previous shortcut")
            self.assertEqual(list(destination.parent.iterdir()), [destination])

    @unittest.skipUnless(sys.platform.startswith("linux") and shutil.which("gio"),
                         "Requires Linux GIO desktop entry launching")
    def test_linux_exec_preserves_special_characters_when_launched(self):
        """Launch through GIO with special characters preserved in the game path."""
        # Launch a harmless Python recorder through the real desktop entry parser.
        root = self.root / 'back\\slash "quotes" `ticks` %f'
        root.mkdir()
        output = root / "received"
        (root / "launcher.py").write_text(
            "from pathlib import Path\n"
            "Path(__file__).with_name('received').write_text(str(Path.cwd()), encoding='utf-8')\n",
            encoding="utf-8",
        )
        destination = shortcuts.create_linux_shortcut(
            root, self.python, self.fetch_template, self.shortcut)
        # Disable the terminal only for this headless integration test.
        destination.write_text(destination.read_text(encoding="utf-8").replace(
            "Terminal=true", "Terminal=false"), encoding="utf-8")
        subprocess.run(["gio", "launch", str(destination)], check=True, capture_output=True)
        deadline = time.monotonic() + 5
        while not output.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(output.read_text(encoding="utf-8"), str(root))

    def test_windows_passes_paths_as_data_and_uses_reported_desktop(self):
        """Pass Windows paths through the environment and use the reported desktop."""
        destination = self.home / "OneDrive/Desktop" / (self.name + ".lnk")
        destination.parent.mkdir(parents=True)

        def windows(command, **kwargs):
            self.assertEqual(command[:4], ["powershell.exe", "-NoProfile", "-NonInteractive",
                                            "-EncodedCommand"])
            script = base64.b64decode(command[4]).decode("utf-16le")
            self.assertNotIn(str(self.root), script)
            self.assertEqual(kwargs["env"]["QUAKE_SHORTCUT_ROOT"], str(self.root))
            self.assertIn("SpecialFolders.Item('Desktop')", script)
            self.assertIn("'launcher.exe'", script)
            self.assertIn("$env:QUAKE_SHORTCUT_ICON", script)
            self.assertEqual(kwargs["env"]["QUAKE_SHORTCUT_NAME"], self.name)
            self.assertEqual(kwargs["env"]["QUAKE_SHORTCUT_ICON"],
                             str(self.root / f"{self.icon}.ico"))
            destination.write_bytes(b"native shortcut")
            return Mock(stdout=str(destination) + "\n")

        with patch.object(shortcuts.subprocess, "run", side_effect=windows):
            self.assertEqual(shortcuts.create_windows_shortcut(self.root, self.shortcut), destination)

    def test_windows_reports_native_failure_or_missing_output(self):
        """Report PowerShell failures and missing shortcut output."""
        with patch.object(shortcuts.subprocess, "run", side_effect=subprocess.CalledProcessError(
            1, "powershell.exe", stderr="access denied"
        )), self.assertRaisesRegex(OSError, "access denied"):
            shortcuts.create_windows_shortcut(self.root, self.shortcut)
        with patch.object(shortcuts.subprocess, "run", return_value=Mock(stdout="")), \
             self.assertRaisesRegex(OSError, "did not create"):
            shortcuts.create_windows_shortcut(self.root, self.shortcut)

    @unittest.skipUnless(sys.platform == "win32", "Requires Windows Script Host")
    def test_native_windows_shortcut_target_icon_and_reinstallation(self):
        """Verify native Windows shortcut properties across repeated installations."""
        # Only redirect Desktop discovery; exercise the actual COM creation code.
        script = shortcuts.WINDOWS_SHORTCUT_SCRIPT.replace(
            "$shell.SpecialFolders.Item('Desktop')", "$env:QUAKE_TEST_DESKTOP")
        desktop = self.home / "OneDrive/Desktop"
        desktop.mkdir(parents=True)
        other = desktop / (self.other_shortcut.name + ".lnk")
        other.write_bytes(b"other engine shortcut")
        with patch.object(shortcuts, "WINDOWS_SHORTCUT_SCRIPT", script), \
             patch.dict(os.environ, QUAKE_TEST_DESKTOP=str(desktop)):
            for _ in range(2):
                destination = shortcuts.create_windows_shortcut(self.root, self.shortcut)
                self.assertEqual(destination.parent, desktop)
                self.assertEqual(other.read_bytes(), b"other engine shortcut")
                self.assertEqual(set(desktop.iterdir()), {destination, other})
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
        self.assertEqual(link["IconLocation"], str(self.root / f"{self.icon}.ico") + ",0")

    def test_macos_builds_automator_app_and_replaces_old_version(self):
        """Build an Automator app with a working shell action and replace the old app."""
        destination = self.home / "Applications" / (self.name + ".app")
        destination.mkdir(parents=True)
        (destination / "old").write_bytes(b"previous application")
        other = destination.with_name(self.other_shortcut.name + ".app")
        other.mkdir()
        (other / "old").write_bytes(b"other engine shortcut")

        def macos(command, **_kwargs):
            staged = Path(command[-2])
            self.assertEqual(command[:4], ["/usr/bin/osascript", "-l", "JavaScript", "-e"])
            self.assertEqual(command[-1], str(self.root / f"{self.icon}.png"))
            self.assertTrue((destination / "old").is_file())
            with (staged / "Contents/Info.plist").open("rb") as source:
                info = plistlib.load(source)
            self.assertEqual(info["CFBundleName"], self.name)
            self.assertEqual(info["CFBundleDisplayName"], self.name)
            self.assertEqual(info["CFBundleIdentifier"], self.bundle_id)
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
            self.assertEqual(shortcuts.create_macos_shortcut(
                self.root, self.python, self.shortcut), destination)
        self.assertFalse((destination / "old").exists())
        self.assertEqual((other / "old").read_bytes(), b"other engine shortcut")
        self.assertEqual(set(destination.parent.iterdir()), {destination, other})

    def test_macos_failure_preserves_previous_application(self):
        """Keep the existing macOS application when icon creation fails."""
        destination = self.home / "Applications" / (self.name + ".app")
        destination.mkdir(parents=True)
        (destination / "old").write_bytes(b"previous application")
        with patch.object(shortcuts, "run_native", side_effect=OSError("invalid icon")), \
             self.assertRaisesRegex(OSError, "invalid icon"):
            shortcuts.create_macos_shortcut(self.root, self.python, self.shortcut)
        self.assertEqual((destination / "old").read_bytes(), b"previous application")
        self.assertEqual(list(destination.parent.iterdir()), [destination])

    def test_macos_publish_failure_restores_previous_application(self):
        """Restore the previous macOS application when publishing its replacement fails."""
        destination = self.home / "Applications" / (self.name + ".app")
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
            shortcuts.create_macos_shortcut(self.root, self.python, self.shortcut)
        self.assertEqual((destination / "old").read_bytes(), b"previous application")
        self.assertEqual(list(destination.parent.iterdir()), [destination])

    @unittest.skipUnless(sys.platform == "darwin", "Requires Automator and AppKit")
    def test_native_macos_workflow_and_png_icon(self):
        """Create a native Automator app using a PNG icon across reinstallations."""
        source = Path(__file__).resolve().parents[1] / f"assets/icons/{self.icon}.png"
        shutil.copyfile(source, self.root / f"{self.icon}.png")
        with patch.object(shortcuts, "AUTOMATOR_STUB", self.native_stub):
            for _ in range(2):
                destination = shortcuts.create_macos_shortcut(self.root, self.python, self.shortcut)
                self.assertTrue((destination / "Contents/document.wflow").is_file())
                self.assertEqual(list(destination.parent.iterdir()), [destination])

    def test_installer_dispatches_to_current_platform(self):
        """Dispatch shortcut installation to the current platform's helper."""
        for system, helper in (("Linux", "create_linux_shortcut"),
                               ("Windows", "create_windows_shortcut"),
                               ("Darwin", "create_macos_shortcut")):
            with self.subTest(system=system), \
                 patch.object(self.installer.platform, "system", return_value=system), \
                 patch.object(self.installer, self.install_directory, self.root), \
                 patch.object(self.installer, helper) as create, \
                 contextlib.redirect_stdout(io.StringIO()):
                getattr(self.installer, self.install_function)()
                expected = [self.root.resolve()]
                if system != "Windows":
                    expected.append(self.python)
                if system == "Linux":
                    expected.append(self.installer.fetch)
                create.assert_called_once_with(*expected, self.shortcut)


class EzQuakeShortcutTests(ShortcutTests):
    """Apply the same platform, quoting and recovery checks to multiplayer."""

    shortcut = shortcuts.EZQUAKE_SHORTCUT
    other_shortcut = shortcuts.VKQUAKE_SHORTCUT
    name = "Quake Multiplayer (ezQuake Port)"
    desktop = "ezQuake.desktop"
    icon = "ezQuake"
    bundle_id = "com.quakebundle.multiplayer"
    installer = multiplayer
    install_directory = "INSTALL_MULTIPLAYER_DIR"
    install_function = "install_ezquake_shortcut"
