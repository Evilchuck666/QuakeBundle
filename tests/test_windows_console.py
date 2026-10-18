"""Windows authentication needs console handles, including in a frozen GUI."""

import importlib.util
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from quake_installer import interactive, windows_console
from quake_installer.events import Cancelled


class ConsoleTransportTests(unittest.TestCase):
    def setUp(self):
        self.terminal = Mock()
        self.terminal.spawn.return_value = True
        self.terminal.isalive.return_value = False
        self.terminal.get_exitstatus.return_value = 7
        self.terminal.read.side_effect = ["Password:", "done", ""]
        self.binding = SimpleNamespace(
            PTY=Mock(return_value=self.terminal),
            Backend=SimpleNamespace(ConPTY=0),
            WinptyError=OSError,
        )
        self.patch = patch.dict(sys.modules, winpty=self.binding)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def run_console(
        self, output=lambda chunk, send: None, checkpoint=lambda: None, timeout=10
    ):
        return windows_console.run_console(
            ["C:/steam dir/steamcmd.exe", "+login", "account", "+quit"],
            "C:/steam dir",
            output,
            checkpoint,
            timeout,
        )

    def test_console_enter_and_exit_code_without_secret_arguments(self):
        received = []

        def output(chunk, send):
            received.append(chunk)
            if chunk == b"Password:":
                send("secret-value")

        self.assertEqual(self.run_console(output), 7)
        self.binding.PTY.assert_called_once_with(240, 40, backend=0)
        self.assertEqual(received, [b"Password:", b"done"])
        self.terminal.write.assert_called_once_with("secret-value\r")
        self.assertNotIn("secret-value", str(self.terminal.spawn.call_args))
        self.terminal.cancel_io.assert_called_once()

    def test_cancellation_terminates_console_and_releases_io(self):
        self.terminal.pid = 12345
        self.terminal.isalive.return_value = True

        def terminate(pid, sig):
            self.terminal.isalive.return_value = False

        with (
            patch.object(windows_console.os, "kill", side_effect=terminate) as kill,
            self.assertRaises(Cancelled),
        ):
            self.run_console(checkpoint=Mock(side_effect=[None, Cancelled()]))
        kill.assert_called_once_with(12345, windows_console.signal.SIGTERM)
        self.terminal.cancel_io.assert_called_once()

    def test_silent_console_times_out_and_is_terminated(self):
        self.terminal.isalive.return_value = True
        self.terminal.read.side_effect = None
        self.terminal.read.return_value = ""

        def terminate(pid, sig):
            self.terminal.isalive.return_value = False

        with (
            patch.object(windows_console.os, "kill", side_effect=terminate),
            self.assertRaises(TimeoutError),
        ):
            self.run_console(timeout=-1)
        self.terminal.cancel_io.assert_called_once()

    def test_rejects_multiline_responses(self):
        for value in ("a\rb", "a\nb", "a\x00b"):
            with self.subTest(value=value):
                self.terminal.read.side_effect = ["Password:"]
                with self.assertRaises(ValueError):
                    self.run_console(
                        output=lambda chunk, send, value=value: send(value)
                    )
        self.terminal.write.assert_not_called()

    def test_unstarted_process_is_not_killed(self):
        self.terminal.spawn.return_value = False
        with (
            patch.object(windows_console.os, "kill") as kill,
            self.assertRaisesRegex(RuntimeError, "Unable to start"),
        ):
            self.run_console()
        kill.assert_not_called()
        self.terminal.cancel_io.assert_called_once()


@unittest.skipUnless(
    os.name == "nt" and importlib.util.find_spec("winpty") is not None,
    "Requires Windows and the bundled GUI console dependency",
)
class NativeConsoleTests(unittest.TestCase):
    def test_real_console_api_password_and_guard_roundtrip(self):
        fixture = Path(__file__).parent / "fixtures" / "windows_steam_console.py"
        run_process = interactive.run_process
        commands, prompts, messages = [], [], []

        def run(command, *args, **kwargs):
            commands.append(command)
            kwargs["timeout"] = 15
            return run_process(
                [sys.executable, "-u", str(fixture), *command[1:]], *args, **kwargs
            )

        def answer(prompt):
            prompts.append(prompt)
            return "test-password" if "password" in prompt else "ABCDE"

        with (
            tempfile.TemporaryDirectory(prefix="steam console ") as cwd,
            patch.object(interactive, "run_process", side_effect=run),
        ):
            interactive.login(
                fixture, "account", cwd, answer, lambda: None, messages.append
            )
        self.assertEqual(prompts, ["Steam password", "Steam Guard code"])
        for secret in ("test-password", "ABCDE"):
            self.assertNotIn(secret, str(commands))
            self.assertNotIn(secret, str(messages))

    def test_native_cancellation_is_responsive(self):
        started = time.monotonic()

        def checkpoint():
            if time.monotonic() - started > 0.5:
                raise Cancelled()

        with tempfile.TemporaryDirectory() as cwd, self.assertRaises(Cancelled):
            interactive.run_process(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                cwd,
                lambda chunk, send: None,
                checkpoint,
                interactive=True,
            )
        self.assertLess(time.monotonic() - started, 6)

    def test_packaged_smoke_test_uses_only_windows_command_processor(self):
        with tempfile.TemporaryDirectory(prefix="console smoke ") as cwd:
            windows_console.smoke_test(cwd)


if __name__ == "__main__":
    unittest.main()
