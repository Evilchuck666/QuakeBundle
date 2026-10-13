#!/usr/bin/env python3
"""Launch the Linux vkQuake AppImage after checking its initial setup.
Includes the initial setup wizard. Install beside vkQuake.AppImage.
"""

from __future__ import annotations

import argparse
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from http.client import HTTPException
from pathlib import Path
from urllib.request import urlopen

CONFIG_FILES = ("autoexec.cfg", "game.cfg", "player.cfg")
CONFIG_BASE_URL = (
    "https://raw.githubusercontent.com/Evilchuck666/QuakeBundle/main/assets/cfg/vkQuake/"
)

NEEDS_SETUP = 2


def get_input(prompt, default=None):
    """Prompt for a value, using the default when the response is empty."""
    if default:
        user_input = input(f"{prompt} [{default}]: ")
        return user_input if user_input else default

    return input(f"{prompt}: ")


def prompt_positive_number(prompt, default, integer=False):
    """Ask again until the input is a finite, positive number."""
    while True:
        value = get_input(prompt, default).strip()

        try:
            number = int(value) if integer else float(value)

            if number > 0 and math.isfinite(number):
                return str(number)

        except (ValueError, OverflowError):
            pass

        print("Please enter a positive " + ("integer." if integer else "number."))


def prompt_player_name(prompt="Enter your player name", default="Player", allow_empty=False):
    """Ask again until a name can be stored safely in the configuration."""
    while True:
        name = get_input(prompt, default).strip()

        if (name or allow_empty) and not any(char in name for char in '\"\r\n'):
            return name

        requirement = "a name" if allow_empty else "a nonempty name"
        print(f"Please enter {requirement} without double quotes or line breaks.")


def prompt_user_settings():
    """Collect the player's name and mouse sensitivity."""
    name = prompt_player_name()
    sensitivity = prompt_positive_number("Enter your mouse sensitivity", "3")

    return name, sensitivity


def get_player_cfg_path():
    """Locate id1/configs/player.cfg beside the installed launcher and engine."""
    return Path(__file__).resolve().parent / "id1" / "configs" / "player.cfg"


def get_setup_marker_path(config_path):
    """Keep first-run state separate from vkQuake's generated configuration files."""
    return Path(config_path).with_name(".wizard-complete")


def path_exists_as(path, expected_type):
    """Check the path type, surfacing access errors and unexpected objects."""
    try:
        mode = path.stat().st_mode
    except FileNotFoundError:
        return False

    if not expected_type(mode):
        raise OSError(f"Unexpected path type: {path}")

    return True


def inspect_configs(config_path):
    """Inspect every required file before any prompts or downloads."""
    directory = config_path.parent
    directory_exists = path_exists_as(directory, stat.S_ISDIR)

    missing = [name for name in CONFIG_FILES
               if not path_exists_as(directory / name, stat.S_ISREG)]

    return directory_exists, missing


def prompt_reset(missing):
    """Offer an explicit reset, preserving existing files by default."""
    print(f"Missing configuration files: {', '.join(missing)}")

    while True:
        answer = get_input(
            "Reset all three .cfg files? This overwrites their custom settings. "
            "Choose no to download only missing files (y/n)", "n"
        ).strip().lower()

        if answer in ("y", "yes"):
            return True

        if answer in ("n", "no"):
            return False

        print("Please enter y or n.")


def recover_configs(config_path, names):
    """Stage all downloads before atomically replacing individual files."""
    directory = config_path.parent
    directory.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix=".wizard-download-", dir=directory) as tmp:
        staging = Path(tmp)

        for name in names:
            print(f"Downloading {name}...")

            try:
                with urlopen(CONFIG_BASE_URL + name, timeout=30) as response:
                    content = response.read()

                if not content.decode("utf-8").strip():
                    raise ValueError("Downloaded file is empty")

                (staging / name).write_bytes(content)

            except (OSError, ValueError, HTTPException) as error:
                raise OSError(f"Could not download {name}: {error}") from error

        # Invalidate before replacement so an interrupted reset cannot leave a
        # default player.cfg marked as already configured.
        if "player.cfg" in names:
            get_setup_marker_path(config_path).unlink(missing_ok=True)

        for name in names:
            (staging / name).replace(directory / name)
            print(f"Recovered {directory / name}")


