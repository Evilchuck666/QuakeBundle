"""Exercise the entry point and game installation without a Steam account."""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from functools import partial
from pathlib import Path
from unittest.mock import Mock, call, patch

import singleplayer
from quake_installer import gamedata


class CommandLineTests(unittest.TestCase):
    """Exercise the Quake single-player CLI entry point."""

    installer = singleplayer

    def run_cli(self, *arguments):
        """Run the CLI in a temporary user directory."""
        with tempfile.TemporaryDirectory(prefix="quake cli ") as directory:
            env = os.environ.copy()
            env.pop("STEAM_USERNAME", None)
            env.update(
                HOME=directory,
                USERPROFILE=directory,
                LOCALAPPDATA=directory,
                XDG_DATA_HOME=directory,
                PYTHONIOENCODING="utf-8",
            )
            return subprocess.run(
                [sys.executable, str(Path(self.installer.__file__).resolve()), *arguments],
                cwd=directory,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=30,
                check=False,
            )

    def test_help_works_from_another_directory(self):
        """The CLI help should work even when launched outside the project directory."""
        result = self.run_cli("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--attempts", result.stdout)
        self.assertIn("--install-dir", result.stdout)
        self.assertIn("username", result.stdout)

    def test_username_is_required(self):
        """The CLI should reject invocations that omit the Steam username."""
        result = self.run_cli()
        self.assertEqual(result.returncode, 2)
        self.assertIn("a Steam username is required", result.stderr)

    def test_attempts_must_be_an_integer(self):
        """The attempts flag should validate its integer argument."""
        result = self.run_cli("--attempts", "invalid", "test-user")
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid int value", result.stderr)


class ImportCompatibilityTests(unittest.TestCase):
    """Keep the single-file API usable by programs embedding the installer."""

    def setUp(self):
        """Copy the entry point and its package away from the checkout."""
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        source = Path(singleplayer.__file__).resolve()
        self.script = self.root / source.name
        shutil.copyfile(source, self.script)
        shutil.copytree(source.parent / "quake_installer", self.root / "quake_installer",
                        ignore=shutil.ignore_patterns("__pycache__"))
        self.working_dir = self.root / "elsewhere"
        self.working_dir.mkdir()

    def run_import(self, body):
        """Verify interpreter state and absence of bytecode in an isolated process."""
        code = (
            "import runpy, sys\n"
            "from pathlib import Path\n"
            "source = sys.argv[1]\n"
            "before_path = sys.path[:]\n"
            "before_bytecode = sys.dont_write_bytecode\n"
            + body + "\n"
            "assert sys.path == before_path\n"
            "assert sys.dont_write_bytecode == before_bytecode\n"
        )
        env = dict(os.environ, PYTHONPATH="")
        env.pop("PYTHONDONTWRITEBYTECODE", None)
        result = subprocess.run(
            [sys.executable, "-c", code, str(self.script)],
            cwd=self.working_dir, env=env, capture_output=True, text=True,
            timeout=30, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list(self.root.rglob("__pycache__")), [])

    def test_runpy_and_exec_preserve_public_api_from_another_directory(self):
        """Expose every compatibility export without depending on the caller's cwd."""
        for body in (
            "namespace = runpy.run_path(source)",
            (
                "namespace = {'__file__': source, '__name__': 'embedded_installer'}\n"
                "exec(compile(Path(source).read_text(encoding='utf-8'), source, 'exec'), namespace)"
            ),
        ):
            with self.subTest(body=body):
                self.run_import(
                    body + "\n"
                    f"assert namespace['__all__'] == {singleplayer.__all__!r}\n"
                    "assert all(name in namespace for name in namespace['__all__'])\n"
                    "assert callable(namespace['install_vkquake'])\n"
                    "assert namespace['APP_ID'] == 2310\n"
                )

    def test_failed_import_restores_interpreter_state(self):
        """An import failure propagates without leaking temporary interpreter changes."""
        (self.root / "quake_installer" / "config.py").write_text(
            "raise ImportError('synthetic dependency failure')\n", encoding="utf-8"
        )
        self.run_import(
            "try:\n"
            "    runpy.run_path(source)\n"
            "except ImportError as error:\n"
            "    assert str(error) == 'synthetic dependency failure'\n"
            "else:\n"
            "    raise AssertionError('Expected dependency failure')\n"
        )


