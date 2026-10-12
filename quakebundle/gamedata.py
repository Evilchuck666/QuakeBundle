"""Quake game data: downloading it with steamcmd and installing the re-release."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from .config import (
    APP_ID,
    DOWNLOAD_DIR,
    EXPECTED_FILES,
    INSTALL_SINGLE_PLAYER_DIR,
    RERELEASE_DIR,
    STEAMCMD_DIR,
)
from .console import byte_bar, mb
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

                downloaded, total = int(match[3]), int(match[4])
                total_size = total or total_size

                if progress_bar is None:

                    state = match[1]
                    progress_bar = byte_bar(total=total or None, desc=f"    {state}")

                # steamcmd goes through several states (preallocating,
                # downloading, verifying...), each counting from zero.
                elif match[1] != state or (total or None) != progress_bar.total:

                    state = match[1]

                    # Before reset(), which redraws the bar.
                    progress_bar.set_description_str(f"    {state}", refresh=False)
                    progress_bar.reset(total=total or None)

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


def clean_up() -> None:
    """Remove the temporary download and steamcmd directories."""
    for path in (DOWNLOAD_DIR, STEAMCMD_DIR):
        shutil.rmtree(path, ignore_errors=True)
        print(f"    Removed {path}")
