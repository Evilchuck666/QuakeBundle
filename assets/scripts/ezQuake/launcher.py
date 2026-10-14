#!/usr/bin/env python3
"""Launch ezQuake on Linux, Windows or macOS after checking its setup.
Includes the initial setup wizard. Install beside the engine executable or .app.
"""

from __future__ import annotations

import argparse
import math
import os
import platform
import plistlib
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from http.client import HTTPException
from pathlib import Path
from urllib.request import urlopen
from xml.parsers.expat import ExpatError

CONFIG_FILES = ("autoexec.cfg", "game.cfg", "player.cfg")
CONFIG_BASE_URL = (
    "https://raw.githubusercontent.com/Evilchuck666/QuakeBundle/main/assets/cfg/ezquake/"
)

NEEDS_SETUP = 2


def get_input(prompt, default=None):
    """Prompt for a value, using the default when the response is empty."""
    if sys.stdin is None or sys.stdout is None:
        raise EOFError("No console available")
    if default:
        return input(f"{prompt} [{default}]: ") or default
    return input(f"{prompt}: ")


def prompt_positive_number(prompt, default):
    """Ask again until the input is a finite, positive number."""
    while True:
        value = get_input(prompt, default).strip()

        try:
            number = float(value)

            if number > 0 and math.isfinite(number):
                return str(number)

        except (ValueError, OverflowError):
            pass

        print("Please enter a positive number.")


def prompt_player_name(prompt="Enter your player name", default="Player"):
    """Ask again until a name can be stored safely in the configuration."""
    while True:
        name = get_input(prompt, default).strip()

        if name and not any(char in name for char in '\"\r\n'):
            return name

        print("Please enter a nonempty name without double quotes or line breaks.")


def prompt_user_settings():
    """Collect the player's name, short name, team and mouse sensitivity."""
    name = prompt_player_name()
    fakename = prompt_player_name("Enter a short version of your player name", name)
    team = prompt_player_name("Enter your team name", "")
    sensitivity = prompt_positive_number("Enter your mouse sensitivity", "3")

    return name, fakename, team, sensitivity


def get_player_cfg_path():
    """Locate ezquake/configs/player.cfg beside the installed launcher and engine."""
    return Path(__file__).resolve().parent / "ezquake" / "configs" / "player.cfg"


def get_setup_marker_path(config_path):
    """Keep first-run state separate from ezQuake's generated configuration files."""
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
    if any(char in str(value) for char in '"\r\n'):
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
        temporary.unlink(missing_ok=True)


def mark_setup_complete(config_path):
    """Write the marker used by the launcher to recognize a completed setup."""
    get_setup_marker_path(config_path).write_text("Configured by launcher.py\n", encoding="utf-8")


def parse_setup_args(argv):
    """Parse the mutually exclusive setup modes."""
    parser = argparse.ArgumentParser(
        prog=f"{Path(__file__).name} --setup-only",
        description="Collect initial player settings for ezQuake.",
    )

    modes = parser.add_mutually_exclusive_group()

    modes.add_argument("--check", action="store_true",
                       help="Check only: exit 0 if ready, 2 if setup is needed, 1 on failure.")

    modes.add_argument("--auto", action="store_true",
                       help="Recover missing files and ask for player settings only if needed.")

    return parser.parse_args(argv)


def recover_missing_configs(config_path, directory_exists, missing):
    """Recover missing files and return whether player.cfg was replaced."""
    if not missing:
        return False

    names = CONFIG_FILES if directory_exists and prompt_reset(missing) else missing
    recover_configs(config_path, names)

    return "player.cfg" in names


def configure_player(config_path):
    """Read the config, collect player settings and save the completed setup."""
    lines = load_config_lines(config_path)
    name, fakename, team, sensitivity = prompt_user_settings()
    updates = {
        "name": name,
        "cl_fakename": fakename,
        "team": team,
        "hostname": f"{name} SV",
        "sensitivity": sensitivity,
    }

    save_config_lines(config_path, update_config_lines(lines, updates))
    mark_setup_complete(config_path)


