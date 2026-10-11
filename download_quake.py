#!/usr/bin/env python3
"""Download Quake and its expansions with steamcmd into ~/.local/share/quake/singleplayer.

Usage:
    python3 download_quake.py STEAM_USERNAME

Quake and its expansions are not free, so a Steam account that owns them is
required.
steamcmd will prompt for the password and the Steam Guard code interactively.
"""

import argparse
import collections
import io
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

APPS = {
    2310: "Quake",
    9030: "Scourge of Armagon",  # Quake Mission Pack 1
    9040: "Dissolution of Eternity",  # Quake Mission Pack 2
}
BASE_DIR = Path.home() / ".local" / "share" / "quake"
INSTALL_DIR = BASE_DIR / "singleplayer"
STEAMCMD_DIR = BASE_DIR / "steamcmd"
# steamcmd creates folders inside the install directory that are not part of
# a Steam client install: "steamapps/" (app manifests) and "_CommonRedist/"
# (DirectX/VC++ redistributables). They are removed when it finishes.
STEAMCMD_ONLY_DIRS = ["steamapps", "_CommonRedist"]

STEAMCMD_URLS = {
    "Linux": "https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz",
    "Darwin": "https://steamcdn-a.akamaihd.net/client/installer/steamcmd_osx.tar.gz",
    "Windows": "https://steamcdn-a.akamaihd.net/client/installer/steamcmd.zip",
}

# Files that must exist after a successful download.
EXPECTED_FILES = [
    "quake.exe",
    "Winquake.exe",
    "id1/PAK0.PAK",
    "id1/PAK1.PAK",
    "hipnotic/pak0.pak",  # 9030
    "rogue/pak0.pak",  # 9040
    "rerelease/quake_x64_steam.exe",
    "rerelease/id1/pak0.pak",
]

# Lines of steamcmd output that drive the progress display.
SELF_UPDATE_RE = re.compile(r"^\[\s*(\d+)%\]\s*(.*)")
PROGRESS_RE = re.compile(
    r"Update state \(0x[0-9a-f]+\) ([a-z ]+), progress: ([\d.]+) \((\d+) / (\d+)\)"
)
SUCCESS_RE = re.compile(r"Success! App '(\d+)' (.*)")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
# Output kept to show the user when steamcmd fails.
TAIL_LINES = 25


class Progress:
    """A single console line that is redrawn in place on a terminal."""

    def __init__(self) -> None:
        self.tty = sys.stdout.isatty()

    def update(self, text: str) -> None:
        if self.tty:
            width = shutil.get_terminal_size().columns - 1
            sys.stdout.write("\r" + text[:width].ljust(width))
            sys.stdout.flush()

    def done(self, text: str) -> None:
        if self.tty:
            self.update(text)
            sys.stdout.write("\n")
        else:
            print(text)
        sys.stdout.flush()


def bar(fraction: float, width: int = 24) -> str:
    filled = round(max(0.0, min(1.0, fraction)) * width)
    return "█" * filled + "░" * (width - filled)


def mb(size: int) -> str:
    return f"{size / 1024 / 1024:,.1f} MB"


def step(text: str) -> None:
    print(f"\n==> {text}")


def steamcmd_executable() -> Path:
    system = platform.system()
    if system == "Windows":
        return STEAMCMD_DIR / "steamcmd.exe"
    return STEAMCMD_DIR / "steamcmd.sh"


def fetch(url: str) -> bytes:
    progress = Progress()
    with urllib.request.urlopen(url) as response:
        total = int(response.headers.get("Content-Length", 0))
        data = bytearray()
        while chunk := response.read(64 * 1024):
            data += chunk
            if total:
                progress.update(f"    {bar(len(data) / total)} {mb(len(data))} / {mb(total)}")
    progress.done(f"    Downloaded {mb(len(data))}")
    return bytes(data)


