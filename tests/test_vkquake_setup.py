"""Offline regression checks for the vkQuake setup wizard and launcher."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from http.client import IncompleteRead
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "assets" / "scripts" / "vkQuake"
CONFIGS = ROOT / "assets" / "cfg" / "vkQuake"
SPEC = importlib.util.spec_from_file_location(
    "vkquake_launcher", SCRIPTS / "launcher.py"
)
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)


class WizardTestCase(unittest.TestCase):
    """Share an isolated installation and offline downloads between wizard tests."""

    def setUp(self):
        """Create an isolated player config and capture wizard output."""
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(
            self.stack.enter_context(
                tempfile.TemporaryDirectory(prefix="vkquake setup ")
            )
        ).resolve()
        self.config = self.root / "id1" / "configs" / "player.cfg"
        shutil.copytree(CONFIGS, self.config.parent)
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.stderr = self.stack.enter_context(
            contextlib.redirect_stderr(io.StringIO())
        )
        self.get_player_cfg_path = launcher.get_player_cfg_path
        self.stack.enter_context(
            patch.object(launcher, "get_player_cfg_path", return_value=self.config)
        )
        self.download = self.stack.enter_context(
            patch.object(
                launcher,
                "urlopen",
                side_effect=lambda url, timeout: io.BytesIO(
                    (CONFIGS / url.rsplit("/", 1)[-1]).read_bytes()
                ),
            )
        )

    def run_wizard(self, answers, *args):
        """Run the wizard with scripted answers to its prompts."""
        with patch("builtins.input", side_effect=answers):
            return launcher.main(["--setup-only", *args])

    def settings(self):
        """Read active player settings from the temporary config."""
        return dict(
            line.split(maxsplit=1)
            for line in self.config.read_text().splitlines()
            if line.strip() and not line.startswith("//")
        )


class WizardSettingsTests(WizardTestCase):
    """Verify player settings, installed paths and saving or cancelling edits."""

    def test_player_settings_update_template_and_record_success(self):
        """Save entered settings and mark setup complete."""
        expected = self.settings()
        expected.update(
            name='"Ranger"',
            _cl_name='"Ranger"',
            hostname='"Ranger SV"',
            sensitivity='"2.5"',
        )
        self.assertEqual(self.run_wizard(["Ranger", "2.5"]), 0)
        self.assertEqual(self.settings(), expected)
        self.assertTrue(launcher.get_setup_marker_path(self.config).is_file())

    def test_default_settings_do_not_add_video_overrides(self):
        """Apply default player settings without adding video overrides."""
        expected = self.settings()
        expected.update(
            name='"Player"',
            _cl_name='"Player"',
            hostname='"Player SV"',
            sensitivity='"3.0"',
        )
        self.assertEqual(self.run_wizard(["", ""]), 0)
        self.assertEqual(self.settings(), expected)

    def test_sensitivity_reprompts_for_invalid_numbers(self):
        """Reject invalid sensitivity values until a valid number is entered."""
        self.assertEqual(self.run_wizard(["Ranger", "nan", "-2", "2.5"]), 0)
        self.assertEqual(self.settings()["sensitivity"], '"2.5"')

    def test_comments_custom_settings_and_url_names_survive_rerun(self):
        """Preserve comments, custom settings, and names containing slashes."""
        self.config.write_text('name "old//name" // keep this\nfov "105"')
        self.assertEqual(self.run_wizard(['bad"name', "new//name", "3"]), 0)
        text = self.config.read_text()
        self.assertIn('"new//name" // keep this\n', text)
        self.assertIn('fov "105"\n', text)
        self.assertEqual(self.run_wizard(["new//name", "3"]), 0)
        self.assertEqual(self.config.read_text(), text)

    def test_cancel_does_not_modify_config_or_mark_setup_complete(self):
        """Leave the config and completion marker untouched on cancellation."""
        before = self.config.read_bytes()
        self.assertEqual(self.run_wizard([EOFError()]), 1)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertFalse(launcher.get_setup_marker_path(self.config).exists())

    def test_failed_save_preserves_existing_config(self):
        """Preserve the config and remove temporary files after a failed save."""
        before = self.config.read_bytes()
        with patch.object(Path, "replace", side_effect=OSError("read-only")):
            self.assertEqual(self.run_wizard(["Ranger", "3"]), 1)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertFalse(launcher.get_setup_marker_path(self.config).exists())
        self.assertFalse(self.config.with_suffix(".cfg.tmp").exists())

    def test_installed_path_is_independent_of_working_directory(self):
        """Resolve the config relative to the installed launcher script."""
        with patch.object(launcher, "__file__", str(self.root / "launcher.py")):
            path = self.get_player_cfg_path()
        self.assertEqual(path, self.config)


class WizardRecoveryTests(WizardTestCase):
    """Verify validation, missing-file recovery, resets and download failures."""

    def test_deleted_directory_recovers_before_player_prompts(self):
        """Recreate the directory without a reset question, then configure it."""
        shutil.rmtree(self.config.parent)

        def answer(prompt):
            self.assertTrue(
                all(
                    (self.config.parent / name).is_file()
                    for name in launcher.CONFIG_FILES
                )
            )
            self.assertNotIn("Reset", prompt)
            return ""

        with patch("builtins.input", side_effect=answer) as prompt:
            self.assertEqual(launcher.setup_main(["--auto"]), 0)
        self.assertEqual(prompt.call_count, 2)
        self.assertEqual(self.download.call_count, 3)
        for call, name in zip(self.download.call_args_list, launcher.CONFIG_FILES):
            self.assertEqual(call.args, (launcher.CONFIG_BASE_URL + name,))
            self.assertEqual(call.kwargs, {"timeout": 30})
        self.assertTrue(launcher.get_setup_marker_path(self.config).is_file())

    def test_missing_files_preserve_custom_configs_by_default(self):
        """Recover one or several non-player files without repeating player setup."""
        self.config.write_text('name "Custom"\nsensitivity "7"\n')
        launcher.mark_setup_complete(self.config)
        before = self.config.read_bytes()
        for names in [("autoexec.cfg",), ("game.cfg",), ("autoexec.cfg", "game.cfg")]:
            for answer in ("", "n"):
                with self.subTest(names=names, answer=answer):
                    untouched = {
                        name: (self.config.parent / name).read_bytes()
                        for name in launcher.CONFIG_FILES
                        if name not in names
                    }
                    for name in names:
                        (self.config.parent / name).unlink()
                    self.download.reset_mock()
                    self.assertEqual(self.run_wizard([answer], "--auto"), 0)
                    self.assertEqual(self.download.call_count, len(names))
                    self.assertEqual(self.config.read_bytes(), before)
                    for name, content in untouched.items():
                        self.assertEqual(
                            (self.config.parent / name).read_bytes(), content
                        )

    def test_missing_player_or_all_files_requires_player_setup(self):
        """A stale marker never skips settings for a recovered player.cfg."""
        for names in [("player.cfg",), launcher.CONFIG_FILES]:
            with self.subTest(names=names):
                launcher.mark_setup_complete(self.config)
                for name in names:
                    (self.config.parent / name).unlink()
                self.download.reset_mock()
                self.assertEqual(self.run_wizard(["n", "Ranger", "2.5"], "--auto"), 0)
                self.assertEqual(self.download.call_count, len(names))
                self.assertEqual(self.settings()["name"], '"Ranger"')
                self.assertTrue(launcher.get_setup_marker_path(self.config).is_file())

    def test_reset_replaces_three_configs_and_keeps_other_files(self):
        """Only an explicit yes replaces existing configs and repeats setup."""
        launcher.mark_setup_complete(self.config)
        self.config.write_text('name "Custom"\nfov "105"\n')
        (self.config.parent / "autoexec.cfg").write_text("custom autoexec\n")
        extra = self.config.parent / "custom.cfg"
        extra.write_text("keep me\n")
        (self.config.parent / "game.cfg").unlink()
        self.assertEqual(self.run_wizard(["invalid", "y", "Ranger", "3"], "--auto"), 0)
        self.assertEqual(self.download.call_count, 3)
        for name in ("autoexec.cfg", "game.cfg"):
            self.assertEqual(
                (self.config.parent / name).read_bytes(), (CONFIGS / name).read_bytes()
            )

        self.assertNotIn('fov "105"', self.config.read_text())
        self.assertEqual(extra.read_text(), "keep me\n")

    def test_complete_installation_needs_no_network_or_input(self):
        """Checking and automatic startup are silent when already configured."""
        launcher.mark_setup_complete(self.config)
        with patch("builtins.input") as prompt:
            self.assertEqual(launcher.setup_main(["--check"]), 0)
            self.assertEqual(launcher.setup_main(["--auto"]), 0)
        prompt.assert_not_called()
        self.download.assert_not_called()

    def test_check_reports_setup_without_mutating_or_prompting(self):
        """Check mode detects missing markers, files and directories read-only."""
        with patch("builtins.input") as prompt:
            self.assertEqual(launcher.setup_main(["--check"]), 2)
            launcher.mark_setup_complete(self.config)
            self.config.unlink()
            self.assertEqual(launcher.setup_main(["--check"]), 2)
            self.assertFalse(self.config.exists())
            shutil.rmtree(self.config.parent)
            self.assertEqual(launcher.setup_main(["--check"]), 2)
            self.assertFalse(self.config.parent.exists())
        prompt.assert_not_called()
        self.download.assert_not_called()

    def test_invalid_paths_fail_before_prompting(self):
        """Reject a file at configs/ or a directory at a required file path."""
        self.config.unlink()
        self.config.mkdir()
        with patch("builtins.input") as prompt:
            self.assertEqual(launcher.setup_main([]), 1)
            shutil.rmtree(self.config.parent)
            self.config.parent.write_text("not a directory")
            self.assertEqual(launcher.setup_main([]), 1)
        prompt.assert_not_called()
        self.download.assert_not_called()

    def test_inspection_permission_failure_precedes_prompts(self):
        """Report inaccessible paths before any recovery question."""
        with (
            patch.object(Path, "stat", side_effect=PermissionError("denied")),
            patch("builtins.input") as prompt,
        ):
            self.assertEqual(launcher.setup_main([]), 1)
        prompt.assert_not_called()
        self.download.assert_not_called()

    def test_failed_reset_downloads_preserve_existing_files_and_marker(self):
        """Stage everything before replacement, including on invalid responses."""
        launcher.mark_setup_complete(self.config)
        (self.config.parent / "game.cfg").unlink()
        before = {path.name: path.read_bytes() for path in self.config.parent.iterdir()}
        failures = [
            URLError("offline"),
            TimeoutError("timeout"),
            IncompleteRead(b"partial", 10),
            HTTPError(launcher.CONFIG_BASE_URL, 404, "Not found", {}, None),
            b" \n",
            b"\xff",
        ]
        for failure in failures:
            with self.subTest(failure=failure):
                second = io.BytesIO(failure) if isinstance(failure, bytes) else failure
                self.download.side_effect = [
                    io.BytesIO(b"valid first download\n"),
                    second,
                ]
                self.assertEqual(self.run_wizard(["y"], "--auto"), 1)
                after = {
                    path.name: path.read_bytes()
                    for path in self.config.parent.iterdir()
                }
                self.assertEqual(after, before)

    def test_failed_replacement_cleans_downloads_and_invalidates_marker(self):
        """A write failure cannot leave a reset marked as complete."""
        launcher.mark_setup_complete(self.config)
        (self.config.parent / "game.cfg").unlink()
        before = self.config.read_bytes()
        with patch.object(Path, "replace", side_effect=PermissionError("denied")):
            self.assertEqual(self.run_wizard(["y"], "--auto"), 1)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertFalse(launcher.get_setup_marker_path(self.config).exists())
        self.assertEqual(list(self.config.parent.glob(".wizard-download-*")), [])

    def test_cancel_reset_does_not_download(self):
        """Cancelling the reset question leaves the surviving files untouched."""
        (self.config.parent / "game.cfg").unlink()
        before = self.config.read_bytes()
        self.assertEqual(self.run_wizard([KeyboardInterrupt()], "--auto"), 1)
        self.assertEqual(self.config.read_bytes(), before)
        self.download.assert_not_called()

    def test_cancel_after_recovery_keeps_files_but_requires_setup(self):
        """Retain recovered defaults and retry personal setup on the next launch."""
        launcher.mark_setup_complete(self.config)
        self.config.unlink()
        self.assertEqual(self.run_wizard(["n", EOFError()], "--auto"), 1)
        self.assertEqual(
            self.config.read_bytes(), (CONFIGS / "player.cfg").read_bytes()
        )
        self.assertFalse(launcher.get_setup_marker_path(self.config).exists())
        self.assertIn("already recovered remain", self.stderr.getvalue())
        self.assertEqual(self.run_wizard(["Ranger", "3"], "--auto"), 0)


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux AppImage launcher")
class LauncherTests(unittest.TestCase):
    """Verify Linux launcher setup, argument handling, and failure paths."""

    autoexec_path = "../id1/configs/autoexec.cfg"

    def setUp(self):
        """Install a single Python launcher with offline downloads and a fake engine."""
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.root = Path(
            stack.enter_context(
                tempfile.TemporaryDirectory(prefix="vkquake launch ' $ ")
            )
        ).resolve()
        self.configs = self.root / "id1" / "configs"
        shutil.copytree(CONFIGS, self.configs)
        self.marker = self.configs / ".wizard-complete"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        (self.bin / "python3").symlink_to(sys.executable)
        self.env = dict(
            os.environ,
            PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
            DISPLAY="",
            PYTHONDONTWRITEBYTECODE="1",
        )
        shutil.copytree(CONFIGS, self.root / "templates")
        self.write_launcher(0)
        engine = self.root / "vkQuake.AppImage"
        engine.write_text(
            "#!/usr/bin/env python3\nimport json, sys\n"
            "from pathlib import Path\n"
            'Path("engine.json").write_text(json.dumps(sys.argv[1:]))\n'
            'with Path("engine-runs").open("a") as log: log.write("run\\n")\n'
        )
        engine.chmod(0o755)

    def write_launcher(self, status):
        """Instrument the standalone launcher for offline downloads and setup failures."""
        source = (SCRIPTS / "launcher.py").read_text()
        source, entrypoint = source.rsplit('\n\nif __name__ == "__main__":', 1)
        instrumentation = (
            "\n\nimport io\n"
            "root = Path(__file__).resolve().parent\n"
            "urlopen = lambda url, timeout: "
            'io.BytesIO((root / "templates" / url.rsplit("/", 1)[-1]).read_bytes())\n'
            "original_setup = setup_main\n"
            "def setup_main(argv=None):\n"
            '    if "--check" not in (argv or []):\n'
            '        with (root / "wizard-runs").open("a") as log: log.write("run\\n")\n'
            + (f"        return {status}\n" if status else "")
            + "    return original_setup(argv)\n"
        )
        (self.root / "launcher.py").write_text(
            source + instrumentation + '\n\nif __name__ == "__main__":' + entrypoint
        )

    def launch(self, *args, answers="\n\n"):
        """Run the launcher from another directory and capture its output."""
        return subprocess.run(
            [sys.executable, str(self.root / "launcher.py"), *args],
            cwd=self.bin,
            env=self.env,
            input=answers,
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )

    def test_first_run_repeat_forced_setup_and_argument_forwarding(self):
        """Run setup when needed or forced and forward engine arguments intact."""
        for args in [("-game", "a mod", "+map", "start"), (), ("--wizard",)]:
            result = self.launch(*args)
            self.assertEqual(result.returncode, 0, result.stderr)
            if args:
                self.assertIn("Configuration saved successfully", result.stdout)
        self.assertEqual(
            (self.root / "wizard-runs").read_text().splitlines(), ["run", "run"]
        )
        self.assertEqual(
            json.loads((self.root / "engine.json").read_text()),
            [
                "-basedir",
                str(self.root),
                "-userdir",
                str(self.root),
                "+exec",
                self.autoexec_path,
            ],
        )
        self.assertEqual(self.launch("-game", "a mod").returncode, 0)
        self.assertEqual(
            json.loads((self.root / "engine.json").read_text())[-2:], ["-game", "a mod"]
        )

    def test_failed_wizard_stops_launch(self):
        """Stop before starting the engine when setup fails."""
        self.write_launcher(1)
        self.assertEqual(self.launch().returncode, 1)
        self.assertFalse((self.root / "engine.json").exists())

    def test_xterm_receives_literal_script_path(self):
        """Run only setup in xterm, preserving paths, arguments and failure status."""
        xterm = self.bin / "xterm"
        xterm.write_text(
            "#!/usr/bin/env python3\nimport json, os, sys\n"
            "from pathlib import Path\n"
            '(Path(__file__).resolve().parent.parent / "xterm.json").write_text('
            "json.dumps(sys.argv[1:]))\n"
            "os.execv(sys.argv[4], sys.argv[4:])\n"
        )
        xterm.chmod(0o755)
        self.env["DISPLAY"] = ":test"
        result = self.launch("--mod", "a mod", "+map", "start")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.marker.exists())
        command = [
            "-T",
            "vkQuake Setup Wizard",
            "-e",
            sys.executable,
            str(self.root / "launcher.py"),
            "--setup-only",
        ]
        self.assertEqual(
            json.loads((self.root / "xterm.json").read_text()), [*command, "--auto"]
        )
        self.assertEqual((self.root / "engine-runs").read_text().splitlines(), ["run"])
        self.assertEqual(
            json.loads((self.root / "engine.json").read_text())[-4:],
            ["-game", "a mod", "+map", "start"],
        )

        result = self.launch("--wizard", answers="Knight\n4\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads((self.root / "xterm.json").read_text()), command)
        self.assertIn('"Knight"', (self.configs / "player.cfg").read_text())

        self.write_launcher(1)
        self.assertEqual(self.launch("--wizard").returncode, 1)
        self.assertEqual(
            (self.root / "engine-runs").read_text().splitlines(), ["run", "run"]
        )

    def test_setup_only_modes_work_with_an_unmodified_standalone_launcher(self):
        """Keep manual, automatic and check modes available without starting the game."""
        shutil.copyfile(SCRIPTS / "launcher.py", self.root / "launcher.py")
        before = (self.configs / "player.cfg").read_bytes()
        result = self.launch("--setup-only", "--check", answers="")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse(self.marker.exists())
        self.assertEqual((self.configs / "player.cfg").read_bytes(), before)

        result = self.launch("--setup-only", answers="Ranger\n2.5\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"Ranger"', (self.configs / "player.cfg").read_text())
        self.assertTrue(self.marker.is_file())
        configured = (self.configs / "player.cfg").read_bytes()
        for mode in ("--check", "--auto"):
            with self.subTest(mode=mode):
                result = self.launch("--setup-only", mode, answers="")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertEqual((self.configs / "player.cfg").read_bytes(), configured)
        self.assertFalse((self.root / "engine.json").exists())

    def test_missing_config_is_recovered_even_with_marker(self):
        """Recover a missing game config without repeating personal setup."""
        self.marker.touch()
        before = (self.configs / "player.cfg").read_bytes()
        (self.configs / "game.cfg").unlink()
        result = self.launch(answers="n\n")
        self.assertEqual(result.returncode, 0, result.stderr)

        self.assertEqual(
            (self.configs / "game.cfg").read_bytes(),
            (CONFIGS / "game.cfg").read_bytes(),
        )

        self.assertEqual((self.configs / "player.cfg").read_bytes(), before)
        self.assertTrue((self.root / "engine.json").exists())

    def test_deleted_config_directory_is_recovered(self):
        """Rebuild a deleted configs directory before launching the engine."""
        shutil.rmtree(self.configs)
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.marker.is_file())
        self.assertTrue((self.root / "engine.json").exists())

    def test_recovery_download_failure_stops_launch(self):
        """Do not start the engine when a required download fails."""
        shutil.rmtree(self.configs)
        (self.root / "templates" / "game.cfg").unlink()
        result = self.launch()
        self.assertEqual(result.returncode, 1)
        self.assertIn("Could not download game.cfg", result.stderr)
        self.assertFalse((self.root / "engine.json").exists())

    def test_check_error_stops_launch_without_running_setup(self):
        """A validation error is not mistaken for the needs-setup exit code."""
        shutil.rmtree(self.configs)
        self.configs.write_text("not a directory")
        result = self.launch()
        self.assertEqual(result.returncode, 1)
        self.assertFalse((self.root / "wizard-runs").exists())
        self.assertFalse((self.root / "engine.json").exists())

    def test_cancelled_recovery_stops_launch(self):
        """EOF at the reset question aborts startup without downloading."""
        self.marker.touch()
        (self.configs / "game.cfg").unlink()
        result = self.launch(answers="")
        self.assertEqual(result.returncode, 1)
        self.assertFalse((self.configs / "game.cfg").exists())
        self.assertFalse((self.root / "engine.json").exists())

    def test_help_does_not_start_wizard_or_engine(self):
        """Display help without running setup or starting the engine."""
        result = self.launch("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--wizard", result.stdout)
        self.assertFalse((self.root / "wizard-runs").exists())
        self.assertFalse((self.root / "engine.json").exists())

    def test_mods_use_shared_autoexec_and_includes_before_starting_map(self):
        """Mod configs cannot shadow the shared autoexec or its game/player files."""
        self.marker.touch()
        for mod in ("ctf", "dopa", "hipnotic", "id1", "mg1", "mg3", "rogue", "a mod"):
            directory = self.root / mod
            if mod != "id1":
                (directory / "configs").mkdir(parents=True)
                for name in ("autoexec.cfg", "game.cfg", "player.cfg"):
                    (directory / "configs" / name).write_text("echo mod settings\n")

            for flags in (("--mod", mod), ("-game", mod), ("--", "-game", mod)):
                with self.subTest(mod=mod, flags=flags):
                    result = self.launch(*flags, "+map", "start")
                    self.assertEqual(result.returncode, 0, result.stderr)
                    args = json.loads((self.root / "engine.json").read_text())
                    self.assertEqual(args[-4:], ["-game", mod, "+map", "start"])
                    self.assertLess(args.index("+exec"), args.index("+map"))
                    # Quake resolves exec paths against the mod first, then id1.
                    autoexec = directory / args[args.index("+exec") + 1]
                    self.assertEqual(autoexec.resolve(), self.configs / "autoexec.cfg")
                    includes = [
                        shlex.split(line)[1]
                        for line in autoexec.read_text().splitlines()
                        if line.startswith("exec ")
                    ]
                    self.assertEqual(
                        [(directory / path).resolve() for path in includes],
                        [self.configs / "game.cfg", self.configs / "player.cfg"],
                    )
                    if mod != "id1":
                        for name in ("autoexec.cfg", "game.cfg", "player.cfg"):
                            self.assertEqual(
                                (directory / "configs" / name).read_text(),
                                "echo mod settings\n",
                            )
        self.assertFalse((self.root / "wizard-runs").exists())

    def test_existing_autoexec_includes_are_migrated_without_resetting_settings(self):
        """Upgrade older configs once, retaining custom commands, comments and modes."""
        self.marker.touch()
        autoexec = self.configs / "autoexec.cfg"
        original = (
            b"// exec configs/game.cfg\r\nvid_unlock\r\n"
            b'  exec "configs/game.cfg" // shared defaults\r\n'
            b"EXEC\tconfigs/player.cfg; sensitivity 7\r\n"
            b'bind F8 "exec configs/player.cfg"\r\nvid_restart\r\n'
        )
        expected = (
            b"// exec configs/game.cfg\r\nvid_unlock\r\n"
            b'  exec "../id1/configs/game.cfg" // shared defaults\r\n'
            b"EXEC\t../id1/configs/player.cfg; sensitivity 7\r\n"
            b'bind F8 "exec configs/player.cfg"\r\nvid_restart\r\n'
        )
        autoexec.write_bytes(original)
        autoexec.chmod(0o640)
        before = {
            name: (self.configs / name).read_bytes()
            for name in ("game.cfg", "player.cfg")
        }
        result = self.launch("-game", "rogue")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(autoexec.read_bytes(), expected)
        self.assertEqual(autoexec.stat().st_mode & 0o777, 0o640)
        modified = autoexec.stat().st_mtime_ns
        self.assertEqual(self.launch().returncode, 0)
        self.assertEqual(autoexec.stat().st_mtime_ns, modified)
        self.assertEqual(
            {name: (self.configs / name).read_bytes() for name in before}, before
        )
        self.assertEqual(list(self.configs.glob(".launcher-autoexec-*")), [])
        self.assertFalse((self.root / "wizard-runs").exists())

    def test_mod_requires_a_value_before_setup_or_launch(self):
        """A missing mod value must not consume another option or start setup."""
        for args in (
            ("--mod",),
            ("--mod", ""),
            ("--mod", "--wizard"),
            ("--mod", "+map"),
        ):
            with self.subTest(args=args):
                result = self.launch(*args)
                self.assertEqual(result.returncode, 2)
                self.assertIn("--mod requires a mod folder name", result.stderr)
                self.assertFalse((self.root / "wizard-runs").exists())
                self.assertFalse((self.root / "engine.json").exists())

    def test_separator_forwards_launcher_options_and_shell_characters_literally(self):
        """Arguments after -- belong to the engine and are never shell code."""
        self.marker.touch()
        arguments = [
            "--setup-only",
            "--check",
            "--auto",
            "--help",
            "--wizard",
            "--mod",
            "a mod",
            "$(touch injected)",
            "'quoted'",
        ]
        result = self.launch("--", *arguments)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads((self.root / "engine.json").read_text())[-len(arguments) :],
            arguments,
        )
        self.assertFalse((self.root / "wizard-runs").exists())
        self.assertFalse((self.root / "injected").exists())

    def test_engine_inherits_sdl_settings_and_returns_its_exit_status(self):
        """Preserve SDL environment overrides and the engine exit status."""
        self.marker.touch()
        variables = {
            "SDL_MOUSE_RELATIVE_MODE_WARP": "0",
            "SDL_MOUSE_RELATIVE_SCALING": "0",
            "SDL_VIDEO_X11_NET_WM_BYPASS_COMPOSITOR": "1",
        }
        self.env.update(dict.fromkeys(variables, "old"))
        (self.root / "vkQuake.AppImage").write_text(
            "#!/usr/bin/env python3\nimport json, os, sys\n"
            "from pathlib import Path\n"
            f"values = {{name: os.environ[name] for name in {list(variables)!r}}}\n"
            'Path("engine-env.json").write_text(json.dumps(values))\n'
            "sys.exit(7)\n"
        )
        self.assertEqual(self.launch().returncode, 7)
        self.assertEqual(
            json.loads((self.root / "engine-env.json").read_text()), variables
        )

    def test_help_works_without_installed_configs_or_engine(self):
        """Help is available before the remaining installation files exist."""
        shutil.rmtree(self.configs)
        (self.root / "vkQuake.AppImage").unlink()
        result = self.launch("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("launcher.py", result.stdout)
        self.assertIn("--mod NAME", result.stdout)
        self.assertIn("id1/configs/autoexec.cfg", result.stdout)
