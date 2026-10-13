#!/usr/bin/env python3
"""Launch the Linux vkQuake AppImage after checking its initial setup.

Install this script beside wizard.py and vkQuake.AppImage.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def main(argv=None):
    """Handle launcher options, run setup if needed, then replace this process."""
    arguments = iter(sys.argv[1:] if argv is None else argv)
    run_wizard = False
    game_args = []

    for argument in arguments:
        if argument == "--help":
            print(f"""Usage: {Path(__file__).name} [--help] [--wizard] [vkQuake arguments...]

Options:
  --help    Show this help message and exit.
  --wizard  Run the setup wizard before launching vkQuake.

All other arguments are passed to vkQuake.
Use -- to pass all remaining arguments directly to vkQuake.""")
            return 0
        if argument == "--wizard":
            run_wizard = True
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
            command = list(wizard)
            if not run_wizard:
                command.append("--auto")
            print("Running Initial Setup Wizard...", flush=True)
            if not (sys.stdin.isatty() and sys.stdout.isatty()):
                xterm = shutil.which("xterm")
                if xterm and os.environ.get("DISPLAY"):
                    command = [xterm, "-T", "vkQuake Setup Wizard", "-e", *command]
            status = subprocess.run(command, check=False).returncode
            if status != 0:
                return status if status >= 0 else 128 - status

        # Keep generated engine settings and saves inside this installation.
        os.chdir(root)
        engine = str(root / "vkQuake.AppImage")
        os.execv(engine, [
            engine, "-basedir", str(root), "-userdir", str(root),
            "+exec", "configs/autoexec.cfg", *game_args,
        ])
    except OSError as error:
        print(f"Error launching vkQuake: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
