"""Configuration: install paths, the Steam app, download URLs and tunables."""

from __future__ import annotations

import argparse
import os
import platform
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
INSTALL_MULTIPLAYER_DIR = BASE_DIR / "multiplayer"
STEAMCMD_DIR = user_data_dir() / "steamcmd"

# steamcmd downloads the whole Steam install here. Single-player keeps the
# re-release game folders; multiplayer keeps the original id1 PAKs.
DOWNLOAD_DIR = BASE_DIR / "download"
RERELEASE_DIR = DOWNLOAD_DIR / "rerelease"


def parse_install_dir(value: str) -> Path:
    """Resolve a CLI destination and keep it outside directories removed by cleanup."""
    try:
        if not value:
            raise ValueError("the installation directory cannot be empty")
        directory = Path(value).expanduser().resolve()
        if directory.exists() and not directory.is_dir():
            raise ValueError(f"not a directory: {directory}")
        for temporary in (DOWNLOAD_DIR, STEAMCMD_DIR):
            if directory.is_relative_to(temporary.resolve()):
                raise ValueError(f"the installation directory cannot be inside {temporary}")
    except (OSError, RuntimeError, ValueError) as error:
        raise argparse.ArgumentTypeError(str(error)) from error
    return directory


VKQUAKE_RELEASE_URL = "https://api.github.com/repos/Novum/vkQuake/releases/latest"
EZQUAKE_RELEASE_URL = "https://api.github.com/repos/QW-Group/ezquake-source/releases/latest"
NQUAKE_INI_URL = "https://raw.githubusercontent.com/nQuake/client-win32/master/etc/nquake.ini"

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

# Output kept to show the user when steamcmd fails.
TAIL_LINES = 25

DOWNLOAD_CHUNK_SIZE = 1024 * 1024

# GitHub's API rejects requests without a User-Agent.
USER_AGENT = "QuakeBundle"
