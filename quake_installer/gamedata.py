"""Quake game data: downloading it with steamcmd and installing it for each engine."""

from __future__ import annotations

import configparser
import errno
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

from .config import (
    APP_ID,
    DOWNLOAD_DIR,
    EXPECTED_FILES,
    INSTALL_MULTIPLAYER_DIR,
    INSTALL_SINGLE_PLAYER_DIR,
    NQUAKE_INI_URL,
    RERELEASE_DIR,
    STEAMCMD_DIR,
)
from .console import byte_bar, mb
from .net import fetch
from .steamcmd import PROGRESS_RE, SUCCESS_RE, print_tail, run_captured


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

        # Created on the first progress line, so no empty bar is shown while
        # steamcmd logs in.
        progress_bar = None
        state = None
        finished = False
        total_size = 0

        def on_line(line: str) -> None:
            nonlocal progress_bar, state, finished, total_size

            if finished:
                return

            if match := PROGRESS_RE.search(line):

                downloaded, total = int(match[3]), int(match[4]) or None
                total_size = total or total_size

                if progress_bar is None:
                    progress_bar = byte_bar(total=total, desc=f"    {match[1]}")

                # steamcmd goes through several states (preallocating,
                # downloading, verifying...), each counting from zero.
                elif match[1] != state or total != progress_bar.total:
                    # Before reset(), which redraws the bar.
                    progress_bar.set_description_str(f"    {match[1]}", refresh=False)
                    progress_bar.reset(total=total)

                state = match[1]
                progress_bar.update(downloaded - progress_bar.n)
            elif match := SUCCESS_RE.search(line):

                if progress_bar is not None:
                    progress_bar.close()

                status = "up to date" if "up to date" in match[2] else "installed"
                size = f" ({mb(total_size)})" if total_size else ""

                print(f"    ✔ {status}{size}")

                finished = True

        returncode, tail = run_captured(command, on_line)

        if progress_bar is not None:
            progress_bar.close()

        if returncode == 0 and finished:
            return

        if not finished:
            print(f"    ✘ failed (steamcmd exit code {returncode})")

        print_tail(tail)

    sys.exit("Could not download Quake with steamcmd")


def _download_nquake_mirror(staging: Path) -> str:
    """Download the package manifest and select its first usable mirror."""
    manifest = staging / "nquake.ini"
    print("    Downloading nQuake package locations")
    fetch(NQUAKE_INI_URL, manifest)
    config = configparser.ConfigParser(interpolation=None)
    try:
        config.read(manifest, encoding="utf-8-sig")
    except (configparser.Error, UnicodeError) as error:
        sys.exit(f"Invalid nQuake package locations: {error}")

    mirrors = config["mirror_addresses"] if config.has_section("mirror_addresses") else {}
    for url in mirrors.values():
        address = urlsplit(url)
        if address.scheme in ("https", "http", "ftp") and address.netloc:
            return url.rstrip("/")
    sys.exit("No download mirrors found in nquake.ini")


def _extract_nquake_package(download: Path, content: Path) -> None:
    """Extract safe package paths, excluding engines and replacement game data."""
    # Steam supplies both PAKs and the registered maps; ezQuake is installed
    # separately. Do not extract the bundled engine or shareware replacements.
    excluded = {
        "id1/pak0.pak", "id1/pak1.pak", "id1/gpl_maps.pk3", "id1/readme.txt",
        "ezquake.exe", "ezquake/sb/wget.exe", "x86.zip", "x64.zip",
    }
    try:
        with zipfile.ZipFile(download) as archive:
            for info in archive.infolist():
                relative = Path(info.filename.replace("\\", "/"))
                if relative.is_absolute() or ".." in relative.parts or ":" in info.filename:
                    sys.exit(f"Unsafe path in {download.name}: {info.filename}")
                if relative.as_posix().lower() in excluded:
                    continue
                destination = content / relative
                if info.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info) as source, destination.open("wb") as output:
                        shutil.copyfileobj(source, output)
    except (OSError, zipfile.BadZipFile, RuntimeError) as error:
        sys.exit(f"Could not extract nQuake {download.name}: {error}")