def extract_inline_comment(line):
    """Find comments outside quoted values, including names containing //."""
    quoted = False

    for index, char in enumerate(line):
        if char == '"':
            quoted = not quoted
        elif not quoted and line[index:index + 2] == "//":
            return " " + line[index:].rstrip()

    return ""


def format_config_entry(key, value, comment=""):
    """Format a Quake string; its console parser cannot escape embedded quotes."""
    if any(char in str(value) for char in '\"\r\n'):
        raise ValueError(f"Invalid value for {key}: quotes and line breaks are not supported")

    return f'{key:<38}"{value}"{comment}\n'


def update_existing_entries(lines, updates):
    """Replace matching entries and return the keys found in the configuration."""
    result = []
    found = set()

    for line in lines:
        stripped = line.strip()

        if not stripped or stripped.startswith("//"):
            result.append(line)
            continue

        key = stripped.split(maxsplit=1)[0]

        if key in updates:
            result.append(format_config_entry(key, updates[key], extract_inline_comment(line)))
            found.add(key)
        else:
            result.append(line)

    return result, found


def append_config_entries(lines, entries):
    """Return configuration lines with an appended block of new entries."""
    result = list(lines)

    if not entries:
        return result

    if result and not result[-1].endswith("\n"):
        result[-1] += "\n"

    result.append("\n// Added by launcher.py\n")
    result.extend(format_config_entry(key, value) for key, value in entries.items())

    return result


def update_config_lines(lines, updates):
    """Apply updates to existing entries and append any missing entries."""
    result, found = update_existing_entries(lines, updates)
    missing = {key: value for key, value in updates.items() if key not in found}

    return append_config_entries(result, missing)


def load_config_lines(config_path):
    """Read UTF-8 configuration lines, retaining their line endings."""
    return config_path.read_text(encoding="utf-8").splitlines(keepends=True)


def save_config_lines(config_path, lines):
    """Replace the configuration after writing it fully, cleaning up the temporary file."""
    temporary = config_path.with_suffix(".cfg.tmp")

    try:
        temporary.write_text("".join(lines), encoding="utf-8")
        temporary.replace(config_path)
    finally:
        if temporary.exists():
            temporary.unlink()


def mark_setup_complete(config_path):
    """Write the marker used by the launcher to recognize a completed setup."""
    get_setup_marker_path(config_path).write_text("Configured by launcher.py\n", encoding="utf-8")


def setup_main(argv=None):
    """Coordinate setup and report success, cancellation or failure to the user."""
    parser = argparse.ArgumentParser(
        prog=f"{Path(__file__).name} --setup-only",
        description="Collect initial player settings for vkQuake.",
    )

    modes = parser.add_mutually_exclusive_group()

    modes.add_argument("--check", action="store_true",
                       help="Check only: exit 0 if ready, 2 if setup is needed, 1 on failure.")

    modes.add_argument("--auto", action="store_true",
                       help="Recover missing files and ask for player settings only if needed.")

    args = parser.parse_args(argv)

    config_path = get_player_cfg_path()

    try:
        directory_exists, missing = inspect_configs(config_path)
        configured = path_exists_as(get_setup_marker_path(config_path), stat.S_ISREG)

        if args.check:
            return NEEDS_SETUP if missing or not configured else 0

        if args.auto and not missing and configured:
            return 0

        print("Welcome to the vkQuake Initial Setup Wizard!")
        print("--------------------------------------------")

        if missing:
            names = CONFIG_FILES if directory_exists and prompt_reset(missing) else missing
            recover_configs(config_path, names)

            if "player.cfg" in names:
                configured = False

        if args.auto and configured:
            return 0

        lines = load_config_lines(config_path)

        name, sensitivity = prompt_user_settings()

        updates = {
            "name": name,
            "_cl_name": name,
            "hostname": f"{name} SV",
            "sensitivity": sensitivity
        }

        new_lines = update_config_lines(lines, updates)

        save_config_lines(config_path, new_lines)
        mark_setup_complete(config_path)

    except (OSError, ValueError) as error:
        print(f"Error preparing {config_path.parent}: {error}", file=sys.stderr)

        return 1

    except (EOFError, KeyboardInterrupt):
        print("\nSetup cancelled; player settings were not saved. "
              "Any configuration files already recovered remain in place.", file=sys.stderr)

        return 1

    print(f"Configuration saved successfully: {config_path}")

    return 0


