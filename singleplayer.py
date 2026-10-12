#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["tqdm"]
# ///
"""Download Quake, with its expansions, with steamcmd into the user's data directory.

It also installs the latest release of the vkQuake engine for the current
system, inside the game data.

Usage:
    python3 singleplayer.py [--attempts N] STEAM_USERNAME

It needs tqdm (pip install tqdm); "uv run singleplayer.py" installs it on the fly.

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
import os
import sys
from pathlib import Path

# A single script never wrote __pycache__; keep it that way for the package.
sys.dont_write_bytecode = True
# The package sits next to this file. "python3 singleplayer.py" already puts
# this directory on sys.path, but runpy, exec() or importing from elsewhere
# don't, and with a single file they used to work.
sys.path.insert(0, str(Path(__file__).resolve().parent))

# main() uses some of these. The rest used to be defined in this file, and are
# imported so that code using singleplayer.<name> (e.g.
# singleplayer.install_vkquake()) keeps working.
# The sys.path tweak above is required so the package can be imported when this
# file is executed directly; keep the compatibility imports in-place even though
# pylint marks them as misplaced/unused.
from quakebundle.config import (  # pylint: disable=wrong-import-position,unused-import
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
    user_data_dir,
)
from quakebundle.console import (  # pylint: disable=wrong-import-position,unused-import
    byte_bar,
    mb,
    progress_bar,
    step,
)
from quakebundle.gamedata import (  # pylint: disable=wrong-import-position,unused-import
    clean_up,
    download_quake,
    install_game_data,
)
from quakebundle.net import (  # pylint: disable=wrong-import-position,unused-import
    curl_get,
    fetch,
    fetch_json,
    open_url,
)
from quakebundle.steamcmd import (  # pylint: disable=wrong-import-position,unused-import
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
from quakebundle.vkquake import (  # pylint: disable=wrong-import-position
    install_vkquake,
    install_vkquake_mac,
    vkquake_release,
)

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
    "install_game_data",
    "install_steamcmd",
    "install_vkquake",
    "install_vkquake_mac",
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
