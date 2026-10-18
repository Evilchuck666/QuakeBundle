"""Offline coverage for the GUI's shared backend; no Qt installation required."""

import importlib.util
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from quake_gui.library import (
    EDITIONS,
    configure,
    create_shortcut,
    game_environment,
    launch_command,
    read_player,
)
from quake_installer import console, interactive, steamcmd
from quake_installer.events import Cancelled, Observer, observe


class DesktopConfigTests(unittest.TestCase):
    def test_shortcuts_use_engine_and_native_icon_without_python(self):
        for edition in EDITIONS.values():
            for system, extension, helper in (
                ("Linux", "svg", "create_linux_shortcut"),
                ("Windows", "ico", "create_windows_shortcut"),
                ("Darwin", "png", "create_macos_shortcut"),
            ):
                with (
                    self.subTest(edition=edition.key, system=system),
                    tempfile.TemporaryDirectory() as temporary,
                    patch("quake_gui.library.platform.system", return_value=system),
                    patch("quake_gui.library.shortcuts." + helper) as create,
                    patch.object(edition.launcher, "resolve_engine") as resolve,
                    patch.object(sys, "frozen", True, create=True),
                ):
                    root = Path(temporary)
                    resolve.return_value = root / "engine"
                    create_shortcut(edition, root)
                    self.assertTrue((root / f"{edition.engine}.{extension}").is_file())
                    command = create.call_args.kwargs["command"]
                    self.assertEqual(command, launch_command(edition, root))
                    self.assertEqual(command[0], str(root / "engine"))

    def test_bundled_configs_recover_missing_files_and_preserve_custom_settings(self):
        for edition in EDITIONS.values():
            with (
                self.subTest(edition=edition.key),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                configure(edition, root, "Ranger", 2.5, team="Red", fakename="Alias")
                player = root / edition.config_dir / "player.cfg"
                with player.open("a") as stream:
                    stream.write('\nbind "x" "+jump"\n')
                custom = root / edition.config_dir / "game.cfg"
                custom.write_text("// custom settings\n", encoding="utf-8")
                (root / edition.config_dir / "autoexec.cfg").unlink()
                configure(edition, root, "New Ranger", 4, team="Blue", fakename="Other")
                self.assertIn('bind "x" "+jump"', player.read_text())
                self.assertEqual(custom.read_text(), "// custom settings\n")
                values = read_player(edition, root)
                self.assertEqual(values["name"], "New Ranger")
                self.assertEqual(values["sensitivity"], "4")
                self.assertTrue(
                    (root / edition.config_dir / ".wizard-complete").is_file()
                )
                self.assertTrue((root / edition.config_dir / "autoexec.cfg").is_file())
                if edition.key == "multiplayer":
                    self.assertEqual(values["team"], "Blue")
                    self.assertEqual(values["cl_fakename"], "Other")

    def test_invalid_player_settings_never_write_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, sensitivity in (
                ("", 3),
                ('bad"name', 3),
                ("bad\nname", 3),
                ("Ranger", float("nan")),
                ("Ranger", 0),
            ):
                with (
                    self.subTest(name=name, sensitivity=sensitivity),
                    self.assertRaises(ValueError),
                ):
                    configure(EDITIONS["singleplayer"], root, name, sensitivity)
            self.assertEqual(list(root.iterdir()), [])

    def test_commands_use_engines_directly_and_preserve_platform_config_paths(self):
        with tempfile.TemporaryDirectory(prefix="quake space ") as temporary:
            root = Path(temporary)
            (root / "hipnotic").mkdir()
            for edition in EDITIONS.values():
                for system in ("Linux", "Darwin", "Windows"):
                    engine = root / (edition.engine + ".exe")
                    with (
                        self.subTest(edition=edition.key, system=system),
                        patch.object(
                            edition.launcher, "resolve_engine", return_value=engine
                        ),
                        patch("quake_gui.library.platform.system", return_value=system),
                    ):
                        command = launch_command(edition, root, "hipnotic")
                        self.assertEqual(command[0], str(engine))
                        self.assertEqual(command[1:3], ["-basedir", str(root)])
                        self.assertEqual(command[-2:], ["-game", "hipnotic"])
                        self.assertEqual(
                            "-userdir" in command,
                            edition.key == "singleplayer" and system != "Windows",
                        )
                        self.assertEqual(
                            "-nohome" in command, edition.key == "multiplayer"
                        )
                        with self.assertRaises(ValueError):
                            launch_command(edition, root, "../outside")

    def test_game_does_not_inherit_bundled_qt_library_paths(self):
        with patch.dict(
            os.environ,
            {
                "LD_LIBRARY_PATH": "/bundle",
                "LD_LIBRARY_PATH_ORIG": "/system",
                "QT_PLUGIN_PATH": "/bundle/qt",
                "KEEP": "yes",
            },
            clear=True,
        ):
            environment = game_environment(EDITIONS["multiplayer"])
        self.assertEqual(environment["LD_LIBRARY_PATH"], "/system")
        self.assertNotIn("QT_PLUGIN_PATH", environment)
        self.assertEqual(environment["KEEP"], "yes")


class DesktopProcessTests(unittest.TestCase):
    @unittest.skipIf(
        os.name == "nt" and importlib.util.find_spec("winpty") is None,
        "Install scripts/requirements-gui.txt to test Windows GUI authentication",
    )
    def test_fragmented_password_and_guard_prompts_use_stdin_without_logging_secrets(
        self,
    ):
        with tempfile.TemporaryDirectory(prefix="steam login ") as temporary:
            root = Path(temporary)
            script = root / "steam.py"
            script.write_text(
                "import sys, time\n"
                "assert sys.argv[1:] == ['+login', 'account', '+quit']\n"
                "sys.stdout.write('Pass'); sys.stdout.flush(); time.sleep(.05)\n"
                "sys.stdout.write('word:'); sys.stdout.flush()\n"
                "assert sys.stdin.readline().strip() == 'very-secret'\n"
                "sys.stdout.write('Steam Guard code:'); sys.stdout.flush()\n"
                "assert sys.stdin.readline().strip() == '12345'\n"
                "print('Logged in OK')\n",
                encoding="utf-8",
            )
            prompts, messages = [], []
            run_process = interactive.run_process

            def run(command, *args, **kwargs):
                return run_process(
                    [sys.executable, "-u", str(script), *command[1:]], *args, **kwargs
                )

            def answer(prompt):
                prompts.append(prompt)
                return "very-secret" if "password" in prompt else "12345"

            with patch.object(interactive, "run_process", side_effect=run):
                interactive.login(
                    script, "account", root, answer, lambda: None, messages.append
                )
            self.assertEqual(prompts, ["Steam password", "Steam Guard code"])
            self.assertNotIn("very-secret", " ".join(messages))
            self.assertNotIn("12345", " ".join(messages))

    def test_guard_explanations_do_not_request_a_code_before_the_prompt(self):
        prompts = []

        def run(command, cwd, output, checkpoint, **kwargs):
            output(
                b"This account uses Steam Guard Mobile Authenticator codes.\n",
                lambda value: None,
            )
            self.assertEqual(prompts, [])
            output(b"Two-factor code:", lambda value: None)
            return 0

        with patch.object(interactive, "run_process", side_effect=run):
            interactive.login(
                Path("steamcmd"),
                "account",
                Path("."),
                lambda prompt: prompts.append(prompt) or "CODE",
                lambda: None,
                lambda _: None,
            )
        self.assertEqual(prompts, ["Steam Guard code"])

    def test_conpty_formatting_and_fragmented_escape_sequences_in_prompts(self):
        prompts, responses, messages = [], [], []

        def run(command, cwd, output, checkpoint, **kwargs):
            for chunk in (
                b"\x1b]0;SteamCMD\x07\x1b[?25lPass\x1b[",
                b"32mword:\x1b[0m",
                b"\r\nSteam Guard \x1b[33",
                b"mcode:\x1b[0m\x1b[?25h",
            ):
                output(chunk, responses.append)
            return 0

        with patch.object(interactive, "run_process", side_effect=run):
            interactive.login(
                Path("steamcmd"),
                "account",
                Path("."),
                lambda prompt: prompts.append(prompt) or "secret-response",
                lambda: None,
                messages.append,
            )
        self.assertEqual(prompts, ["Steam password", "Steam Guard code"])
        self.assertEqual(responses, ["secret-response", "secret-response"])
        self.assertNotIn("secret-response", " ".join(messages))

    def test_cancellation_reaps_a_silent_child(self):
        started = time.monotonic()

        def checkpoint():
            if time.monotonic() - started > 0.2:
                raise Cancelled()

        with tempfile.TemporaryDirectory() as temporary, self.assertRaises(Cancelled):
            interactive.run_process(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                temporary,
                lambda chunk, send: None,
                checkpoint,
            )
        self.assertLess(time.monotonic() - started, 5)

    def test_gui_capture_drives_the_existing_steam_progress_parser(self):
        lines, progress = [], []
        observer = Observer(lambda *values: progress.append(values), lambda: None)
        with (
            tempfile.TemporaryDirectory() as temporary,
            observe(observer),
            patch.object(steamcmd, "STEAMCMD_DIR", Path(temporary)),
        ):
            result = steamcmd.run_captured(
                [
                    sys.executable,
                    "-c",
                    "print('\\x1b[32mfirst\\x1b[0m'); print('last', end='')",
                ],
                lines.append,
            )
            with console.byte_bar(total=10, desc="Download") as bar:
                bar.update(4)
                bar.reset(total=20)
                bar.update(8)
        self.assertEqual(result, (0, ["first", "last"]))
        self.assertEqual(lines, ["first", "last"])
        self.assertEqual(progress[-1], (8, 20, "Download"))

    def test_observer_is_thread_local_and_cancelled_downloads_raise(self):
        from quake_installer.events import current

        child_values = []

        def stop():
            raise Cancelled()

        with observe(Observer(lambda *args: None, stop)):
            thread = threading.Thread(target=lambda: child_values.append(current.get()))
            thread.start()
            thread.join()
            with self.assertRaises(Cancelled):
                console.byte_bar(total=10)
        self.assertEqual(child_values, [None])
        self.assertIsNone(current.get())


if __name__ == "__main__":
    unittest.main()
