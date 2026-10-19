"""Hidden Windows console for SteamCMD authentication and live progress."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

from .runtime import external_environment, external_libraries


def run_console(command, cwd, output, checkpoint, timeout):
    # Imported only on Windows. PyInstaller bundles the extension and its DLLs.
    from winpty import PTY, Backend, WinptyError

    checkpoint()
    terminal = PTY(240, 40, backend=Backend.ConPTY)
    started = False
    try:
        environment = external_environment()
        with external_libraries():
            started = terminal.spawn(
                str(command[0]),
                cmdline=" "
                + subprocess.list2cmdline([str(arg) for arg in command[1:]]),
                cwd=str(cwd),
                env="\0".join(f"{key}={value}" for key, value in environment.items())
                + "\0",
            )
        if not started:
            raise RuntimeError("Unable to start SteamCMD in its Windows console.")

        def send(value):
            if any(char in value for char in "\r\n\x00"):
                raise ValueError("The response cannot contain line breaks.")
            # A terminal Enter is CR; a pipe-style LF alone does not submit
            # ReadConsole/getch input consistently.
            terminal.write(value + "\r")

        last_output = time.monotonic()
        while True:
            checkpoint()
            try:
                chunk = terminal.read(blocking=False)
            except WinptyError:
                if terminal.isalive():
                    raise
                break
            if chunk:
                output(chunk.encode("utf-8"), send)
                # Time spent waiting for the user's answer is not a timeout.
                last_output = time.monotonic()
            elif not terminal.isalive():
                # Drain any output already queued before checking the exit code.
                break
            else:
                if time.monotonic() - last_output > timeout:
                    raise TimeoutError("SteamCMD is not responding. Try again.")
                time.sleep(0.05)
        code = terminal.get_exitstatus()
        if code is None:
            raise RuntimeError("Unable to read SteamCMD's exit status.")
        return code
    finally:
        try:
            if started and terminal.isalive():
                try:
                    os.kill(terminal.pid, signal.SIGTERM)
                except OSError:
                    if terminal.isalive():
                        raise
                deadline = time.monotonic() + 5
                while terminal.isalive() and time.monotonic() < deadline:
                    time.sleep(0.05)
        finally:
            terminal.cancel_io()
            # Release ConPTY handles even if the caller retains an exception.
            terminal = None


def smoke_test(cwd):
    """Check packaged console input/output with Windows' own command processor."""
    command_processor = Path(os.environ["SystemRoot"]) / "System32" / "cmd.exe"
    script = Path(cwd) / "console-smoke.cmd"
    script.write_text(
        '@echo off\nset /p "answer=Console input:"\n'
        'if "%answer%"=="quakebundle-test" exit /b 0\nexit /b 7\n',
        encoding="ascii",
    )
    pending = ""
    answered = False

    def output(chunk, send):
        nonlocal pending, answered
        pending += chunk.decode("utf-8")
        if "Console input:" in pending and not answered:
            answered = True
            send("quakebundle-test")

    code = run_console(
        [str(command_processor), "/d", "/q", "/c", str(script)],
        cwd,
        output,
        lambda: None,
        timeout=15,
    )
    if code != 0 or not answered:
        raise RuntimeError("The bundled Windows console input/output test failed.")
