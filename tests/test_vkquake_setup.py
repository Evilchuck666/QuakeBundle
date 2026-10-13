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
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "assets" / "scripts" / "vkQuake"
CONFIGS = ROOT / "assets" / "cfg" / "vkQuake"
SPEC = importlib.util.spec_from_file_location("vkquake_wizard", SCRIPTS / "wizard.py")
wizard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(wizard)


class WizardTests(unittest.TestCase):
    """Verify wizard settings, failure handling, and installed paths."""

    def setUp(self):
        """Create an isolated player config and capture wizard output."""
        temporary = tempfile.TemporaryDirectory(prefix="vkquake setup ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = self.root / "id1" / "configs" / "player.cfg"
        self.config.parent.mkdir(parents=True)
        shutil.copyfile(CONFIGS / "player.cfg", self.config)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
        self.get_player_cfg_path = wizard.get_player_cfg_path
        self.stack.enter_context(
            patch.object(wizard, "get_player_cfg_path", return_value=self.config)
        )

    def run_wizard(self, answers):
        """Run the wizard with scripted answers to its prompts."""
        with patch("builtins.input", side_effect=answers):
            return wizard.main()

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

    def test_missing_template_fails_before_prompting(self):
        """Fail before requesting input when the config template is missing."""
        self.config.unlink()
        with patch("builtins.input") as prompt:
            self.assertEqual(wizard.main(), 1)
        prompt.assert_not_called()

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
        """Create an isolated installation with stub wizard and engine scripts."""
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
        self.write_wizard(0)
        engine = self.root / "vkQuake.AppImage"
        engine.write_text('#!/usr/bin/env python3\nimport json, sys\n'
                          'from pathlib import Path\n'
                          'Path("engine.json").write_text(json.dumps(sys.argv[1:]))\n')
        engine.chmod(0o755)

    def write_wizard(self, status):
        """Write a wizard stub that records runs and exits with the given status."""
        (self.root / "wizard.py").write_text(
            'from pathlib import Path\nimport sys\n'
            'root = Path(__file__).resolve().parent\n'
            'with (root / "wizard-runs").open("a") as log: log.write("run\\n")\n'
            + ('(root / "id1/configs/.wizard-complete").touch()\n' if status == 0 else '')
            + f'sys.exit({status})\n'
        )

    def launch(self, *args):
        """Run the launcher from another directory and capture its output."""
        return subprocess.run(["bash", str(self.root / "vkQuake.sh"), *args],
                              cwd=self.bin, env=self.env, input="", text=True,
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

    def test_missing_config_stops_launch(self):
        """Report missing configuration without starting the engine."""
        (self.configs / "game.cfg").unlink()
        result = self.launch()
        self.assertEqual(result.returncode, 1)
        self.assertIn("Missing configuration", result.stderr)
        self.assertFalse((self.root / "engine.json").exists())

    def test_help_does_not_start_wizard_or_engine(self):
        """Display help without running setup or starting the engine."""
        result = self.launch("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--wizard", result.stdout)
        self.assertFalse((self.root / "wizard-runs").exists())
        self.assertFalse((self.root / "engine.json").exists())
