#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["tqdm"]
# ///
"""Download Quake with steamcmd into the user's data directory for multiplayer.

It also installs the latest release of the ezQuake engine for the current
system, alongside its launcher with integrated setup and Quake icon, inside
the game data.

Usage:
    python3 multiplayer.py [--attempts N] STEAM_USERNAME

It needs tqdm (pip install tqdm); "uv run multiplayer.py" installs it on the fly.

The username can also be given in the STEAM_USERNAME environment variable.

Install location (quake/multiplayer inside the user's data directory):
    Linux:   $XDG_DATA_HOME/quake/multiplayer (~/.local/share by default)
    macOS:   ~/Library/Application Support/quake/multiplayer
    Windows: %LOCALAPPDATA%\\quake\\multiplayer

Quake is not free, so a Steam account that owns it is required; the
expansions are included with it.
steamcmd will prompt for the password and the Steam Guard code interactively.

The original game's id1/pak0.pak and id1/pak1.pak are installed alongside
ezQuake in quake/multiplayer, with filenames normalized to lowercase.
nQuake's base files, Clan Arena, Team Fortress and High Resolution Textures
are also downloaded and installed automatically.
"""

from __future__ import annotations

import argparse
import os
import platform
import sys
import tempfile
from pathlib import Path

# Keep direct execution, runpy and exec() working from other directories without
# leaving changes to the caller's import path or bytecode settings.
ORIGINAL_IMPORT_PATH = sys.path[:]
ORIGINAL_BYTECODE_SETTING = sys.dont_write_bytecode
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.dont_write_bytecode = True

    # Re-export the original single-file API for existing callers.
    from quake_installer.config import (
        APP_ID,
        BASE_DIR,
        DOWNLOAD_CHUNK_SIZE,
        DOWNLOAD_DIR,
        EXPECTED_FILES,
        EZQUAKE_RELEASE_URL,
        INSTALL_MULTIPLAYER_DIR,
        STEAMCMD_DIR,
        STEAMCMD_URLS,
        TAIL_LINES,
        USER_AGENT,
        user_data_dir,
    )
    from quake_installer.console import (
        byte_bar,
        mb,
        progress_bar,
        step,
    )
    from quake_installer.ezquake import (
        ezquake_release,
        install_ezquake,
        install_ezquake_mac,
    )
    from quake_installer.gamedata import (
        clean_up,
        download_quake,
        install_multiplayer_game_data,
    )
    from quake_installer.net import (
        curl_get,
        fetch,
        fetch_json,
        open_url,
    )
    from quake_installer.shortcuts import (
        create_linux_shortcut,
        create_macos_shortcut,
        create_windows_shortcut,
    )
    from quake_installer.steamcmd import (
        ANSI_RE,
        PROGRESS_RE,
        SELF_UPDATE_RE,
        SUCCESS_RE,
        check_rosetta,
        install_steamcmd,
        login,
        print_tail,
        run_captured,
        steamcmd_executable,
        update_steamcmd,
    )
finally:
    sys.path[:] = ORIGINAL_IMPORT_PATH
    sys.dont_write_bytecode = ORIGINAL_BYTECODE_SETTING
    del ORIGINAL_IMPORT_PATH, ORIGINAL_BYTECODE_SETTING

__all__ = [
    "ANSI_RE",
    "APP_ID",
    "BASE_DIR",
    "DOWNLOAD_CHUNK_SIZE",
    "DOWNLOAD_DIR",
    "EXPECTED_FILES",
    "EZQUAKE_RELEASE_URL",
    "INSTALL_MULTIPLAYER_DIR",
    "PROGRESS_RE",
    "SELF_UPDATE_RE",
    "STEAMCMD_DIR",
    "STEAMCMD_URLS",
    "SUCCESS_RE",
    "TAIL_LINES",
    "USER_AGENT",
    "byte_bar",
    "check_rosetta",
    "clean_up",
    "curl_get",
    "download_quake",
    "ezquake_release",
    "fetch",
    "fetch_json",
    "install_ezquake",
    "install_ezquake_assets",
    "install_ezquake_mac",
    "install_ezquake_shortcut",
    "install_multiplayer_game_data",
    "install_steamcmd",
    "login",
    "mb",
    "open_url",
    "print_tail",
    "progress_bar",
    "run_captured",
    "steamcmd_executable",
    "step",
    "update_steamcmd",
    "user_data_dir",
]


def install_ezquake_assets() -> None:
    """Download the launcher with integrated setup and icon beside the engine."""
    base_url = "https://raw.githubusercontent.com/Evilchuck666/QuakeBundle/main/assets/"
    system = platform.system()
    launcher = "launcher.exe" if system == "Windows" else "launcher.py"
    icon = {"Windows": "ezquake.ico", "Darwin": "ezquake.png"}.get(system, "ezquake.svg")
    assets = (
        (f"scripts/ezQuake/{launcher}", launcher),
        (f"icons/{icon}", icon),
    )
    INSTALL_MULTIPLAYER_DIR.mkdir(parents=True, exist_ok=True)

    # Finish every download before replacing files from a previous installation.
    with tempfile.TemporaryDirectory(
        prefix=".ezquake-assets-", dir=INSTALL_MULTIPLAYER_DIR
    ) as tmp:
        staging = Path(tmp)
        for relative, filename in assets:
            print(f"    Downloading {filename}")
            downloaded = staging / filename
            fetch(base_url + relative, downloaded)
            if os.name != "nt":
                downloaded.chmod(0o755 if filename == launcher else 0o644)

        for _, filename in assets:
            destination = INSTALL_MULTIPLAYER_DIR / filename
            (staging / filename).replace(destination)
            print(f"    ✔ Installed {destination}")


def install_ezquake_shortcut() -> None:
    """Create the current user's shortcut after all launch assets are installed."""
    system = platform.system()
    root = INSTALL_MULTIPLAYER_DIR.resolve()
    python = Path(sys.executable).absolute()
    if system == "Linux":
        destination = create_linux_shortcut(root, python, fetch)
    elif system == "Windows":
        destination = create_windows_shortcut(root)
    elif system == "Darwin":
        destination = create_macos_shortcut(root, python)
    else:
        raise OSError(f"Unsupported shortcut platform: {system}")
    print(f"    ✔ Created {destination}")


def main() -> None:
    """Install and configure Steam Quake for multiplayer use."""
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
    install_multiplayer_game_data()

    # Only once the game data is installed, so a failed run can be retried
    # without downloading steamcmd and the game again.
    step("Cleaning up")
    clean_up()

    step("Installing ezQuake")
    install_ezquake()

    step("Installing ezQuake launcher and icon")
    install_ezquake_assets()

    step("Creating ezQuake shortcut")
    try:
        install_ezquake_shortcut()
    except OSError as error:
        sys.exit(f"Could not create ezQuake shortcut: {error}")

    print("\nDone.")


if __name__ == "__main__":
    main()
