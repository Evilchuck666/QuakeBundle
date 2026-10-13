#!/usr/bin/env python3
"""Launch the Linux vkQuake AppImage after checking its initial setup.

Install this script beside wizard.py and vkQuake.AppImage.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


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


def run_setup_wizard(wizard, automatic=False):
    """Run setup in a terminal when needed and return its exit status."""
    command = list(wizard)

    if automatic:
        command.append("--auto")

    print("Running Initial Setup Wizard...", flush=True)

    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        xterm = shutil.which("xterm")

        if xterm and os.environ.get("DISPLAY"):
            command = [xterm, "-T", "vkQuake Setup Wizard", "-e", *command]

    return subprocess.run(command, check=False).returncode


def main(argv=None):
    """Handle launcher options, run setup if needed, then replace this process."""
    arguments = iter(sys.argv[1:] if argv is None else argv)
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

    wizard = [sys.executable, str(root / "wizard.py")]

    try:
        status = subprocess.run([*wizard, "--check"], check=False).returncode
        if status not in (0, 2):
            return status if status >= 0 else 128 - status

        if status == 2 or run_wizard:
            status = run_setup_wizard(wizard, automatic=not run_wizard)
            if status != 0:
                return status if status >= 0 else 128 - status

        prepare_shared_autoexec(root)

        # Keep generated engine settings and saves inside this installation.
        os.chdir(root)
        engine = str(root / "vkQuake.AppImage")

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
