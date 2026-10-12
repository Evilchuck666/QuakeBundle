"""vkQuake: picking the build for this system and installing it."""

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
    INSTALL_SINGLE_PLAYER_DIR,
    VKQUAKE_MAC_RELEASES_URL,
    VKQUAKE_RELEASE_URL,
)
from .net import fetch, fetch_json


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

    suffix = {
        ("Linux", "x86_64"): "-x86_64.AppImage",
        ("Windows", "arm64"): "_windows_arm64.zip",
        ("Windows", "x86_64"): "_windows_x64.zip",
    }.get((system, "x86_64" if machine == "amd64" else machine))
    if suffix is None:
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
            subprocess.run(
                ["hdiutil", "detach", mountpoint],
                check=False,
                stdout=subprocess.DEVNULL
            )

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
            installed = install_vkquake_mac(download)
            instructions = "    Run it by opening vkQuake.app; it uses the folder it is in."
        elif system == "Windows":
            with zipfile.ZipFile(download) as archive:
                for info in archive.infolist():
                    # Debug symbols, not needed to play.
                    if not info.filename.endswith(".pdb"):
                        archive.extract(info, INSTALL_SINGLE_PLAYER_DIR)

            installed = INSTALL_SINGLE_PLAYER_DIR / "vkQuake.exe"
            instructions = "    Run it from its folder; it uses the current directory as the game folder."
        else:
            # Fixed name, so the command to run it doesn't change with each version.
            installed = INSTALL_SINGLE_PLAYER_DIR / "vkQuake.AppImage"
            download.chmod(0o755)
            os.replace(download, installed)
            instructions = f'    Run it with:\n      "{installed}" -basedir "{INSTALL_SINGLE_PLAYER_DIR}"'

        print(f"    ✔ Installed {installed}")
        print(instructions)
