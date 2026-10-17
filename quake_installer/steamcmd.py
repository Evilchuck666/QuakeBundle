"""steamcmd: installing, updating and logging in with it, and running it quietly."""

from __future__ import annotations

import collections
import os
import platform
import re
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

try:
    import pty
except ImportError:
    pty = None

from .config import STEAMCMD_DIR, STEAMCMD_URLS, TAIL_LINES
from .console import progress_bar
from .events import current
from .net import fetch

# Lines of steamcmd output that drive the progress display.
SELF_UPDATE_RE = re.compile(r"^\[\s*(\d+)%\]\s*(.*)")

PROGRESS_RE = re.compile(
    r"Update state \(0x[0-9a-f]+\) ([a-z ]+), progress: ([\d.]+) \((\d+) / (\d+)\)"
)

SUCCESS_RE = re.compile(r"Success! App '(\d+)' (.*)")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def steamcmd_executable() -> Path:
    """Return the platform-specific steamcmd executable path."""
    filename = "steamcmd.exe" if platform.system() == "Windows" else "steamcmd.sh"
    return STEAMCMD_DIR / filename


def install_steamcmd() -> Path:
    """Install the SteamCMD client if it is not already present."""
    exe = steamcmd_executable()

    if exe.exists():
        print(f"    steamcmd is already installed in {STEAMCMD_DIR}")
        return exe

    system = platform.system()
    url = STEAMCMD_URLS.get(system)

    if url is None:
        sys.exit(f"Unsupported operating system: {system}")

    print(f"    {url}")
    with tempfile.TemporaryDirectory() as tmp:
        download = Path(tmp) / "steamcmd.download"
        fetch(url, download)

        STEAMCMD_DIR.mkdir(parents=True, exist_ok=True)

        if url.endswith(".zip"):
            with zipfile.ZipFile(download) as archive:
                archive.extractall(STEAMCMD_DIR)
        else:
            with tarfile.open(download, mode="r:gz") as archive:
                # Extraction filters only exist since Python 3.12 (and some
                # security releases of 3.8-3.11); macOS ships Python 3.9.6.
                if hasattr(tarfile, "tar_filter"):
                    archive.extractall(STEAMCMD_DIR, filter="tar")
                else:
                    archive.extractall(STEAMCMD_DIR)

    if not exe.exists():
        sys.exit(f"{exe} not found after extracting steamcmd")

    if system != "Windows":
        exe.chmod(0o755)

    return exe


def check_rosetta() -> None:
    """Ensure steamcmd can run on Apple Silicon by requiring Rosetta 2."""
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        return

    result = subprocess.run(
        ["arch", "-x86_64", "/usr/bin/true"],
        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

    if result.returncode != 0:
        sys.exit(
            "steamcmd needs Rosetta 2 on Apple Silicon. Install it with:\n"
            "    softwareupdate --install-rosetta --agree-to-license"
        )


def run_captured(command: list[str], on_line) -> tuple[int, list[str]]:
    """Run steamcmd without showing its output, passing each line to on_line.

    Returns the exit code and the last lines of output.
    """
    tail = collections.deque(maxlen=TAIL_LINES)

    def handle(raw: bytes) -> None:
        line = ANSI_RE.sub("", raw.decode(errors="replace")).strip()

        if line:
            tail.append(line)
            on_line(line)

    if observer := current.get():
        from .interactive import run_process

        pending = b""

        def output(chunk, send):
            nonlocal pending
            *lines, pending = re.split(rb"[\r\n]", pending + chunk)
            for raw in lines:
                handle(raw)

        code = run_process(command, STEAMCMD_DIR, output, observer.checkpoint)
        handle(pending)
        return code, list(tail)

    if os.name == "posix":
        # steamcmd buffers its output when writing to a pipe, so progress
        # would only arrive at the end; a pseudo-terminal avoids that.
        master, output = pty.openpty()
    else:
        master, output = None, subprocess.PIPE

    with subprocess.Popen(
        command,
        cwd=STEAMCMD_DIR,
        stdin=subprocess.DEVNULL,
        stdout=output,
        stderr=subprocess.STDOUT,
    ) as proc:
        if master is not None:
            os.close(output)
            pending = b""

            while True:
                try:
                    chunk = os.read(master, 4096)
                except OSError:  # EIO once steamcmd closes the terminal
                    break

                if not chunk:
                    break

                *lines, pending = re.split(rb"[\r\n]", pending + chunk)
                for raw in lines:
                    handle(raw)

            handle(pending)
            os.close(master)
        else:
            # Windows has no pseudo-terminals in the standard library, so progress
            # may arrive in bursts here.
            with proc.stdout:
                for raw in proc.stdout:
                    handle(raw)
        return proc.wait(), list(tail)


def print_tail(tail: list[str]) -> None:
    """Print the non-progress tail from the most recent steamcmd output."""
    print("    Last steamcmd output:")

    for line in tail:
        # Progress lines were already shown and would hide the actual error.
        if not PROGRESS_RE.search(line):
            print(f"      {line}")

    sys.stdout.flush()


def update_steamcmd(steamcmd: Path) -> None:
    """Ensure the bundled steamcmd installation is current."""
    with progress_bar(total=100, bar_format="    {l_bar}{bar}|") as prog_bar:

        def on_line(line: str) -> None:
            if match := SELF_UPDATE_RE.match(line):
                prog_bar.set_description_str(match[2], refresh=False)
                prog_bar.update(int(match[1]) - prog_bar.n)

        # steamcmd exits with a non-zero code after updating itself, so the
        # result is not checked; a broken steamcmd fails in the next steps.
        run_captured([str(steamcmd), "+quit"], on_line)

    print("    steamcmd is up to date")


def login(steamcmd: Path, username: str) -> None:
    """Log in to Steam so the download can proceed without interactive prompts."""
    # Interactive, so steamcmd can ask for the password and Steam Guard code.
    # The credentials it caches let the download run without prompts.
    result = subprocess.run(
        [str(steamcmd), "+login", username, "+quit"],
        check=False, cwd=STEAMCMD_DIR
    )

    if result.returncode != 0:
        sys.exit(f"Could not log in to Steam as {username}")
