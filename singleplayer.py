#!/usr/bin/env python3
"""Download Quake, with its expansions, with steamcmd into the user's data directory.

It also installs the latest release of the vkQuake engine for the current
system, inside the game data.

Usage:
    python3 singleplayer.py [--attempts N] STEAM_USERNAME

The username can also be given in the STEAM_USERNAME environment variable.

Install location (quake/singleplayer inside the user's data directory):
    Linux:   $XDG_DATA_HOME/quake/singleplayer (~/.local/share by default)
    macOS:   ~/Library/Application Support/quake/singleplayer
    Windows: %LOCALAPPDATA%\\quake\\singleplayer

Quake is not free, so a Steam account that owns it is required; the
expansions are included with it.
steamcmd will prompt for the password and the Steam Guard code interactively.

Only the data of the re-release is kept: its game folders (id1, hipnotic,
rogue, ...) with their pak0.pak and music go directly into quake/singleplayer,
next to vkQuake. It is the only data that vkQuake can use everywhere: it needs
lowercase "id1/pak0.pak" on case-sensitive file systems, and the original data
has "id1/PAK0.PAK".
"""

from __future__ import annotations

import argparse
import collections
import email.message
import json
import os
import platform
import re
import shutil
import ssl
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import urllib.response
import zipfile
from pathlib import Path


def user_data_dir() -> Path:
    """Return the platform-specific per-user data directory for Quake."""
    system = platform.system()
    if system == "Windows":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


# The Steam app of Quake; it includes the re-release and its expansions.
APP_ID = 2310

BASE_DIR = user_data_dir() / "quake"
INSTALL_SINGLE_PLAYER_DIR = BASE_DIR / "singleplayer"
STEAMCMD_DIR = BASE_DIR / "steamcmd"

# steamcmd downloads the whole Steam install here; only the re-release game
# folders are moved to INSTALL_DIR, and the rest is removed.
DOWNLOAD_DIR = BASE_DIR / "download"
RERELEASE_DIR = DOWNLOAD_DIR / "rerelease"

VKQUAKE_RELEASE_URL = "https://api.github.com/repos/Novum/vkQuake/releases/latest"

# vkQuake publishes no macOS build; Mac Source Ports does (universal binary).
VKQUAKE_MAC_RELEASES_URL = "https://api.github.com/repos/MacSourcePorts/MSPBuildSystem/releases"

STEAMCMD_URLS = {
    "Linux": "https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz",
    "Darwin": "https://steamcdn-a.akamaihd.net/client/installer/steamcmd_osx.tar.gz",
    "Windows": "https://steamcdn-a.akamaihd.net/client/installer/steamcmd.zip",
}

