#!/usr/bin/env python3
"""Collect initial player settings for the ezQuake configuration."""

import os


def get_input(prompt, default=None):
    """Prompt for a value, using the default when the response is empty."""
    if default:
        user_input = input(f"{prompt} [{default}]: ")
        return user_input if user_input else default
    return input(f"{prompt}: ")


def print_banner():
    """Print the setup wizard's welcome message."""
    print("Welcome to the ezQuake Initial Setup Wizard!")
    print("--------------------------------------------")


def prompt_user_settings():
    """Ask for the player's name, team and mouse sensitivity."""
    name = get_input("Enter your player name", "Player")
    team = get_input("Enter your team name", "")
    sensitivity = get_input("Enter your mouse sensitivity", "3")
    return name, team, sensitivity


def build_updates_dict(name, team, sensitivity):
    """Build configuration updates for the player's personal settings."""
    return {
        "name": name,
        "team": team,
        "sensitivity": sensitivity,
    }


def get_player_cfg_path():
    """Return the player configuration path relative to the installed wizard."""
    return os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "ezquake", "configs", "player.cfg"
    )


def load_config_lines(config_path):
    """Read configuration lines as UTF-8 text."""
    with open(config_path, 'r', encoding='utf-8') as f:
        return f.readlines()


def extract_inline_comment(line):
    """Extract and normalize a configuration line's trailing comment."""
    if "//" in line:
        return " //" + line.split("//", 1)[1].strip()
    return ""


def format_config_entry(key, value, comment=""):
    """Format a quoted configuration value with padding and an optional comment."""
    padding = max(1, 38 - len(key))
    return f"{key}{' ' * padding}\"{value}\"{comment}\n"


def is_ignorable_line(line: str) -> bool:
    """Return whether a line is blank or contains only a comment."""
    stripped = line.strip()
    return (not stripped) or stripped.startswith("//")


def get_key_from_line(line: str) -> str | None:
    """Return the first token of a configuration line, if present."""
    parts = line.split(maxsplit=1)
    return parts[0] if parts else None


def rewrite_line_if_update(line: str, updates: dict[str, str], keys_found: set[str]) -> str:
    """Replace a matching setting, preserving its comment and recording its key."""
    key = get_key_from_line(line)
    if not key:
        return line

    if key not in updates:
        return line

    comment = extract_inline_comment(line)
    keys_found.add(key)
    return format_config_entry(key, updates[key], comment)


def update_lines_with_keys(
    lines: list[str], updates: dict[str, str]
) -> tuple[list[str], set[str]]:
    """Update existing player settings, preserving other configuration lines."""
    new_lines: list[str] = []
    keys_found: set[str] = set()

    for line in lines:
        if is_ignorable_line(line):
            new_lines.append(line)
            continue

        key = get_key_from_line(line)
        if not key:
            new_lines.append(line)
            continue

        new_lines.append(rewrite_line_if_update(line, updates, keys_found))

    return new_lines, keys_found


def append_missing_keys(
    new_lines: list[str], updates: dict[str, str], keys_found: set[str]
) -> list[str]:
    """Append any missing player settings."""
    added_new = False
    for key, value in updates.items():
        if key in keys_found:
            continue

        if not added_new:
            new_lines.append("\n// Added by wizard.py\n")
            added_new = True

        new_lines.append(format_config_entry(key, value))

    return new_lines


def save_config_lines(config_path, lines):
    """Write the updated configuration as UTF-8 text."""
    with open(config_path, 'w', encoding='utf-8') as f:
        f.writelines(lines)


def main():
    """Run the interactive setup and save the selected player settings."""
    print_banner()

    name, team, sensitivity = prompt_user_settings()

    updates = build_updates_dict(
        name=name,
        team=team,
        sensitivity=sensitivity,
    )

    config_path = get_player_cfg_path()

    if not os.path.exists(config_path):
        print(f"Error: Config file not found at {config_path}")
        return

    print(f"Updating {config_path}...")

    lines = load_config_lines(config_path)
    new_lines, keys_found = update_lines_with_keys(lines, updates)
    new_lines = append_missing_keys(new_lines, updates, keys_found)
    save_config_lines(config_path, new_lines)

    print("Configuration saved successfully!")


if __name__ == "__main__":
    main()
