"""Exercise the entry point and game installation without a Steam account."""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

import singleplayer
from quakebundle import gamedata


class CommandLineTests(unittest.TestCase):
    """Exercise the Quake single-player CLI entry point."""

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
                [sys.executable, str(Path(singleplayer.__file__).resolve()), *arguments],
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


class InstallerTests(unittest.TestCase):
    """Exercise the Quake single-player installation workflow."""

    def setUp(self):
        """Create isolated game-install directories for each test."""
        temporary = tempfile.TemporaryDirectory(prefix="quake install ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.download = self.root / "download"
        self.rerelease = self.download / "rerelease"
        self.install = self.root / "singleplayer"
        self.steam = self.root / "steamcmd"
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
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
                "download_quake", "install_vkquake",
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
            def check_engine_install():
                self.assertTrue((self.install / "id1/pak0.pak").is_file())
                self.assertFalse(self.download.exists())
                self.assertFalse(self.steam.exists())

            calls.install_vkquake.side_effect = check_engine_install
            singleplayer.main()
            exe = self.steam / "steamcmd"
            self.assertEqual(calls.mock_calls, [
                call.check_rosetta(), call.install_steamcmd(),
                call.update_steamcmd(exe), call.login(exe, "argument-user"),
                call.download_quake(exe, "argument-user", 2), call.install_vkquake(),
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
        for code, lines in ((0, []), (1, ["Success! App '2310' fully installed."])):
            with self.subTest(code=code):
                def run(command, on_line):
                    for line in lines:
                        on_line(line)
                    return code, lines

                with (
                    patch.object(gamedata, "run_captured", side_effect=run) as captured,
                    self.assertRaisesRegex(SystemExit, "Could not download Quake")
                ):
                    gamedata.download_quake(self.steam / "steamcmd", "test-user", 2)
                self.assertEqual(captured.call_count, 2)


if __name__ == "__main__":
    unittest.main()
