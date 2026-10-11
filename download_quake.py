#!/usr/bin/env python3
"""Download Quake and its expansions with steamcmd into ~/.local/share/quake/singleplayer.

Usage:
    python3 download_quake.py STEAM_USERNAME

Quake and its expansions are not free, so a Steam account that owns them is
required.
steamcmd will prompt for the password and the Steam Guard code interactively.
"""

import argparse
import io
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

APP_IDS = [
    2310,  # Quake
    9030,  # Quake Mission Pack 1: Scourge of Armagon
    9040,  # Quake Mission Pack 2: Dissolution of Eternity
]
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


def steamcmd_executable() -> Path:
    system = platform.system()
    if system == "Windows":
        return STEAMCMD_DIR / "steamcmd.exe"
    return STEAMCMD_DIR / "steamcmd.sh"


def install_steamcmd() -> Path:
    exe = steamcmd_executable()
    if exe.exists():
        print(f"steamcmd is already installed in {STEAMCMD_DIR}")
        return exe

    system = platform.system()
    url = STEAMCMD_URLS.get(system)
    if url is None:
        sys.exit(f"Unsupported operating system: {system}")

    print(f"Downloading steamcmd for {system} from {url}")
    with urllib.request.urlopen(url) as response:
        data = response.read()

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


def remove_steamcmd_dirs() -> None:
    for name in STEAMCMD_ONLY_DIRS:
        shutil.rmtree(INSTALL_DIR / name, ignore_errors=True)


def remove_steamcmd() -> None:
    shutil.rmtree(STEAMCMD_DIR, ignore_errors=True)
    print(f"Removed {STEAMCMD_DIR}")


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
    for app_id in APP_IDS:
        command += ["+app_update", str(app_id), "validate"]
    command.append("+quit")

    try:
        # steamcmd updates itself on its first run and app_update sometimes
        # fails; retrying is the usual fix.
        for attempt in range(1, attempts + 1):
            print(f"Running steamcmd (attempt {attempt}/{attempts})")
            result = subprocess.run(command, cwd=STEAMCMD_DIR)
            if result.returncode == 0:
                return
            print(f"steamcmd exited with code {result.returncode}")
        sys.exit("Could not download Quake and its expansions with steamcmd")
    finally:
        remove_steamcmd_dirs()


def verify() -> None:
    missing = [f for f in EXPECTED_FILES if not (INSTALL_DIR / f).exists()]
    if missing:
        sys.exit("Files missing after download:\n  " + "\n  ".join(missing))
    print(f"Quake downloaded successfully to {INSTALL_DIR}")


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

    steamcmd = install_steamcmd()
    download_quake(steamcmd, args.username, args.attempts)
    verify()
    # Only after a successful download, so a retry doesn't re-download steamcmd.
    remove_steamcmd()


if __name__ == "__main__":
    main()
