#!/usr/bin/env python3
"""Collect initial player settings for the vkQuake configuration."""

from __future__ import annotations

import math
import sys
from pathlib import Path


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


def prompt_player_name():
    """Ask again until the player enters a nonempty, valid Quake name."""
    while True:
        name = get_input("Enter your player name", "Player").strip()

        if name and not any(char in name for char in '\"\r\n'):
            return name

        print('Please enter a nonempty name without double quotes or line breaks.')


def prompt_user_settings():
    """Collect the player's name and mouse sensitivity."""
    name = prompt_player_name()
    sensitivity = prompt_positive_number("Enter your mouse sensitivity", "3")

    return name, sensitivity


def get_player_cfg_path():
    """Locate id1/configs/player.cfg beside the installed wizard and engine."""
    return Path(__file__).resolve().parent / "id1" / "configs" / "player.cfg"


def get_setup_marker_path(config_path):
    """Keep first-run state separate from vkQuake's generated configuration files."""
    return Path(config_path).with_name(".wizard-complete")


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

    result.append("\n// Added by wizard.py\n")
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
    get_setup_marker_path(config_path).write_text("Configured by wizard.py\n", encoding="utf-8")


def main():
    """Coordinate setup and report success, cancellation or failure to the user."""
    print("Welcome to the vkQuake Initial Setup Wizard!")
    print("--------------------------------------------")

    config_path = get_player_cfg_path()

    try:
        lines = load_config_lines(config_path)
        name, sensitivity = prompt_user_settings()
        updates = {"name": name, "sensitivity": sensitivity}
        new_lines = update_config_lines(lines, updates)

        save_config_lines(config_path, new_lines)
        mark_setup_complete(config_path)

    except (OSError, ValueError) as error:
        print(f"Error updating {config_path}: {error}", file=sys.stderr)

        return 1
    except (EOFError, KeyboardInterrupt):
        print("\nSetup cancelled; configuration was not saved.", file=sys.stderr)

        return 1

    print(f"Configuration saved successfully: {config_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
