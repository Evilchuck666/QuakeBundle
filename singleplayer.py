#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["tqdm"]
# ///
"""Download and install Quake with its expansions using steamcmd.

It also installs the latest release of the vkQuake engine for the current
system, alongside its launcher with integrated setup and Quake icon, inside
the game data.

Usage:
    python3 singleplayer.py [--attempts N] [--install-dir PATH] STEAM_USERNAME

It needs tqdm (pip install tqdm); "uv run singleplayer.py" installs it on the fly.

The username can also be given in the STEAM_USERNAME environment variable.

Use --install-dir PATH to choose the final game directory (no subfolder is added).
Relative paths and ~ are supported. The shortcut uses the chosen directory.
SteamCMD and temporary downloads remain in the user's data directory.

Default install location (quake/singleplayer inside the user's data directory):
    Linux:   $XDG_DATA_HOME/quake/singleplayer (~/.local/share by default)
    macOS:   ~/Library/Application Support/quake/singleplayer
    Windows: %LOCALAPPDATA%\\quake\\singleplayer

Quake is not free, so a Steam account that owns it is required; the
expansions are included with it.
steamcmd will prompt for the password and the Steam Guard code interactively.

Only the data of the re-release is kept: its game folders (id1, hipnotic,
rogue, ...) with their pak0.pak and music go directly into the chosen directory,
next to vkQuake. It is the only data that vkQuake can use everywhere: it needs
lowercase "id1/pak0.pak" on case-sensitive file systems, and the original data
has "id1/PAK0.PAK".
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
        INSTALL_SINGLE_PLAYER_DIR,
        RERELEASE_DIR,
        STEAMCMD_DIR,
        STEAMCMD_URLS,
        TAIL_LINES,
        USER_AGENT,
        VKQUAKE_MAC_RELEASES_URL,
        VKQUAKE_RELEASE_URL,
        parse_install_dir,
        user_data_dir,
    )
    from quake_installer.console import (
        byte_bar,
        mb,
        progress_bar,
        step,
    )
    from quake_installer.gamedata import (
        clean_up,
        download_quake,
        install_single_player_game_data,
    )
    from quake_installer.net import (
        curl_get,
        fetch,
        fetch_json,
        open_url,
    )
    from quake_installer.shortcuts import (
        VKQUAKE_SHORTCUT,
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
    from quake_installer.vkquake import (
        install_vkquake,
        install_vkquake_mac,
        vkquake_release,
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
    "INSTALL_SINGLE_PLAYER_DIR",
    "PROGRESS_RE",
    "RERELEASE_DIR",
    "SELF_UPDATE_RE",
    "STEAMCMD_DIR",
    "STEAMCMD_URLS",
    "SUCCESS_RE",
    "TAIL_LINES",
    "USER_AGENT",
    "VKQUAKE_MAC_RELEASES_URL",
    "VKQUAKE_RELEASE_URL",
    "byte_bar",
    "check_rosetta",
    "clean_up",
    "curl_get",
    "download_quake",
    "fetch",
    "fetch_json",
    "install_single_player_game_data",
    "install_steamcmd",
    "install_vkquake",
    "install_vkquake_assets",
    "install_vkquake_mac",
    "install_vkquake_shortcut",
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
    "vkquake_release",
]


def install_vkquake_assets(install_dir: Path | None = None) -> None:
    """Download the launcher with integrated setup and icon beside the engine."""
    install_dir = INSTALL_SINGLE_PLAYER_DIR if install_dir is None else install_dir
    base_url = "https://raw.githubusercontent.com/Evilchuck666/QuakeBundle/main/assets/"
    system = platform.system()
    launcher = "launcher.exe" if system == "Windows" else "launcher.py"
    icon = {"Windows": "vkQuake.ico", "Darwin": "vkQuake.png"}.get(system, "vkQuake.svg")
    assets = (
        (f"scripts/vkQuake/{launcher}", launcher),
        (f"icons/{icon}", icon),
    )
    install_dir.mkdir(parents=True, exist_ok=True)

    # Finish every download before replacing files from a previous installation.
    with tempfile.TemporaryDirectory(
        prefix=".vkquake-assets-", dir=install_dir
    ) as tmp:
        staging = Path(tmp)
        for relative, filename in assets:
            print(f"    Downloading {filename}")
            downloaded = staging / filename
            fetch(base_url + relative, downloaded)
            if os.name != "nt":
                downloaded.chmod(0o755 if filename == launcher else 0o644)

        for _, filename in assets:
            destination = install_dir / filename
            (staging / filename).replace(destination)
            print(f"    ✔ Installed {destination}")


def install_vkquake_shortcut(install_dir: Path | None = None) -> None:
    """Create the current user's shortcut after all launch assets are installed."""
    system = platform.system()
    root = (INSTALL_SINGLE_PLAYER_DIR if install_dir is None else install_dir).resolve()
    python = Path(sys.executable).absolute()
    if system == "Linux":
        destination = create_linux_shortcut(root, python, fetch, VKQUAKE_SHORTCUT)
    elif system == "Windows":
        destination = create_windows_shortcut(root, VKQUAKE_SHORTCUT)
    elif system == "Darwin":
        destination = create_macos_shortcut(root, python, VKQUAKE_SHORTCUT)
    else:
        raise OSError(f"Unsupported shortcut platform: {system}")
    print(f"    ✔ Created {destination}")


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
    parser.add_argument(
        "--install-dir", type=parse_install_dir, default=str(INSTALL_SINGLE_PLAYER_DIR),
        metavar="PATH", help="game installation directory (default: %(default)s)",
    )

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

    step(f"Installing game data in {args.install_dir}")
    install_single_player_game_data(args.install_dir)

    # Only once the game data is installed, so a failed run can be retried
    # without downloading steamcmd and the game again.
    step("Cleaning up")
    clean_up()

    step("Installing vkQuake")
    install_vkquake(args.install_dir)

    step("Installing vkQuake launcher and icon")
    install_vkquake_assets(args.install_dir)

    step("Creating vkQuake shortcut")
    try:
        install_vkquake_shortcut(args.install_dir)
    except OSError as error:
        sys.exit(f"Could not create vkQuake shortcut: {error}")

    print("\nDone.")


if __name__ == "__main__":
    main()
