"""Offline regression checks for the standalone ezQuake launcher."""

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
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "assets" / "scripts" / "ezQuake"
CONFIGS = ROOT / "assets" / "cfg" / "ezQuake"
SPEC = importlib.util.spec_from_file_location("ezquake_launcher", SCRIPTS / "launcher.py")
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)


class WizardTests(unittest.TestCase):
    """Check first-run state, recovery and preservation of player settings."""

    def setUp(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        stack.enter_context(patch.object(launcher, "__file__", str(self.root / "launcher.py")))
        self.configs = self.root / "ezquake" / "configs"
        shutil.copytree(CONFIGS, self.configs)
        self.player = self.configs / "player.cfg"
        self.marker = self.configs / ".wizard-complete"
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
        self.download = stack.enter_context(patch.object(
            launcher, "urlopen", side_effect=lambda url, timeout: io.BytesIO(
                (CONFIGS / url.rsplit("/", 1)[-1]).read_bytes()
            )
        ))

    def settings(self):
        """Read active player settings from the temporary config."""
        return dict(line.split(maxsplit=1) for line in self.player.read_text().splitlines()
                    if line.strip() and not line.startswith("//"))

    def test_check_is_read_only_and_requires_all_configs_and_setup_marker(self):
        """Engine config.cfg does not replace first-run state or required files."""
        (self.configs / "config.cfg").touch()
        before = self.player.read_bytes()
        with patch("builtins.input") as prompt:
            self.assertEqual(launcher.setup_main(["--check"]), 2)
            self.assertFalse(self.marker.exists())
            launcher.mark_setup_complete(self.player)
            self.assertEqual(launcher.setup_main(["--check"]), 0)
            for name in launcher.CONFIG_FILES:
                with self.subTest(name=name):
                    (self.configs / name).unlink()
                    self.assertEqual(launcher.setup_main(["--check"]), 2)
                    self.assertFalse((self.configs / name).exists())
                    shutil.copyfile(CONFIGS / name, self.configs / name)
            self.assertEqual(self.player.read_bytes(), before)
            shutil.rmtree(self.configs)
            self.assertEqual(launcher.setup_main(["--check"]), 2)
            self.assertFalse(self.configs.exists())
        prompt.assert_not_called()
        self.download.assert_not_called()

    def test_auto_sets_player_settings_once_and_forced_setup_updates_them(self):
        """Record completion without depending on the engine's config.cfg."""
        with patch("builtins.input", side_effect=["Ranger", "Rng", "Red", "2.5"]):
            self.assertEqual(launcher.setup_main(["--auto"]), 0)
        self.assertTrue(self.marker.is_file())
        self.assertFalse((self.configs / "config.cfg").exists())
        before = self.player.read_bytes()
        self.assertEqual(self.settings(), {
            "name": '"Ranger"', "cl_fakename": '"Rng"', "team": '"Red"',
            "hostname": '"Ranger SV"', "sensitivity": '"2.5"',
        })
        with patch("builtins.input") as prompt:
            self.assertEqual(launcher.setup_main(["--auto"]), 0)
        prompt.assert_not_called()
        self.assertEqual(self.player.read_bytes(), before)
        with patch("builtins.input", side_effect=["Knight", "Knt", "Blue", "4"]):
            self.assertEqual(launcher.setup_main([]), 0)
        self.assertEqual(self.settings(), {
            "name": '"Knight"', "cl_fakename": '"Knt"', "team": '"Blue"',
            "hostname": '"Knight SV"', "sensitivity": '"4.0"',
        })
        self.download.assert_not_called()

    def test_default_short_name_uses_player_name_with_required_team(self):
        """Accept player defaults while supplying the required team name."""
        for answer, name in (("", "Player"), ("  Ranger  ", "Ranger")):
            with self.subTest(name=name):
                with patch("builtins.input", side_effect=[answer, "", "Red", ""]):
                    self.assertEqual(launcher.setup_main([]), 0)
                self.assertEqual(self.settings(), {
                    "name": f'"{name}"', "cl_fakename": f'"{name}"', "team": '"Red"',
                    "hostname": f'"{name} SV"', "sensitivity": '"3.0"',
                })

    def test_names_reprompt_for_quotes_line_breaks_and_required_empty_values(self):
        """Validate all three names before storing quoted configuration values."""
        answers = [
            'bad"name', "bad\nname", "bad\rname", "   ", "  Ranger  ",
            'bad"short', "bad\nshort", "bad\rshort", "   ", "  Rng  ",
            'bad"team', "bad\nteam", "bad\rteam", "", "   ", "  Red  ", "3",
        ]
        with patch("builtins.input", side_effect=answers):
            self.assertEqual(launcher.setup_main([]), 0)
        self.assertEqual(self.settings(), {
            "name": '"Ranger"', "cl_fakename": '"Rng"', "team": '"Red"',
            "hostname": '"Ranger SV"', "sensitivity": '"3.0"',
        })

    def test_sensitivity_reprompts_for_invalid_or_nonfinite_numbers(self):
        """Only save a positive, finite sensitivity, as in the vkQuake launcher."""
        answers = ["Ranger", "Rng", "Red", "abc", "nan", "inf", "-inf", "1e309",
                   "0", "-2", "2.5"]
        with patch("builtins.input", side_effect=answers) as prompt:
            self.assertEqual(launcher.setup_main([]), 0)
        self.assertEqual(prompt.call_count, len(answers))
        self.assertEqual(self.settings()["sensitivity"], '"2.5"')

    def test_comments_custom_settings_and_slashes_survive_rerun(self):
        """Append missing settings and keep // inside names separate from comments."""
        self.player.write_text(
            '// player settings\nname "old//name" // full name\n'
            'cl_fakename "old//short" // short name\n'
            'team "old//team" // team name\nfov "105"'
        )
        answers = ["new//name", "new//short", "new//team", "3"]
        with patch("builtins.input", side_effect=answers):
            self.assertEqual(launcher.setup_main([]), 0)
        text = self.player.read_text()
        for line in ('// player settings\n', '"new//name" // full name\n',
                     '"new//short" // short name\n', '"new//team" // team name\n',
                     'fov "105"\n'):
            self.assertIn(line, text)
        self.assertEqual(self.settings()["hostname"], '"new//name SV"')
        self.assertEqual(self.settings()["sensitivity"], '"3.0"')
        with patch("builtins.input", side_effect=answers):
            self.assertEqual(launcher.setup_main([]), 0)
        self.assertEqual(self.player.read_text(), text)

    def test_cancellation_at_any_player_prompt_preserves_config(self):
        """Do not save partial player settings or complete a cancelled setup."""
        before = self.player.read_bytes()
        answers = ["Ranger", "Rng", "Red"]
        for index in range(4):
            for error in (EOFError(), KeyboardInterrupt()):
                with self.subTest(prompt=index, error=type(error).__name__):
                    with patch("builtins.input", side_effect=answers[:index] + [error]):
                        self.assertEqual(launcher.setup_main([]), 1)
                    self.assertEqual(self.player.read_bytes(), before)
                    self.assertFalse(self.marker.exists())

    def test_deleted_directory_is_recovered_before_player_prompts(self):
        """Recover ezQuake templates before asking for the player's settings."""
        shutil.rmtree(self.configs)
        answers = iter(["", "", "Red", ""])

        def answer(prompt):
            self.assertNotIn("Reset", prompt)
            self.assertTrue(all((self.configs / name).is_file() for name in launcher.CONFIG_FILES))
            return next(answers)

        with patch("builtins.input", side_effect=answer) as prompt:
            self.assertEqual(launcher.setup_main(["--auto"]), 0)
        self.assertEqual(prompt.call_count, 4)
        self.assertEqual(self.settings()["team"], '"Red"')
        self.assertTrue(self.marker.is_file())
        self.assertEqual(self.download.call_count, 3)
        for call, name in zip(self.download.call_args_list, launcher.CONFIG_FILES):
            self.assertEqual(call.args, (launcher.CONFIG_BASE_URL + name,))
            self.assertEqual(call.kwargs, {"timeout": 30})
        self.assertTrue(launcher.CONFIG_BASE_URL.endswith("/assets/cfg/ezQuake/"))

    def test_missing_game_config_preserves_player_and_other_user_files_by_default(self):
        """Default recovery downloads only missing files and keeps personal setup."""
        self.player.write_text('name "Custom"\nteam "Custom Team"\nsensitivity "7"\n')
        (self.configs / "config.cfg").write_text("engine settings\n")
        (self.configs / "extra.cfg").write_text("custom settings\n")
        launcher.mark_setup_complete(self.player)
        before = {path.name: path.read_bytes() for path in self.configs.iterdir()}
        (self.configs / "game.cfg").unlink()
        with patch("builtins.input", side_effect=[""]):
            self.assertEqual(launcher.setup_main(["--auto"]), 0)
        self.download.assert_called_once_with(launcher.CONFIG_BASE_URL + "game.cfg", timeout=30)
        self.assertEqual({path.name: path.read_bytes() for path in self.configs.iterdir()}, before)

    def test_reset_replaces_defaults_and_only_marks_complete_after_player_setup(self):
        """An explicit reset restores all three templates and reapplies settings."""
        self.player.write_text('name "Custom"\nfov "105"\n')
        (self.configs / "extra.cfg").write_text("keep me\n")
        launcher.mark_setup_complete(self.player)
        (self.configs / "game.cfg").unlink()
        with patch("builtins.input", side_effect=["invalid", "y", "Ranger", "Rng", "Red", "3"]):
            self.assertEqual(launcher.setup_main(["--auto"]), 0)
        self.assertEqual(self.download.call_count, 3)
        self.assertNotIn("fov", self.player.read_text())
        self.assertIn('"Red"', self.player.read_text())
        self.assertEqual((self.configs / "extra.cfg").read_text(), "keep me\n")
        self.assertTrue(self.marker.is_file())

    def test_cancel_after_player_recovery_requires_setup_on_next_run(self):
        """A restored default player config cannot retain an old completion marker."""
        launcher.mark_setup_complete(self.player)
        self.player.unlink()
        with patch("builtins.input", side_effect=["n", EOFError()]):
            self.assertEqual(launcher.setup_main(["--auto"]), 1)
        self.assertEqual(self.player.read_bytes(), (CONFIGS / "player.cfg").read_bytes())
        self.assertFalse(self.marker.exists())
        self.assertEqual(launcher.setup_main(["--check"]), 2)

    def test_failed_reset_download_preserves_existing_files_and_marker(self):
        """Stage downloads so an incomplete or invalid response cannot overwrite files."""
        launcher.mark_setup_complete(self.player)
        (self.configs / "game.cfg").unlink()
        before = {path.name: path.read_bytes() for path in self.configs.iterdir()}
        for failure in (URLError("offline"), b" \n", b"\xff"):
            with self.subTest(failure=failure):
                response = io.BytesIO(failure) if isinstance(failure, bytes) else failure
                self.download.side_effect = [io.BytesIO(b"new autoexec\n"), response]
                with patch("builtins.input", side_effect=["y"]):
                    self.assertEqual(launcher.setup_main(["--auto"]), 1)
                self.assertEqual(
                    {path.name: path.read_bytes() for path in self.configs.iterdir()}, before
                )

    def test_failed_save_preserves_player_and_does_not_mark_complete(self):
        """A write failure cannot truncate the existing settings or complete setup."""
        before = self.player.read_bytes()
        with patch("builtins.input", side_effect=["Ranger", "Rng", "Red", "3"]), \
                patch.object(Path, "replace", side_effect=PermissionError("read-only")):
            self.assertEqual(launcher.setup_main([]), 1)
        self.assertEqual(self.player.read_bytes(), before)
        self.assertFalse(self.marker.exists())
        self.assertFalse(self.player.with_suffix(".cfg.tmp").exists())

    def test_failed_reset_replacement_invalidates_previous_setup(self):
        """A failed replacement leaves no stale marker or staged downloads."""
        launcher.mark_setup_complete(self.player)
        (self.configs / "game.cfg").unlink()
        before = self.player.read_bytes()
        with patch("builtins.input", side_effect=["y"]), \
                patch.object(Path, "replace", side_effect=PermissionError("read-only")):
            self.assertEqual(launcher.setup_main(["--auto"]), 1)
        self.assertEqual(self.player.read_bytes(), before)
        self.assertFalse(self.marker.exists())
        self.assertEqual(list(self.configs.glob(".wizard-download-*")), [])

    def test_invalid_config_path_fails_before_input_or_download(self):
        """Filesystem errors must propagate instead of triggering setup."""
        self.player.unlink()
        self.player.mkdir()
        with patch("builtins.input") as prompt:
            self.assertEqual(launcher.setup_main(["--check"]), 1)
            self.assertEqual(launcher.setup_main(["--auto"]), 1)
        prompt.assert_not_called()
        self.download.assert_not_called()


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux AppImage launcher")
class LauncherTests(unittest.TestCase):
    """Exercise the standalone launcher with a fake engine and terminal."""

    autoexec_path = "ezquake/configs/autoexec.cfg"

    def setUp(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.root = Path(stack.enter_context(
            tempfile.TemporaryDirectory(prefix="ezquake launch ' $HOME ")
        )).resolve()
        shutil.copytree(CONFIGS, self.root / "templates")
        self.write_launcher(0)
        self.configs = self.root / "ezquake" / "configs"
        shutil.copytree(CONFIGS, self.configs)
        self.marker = self.configs / ".wizard-complete"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        (self.bin / "python3").symlink_to(sys.executable)
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
                        DISPLAY="", PYTHONDONTWRITEBYTECODE="1",
                        PYTHONIOENCODING="utf-8",
                        __GL_SYNC_TO_VBLANK="1", __GL_MaxFramesAllowed="9")
        engine = self.root / "ezQuake-x86_64.AppImage"
        engine.write_text(
            '#!/usr/bin/env python3\nimport json, os, sys\nfrom pathlib import Path\n'
            'Path(__file__).with_name("engine.json").write_text(json.dumps({\n'
            '    "args": sys.argv[1:], "cwd": os.getcwd(),\n'
            '    "env": {name: os.environ.get(name) for name in (\n'
            '        "__GL_SYNC_TO_VBLANK", "__GL_MaxFramesAllowed",\n'
            '        "SDL_MOUSE_RELATIVE_MODE_WARP", "SDL_MOUSE_RELATIVE_SCALING",\n'
            '        "SDL_VIDEO_X11_NET_WM_BYPASS_COMPOSITOR")}\n'
            '}))\n'
            'with Path("engine-runs").open("a") as log: log.write("run\\n")\n',
            encoding="utf-8"
        )
        engine.chmod(0o755)

    def write_launcher(self, status):
        """Instrument the standalone launcher for offline downloads and setup failures."""
        source = (SCRIPTS / "launcher.py").read_text()
        source, entrypoint = source.rsplit('\n\nif __name__ == "__main__":', 1)
        instrumentation = (
            '\n\nimport io\n'
            'root = Path(__file__).resolve().parent\n'
            'urlopen = lambda url, timeout: '
            'io.BytesIO((root / "templates" / url.rsplit("/", 1)[-1]).read_bytes())\n'
            'original_setup = setup_main\n'
            'def setup_main(argv=None):\n'
            '    if "--check" not in (argv or []):\n'
            '        with (root / "wizard-runs").open("a") as log: log.write("run\\n")\n'
            + (f'        return {status}\n' if status else '')
            + '    return original_setup(argv)\n'
        )
        (self.root / "launcher.py").write_text(
            source + instrumentation + '\n\nif __name__ == "__main__":' + entrypoint,
            encoding="utf-8"
        )

    def launch(self, *args, answers="Ranger\nRng\nRed\n2.5\n"):
        """Start outside the installation, passing input to the real launcher."""
        return subprocess.run([sys.executable, str(self.root / "launcher.py"), *args],
                              cwd=self.bin, env=self.env, input=answers, text=True,
                              encoding="utf-8", capture_output=True, timeout=15,
                              check=False)

    def test_engine_receives_nvidia_settings_install_directory_and_arguments(self):
        """Preserve OpenGL settings and forward arguments after -- literally."""
        args = ["-game", "a mod", "--wizard", "--help"]
        result = self.launch("--", *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads((self.root / "engine.json").read_text())
        self.assertEqual(state["args"], [
            "-basedir", str(self.root), "-nohome", "+exec", self.autoexec_path, *args
        ])
        self.assertEqual(state["cwd"], str(self.root))
        self.assertEqual(state["env"], {
            "__GL_SYNC_TO_VBLANK": "0", "__GL_MaxFramesAllowed": "1",
            "SDL_MOUSE_RELATIVE_MODE_WARP": "0", "SDL_MOUSE_RELATIVE_SCALING": "0",
            "SDL_VIDEO_X11_NET_WM_BYPASS_COMPOSITOR": "1",
        })
        player = (self.configs / "player.cfg").read_text()
        for value in ('"Ranger"', '"Rng"', '"Red"', '"Ranger SV"', '"2.5"'):
            self.assertIn(value, player)

    def test_xterm_preserves_script_path_and_stops_engine_on_cancellation(self):
        """Run setup only in xterm, preserving paths, arguments and failure status."""
        xterm = self.bin / "xterm"
        xterm.write_text(
            '#!/usr/bin/env python3\nimport json, os, sys\n'
            'from pathlib import Path\n'
            '(Path(__file__).resolve().parent.parent / "xterm.json").write_text('
            'json.dumps(sys.argv[1:]))\n'
            'os.execv(sys.argv[4], sys.argv[4:])\n', encoding="utf-8"
        )
        xterm.chmod(0o755)
        self.env["DISPLAY"] = ":test"
        result = self.launch("--mod", "a mod", "+connect", "127.0.0.1:27500")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.marker.is_file())
        command = ["-T", "ezQuake Setup Wizard", "-e", sys.executable,
                   str(self.root / "launcher.py"), "--setup-only"]
        self.assertEqual(json.loads((self.root / "xterm.json").read_text()),
                         [*command, "--auto"])
        self.assertEqual((self.root / "engine-runs").read_text().splitlines(), ["run"])
        state = json.loads((self.root / "engine.json").read_text())
        self.assertEqual(state["args"][-4:],
                         ["-game", "a mod", "+connect", "127.0.0.1:27500"])

        result = self.launch("--wizard", answers="Knight\nKnt\nBlue\n4\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads((self.root / "xterm.json").read_text()), command)
        self.assertIn('"Knight"', (self.configs / "player.cfg").read_text())

        (self.root / "engine.json").unlink()
        result = self.launch("--wizard", answers="")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse((self.root / "engine.json").exists())
        self.assertEqual((self.root / "engine-runs").read_text().splitlines(), ["run", "run"])

    def test_setup_only_modes_work_with_an_unmodified_standalone_launcher(self):
        """Keep manual, automatic and check modes available without starting the game."""
        shutil.copyfile(SCRIPTS / "launcher.py", self.root / "launcher.py")
        (self.root / "ezQuake-x86_64.AppImage").unlink()
        before = (self.configs / "player.cfg").read_bytes()
        result = self.launch("--setup-only", "--check", answers="")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse(self.marker.exists())
        self.assertEqual((self.configs / "player.cfg").read_bytes(), before)

        result = self.launch("--setup-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        player = (self.configs / "player.cfg").read_text()
        for value in ('"Ranger"', '"Rng"', '"Red"', '"Ranger SV"', '"2.5"'):
            self.assertIn(value, player)
        self.assertTrue(self.marker.is_file())
        configured = (self.configs / "player.cfg").read_bytes()
        for mode in ("--check", "--auto"):
            with self.subTest(mode=mode):
                result = self.launch("--setup-only", mode, answers="")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertEqual((self.configs / "player.cfg").read_bytes(), configured)
        self.assertFalse((self.root / "engine.json").exists())

    def test_failed_setup_stops_launch(self):
        """Propagate setup failures before starting the engine."""
        self.write_launcher(1)
        self.assertEqual(self.launch().returncode, 1)
        self.assertFalse((self.root / "engine.json").exists())

    def test_help_does_not_start_setup_or_engine(self):
        """Show launcher help without checking or modifying configuration."""
        result = self.launch("--help", answers="")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--wizard", result.stdout)
        self.assertFalse((self.root / "wizard-runs").exists())
        self.assertFalse((self.root / "engine.json").exists())

    def test_first_run_repeat_and_forced_setup(self):
        """Configure once even if ezQuake never creates config.cfg."""
        for args, answers in (((), "Ranger\nRng\nRed\n3\n"), ((), ""),
                              (("--wizard",), "Knight\nKnt\nBlue\n4\n")):
            result = self.launch(*args, answers=answers)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "wizard-runs").read_text().splitlines(), ["run", "run"])
        self.assertIn('"Blue"', (self.configs / "player.cfg").read_text())
        self.assertIn('"Knt"', (self.configs / "player.cfg").read_text())
        self.assertIn('"Knight SV"', (self.configs / "player.cfg").read_text())
        self.assertFalse((self.configs / "config.cfg").exists())

    def test_deleted_configs_are_recovered_before_engine_launch(self):
        """The launcher recovers configs before starting the engine."""
        shutil.rmtree(self.configs)
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.configs / ".wizard-complete").is_file())
        self.assertTrue((self.root / "engine.json").exists())

    def test_failed_recovery_stops_engine(self):
        """A missing download cannot be ignored by the launcher."""
        shutil.rmtree(self.configs)
        (self.root / "templates" / "game.cfg").unlink()
        result = self.launch()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("Could not download game.cfg", result.stderr)
        self.assertFalse((self.root / "engine.json").exists())

    def test_check_error_stops_without_running_interactive_setup(self):
        """A check failure is distinct from the needs-setup exit code."""
        shutil.rmtree(self.configs)
        self.configs.write_text("not a directory")
        result = self.launch()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse((self.root / "wizard-runs").exists())
        self.assertFalse((self.root / "engine.json").exists())

    def test_mod_uses_shared_configs_before_connecting(self):
        """Select a QuakeWorld mod without reading its configs in place of ours."""
        (self.configs / ".wizard-complete").touch()
        mod_configs = self.root / "fortress" / "configs"
        mod_configs.mkdir(parents=True)
        for name in ("autoexec.cfg", "game.cfg", "player.cfg"):
            (mod_configs / name).write_text("echo mod settings\n")

        for flags in (("--mod", "fortress"), ("-game", "fortress"),
                      ("--", "-game", "fortress")):
            with self.subTest(flags=flags):
                result = self.launch(*flags, "+connect", "127.0.0.1:27500", answers="")
                self.assertEqual(result.returncode, 0, result.stderr)
                state = json.loads((self.root / "engine.json").read_text())
                args = state["args"]
                self.assertEqual(args[-4:], ["-game", "fortress", "+connect", "127.0.0.1:27500"])
                self.assertLess(args.index("+exec"), args.index("+connect"))
                autoexec = Path(state["cwd"]) / args[args.index("+exec") + 1]
                self.assertEqual(autoexec, self.configs / "autoexec.cfg")
                includes = [shlex.split(line)[1] for line in autoexec.read_text().splitlines()
                            if line.startswith("exec ")]
                self.assertEqual([Path(state["cwd"]) / path for path in includes],
                                 [self.configs / "game.cfg", self.configs / "player.cfg"])
        for name in ("autoexec.cfg", "game.cfg", "player.cfg"):
            self.assertEqual((mod_configs / name).read_text(), "echo mod settings\n")
        self.assertFalse((self.root / "wizard-runs").exists())

    def test_existing_shared_includes_are_updated_without_resetting_settings(self):
        """Keep custom settings and formatting when upgrading the old exec paths."""
        (self.configs / ".wizard-complete").touch()
        autoexec = self.configs / "autoexec.cfg"
        original = (b'// exec configs/game.cfg\r\n'
                    b'exec "configs/game.cfg" // defaults\r\n'
                    b'EXEC\tconfigs/player.cfg; sensitivity 7\r\n')
        expected = (b'// exec configs/game.cfg\r\n'
                    b'exec "ezquake/configs/game.cfg" // defaults\r\n'
                    b'EXEC\tezquake/configs/player.cfg; sensitivity 7\r\n')
        autoexec.write_bytes(original)
        autoexec.chmod(0o640)
        before = {name: (self.configs / name).read_bytes() for name in ("game.cfg", "player.cfg")}
        result = self.launch(answers="")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(autoexec.read_bytes(), expected)
        self.assertEqual(autoexec.stat().st_mode & 0o777, 0o640)
        modified = autoexec.stat().st_mtime_ns
        self.assertEqual(self.launch(answers="").returncode, 0)
        self.assertEqual(autoexec.stat().st_mtime_ns, modified)
        self.assertEqual({name: (self.configs / name).read_bytes() for name in before}, before)
        self.assertEqual(list(self.configs.glob(".launcher-autoexec-*")), [])
        self.assertFalse((self.root / "id1").exists())

    def test_missing_mod_value_stops_before_setup(self):
        """Do not consume engine options as mod names or run the wizard on failure."""
        for args in (("--mod",), ("--mod", ""), ("--mod", "--wizard"), ("--mod", "+connect")):
            with self.subTest(args=args):
                result = self.launch(*args)
                self.assertEqual(result.returncode, 2)
                self.assertIn("--mod requires a mod folder name", result.stderr)
        self.assertFalse((self.root / "engine.json").exists())
        self.assertFalse((self.root / "wizard-runs").exists())

    def test_separator_forwards_launcher_options_and_shell_characters_literally(self):
        """Preserve engine arguments without invoking a shell or launcher options."""
        (self.configs / ".wizard-complete").touch()
        args = ["--setup-only", "--check", "--auto", "--mod", "a mod",
                "--wizard", "--help", "$(touch injected)", "'quoted'"]
        result = self.launch("--", *args, answers="")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads((self.root / "engine.json").read_text())
        self.assertEqual(state["args"][-len(args):], args)
        self.assertFalse((self.root / "injected").exists())
        self.assertFalse((self.root / "wizard-runs").exists())

    def test_engine_exit_status_is_preserved(self):
        """The launcher exits with the engine's status."""
        (self.configs / ".wizard-complete").touch()
        (self.root / "ezQuake-x86_64.AppImage").write_text(
            '#!/usr/bin/env python3\nimport sys\nsys.exit(7)\n'
        )
        self.assertEqual(self.launch(answers="").returncode, 7)

    def test_missing_engine_reports_ezquake_error(self):
        """Report an unavailable executable after checking an existing configuration."""
        (self.configs / ".wizard-complete").touch()
        (self.root / "ezQuake-x86_64.AppImage").unlink()
        result = self.launch(answers="")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Error launching ezQuake", result.stderr)

    def test_help_describes_ezquake_without_installed_files(self):
        """Help includes the port-specific executable options and config location."""
        shutil.rmtree(self.configs)
        (self.root / "ezQuake-x86_64.AppImage").unlink()
        result = self.launch("--help", answers="")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ezQuake arguments", result.stdout)
        self.assertIn("--mod NAME", result.stdout)
        self.assertIn("ezquake/configs/autoexec.cfg", result.stdout)
        self.assertNotIn("vkQuake", result.stdout)