# Files that must exist in the re-release before installing it.
EXPECTED_FILES = [
    "id1/pak0.pak",
    "hipnotic/pak0.pak",
    "rogue/pak0.pak",
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

DOWNLOAD_CHUNK_SIZE = 1024 * 1024
PROGRESS_INTERVAL = 0.1


class Progress:
    """A single console line that is redrawn in place on a terminal."""

    def __init__(self) -> None:
        self.tty = sys.stdout.isatty()
        self._last_update = float("-inf")

    def update(self, text: str, *, force: bool = False) -> None:
        """Redraw at most ten times per second, unless forced."""
        if not self.tty:
            return
        now = time.monotonic()
        if not force and now - self._last_update < PROGRESS_INTERVAL:
            return
        width = shutil.get_terminal_size().columns - 1
        sys.stdout.write("\r" + text[:width].ljust(width))
        sys.stdout.flush()
        self._last_update = now

    def done(self, text: str) -> None:
        """Finish the current progress line and leave the terminal on a new line."""
        if self.tty:
            self.update(text, force=True)
            sys.stdout.write("\n")
        else:
            print(text)
        sys.stdout.flush()


def progress_bar(fraction: float, width: int = 24) -> str:
    """Return a visual progress bar for a value between 0.0 and 1.0."""
    filled = round(max(0.0, min(1.0, fraction)) * width)
    return "█" * filled + "░" * (width - filled)


def mb(size: int) -> str:
    """Return a size string in megabytes."""
    return f"{size / 1024 / 1024:,.1f} MB"


def step(text: str) -> None:
    """Print a section header for a step in the workflow."""
    print(f"\n==> {text}")


def steamcmd_executable() -> Path:
    """Return the platform-specific steamcmd executable path."""
    system = platform.system()
    if system == "Windows":
        return STEAMCMD_DIR / "steamcmd.exe"
    return STEAMCMD_DIR / "steamcmd.sh"


# GitHub's API rejects requests without a User-Agent.
USER_AGENT = "QuakeBundle"


def curl_get(url: str):
    """Download url with curl, returning a response like urlopen's."""
    response = tempfile.TemporaryFile()
    try:
        try:
            result = subprocess.run(
                ["curl", "-fsSL", "-A", USER_AGENT, url],
                stdout=response,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError:
            sys.exit(f"Could not verify the certificate of {url}, and curl is not available")
        if result.returncode != 0:
            sys.exit(f"Could not download {url}: {result.stderr.decode(errors='replace').strip()}")
        headers = email.message.Message()
        headers["Content-Length"] = str(response.tell())
        response.seek(0)
        return urllib.response.addinfourl(response, headers, url, 200)
    except BaseException:
        response.close()
        raise


def open_url(url: str):
    """Open a URL, falling back to curl when the system certificate store is needed."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        return urllib.request.urlopen(request)
    except urllib.error.URLError as error:
        if isinstance(error.reason, ssl.SSLCertVerificationError):
            if platform.system() == "Windows":
                # Python only trusts the root certificates already in the
                # Windows store, while Windows itself downloads missing ones
                # on demand. curl.exe (bundled since Windows 10) uses Windows'
                # own TLS, so it works where Python fails.
                return curl_get(url)
            if platform.system() == "Darwin":
                # Python from python.org ships without CA certificates on macOS.
                sys.exit(
                    f"Could not verify the certificate of {url}.\n"
                    'Run "Install Certificates.command" from your Python folder in Applications.'
                )
        sys.exit(f"Could not download {url}: {error.reason}")


def fetch_json(url: str):
    """Download and deserialize a JSON payload from a URL."""
    with open_url(url) as response:
        return json.load(response)


def fetch(url: str, destination: Path) -> None:
    """Download a URL to disk using a fixed-size buffer."""
    progress = Progress()
    downloaded = 0
    with open_url(url) as response, destination.open("wb") as output:
        total = int(response.headers.get("Content-Length", 0))
        while chunk := response.read(DOWNLOAD_CHUNK_SIZE):
            output.write(chunk)
            downloaded += len(chunk)
            if total and progress.tty:
                progress.update(f"    {progress_bar(downloaded / total)} {mb(downloaded)} / {mb(total)}")
    progress.done(f"    Downloaded {mb(downloaded)}")


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
        ["arch", "-x86_64", "/usr/bin/true"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
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
        # Windows has no pseudo-terminals in the standard library, so progress
        # may arrive in bursts here.
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
    progress = Progress()

    def on_line(line: str) -> None:
        if match := SELF_UPDATE_RE.match(line):
            percent = int(match[1])
            progress.update(f"    {progress_bar(percent / 100)} {percent:3d}%  {match[2]}")

    # steamcmd exits with a non-zero code after updating itself, so the
    # result is not checked; a broken steamcmd fails in the next steps.
    run_captured([str(steamcmd), "+quit"], on_line)
    progress.done("    steamcmd is up to date")


def login(steamcmd: Path, username: str) -> None:
    """Log in to Steam so the download can proceed without interactive prompts."""
    # Interactive, so steamcmd can ask for the password and Steam Guard code.
    # The credentials it caches let the download run without prompts.
    result = subprocess.run([str(steamcmd), "+login", username, "+quit"], cwd=STEAMCMD_DIR)
    if result.returncode != 0:
        sys.exit(f"Could not log in to Steam as {username}")


def clean_up() -> None:
    """Remove the temporary download and steamcmd directories."""
    for path in (DOWNLOAD_DIR, STEAMCMD_DIR):
        shutil.rmtree(path, ignore_errors=True)
        print(f"    Removed {path}")


def download_quake(steamcmd: Path, username: str, attempts: int) -> None:
    """Download the Quake game data via steamcmd, retrying if needed."""
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    command = [
        str(steamcmd),
        # Quake only has Windows depots; without this, steamcmd on Linux/macOS
        # would download nothing.
        "+@sSteamCmdForcePlatformType", "windows",
        # force_install_dir must come before +login.
        "+force_install_dir", str(DOWNLOAD_DIR),
        "+login", username,
        "+app_update", str(APP_ID),
        "+quit",
    ]

    # steamcmd updates itself on its first run and app_update sometimes
    # fails; retrying is the usual fix.
    for attempt in range(1, attempts + 1):
        if attempt > 1:
            print(f"    Retrying (attempt {attempt}/{attempts})")
        progress = Progress()
        finished = False
        total_size = 0

        def on_line(line: str) -> None:
            nonlocal finished, total_size
            if finished:
                return
            if match := PROGRESS_RE.search(line):
                state, downloaded, total = match[1], int(match[3]), int(match[4])
                total_size = total or total_size
                fraction = downloaded / total if total else 0.0
                sizes = f"{downloaded / 1024 / 1024:,.1f}/{mb(total)}" if total else ""
                progress.update(f"    {progress_bar(fraction)} {fraction * 100:5.1f}%  {state}  {sizes}")
            elif match := SUCCESS_RE.search(line):
                status = "up to date" if "up to date" in match[2] else "installed"
                size = f" ({mb(total_size)})" if total_size else ""
                progress.done(f"    ✔ {status}{size}")
                finished = True

        returncode, tail = run_captured(command, on_line)
        if returncode == 0 and finished:
            return
        if not finished:
            progress.done(f"    ✘ failed (steamcmd exit code {returncode})")
        print_tail(tail)
    sys.exit("Could not download Quake with steamcmd")


def install_game_data() -> None:
    """Move the downloaded game data into the install directory."""
    missing = [f for f in EXPECTED_FILES if not (RERELEASE_DIR / f).exists()]
    if missing:
        sys.exit("Files missing after download:\n  " + "\n  ".join(missing))

    def game_files():
        # Include only game folders, following directory links at this first
        # level as glob("*/**/*") does, but never recurse through nested links.
        with os.scandir(RERELEASE_DIR) as entries:
            pending = [Path(entry.path) for entry in entries if entry.is_dir()]
        while pending:
            directory = pending.pop()
            with os.scandir(directory) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(Path(entry.path))
                    else:
                        yield directory, entry

    count = size = 0
    file_links = set()
    # Measure before moving: relative file links may become temporarily broken
    # when their targets move. DirEntry caches the metadata used by is_file().
    for _, entry in game_files():
        if entry.is_file():
            count += 1
            size += entry.stat().st_size
            if entry.is_symlink():
                file_links.add(entry.path)

    previous_directory = None
    # Stream the files, creating each destination directory only once. Moving
    # individual files preserves existing saves and configs in the game folders.
    for directory, entry in game_files():
        if entry.path not in file_links and not entry.is_file(follow_symlinks=False):
            continue
        if directory != previous_directory:
            destination = INSTALL_SINGLE_PLAYER_DIR / directory.relative_to(RERELEASE_DIR)
            destination.mkdir(parents=True, exist_ok=True)
            previous_directory = directory
        os.replace(entry.path, destination / entry.name)
    print(f"    ✔ {count} files, {mb(size)} in {INSTALL_SINGLE_PLAYER_DIR}")


def vkquake_release() -> tuple[str, dict] | None:
    """Return the version and release asset of vkQuake for this system."""
    system, machine = platform.system(), platform.machine().lower()

    if system == "Darwin":
        # Mac Source Ports tags are "vkQuake-<version>" or "vkQuake_<version>"
        # and are not listed in version order, so pick the newest one.
        releases = []
        for page in range(1, 10):
            batch = fetch_json(f"{VKQUAKE_MAC_RELEASES_URL}?per_page=100&page={page}")
            if not batch:
                break
            releases += [r for r in batch if r["tag_name"].lower().startswith("vkquake")]
        for release in sorted(releases, key=lambda r: r["published_at"], reverse=True):
            for asset in release["assets"]:
                if asset["name"].endswith(".dmg"):
                    return release["tag_name"][len("vkquake") + 1 :], asset
        sys.exit("No vkQuake build found on Mac Source Ports")

    if system == "Linux" and machine in ("x86_64", "amd64"):
        suffix = "-x86_64.AppImage"
    elif system == "Windows" and machine == "arm64":
        suffix = "_windows_arm64.zip"
    elif system == "Windows" and machine in ("amd64", "x86_64"):
        suffix = "_windows_x64.zip"
    else:
        return None

    release = fetch_json(VKQUAKE_RELEASE_URL)
    for asset in release["assets"]:
        if asset["name"].endswith(suffix):
            return release["tag_name"], asset
    sys.exit(f"vkQuake {release['tag_name']} has no *{suffix} build")


def install_vkquake_mac(image: Path) -> Path:
    """Install a downloaded vkQuake macOS .dmg into the bundle directory."""
    with tempfile.TemporaryDirectory() as tmp:
        mountpoint = Path(tmp) / "mnt"
        mountpoint.mkdir()
        subprocess.run(
            ["hdiutil", "attach", "-nobrowse", "-readonly", "-mountpoint", mountpoint, image],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        try:
            app = next(mountpoint.glob("*.app"))
            dest = INSTALL_SINGLE_PLAYER_DIR / app.name
            shutil.rmtree(dest, ignore_errors=True)
            # ditto keeps the bundle's symlinks, permissions and signature.
            subprocess.run(["ditto", app, dest], check=True)
        finally:
            subprocess.run(["hdiutil", "detach", mountpoint], stdout=subprocess.DEVNULL)
    return dest


def install_vkquake() -> None:
    """Download and install the matching vkQuake build for this platform."""
    found = vkquake_release()
    if found is None:
        print(f"    No vkQuake build for {platform.system()} {platform.machine()}; skipping")
        return
    version, asset = found

    print(f"    vkQuake {version} ({asset['name']})")
    system = platform.system()
    INSTALL_SINGLE_PLAYER_DIR.mkdir(parents=True, exist_ok=True)
    # Keep the temporary file on the install filesystem so the AppImage can
    # replace the previous version without an extra copy or a partial write.
    with tempfile.TemporaryDirectory(dir=INSTALL_SINGLE_PLAYER_DIR) as tmp:
        download = Path(tmp) / ("vkQuake.dmg" if system == "Darwin" else "vkQuake.download")
        fetch(asset["browser_download_url"], download)

        if system == "Darwin":
            app = install_vkquake_mac(download)
            print(f"    ✔ Installed {app}")
            print("    Run it by opening vkQuake.app; it uses the folder it is in.")
        elif system == "Windows":
            with zipfile.ZipFile(download) as archive:
                for info in archive.infolist():
                    # Debug symbols, not needed to play.
                    if not info.filename.endswith(".pdb"):
                        archive.extract(info, INSTALL_SINGLE_PLAYER_DIR)
            print(f"    ✔ Installed {INSTALL_SINGLE_PLAYER_DIR / 'vkQuake.exe'}")
            print("    Run it from its folder; it uses the current directory as the game folder.")
        else:
            # Fixed name, so the command to run it doesn't change with each version.
            appimage = INSTALL_SINGLE_PLAYER_DIR / "vkQuake"
            download.chmod(0o755)
            os.replace(download, appimage)
            print(f"    ✔ Installed {appimage}")
            print("    Run it with:")
            print(f'      "{appimage}" -basedir "{INSTALL_SINGLE_PLAYER_DIR}"')


def main() -> None:
    """Install and configure Steam Quake for single-player use."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "username",
        nargs="?",
        default=os.environ.get("STEAM_USERNAME"),
        help="Steam user that owns Quake (or STEAM_USERNAME variable)",
    )
    parser.add_argument("--attempts", type=int, default=3, help="steamcmd retries")
    args = parser.parse_args()
    if not args.username:
        parser.error("a Steam username is required (argument or STEAM_USERNAME)")
    # Avoid crashing on the progress bar characters when the output encoding
    # can't represent them (e.g. Windows redirected to a file).
    sys.stdout.reconfigure(errors="replace")
    check_rosetta()

    step("Installing steamcmd")
    steamcmd = install_steamcmd()
    step("Updating steamcmd")
    update_steamcmd(steamcmd)
    step(f"Logging in to Steam as {args.username}")
    login(steamcmd, args.username)
    step("Downloading Quake")
    download_quake(steamcmd, args.username, args.attempts)
    step("Installing game data")
    install_game_data()
    # Only once the game data is installed, so a failed run can be retried
    # without downloading steamcmd and the game again.
    step("Cleaning up")
    clean_up()
    step("Installing vkQuake")
    install_vkquake()
    print("\nDone.")


if __name__ == "__main__":
    main()