class VkQuakeAssetTests(unittest.TestCase):
    """Install support files without network access or changes to user data."""

    def setUp(self):
        """Use local source files and a fresh installation directory."""
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.root = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="quake assets ")))
        self.install = self.root / "singleplayer"
        stack.enter_context(patch.object(singleplayer, "INSTALL_SINGLE_PLAYER_DIR", self.install))
        self.system = stack.enter_context(patch.object(singleplayer.platform, "system"))
        self.system.return_value = "Linux"
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def test_downloads_assets_into_install_directory(self):
        """Install the platform's launcher and icon from direct GitHub URLs."""
        for system, launcher, icon in (
            ("Linux", "launcher.py", "vkQuake.svg"),
            ("Windows", "launcher.exe", "vkQuake.ico"),
            ("Darwin", "launcher.py", "vkQuake.png"),
        ):
            with self.subTest(system=system):
                self.system.return_value = system
                with patch.object(singleplayer, "INSTALL_SINGLE_PLAYER_DIR", self.install / system):
                    self.check_downloaded_assets(self.install / system, launcher, icon)

    def check_downloaded_assets(self, install, launcher, icon):
        """Verify downloaded bytes, filenames and launcher/icon permissions."""
        sources = Path(singleplayer.__file__).resolve().parent / "assets"
        expected = {
            "https://raw.githubusercontent.com/Evilchuck666/QuakeBundle/main/assets/" + relative:
            sources / relative
            for relative in (
                f"scripts/vkQuake/{launcher}", f"icons/{icon}"
            )
        }
        real_fetch = singleplayer.fetch

        def local_fetch(url, destination):
            real_fetch(expected[url].as_uri(), destination)

        with patch.object(singleplayer, "fetch", side_effect=local_fetch) as download:
            singleplayer.install_vkquake_assets()

        self.assertEqual([args.args[0] for args in download.call_args_list], list(expected))
        self.assertEqual(sorted(p.name for p in install.iterdir()),
                         sorted([launcher, icon]))
        for source in expected.values():
            installed = install / source.name
            self.assertEqual(installed.read_bytes(), source.read_bytes())
            if os.name != "nt":
                self.assertEqual(installed.stat().st_mode & 0o777,
                                 0o755 if source.name == launcher else 0o644)

    def test_failed_download_preserves_existing_assets_and_removes_temporary_files(self):
        """A partial download must not overwrite an existing installation."""
        self.install.mkdir()
        names = ("launcher.py", "vkQuake.svg")
        for name in names:
            (self.install / name).write_bytes(b"previous version")

        def failing_fetch(url, destination):
            del url
            destination.write_bytes(b"new content")
            if destination.name == "vkQuake.svg":
                raise OSError("download interrupted")

        with (
            patch.object(singleplayer, "fetch", side_effect=failing_fetch),
            self.assertRaisesRegex(OSError, "download interrupted")
        ):
            singleplayer.install_vkquake_assets()

        self.assertEqual(sorted(p.name for p in self.install.iterdir()), sorted(names))
        for name in names:
            self.assertEqual((self.install / name).read_bytes(), b"previous version")