def _stage_nquake_packages(staging: Path, mirror: str) -> Path:
    """Download and extract every package before touching installed files."""
    packages = (
        "gpl.zip",
        "non-gpl.zip",
        "addon-clanarena.zip",
        "addon-fortress.zip",
        "addon-textures.zip",
    )
    content = staging / "content"
    content.mkdir()
    for name in packages:
        print(f"    Downloading nQuake {name}")
        download = staging / name
        fetch(f"{mirror}/{name}", download)
        _extract_nquake_package(download, content)
        download.unlink()
    return content


def _install_nquake_files(content: Path, install_dir: Path) -> tuple[int, int]:
    """Merge staged files, preserving settings and saves; return count and bytes."""
    count = size = 0
    root = install_dir.resolve()
    for source in content.rglob("*"):
        destination = install_dir / source.relative_to(content)
        if not destination.resolve().is_relative_to(root):
            sys.exit(f"nQuake destination is outside the install directory: {destination}")
        if source.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        # Keep user settings and saves when installing again.
        if source.suffix.lower() in (".cfg", ".sav") and destination.exists():
            continue
        if os.name != "nt" and source.suffix.lower() in (".cfg", ".txt"):
            source.write_bytes(source.read_bytes().replace(b"\r\n", b"\n"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        size += source.stat().st_size
        os.replace(source, destination)
        count += 1
    return count, size


def install_nquake_game_data(install_dir: Path | None = None) -> None:
    """Install nQuake's base files and all three addons without prompting."""
    install_dir = INSTALL_MULTIPLAYER_DIR if install_dir is None else install_dir
    install_dir.mkdir(parents=True, exist_ok=True)
    # Temporary ZIPs and extracted files are removed even if staging fails.
    with tempfile.TemporaryDirectory(
        prefix=".nquake-", dir=install_dir
    ) as tmp:
        staging = Path(tmp)
        mirror = _download_nquake_mirror(staging)
        content = _stage_nquake_packages(staging, mirror)
        count, size = _install_nquake_files(content, install_dir)
        print(f"    ✔ nQuake: {count} files, {mb(size)} in {install_dir}")


def _move_game_file(source, destination: Path) -> None:
    """Replace game files, also supporting destinations on a different filesystem."""
    try:
        os.replace(source, destination)
    except OSError as error:
        if error.errno != errno.EXDEV:
            raise
        shutil.move(str(source), str(destination))


def install_multiplayer_game_data(install_dir: Path | None = None) -> None:
    """Install the original Quake PAKs, nQuake base files and multiplayer addons."""
    install_dir = INSTALL_MULTIPLAYER_DIR if install_dir is None else install_dir
    source = DOWNLOAD_DIR / "id1"
    expected = ("pak0.pak", "pak1.pak")
    # Steam's original data uses uppercase names; accept either case, but
    # install lowercase names so ezQuake also finds them on Linux.
    files = {
        path.name.lower(): path for path in source.iterdir() if path.is_file()
    } if source.is_dir() else {}
    missing = [f"id1/{name}" for name in expected if name not in files]
    if missing:
        sys.exit("Files missing after download:\n  " + "\n  ".join(missing))

    size = sum(files[name].stat().st_size for name in expected)
    # Leave the Steam download intact if fetching the nQuake packages fails.
    install_nquake_game_data(install_dir)
    destination = install_dir / "id1"
    destination.mkdir(parents=True, exist_ok=True)

    # Replace only the game archives, preserving existing saves and configs.
    for name in expected:
        _move_game_file(files[name], destination / name)

    print(f"    ✔ {len(expected)} files, {mb(size)} in {install_dir}")


def install_single_player_game_data(install_dir: Path | None = None) -> None:
    """Move the downloaded game data into the install directory."""
    install_dir = INSTALL_SINGLE_PLAYER_DIR if install_dir is None else install_dir
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
            destination = install_dir / directory.relative_to(RERELEASE_DIR)
            destination.mkdir(parents=True, exist_ok=True)
            previous_directory = directory

        _move_game_file(entry.path, destination / entry.name)

    print(f"    ✔ {count} files, {mb(size)} in {install_dir}")


def clean_up(*, keep_steamcmd: bool = False) -> None:
    """Remove downloaded game data, optionally retaining SteamCMD for reuse."""
    paths = (DOWNLOAD_DIR,) if keep_steamcmd else (DOWNLOAD_DIR, STEAMCMD_DIR)
    for path in paths:
        shutil.rmtree(path, ignore_errors=True)
        print(f"    Removed {path}")