def prepare_shared_autoexec(root):
    """Pin the shared includes in existing installations without resetting settings."""
    autoexec = root / "id1" / "configs" / "autoexec.cfg"
    original = autoexec.read_bytes()
    # exec paths are relative to Quake's search directories, not the script.
    # Preserve comments, quoting, line endings and custom autoexec commands.
    updated = re.sub(
        rb'(^[ \t]*(?i:exec)[ \t]+)("?)(configs/(?:game|player)\.cfg)\2(?=[ \t;\r\n]|$)',
        rb'\1\2../id1/\3\2', original, flags=re.MULTILINE,
    )
    if updated == original:
        return

    with tempfile.NamedTemporaryFile(dir=autoexec.parent, prefix=".launcher-autoexec-",
                                     delete=False) as temporary:
        staged = Path(temporary.name)
        try:
            temporary.write(updated)
            temporary.close()
            shutil.copymode(autoexec, staged)
            staged.replace(autoexec)
        finally:
            staged.unlink(missing_ok=True)


def run_setup_wizard(automatic=False):
    """Run setup in a terminal when needed and return its exit status."""
    arguments = ["--auto"] if automatic else []
    print("Running Initial Setup Wizard...", flush=True)

    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        xterm = shutil.which("xterm")

        if xterm and os.environ.get("DISPLAY"):
            command = [xterm, "-T", "vkQuake Setup Wizard", "-e", sys.executable,
                       str(Path(__file__).resolve()), "--setup-only", *arguments]
            return subprocess.run(command, check=False).returncode

    return setup_main(arguments)


def main(argv=None):
    """Handle launcher options, run setup if needed, then replace this process."""
    argv = list(sys.argv[1:] if argv is None else argv)
    # A terminal child must configure the game and return, without launching it.
    if argv[:1] == ["--setup-only"]:
        return setup_main(argv[1:])

    arguments = iter(argv)
    run_wizard = False
    game_args = []

    for argument in arguments:
        if argument == "--help":
            print(f"Usage: {Path(__file__).name} [--help] [--wizard] [--mod NAME] "
                  f"""[vkQuake arguments...]

Options:
  --help      Show this help message and exit.
  --wizard    Run the setup wizard before launching vkQuake.
  --mod NAME  Load a mod folder beside id1 (equivalent to -game NAME).

Setup without launching vkQuake:
  {Path(__file__).name} --setup-only [--check | --auto]
  --check     Exit 0 if ready, 2 if setup is needed, or 1 on failure.
  --auto      Recover missing files and ask for player settings only if needed.

Examples:
  {Path(__file__).name} --mod mg1
  {Path(__file__).name} -game rogue +map start

Every mod uses id1/configs/autoexec.cfg and its shared game/player settings.

All other arguments are passed to vkQuake.
Use -- to pass all remaining arguments directly to vkQuake.""")
            return 0

        if argument == "--wizard":
            run_wizard = True
        elif argument == "--mod":
            mod = next(arguments, "")
            if not mod or mod.startswith(("-", "+")):
                print("Error: --mod requires a mod folder name (for example: --mod mg1).",
                      file=sys.stderr)
                return 2
            game_args.extend(["-game", mod])
        elif argument == "--":
            game_args.extend(arguments)
            break
        else:
            game_args.append(argument)

    root = Path(__file__).resolve().parent

    # SDL/X11: avoid warp mode and request compositor bypass.
    os.environ.update(
        SDL_MOUSE_RELATIVE_MODE_WARP="0",
        SDL_MOUSE_RELATIVE_SCALING="0",
        SDL_VIDEO_X11_NET_WM_BYPASS_COMPOSITOR="1",
    )

    try:
        status = setup_main(["--check"])
        if status not in (0, NEEDS_SETUP):
            return status if status >= 0 else 128 - status

        if status == NEEDS_SETUP or run_wizard:
            status = run_setup_wizard(automatic=not run_wizard)
            if status != 0:
                return status if status >= 0 else 128 - status

        prepare_shared_autoexec(root)

        # Keep generated engine settings and saves inside this installation.
        os.chdir(root)
        engine = str(root / "vkQuake.AppImage")

        sys.stdout.flush()
        os.execv(
            engine,
            [
                engine, "-basedir", str(root), "-userdir", str(root),
                "+exec", "../id1/configs/autoexec.cfg", *game_args,
            ]
        )

        return 0
    except OSError as error:
        print(f"Error launching vkQuake: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