def install_steamcmd() -> Path:
    exe = steamcmd_executable()
    if exe.exists():
        print(f"    steamcmd is already installed in {STEAMCMD_DIR}")
        return exe

    system = platform.system()
    url = STEAMCMD_URLS.get(system)
    if url is None:
        sys.exit(f"Unsupported operating system: {system}")

    print(f"    {url}")
    data = fetch(url)

    STEAMCMD_DIR.mkdir(parents=True, exist_ok=True)
    if url.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            archive.extractall(STEAMCMD_DIR)
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            archive.extractall(STEAMCMD_DIR, filter="tar")

    if not exe.exists():
        sys.exit(f"{exe} not found after extracting steamcmd")
    if system != "Windows":
        exe.chmod(0o755)
    return exe


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

    if os.name == "posix":
        import pty

        # steamcmd buffers its output when writing to a pipe, so progress
        # would only arrive at the end; a pseudo-terminal avoids that.
        master, slave = pty.openpty()
        proc = subprocess.Popen(
            command, cwd=STEAMCMD_DIR, stdin=subprocess.DEVNULL, stdout=slave, stderr=slave
        )
        os.close(slave)
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
        proc = subprocess.Popen(
            command,
            cwd=STEAMCMD_DIR,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        for raw in proc.stdout:
            handle(raw)
    return proc.wait(), list(tail)


def print_tail(tail: list[str]) -> None:
    print("    Last steamcmd output:")
    for line in tail:
        # Progress lines were already shown and would hide the actual error.
        if not PROGRESS_RE.search(line):
            print(f"      {line}")
    sys.stdout.flush()


def update_steamcmd(steamcmd: Path) -> None:
    progress = Progress()

    def on_line(line: str) -> None:
        if match := SELF_UPDATE_RE.match(line):
            percent = int(match[1])
            progress.update(f"    {bar(percent / 100)} {percent:3d}%  {match[2]}")

    # steamcmd exits with a non-zero code after updating itself, so the
    # result is not checked; a broken steamcmd fails in the next steps.
    run_captured([str(steamcmd), "+quit"], on_line)
    progress.done("    steamcmd is up to date")


def login(steamcmd: Path, username: str) -> None:
    # Interactive, so steamcmd can ask for the password and Steam Guard code.
    # The credentials it caches let the download run without prompts.
    result = subprocess.run([str(steamcmd), "+login", username, "+quit"], cwd=STEAMCMD_DIR)
    if result.returncode != 0:
        sys.exit(f"Could not log in to Steam as {username}")


def remove_steamcmd_dirs() -> None:
    for name in STEAMCMD_ONLY_DIRS:
        shutil.rmtree(INSTALL_DIR / name, ignore_errors=True)


def remove_steamcmd() -> None:
    shutil.rmtree(STEAMCMD_DIR, ignore_errors=True)
    print(f"    Removed {STEAMCMD_DIR}")


def download_quake(steamcmd: Path, username: str, attempts: int) -> None:
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    command = [
        str(steamcmd),
        # Quake only has Windows depots; without this, steamcmd on Linux/macOS
        # would download nothing.
        "+@sSteamCmdForcePlatformType", "windows",
        # force_install_dir must come before +login.
        "+force_install_dir", str(INSTALL_DIR),
        "+login", username,
    ]
    # The expansions are installed into the same directory as Quake.
    for app_id in APPS:
        command += ["+app_update", str(app_id), "validate"]
    command.append("+quit")

    apps = list(APPS.items())
    name_width = max(len(name) for name in APPS.values())

    try:
        # steamcmd updates itself on its first run and app_update sometimes
        # fails; retrying is the usual fix.
        for attempt in range(1, attempts + 1):
            if attempt > 1:
                print(f"    Retrying (attempt {attempt}/{attempts})")
            progress = Progress()
            # steamcmd installs the apps in order and only reports progress
            # for the current one, so track which app it is on.
            current = 0
            total_size = 0

            def label() -> str:
                return f"    [{current + 1}/{len(apps)}] {apps[current][1]:<{name_width}}"

            def on_line(line: str) -> None:
                nonlocal current, total_size
                if current >= len(apps):
                    return
                if match := PROGRESS_RE.search(line):
                    state, done, total = match[1], int(match[3]), int(match[4])
                    total_size = total or total_size
                    fraction = done / total if total else 0.0
                    sizes = f"{done / 1024 / 1024:,.1f}/{mb(total)}" if total else ""
                    progress.update(
                        f"{label()}  {bar(fraction, 16)} {fraction * 100:5.1f}%  {state}  {sizes}"
                    )
                elif match := SUCCESS_RE.search(line):
                    status = "up to date" if "up to date" in match[2] else "installed"
                    size = f" ({mb(total_size)})" if total_size else ""
                    progress.done(f"{label()}  ✔ {status}{size}")
                    current += 1
                    total_size = 0

            returncode, tail = run_captured(command, on_line)
            if returncode == 0 and current == len(apps):
                return
            if current < len(apps):
                progress.done(f"{label()}  ✘ failed (steamcmd exit code {returncode})")
            print_tail(tail)
        sys.exit("Could not download Quake and its expansions with steamcmd")
    finally:
        remove_steamcmd_dirs()


def verify() -> None:
    missing = [f for f in EXPECTED_FILES if not (INSTALL_DIR / f).exists()]
    if missing:
        sys.exit("Files missing after download:\n  " + "\n  ".join(missing))
    files = [p for p in INSTALL_DIR.rglob("*") if p.is_file()]
    size = sum(p.stat().st_size for p in files)
    print(f"    ✔ {len(files)} files, {mb(size)} in {INSTALL_DIR}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "username",
        nargs="?",
        default=os.environ.get("STEAM_USERNAME"),
        help="Steam user that owns Quake and its expansions (or STEAM_USERNAME variable)",
    )
    parser.add_argument("--attempts", type=int, default=3, help="steamcmd retries")
    args = parser.parse_args()
    if not args.username:
        parser.error("a Steam username is required (argument or STEAM_USERNAME)")

    step("Installing steamcmd")
    steamcmd = install_steamcmd()
    step("Updating steamcmd")
    update_steamcmd(steamcmd)
    step(f"Logging in to Steam as {args.username}")
    login(steamcmd, args.username)
    step("Downloading Quake and its expansions")
    download_quake(steamcmd, args.username, args.attempts)
    step("Verifying files")
    verify()
    # Only after a successful download, so a retry doesn't re-download steamcmd.
    step("Cleaning up")
    remove_steamcmd()
    print("\nDone.")


if __name__ == "__main__":
    main()
