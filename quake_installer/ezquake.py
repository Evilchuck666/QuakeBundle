"""ezQuake: picking the build for this system and installing it."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from .config import (
    EZQUAKE_RELEASE_URL,
    INSTALL_MULTIPLAYER_DIR,
)
from .net import (
    fetch,
    fetch_json,
)


def ezquake_release() -> tuple[str, dict] | None:
    """Return the version and release asset of ezQuake for this system."""
    system, machine = platform.system(), platform.machine().lower()

    suffix = {
        ("Linux", "x86_64"): "linux-x86_64.zip",
        ("Darwin", "arm64"): "macOS-universal.zip",
        ("Darwin", "x86_64"): "macOS-universal.zip",
        ("Windows", "x86_64"): "windows-x64.zip",
    }.get((system, "x86_64" if machine == "amd64" else machine))
    if suffix is None:
        return None

    release = fetch_json(EZQUAKE_RELEASE_URL)
    for asset in release["assets"]:
        if asset["name"].endswith(suffix):
            return release["tag_name"], asset

    sys.exit(f"ezQuake {release['tag_name']} has no *{suffix} build")


def install_ezquake_mac(download: Path) -> Path:
    """Extract the macOS ZIP, preserving bundle permissions and symlinks."""
    with tempfile.TemporaryDirectory() as tmp:
        extracted = Path(tmp)
        # ditto preserves the executable bit and the app bundle's metadata.
        subprocess.run(
            ["ditto", "-x", "-k", download, extracted],
            check=True,
        )
        app = extracted / "ezQuake.app"
        if not app.is_dir():
            raise OSError("ezQuake.app not found in the downloaded ZIP")
        dest = INSTALL_MULTIPLAYER_DIR / app.name
        shutil.rmtree(dest, ignore_errors=True)
        subprocess.run(["ditto", app, dest], check=True)

    return dest


def install_ezquake() -> None:
    """Download and install the matching ezQuake build for this platform."""
    found = ezquake_release()
    if found is None:
        print(f"    No ezQuake build for {platform.system()} {platform.machine()}; skipping")
        return

    version, asset = found

    print(f"    ezQuake {version} ({asset['name']})")

    system = platform.system()
    INSTALL_MULTIPLAYER_DIR.mkdir(parents=True, exist_ok=True)

    # Keep the temporary file on the install filesystem so the AppImage can
    # replace the previous version without an extra copy or a partial write.
    with tempfile.TemporaryDirectory(dir=INSTALL_MULTIPLAYER_DIR) as tmp:
        download = Path(tmp) / "ezQuake.zip"
        fetch(asset["browser_download_url"], download)

        if system == "Darwin":
            installed = install_ezquake_mac(download)
            instructions = "    Run it by opening ezQuake.app; it uses the folder it is in."
        elif system == "Windows":
            with zipfile.ZipFile(download) as archive:
                for info in archive.infolist():
                    # Debug symbols, not needed to play.
                    if not info.filename.endswith(".pdb"):
                        archive.extract(info, INSTALL_MULTIPLAYER_DIR)

            installed = INSTALL_MULTIPLAYER_DIR / "ezQuake.exe"

            instructions = "    Run it from its folder; "
            instructions += "it uses the current directory as the game folder."
        else:
            # Fixed name, so the command to run it doesn't change with each version.
            installed = INSTALL_MULTIPLAYER_DIR / "ezQuake-x86_64.AppImage"

            with zipfile.ZipFile(download) as archive:
                binary = Path(archive.extract(installed.name, tmp))
            binary.chmod(0o755)
            os.replace(binary, installed)

            instructions = f'    Run it with:\n      "{installed}"'
            instructions += f' -basedir "{INSTALL_MULTIPLAYER_DIR}"'

        print(f"    ✔ Installed {installed}")
        print(instructions)
