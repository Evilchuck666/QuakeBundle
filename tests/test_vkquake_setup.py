"""Offline regression checks for the vkQuake setup wizard and Linux launcher."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
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
SPEC = importlib.util.spec_from_file_location("vkquake_wizard", SCRIPTS / "wizard.py")
wizard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(wizard)


class WizardTests(unittest.TestCase):
    """Verify wizard settings, failure handling, and installed paths."""

    # pylint: disable=too-many-public-methods

    def setUp(self):
        """Create an isolated player config and capture wizard output."""
        temporary = tempfile.TemporaryDirectory(prefix="vkquake setup ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = self.root / "id1" / "configs" / "player.cfg"
        shutil.copytree(CONFIGS, self.config.parent)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.stderr = self.stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
        self.get_player_cfg_path = wizard.get_player_cfg_path
        self.stack.enter_context(
            patch.object(wizard, "get_player_cfg_path", return_value=self.config)
        )
        self.download = self.stack.enter_context(patch.object(
            wizard, "urlopen", side_effect=lambda url, timeout: io.BytesIO(
                (CONFIGS / url.rsplit("/", 1)[-1]).read_bytes()
            )
        ))

    def run_wizard(self, answers, *args):
        """Run the wizard with scripted answers to its prompts."""
        with patch("builtins.input", side_effect=answers):
            return wizard.main(list(args))

    def settings(self):
        """Read active player settings from the temporary config."""
        return dict(line.split(maxsplit=1) for line in self.config.read_text().splitlines()
                    if line.strip() and not line.startswith("//"))

    def test_player_settings_update_template_and_record_success(self):
        """Save entered settings and mark setup complete."""
        expected = self.settings()
        expected.update(name='"Ranger"', _cl_name='"Ranger"', sensitivity='"2.5"')
        self.assertEqual(self.run_wizard(["Ranger", "2.5"]), 0)
        self.assertEqual(self.settings(), expected)
        self.assertTrue(wizard.get_setup_marker_path(self.config).is_file())

    def test_default_settings_do_not_add_video_overrides(self):
        """Apply default player settings without adding video overrides."""
        expected = self.settings()
        expected.update(name='"Player"', _cl_name='"Player"', sensitivity='"3.0"')
        self.assertEqual(self.run_wizard(["", ""]), 0)
        self.assertEqual(self.settings(), expected)

    def test_sensitivity_reprompts_for_invalid_numbers(self):
        """Reject invalid sensitivity values until a valid number is entered."""
        self.assertEqual(self.run_wizard(["Ranger", "nan", "-2", "2.5"]), 0)
        self.assertEqual(self.settings()["sensitivity"], '"2.5"')

    def test_comments_custom_settings_and_url_names_survive_rerun(self):
        """Preserve comments, custom settings, and names containing slashes."""
        self.config.write_text('name "old//name" // keep this\nfov "105"')
        self.assertEqual(self.run_wizard(['bad"name', 'new//name', "3"]), 0)
        text = self.config.read_text()
        self.assertIn('"new//name" // keep this\n', text)
        self.assertIn('fov "105"\n', text)
        self.assertEqual(self.run_wizard(['new//name', "3"]), 0)
        self.assertEqual(self.config.read_text(), text)

    def test_cancel_does_not_modify_config_or_mark_setup_complete(self):
        """Leave the config and completion marker untouched on cancellation."""
        before = self.config.read_bytes()
        self.assertEqual(self.run_wizard([EOFError()]), 1)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertFalse(wizard.get_setup_marker_path(self.config).exists())

    def test_deleted_directory_recovers_before_player_prompts(self):
        """Recreate the directory without a reset question, then configure it."""
        shutil.rmtree(self.config.parent)

        def answer(prompt):
            self.assertTrue(all((self.config.parent / name).is_file()
                                for name in wizard.CONFIG_FILES))
            self.assertNotIn("Reset", prompt)
            return ""

        with patch("builtins.input", side_effect=answer) as prompt:
            self.assertEqual(wizard.main(["--auto"]), 0)
        self.assertEqual(prompt.call_count, 2)
        self.assertEqual(self.download.call_count, 3)
        for call, name in zip(self.download.call_args_list, wizard.CONFIG_FILES):
            self.assertEqual(call.args, (wizard.CONFIG_BASE_URL + name,))
            self.assertEqual(call.kwargs, {"timeout": 30})
        self.assertTrue(wizard.get_setup_marker_path(self.config).is_file())

    def test_missing_files_preserve_custom_configs_by_default(self):
        """Recover one or several non-player files without repeating player setup."""
        self.config.write_text('name "Custom"\nsensitivity "7"\n')
        wizard.mark_setup_complete(self.config)
        before = self.config.read_bytes()
        for names in [("autoexec.cfg",), ("game.cfg",), ("autoexec.cfg", "game.cfg")]:
            for answer in ("", "n"):
                with self.subTest(names=names, answer=answer):
                    untouched = {name: (self.config.parent / name).read_bytes()
                                 for name in wizard.CONFIG_FILES if name not in names}
                    for name in names:
                        (self.config.parent / name).unlink()
                    self.download.reset_mock()
                    self.assertEqual(self.run_wizard([answer], "--auto"), 0)
                    self.assertEqual(self.download.call_count, len(names))
                    self.assertEqual(self.config.read_bytes(), before)
                    for name, content in untouched.items():
                        self.assertEqual((self.config.parent / name).read_bytes(), content)

    def test_missing_player_or_all_files_requires_player_setup(self):
        """A stale marker never skips settings for a recovered player.cfg."""
        for names in [("player.cfg",), wizard.CONFIG_FILES]:
            with self.subTest(names=names):
                wizard.mark_setup_complete(self.config)
                for name in names:
                    (self.config.parent / name).unlink()
                self.download.reset_mock()
                self.assertEqual(self.run_wizard(["n", "Ranger", "2.5"], "--auto"), 0)
                self.assertEqual(self.download.call_count, len(names))
                self.assertEqual(self.settings()["name"], '"Ranger"')
                self.assertTrue(wizard.get_setup_marker_path(self.config).is_file())

    def test_reset_replaces_three_configs_and_keeps_other_files(self):
        """Only an explicit yes replaces existing configs and repeats setup."""
        wizard.mark_setup_complete(self.config)
        self.config.write_text('name "Custom"\nfov "105"\n')
        (self.config.parent / "autoexec.cfg").write_text("custom autoexec\n")
        extra = self.config.parent / "custom.cfg"
        extra.write_text("keep me\n")
        (self.config.parent / "game.cfg").unlink()
        self.assertEqual(self.run_wizard(["invalid", "y", "Ranger", "3"], "--auto"), 0)
        self.assertEqual(self.download.call_count, 3)
        for name in ("autoexec.cfg", "game.cfg"):
            self.assertEqual(
                (self.config.parent / name).read_bytes(),
                (CONFIGS / name).read_bytes()
            )

        self.assertNotIn('fov "105"', self.config.read_text())
        self.assertEqual(extra.read_text(), "keep me\n")

    def test_complete_installation_needs_no_network_or_input(self):
        """Checking and automatic startup are silent when already configured."""
        wizard.mark_setup_complete(self.config)
        with patch("builtins.input") as prompt:
            self.assertEqual(wizard.main(["--check"]), 0)
            self.assertEqual(wizard.main(["--auto"]), 0)
        prompt.assert_not_called()
        self.download.assert_not_called()

    def test_check_reports_setup_without_mutating_or_prompting(self):
        """Check mode detects missing markers, files and directories read-only."""
        with patch("builtins.input") as prompt:
            self.assertEqual(wizard.main(["--check"]), 2)
            wizard.mark_setup_complete(self.config)
            self.config.unlink()
            self.assertEqual(wizard.main(["--check"]), 2)
            self.assertFalse(self.config.exists())
            shutil.rmtree(self.config.parent)
            self.assertEqual(wizard.main(["--check"]), 2)
            self.assertFalse(self.config.parent.exists())
        prompt.assert_not_called()
        self.download.assert_not_called()

    def test_invalid_paths_fail_before_prompting(self):
        """Reject a file at configs/ or a directory at a required file path."""
        self.config.unlink()
        self.config.mkdir()
        with patch("builtins.input") as prompt:
            self.assertEqual(wizard.main([]), 1)
            shutil.rmtree(self.config.parent)
            self.config.parent.write_text("not a directory")
            self.assertEqual(wizard.main([]), 1)
        prompt.assert_not_called()
        self.download.assert_not_called()

    def test_inspection_permission_failure_precedes_prompts(self):
        """Report inaccessible paths before any recovery question."""
        with patch.object(Path, "stat", side_effect=PermissionError("denied")), \
                patch("builtins.input") as prompt:
            self.assertEqual(wizard.main([]), 1)
        prompt.assert_not_called()
        self.download.assert_not_called()

    def test_failed_reset_downloads_preserve_existing_files_and_marker(self):
        """Stage everything before replacement, including on invalid responses."""
        wizard.mark_setup_complete(self.config)
        (self.config.parent / "game.cfg").unlink()
        before = {path.name: path.read_bytes() for path in self.config.parent.iterdir()}
        failures = [URLError("offline"), TimeoutError("timeout"), IncompleteRead(b"partial", 10),
                    HTTPError(wizard.CONFIG_BASE_URL, 404, "Not found", {}, None),
                    b" \n", b"\xff"]
        for failure in failures:
            with self.subTest(failure=failure):
                second = io.BytesIO(failure) if isinstance(failure, bytes) else failure
                self.download.side_effect = [io.BytesIO(b"valid first download\n"), second]
                self.assertEqual(self.run_wizard(["y"], "--auto"), 1)
                after = {path.name: path.read_bytes() for path in self.config.parent.iterdir()}
                self.assertEqual(after, before)

    def test_failed_replacement_cleans_downloads_and_invalidates_marker(self):
        """A write failure cannot leave a reset marked as complete."""
        wizard.mark_setup_complete(self.config)
        (self.config.parent / "game.cfg").unlink()
        before = self.config.read_bytes()
        with patch.object(Path, "replace", side_effect=PermissionError("denied")):
            self.assertEqual(self.run_wizard(["y"], "--auto"), 1)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertFalse(wizard.get_setup_marker_path(self.config).exists())
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
        wizard.mark_setup_complete(self.config)
        self.config.unlink()
        self.assertEqual(self.run_wizard(["n", EOFError()], "--auto"), 1)
        self.assertEqual(self.config.read_bytes(), (CONFIGS / "player.cfg").read_bytes())
        self.assertFalse(wizard.get_setup_marker_path(self.config).exists())
        self.assertIn("already recovered remain", self.stderr.getvalue())
        self.assertEqual(self.run_wizard(["Ranger", "3"], "--auto"), 0)

    def test_failed_save_preserves_existing_config(self):
        """Preserve the config and remove temporary files after a failed save."""
        before = self.config.read_bytes()
        with patch.object(Path, "replace", side_effect=OSError("read-only")):
            self.assertEqual(self.run_wizard(["Ranger", "3"]), 1)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertFalse(wizard.get_setup_marker_path(self.config).exists())
        self.assertFalse(self.config.with_suffix(".cfg.tmp").exists())

    def test_installed_path_is_independent_of_working_directory(self):
        """Resolve the config relative to the installed wizard script."""
        with patch.object(wizard, "__file__", str(self.root / "wizard.py")):
            path = self.get_player_cfg_path()
        self.assertEqual(path, self.config)


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux Bash launcher")
class LauncherTests(unittest.TestCase):
    """Verify Linux launcher setup, argument handling, and failure paths."""

    def setUp(self):
        """Create an installation with the real wizard and offline downloads."""
        temporary = tempfile.TemporaryDirectory(prefix="vkquake launch ' $ ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        shutil.copyfile(SCRIPTS / "vkQuake.sh", self.root / "vkQuake.sh")
        self.configs = self.root / "id1" / "configs"
        shutil.copytree(CONFIGS, self.configs)
        self.marker = self.configs / ".wizard-complete"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        (self.bin / "python3").symlink_to(sys.executable)
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
                        DISPLAY="", PYTHONDONTWRITEBYTECODE="1")
        shutil.copyfile(SCRIPTS / "wizard.py", self.root / "setup.py")
        shutil.copytree(CONFIGS, self.root / "templates")
        self.write_wizard(0)
        engine = self.root / "vkQuake.AppImage"
        engine.write_text('#!/usr/bin/env python3\nimport json, sys\n'
                          'from pathlib import Path\n'
                          'Path("engine.json").write_text(json.dumps(sys.argv[1:]))\n')
        engine.chmod(0o755)

    def write_wizard(self, status):
        """Run the actual wizard with local downloads and optional setup failure."""
        (self.root / "wizard.py").write_text(
            'from pathlib import Path\nimport io, runpy, sys\n'
            'root = Path(__file__).resolve().parent\n'
            'scope = runpy.run_path(str(root / "setup.py"))\n'
            'main = scope["main"]\n'
            'main.__globals__["urlopen"] = lambda url, timeout: '
            'io.BytesIO((root / "templates" / url.rsplit("/", 1)[-1]).read_bytes())\n'
            'if "--check" not in sys.argv:\n'
            '    with (root / "wizard-runs").open("a") as log: log.write("run\\n")\n'
            + (f'    sys.exit({status})\n' if status else '')
            + 'sys.exit(main())\n'
        )

    def launch(self, *args, answers="\n\n"):
        """Run the launcher from another directory and capture its output."""
        return subprocess.run(["bash", str(self.root / "vkQuake.sh"), *args],
                              cwd=self.bin, env=self.env, input=answers, text=True,
                              capture_output=True, timeout=15, check=False)

    def test_first_run_repeat_forced_setup_and_argument_forwarding(self):
        """Run setup when needed or forced and forward engine arguments intact."""
        for args in [("-game", "a mod", "+map", "start"), (), ("--wizard",)]:
            result = self.launch(*args)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "wizard-runs").read_text().splitlines(), ["run", "run"])
        self.assertEqual(json.loads((self.root / "engine.json").read_text()), [
            "-basedir",
            str(self.root),
            "-userdir",
            str(self.root),
            "+exec", "configs/autoexec.cfg"
        ])
        self.assertEqual(self.launch("-game", "a mod").returncode, 0)
        self.assertEqual(
            json.loads((self.root / "engine.json").read_text())[-2:], ["-game", "a mod"]
        )

    def test_failed_wizard_stops_launch(self):
        """Stop before starting the engine when setup fails."""
        self.write_wizard(1)
        self.assertEqual(self.launch().returncode, 1)
        self.assertFalse((self.root / "engine.json").exists())

    def test_xterm_receives_literal_script_path(self):
        """Preserve the script path and wizard exit status when using xterm."""
        xterm = self.bin / "xterm"
        xterm.write_text('#!/usr/bin/env bash\nshift 3\nexec "$@"\n')
        xterm.chmod(0o755)
        self.env["DISPLAY"] = ":test"
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.marker.exists())
        self.write_wizard(1)
        self.assertEqual(self.launch("--wizard").returncode, 1)

    def test_missing_config_is_recovered_even_with_marker(self):
        """Recover a missing game config without repeating personal setup."""
        self.marker.touch()
        before = (self.configs / "player.cfg").read_bytes()
        (self.configs / "game.cfg").unlink()
        result = self.launch(answers="n\n")
        self.assertEqual(result.returncode, 0, result.stderr)

        self.assertEqual(
            (self.configs / "game.cfg").read_bytes(),
            (CONFIGS / "game.cfg").read_bytes()
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