class InstallerTests(unittest.TestCase):
    """Exercise the Quake single-player installation workflow."""

    def setUp(self):
        """Create isolated game-install directories for each test."""
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.root = Path(stack.enter_context(
            tempfile.TemporaryDirectory(prefix="quake install ")
        )).resolve()
        self.download = self.root / "download"
        self.rerelease = self.download / "rerelease"
        self.install = self.root / "singleplayer"
        self.steam = self.root / "steamcmd"
        stack.enter_context(patch.object(singleplayer, "INSTALL_SINGLE_PLAYER_DIR", self.install))
        for name, value in {
            "DOWNLOAD_DIR": self.download,
            "RERELEASE_DIR": self.rerelease,
            "INSTALL_SINGLE_PLAYER_DIR": self.install,
            "STEAMCMD_DIR": self.steam,
        }.items():
            stack.enter_context(patch.object(gamedata, name, value))
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    @staticmethod
    def write(path, content=b"synthetic game data"):
        """Write a synthetic game artifact to the given path."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def make_download(self):
        """Populate the fake downloaded Quake files used by the tests."""
        for relative in gamedata.EXPECTED_FILES:
            self.write(self.rerelease / relative)
        self.write(self.rerelease / "id1/music/track02.ogg", b"music")
        self.write(self.rerelease / "mg1/pak0.pak", b"extra expansion")
        self.write(self.rerelease / "Quake_x64.exe", b"unused re-release engine")
        self.write(self.download / "id1/PAK0.PAK", b"unused original game")
        self.write(self.steam / "cached-login", b"synthetic credentials")

    @contextlib.contextmanager
    def main_dependencies(self, arguments, username="environment-user"):
        """Patch the CLI dependencies needed when exercising main()."""
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(sys, "argv", ["singleplayer.py", *arguments]))
            stack.enter_context(patch.dict(os.environ, {"STEAM_USERNAME": username}))
            # main() reconfigures stdout, which StringIO does not support.
            output = stack.enter_context(patch.object(singleplayer.sys, "stdout"))
            calls = Mock()
            for name in (
                "check_rosetta", "install_steamcmd", "update_steamcmd", "login",
                "download_quake", "install_vkquake", "install_vkquake_assets",
                "install_vkquake_shortcut",
            ):
                mock = stack.enter_context(patch.object(singleplayer, name))
                calls.attach_mock(mock, name)
            calls.install_steamcmd.return_value = self.steam / "steamcmd"
            yield calls, output

    def test_main_installs_rerelease_preserves_saves_and_cleans_up(self):
        """Installation should copy the re-release, keep user files, and clean up temp folders."""
        self.make_download()
        self.write(self.install / "id1/pak0.pak", b"old data")
        self.write(self.install / "id1/save.sav", b"saved game")
        self.write(self.install / "id1/config.cfg", b"settings")
        with self.main_dependencies(["--attempts", "2", "argument-user"]) as (calls, output):
            # vkQuake must run after installation and cleanup have succeeded.
            def check_engine_install(install_dir):
                self.assertEqual(install_dir, self.install)
                self.assertTrue((self.install / "id1/pak0.pak").is_file())
                self.assertFalse(self.download.exists())
                self.assertFalse(self.steam.exists())

            calls.install_vkquake.side_effect = check_engine_install
            singleplayer.main()
            exe = self.steam / "steamcmd"
            self.assertEqual(calls.mock_calls, [
                call.check_rosetta(), call.install_steamcmd(),
                call.update_steamcmd(exe), call.login(exe, "argument-user"),
                call.download_quake(exe, "argument-user", 2), call.install_vkquake(self.install),
                call.install_vkquake_assets(self.install), call.install_vkquake_shortcut(self.install),
            ])
            output.reconfigure.assert_called_once_with(errors="replace")
        for relative in gamedata.EXPECTED_FILES:
            self.assertEqual((self.install / relative).read_bytes(), b"synthetic game data")
        self.assertEqual((self.install / "id1/music/track02.ogg").read_bytes(), b"music")
        self.assertEqual((self.install / "mg1/pak0.pak").read_bytes(), b"extra expansion")
        self.assertEqual((self.install / "id1/save.sav").read_bytes(), b"saved game")
        self.assertEqual((self.install / "id1/config.cfg").read_bytes(), b"settings")
        self.assertFalse((self.install / "Quake_x64.exe").exists())

    def test_main_uses_environment_username_and_default_attempts(self):
        """The main entry point should use the environment username and default attempt count."""
        self.make_download()
        with self.main_dependencies([]) as (calls, _):
            singleplayer.main()
            calls.login.assert_called_once_with(self.steam / "steamcmd", "environment-user")
            calls.download_quake.assert_called_once_with(
                self.steam / "steamcmd", "environment-user", 3
            )

    def test_missing_game_data_preserves_download_for_retry(self):
        """Missing game files should leave the downloaded directory intact for retry."""
        self.make_download()
        (self.rerelease / "rogue/pak0.pak").unlink()
        with self.main_dependencies([]) as (calls, _):
            with self.assertRaisesRegex(SystemExit, "rogue/pak0.pak"):
                singleplayer.main()
            calls.install_vkquake.assert_not_called()
        self.assertTrue((self.rerelease / "id1/pak0.pak").is_file())
        self.assertTrue(self.steam.is_dir())
        self.assertFalse(self.install.exists())

    def test_engine_failure_stops_before_asset_downloads(self):
        """Only download support files after vkQuake installs successfully."""
        self.make_download()
        with self.main_dependencies([]) as (calls, _):
            calls.install_vkquake.side_effect = SystemExit("engine download failed")
            with self.assertRaisesRegex(SystemExit, "engine download failed"):
                singleplayer.main()
            calls.install_vkquake_assets.assert_not_called()
            calls.install_vkquake_shortcut.assert_not_called()

    def test_asset_failure_stops_before_shortcut_creation(self):
        """Never create a shortcut when its target could not be installed."""
        self.make_download()
        with self.main_dependencies([]) as (calls, _):
            calls.install_vkquake_assets.side_effect = OSError("download failed")
            with self.assertRaisesRegex(OSError, "download failed"):
                singleplayer.main()
            calls.install_vkquake_shortcut.assert_not_called()

    def test_shortcut_failure_reports_error_without_claiming_success(self):
        """Keep installed game data, but report shortcut creation failure."""
        self.make_download()
        with self.main_dependencies([]) as (calls, output):
            calls.install_vkquake_shortcut.side_effect = OSError("permission denied")
            with self.assertRaisesRegex(SystemExit, "Could not create vkQuake shortcut"):
                singleplayer.main()
            self.assertNotIn("Done.", "".join(str(c) for c in output.write.call_args_list))
        self.assertTrue((self.install / "id1/pak0.pak").is_file())

    def test_download_failure_stops_before_installation_and_cleanup(self):
        """A download failure should stop before any install or cleanup phase runs."""
        self.make_download()
        with self.main_dependencies([]) as (calls, _):
            calls.download_quake.side_effect = SystemExit("download failed")
            with self.assertRaisesRegex(SystemExit, "download failed"):
                singleplayer.main()
            calls.install_vkquake.assert_not_called()
        self.assertTrue(self.download.is_dir())
        self.assertTrue(self.steam.is_dir())
        self.assertFalse(self.install.exists())

    def test_download_retries_and_forces_windows_depots(self):
        """SteamCMD downloads should retry failures and force Windows depot downloads."""
        responses = iter([
            (1, ["temporary failure"]),
            (0, [
                "Update state (0x61) downloading, progress: 50.00 (10 / 20)",
                "Update state (0x81) verifying, progress: 100.00 (20 / 20)",
                "Success! App '2310' fully installed.",
            ]),
        ])

        def run(_command, on_line):
            code, lines = next(responses)
            for line in lines:
                on_line(line)
            return code, lines

        exe = self.steam / "steamcmd"
        with patch.object(gamedata, "run_captured", side_effect=run) as captured:
            gamedata.download_quake(exe, "test-user", 3)
        self.assertEqual(captured.call_count, 2)
        self.assertEqual(captured.call_args.args[0], [
            str(exe), "+@sSteamCmdForcePlatformType", "windows",
            "+force_install_dir", str(self.download), "+login", "test-user",
            "+app_update", "2310", "+quit",
        ])

    def test_download_requires_both_success_message_and_zero_exit(self):
        """The game download should fail if the success message or exit code is missing."""
        def run(returncode, output_lines, command, on_line):
            del command
            for line in output_lines:
                on_line(line)
            return returncode, output_lines

        for code, lines in ((0, []), (1, ["Success! App '2310' fully installed."])):
            with self.subTest(code=code):
                with (
                    patch.object(
                        gamedata, "run_captured", side_effect=partial(run, code, lines)
                    ) as captured,
                    self.assertRaisesRegex(SystemExit, "Could not download Quake")
                ):
                    gamedata.download_quake(self.steam / "steamcmd", "test-user", 2)
                self.assertEqual(captured.call_count, 2)


if __name__ == "__main__":
    unittest.main()