def setup_main(argv=None):
    """Coordinate setup and report success, cancellation or failure to the user."""
    args = parse_setup_args(argv)

    config_path = get_player_cfg_path()

    try:
        directory_exists, missing = inspect_configs(config_path)
        configured = path_exists_as(get_setup_marker_path(config_path), stat.S_ISREG)

        if args.check:
            return NEEDS_SETUP if missing or not configured else 0

        if args.auto and not missing and configured:
            return 0

        print("Welcome to the ezQuake Initial Setup Wizard!")
        print("--------------------------------------------")

        if recover_missing_configs(config_path, directory_exists, missing):
            configured = False

        if args.auto and configured:
            return 0

        configure_player(config_path)

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
    autoexec = root / "ezquake" / "configs" / "autoexec.cfg"
    original = autoexec.read_bytes()
    # ezQuake can load exec paths from the working directory (the install root).
    # Preserve comments, quoting, line endings and custom autoexec commands.
    updated = re.sub(
        rb'(^[ \t]*(?i:exec)[ \t]+)("?)(configs/(?:game|player)\.cfg)\2(?=[ \t;\r\n]|$)',
        rb'\1\2ezquake/\3\2', original, flags=re.MULTILINE,
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


def console_python():
    """Use the console interpreter when launched through Windows pythonw.exe."""
    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe":
        executable = executable.with_name("python.exe")
        if not executable.is_file():
            raise OSError(f"Console Python not found: {executable}")
    return str(executable)


def run_macos_setup(command):
    """Wait for Terminal's setup command, including its actual exit status."""
    with tempfile.TemporaryDirectory(prefix="quake-setup-") as tmp:
        directory = Path(tmp)
        result_path = directory / "status"
        script = directory / "setup.sh"
        # Terminal runs a shell command. Quote every path, including Python and
        # the launcher; never interpolate them into AppleScript source.
        staged_result = directory / "status.tmp"
        script.write_text(
            shlex.join(command) + "\nstatus=$?\n"
            + "printf '%s\\n' \"$status\" > " + shlex.quote(str(staged_result)) + "\n"
            + shlex.join(["/bin/mv", str(staged_result), str(result_path)]) + "\n",
            encoding="utf-8",
        )
        applescript = '''on run argv
    tell application "Terminal"
        activate
        set setupTab to do script (item 1 of argv)
    end tell
    delay 1
    repeat
        try
            return do shell script ("/bin/cat " & quoted form of (item 2 of argv))
        end try
        tell application "Terminal"
            if not (exists setupTab) then error "Setup terminal was closed."
            set stillRunning to busy of setupTab
        end tell
        -- The command may finish between the first read and the busy check.
        if not stillRunning then
            return do shell script ("/bin/cat " & quoted form of (item 2 of argv))
        end if
        delay 0.2
    end repeat
end run'''
        result = subprocess.run(
            ["/usr/bin/osascript", "-e", applescript,
             shlex.join(["/bin/sh", str(script)]), str(result_path)],
            check=False, capture_output=True, text=True,
        )
        if result.returncode:
            raise OSError("Could not complete setup in Terminal: " + result.stderr.strip()
                          + " Run launcher.py --setup-only from a terminal.")
        try:
            status = int(result_path.read_text(encoding="utf-8").strip())
            if not 0 <= status <= 255:
                raise ValueError("invalid exit status")
            return status
        except (OSError, ValueError) as error:
            raise OSError("Setup did not return a valid exit status; game not started.") from error


def run_setup_wizard(automatic=False):
    """Run setup in the current console or a platform-specific terminal."""
    arguments = ["--auto"] if automatic else []
    print("Running Initial Setup Wizard...", flush=True)

    if all(stream is not None and stream.isatty() for stream in (sys.stdin, sys.stdout)):
        return setup_main(arguments)

    command = [console_python(), str(Path(__file__).resolve()), "--setup-only", *arguments]
    system = platform.system()
    if system == "Windows":
        return subprocess.run(command, check=False,
                              creationflags=subprocess.CREATE_NEW_CONSOLE).returncode
    if system == "Darwin":
        return run_macos_setup(command)
    if system == "Linux":
        xterm = shutil.which("xterm")
        if xterm and os.environ.get("DISPLAY"):
            return subprocess.run([xterm, "-T", "ezQuake Setup Wizard", "-e", *command],
                                  check=False).returncode

    # Retain redirected-input support and headless Linux operation.
    if sys.stdin is None or sys.stdout is None:
        raise OSError("Setup requires a console. Run launcher.py --setup-only in a terminal.")
    return setup_main(arguments)


def print_launcher_help():
    """Show launcher options, setup modes and examples."""
    program = Path(__file__).name
    print(f"Usage: {program} [--help] [--wizard] [--mod NAME] [--engine PATH] "
          f"""[ezQuake arguments...]

Options:
  --engine PATH  Select an executable or macOS .app (relative to the current directory).
  --help      Show this help message and exit.
  --wizard    Run the setup wizard before launching ezQuake.
  --mod NAME  Load a QuakeWorld mod folder beside qw (equivalent to -game NAME).

Setup without launching ezQuake:
  {program} --setup-only [--check | --auto]
  --check     Exit 0 if ready, 2 if setup is needed, or 1 on failure.
  --auto      Recover missing files and ask for player settings only if needed.

Examples:
  {program} --mod fortress
  {program} +connect 127.0.0.1:27500

Every mod uses ezquake/configs/autoexec.cfg and its shared game/player settings.

Supports Linux AppImage, Windows .exe and macOS .app installations.
Install this launcher beside the engine and game data.

All other arguments are passed to ezQuake.
Use -- to pass all remaining arguments directly to ezQuake.""")


def parse_launcher_args(argv):
    """Read launcher options in order, preserving all other engine arguments."""
    options = argparse.Namespace(help=False, wizard=False, engine=None, game_args=[])
    arguments = iter(argv)

    for argument in arguments:
        if argument == "--help":
            options.help = True
            break
        elif argument == "--wizard":
            options.wizard = True
        elif argument == "--engine":
            value = next(arguments, "")
            if not value or value.startswith(("-", "+")):
                raise ValueError("--engine requires an executable or .app path.")
            options.engine = value
        elif argument == "--mod":
            mod = next(arguments, "")
            if not mod or mod.startswith(("-", "+")):
                raise ValueError("--mod requires a mod folder name (for example: --mod fortress).")
            options.game_args.extend(["-game", mod])
        elif argument == "--":
            options.game_args.extend(arguments)
            break
        else:
            options.game_args.append(argument)

    return options


def configure_sdl_environment():
    """Apply common SDL settings and Linux-specific graphics overrides."""
    os.environ.update(
        SDL_MOUSE_RELATIVE_MODE_WARP="0",
        SDL_MOUSE_RELATIVE_SCALING="0",
    )
    if platform.system() == "Linux":
        os.environ["SDL_VIDEO_X11_NET_WM_BYPASS_COMPOSITOR"] = "1"
        os.environ.update(__GL_SYNC_TO_VBLANK="0", __GL_MaxFramesAllowed="1")


def ensure_setup(force=False):
    """Run required or forced setup and normalize its exit status."""
    status = setup_main(["--check"])
    if status == NEEDS_SETUP or (status == 0 and force):
        status = run_setup_wizard(automatic=not force)

    return status if status >= 0 else 128 + abs(status)


def resolve_engine(root, override=None):
    """Locate an installed engine, resolving macOS bundles through their metadata."""
    system = platform.system()
    if system not in ("Linux", "Windows", "Darwin"):
        raise OSError(f"Unsupported operating system: {system}")

    if override:
        # Resolve relative overrides before changing to the installation directory.
        engine = Path(override).expanduser().resolve()
    else:
        def matches(path):
            name = path.name.lower()
            if system == "Linux":
                return name == "ezquake-x86_64.appimage" and path.is_file()
            if system == "Windows":
                return (name == "ezquake.exe"
                        or name.startswith("ezquake-gl") and name.endswith(".exe")) and path.is_file()
            return (name == "ezquake.app" or name.startswith("ezquake-")
                    and name.endswith(".app")) and path.is_dir()

        candidates = sorted(path for path in root.iterdir() if matches(path))
        if not candidates:
            raise OSError(f"No ezQuake engine found for {system} in {root}. "
                          "Use --engine PATH to select an installed executable or macOS .app.")
        if len(candidates) > 1:
            raise OSError("Multiple ezQuake engines found: "
                          + ", ".join(path.name for path in candidates)
                          + ". Select one with --engine PATH.")
        engine = candidates[0].resolve()

    if system == "Darwin" and engine.suffix.lower() == ".app":
        try:
            with (engine / "Contents" / "Info.plist").open("rb") as metadata:
                info = plistlib.load(metadata)
            name = info.get("CFBundleExecutable") if isinstance(info, dict) else None
            if (not isinstance(name, str) or not name or name in (".", "..")
                    or any(char in name for char in ("/", "\\", "\x00"))):
                raise ValueError("invalid CFBundleExecutable")
        except (OSError, ValueError, plistlib.InvalidFileException, ExpatError) as error:
            raise OSError(f"Invalid application bundle {engine}: {error}") from error
        engine = engine / "Contents" / "MacOS" / name

    if not engine.is_file():
        raise OSError(f"Engine executable not found: {engine}")
    if system == "Windows" and engine.suffix.lower() != ".exe":
        raise OSError(f"Windows requires an .exe engine: {engine}")
    if system != "Windows" and not os.access(engine, os.X_OK):
        raise OSError(f"Engine is not executable: {engine}")
    return engine


def launch_engine(root, game_args, engine):
    """Start the engine with shared settings and preserve its exit status."""
    prepare_shared_autoexec(root)
    engine = str(engine)
    command = [engine, "-basedir", str(root)]
    command.append("-nohome")
    command.extend(["+exec", "ezquake/configs/autoexec.cfg", *game_args])

    if sys.stdout is not None:
        sys.stdout.flush()
    if platform.system() == "Windows":
        return subprocess.run(command, cwd=root, check=False).returncode

    os.chdir(root)
    os.execv(engine, command)
    return 0


def report_launcher_error(message):
    """Report launch failures even when Windows pythonw has no output streams."""
    if sys.stderr is None and platform.system() == "Windows":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, "ezQuake Launcher", 0x10)
    else:
        print(message, file=sys.stderr)


def main(argv=None):
    """Handle options, validate the engine, run setup and start the game."""
    argv = list(sys.argv[1:] if argv is None else argv)
    # A terminal child must configure the game and return, without launching it.
    if argv[:1] == ["--setup-only"]:
        return setup_main(argv[1:])

    try:
        options = parse_launcher_args(argv)
    except ValueError as error:
        report_launcher_error(f"Error: {error}")
        return 2

    if options.help:
        print_launcher_help()
        return 0

    root = Path(__file__).resolve().parent
    configure_sdl_environment()

    try:
        engine = resolve_engine(root, options.engine)
        status = ensure_setup(force=options.wizard)
        if status != 0:
            return status

        return launch_engine(root, options.game_args, engine)
    except OSError as error:
        report_launcher_error(f"Error launching ezQuake: {error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
