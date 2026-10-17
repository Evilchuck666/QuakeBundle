"""Cancellable SteamCMD sessions, without a terminal window or password arguments."""

from __future__ import annotations

import codecs
import os
import queue
import re
import signal
import subprocess
import threading
import time

from .runtime import external_environment, external_libraries


def run_process(command, cwd, output, checkpoint, interactive=False, timeout=1800):
    """Deliver chunks (including prompts without newlines) and reap the child.

    A PTY on POSIX prevents SteamCMD buffering its prompts. Windows uses pipes
    and CREATE_NO_WINDOW. The reader never calls UI code. Cancellation kills
    the process group on POSIX so steamcmd.sh cannot leave a child behind.
    """
    master = slave = None
    proc = reader = None
    chunks = queue.Queue()
    try:
        if os.name == "posix":
            import pty
            import termios

            master, slave = pty.openpty()
            attrs = termios.tcgetattr(slave)
            attrs[3] &= ~(termios.ECHO | termios.ECHONL)
            termios.tcsetattr(slave, termios.TCSANOW, attrs)

        with external_libraries():
            proc = subprocess.Popen(
                command,
                cwd=cwd,
                env=external_environment(),
                stdin=(slave if master is not None else subprocess.PIPE)
                if interactive
                else subprocess.DEVNULL,
                stdout=slave if master is not None else subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=os.name == "posix",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        if slave is not None:
            os.close(slave)
            slave = None
        descriptor = master if master is not None else proc.stdout.fileno()

        def read():
            try:
                while chunk := os.read(descriptor, 4096):
                    chunks.put(chunk)
            except OSError:
                pass  # PTYs signal EOF with EIO.
            finally:
                chunks.put(None)

        def send(value):
            if any(char in value for char in "\r\n\x00"):
                raise ValueError("La respuesta no puede contener saltos de línea.")
            data = (value + "\n").encode("utf-8")
            if master is not None:
                os.write(master, data)
            else:
                proc.stdin.write(data)
                proc.stdin.flush()

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        last_output = time.monotonic()
        while True:
            checkpoint()
            try:
                chunk = chunks.get(timeout=0.1)
            except queue.Empty:
                if time.monotonic() - last_output > timeout:
                    raise TimeoutError("SteamCMD no responde. Vuelve a intentarlo.")
                continue
            if chunk is None:
                break
            output(chunk, send)
            last_output = time.monotonic()
        while proc.poll() is None:
            checkpoint()
            if time.monotonic() - last_output > timeout:
                raise TimeoutError("SteamCMD no ha terminado.")
            time.sleep(0.1)
        return proc.returncode
    finally:
        if proc is not None:
            if os.name == "posix":
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            elif proc.poll() is None:
                proc.kill()
            proc.wait()
        if reader is not None:
            reader.join(timeout=2)
        for descriptor in (master, slave):
            if descriptor is not None:
                os.close(descriptor)
        if proc is not None:
            for stream in (proc.stdin, proc.stdout):
                if stream is not None:
                    stream.close()


PASSWORD = re.compile(
    r"(?:password\s*:|enter[^\r\n]*password[^\r\n]*[:>])", re.IGNORECASE
)
GUARD = re.compile(
    r"(?:(?:steam\s+guard|two.factor|auth(?:entication)?|authenticator)[^\r\n]*code"
    r"|(?:enter|provide)[^\r\n]*(?:code|token))\s*[:>]\s*$",
    re.IGNORECASE,
)


def login(steamcmd, username, cwd, ask, checkpoint, status):
    """Pass secrets only to stdin. Never forward Steam's authentication output."""
    pending = ""
    decoder = codecs.getincrementaldecoder("utf-8")("replace")

    def output(chunk, send):
        nonlocal pending
        pending = (pending + decoder.decode(chunk))[-8192:]
        # Keep partial prompts across reads, but consume each challenge once.
        if match := PASSWORD.search(pending):
            pending = pending[match.end() :]
            send(ask("Contraseña de Steam"))
        elif match := GUARD.search(pending):
            pending = pending[match.end() :]
            send(ask("Código de Steam Guard"))
        if "confirm" in pending.lower() and "mobile" in pending.lower():
            status("Confirma el inicio de sesión en la aplicación móvil de Steam.")
            pending = ""

    status("Conectando con Steam…")
    code = run_process(
        [str(steamcmd), "+login", username, "+quit"],
        cwd,
        output,
        checkpoint,
        interactive=True,
        timeout=300,
    )
    if code != 0:
        raise RuntimeError(
            "No se pudo iniciar sesión en Steam. Comprueba la cuenta y Steam Guard."
        )
