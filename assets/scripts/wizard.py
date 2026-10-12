#!/usr/bin/env python3
"""Collect initial player and display settings for the ezQuake configuration."""

import ctypes
import os
import platform
import re
import subprocess

VIDEO_KEYS = {"vid_width", "vid_height", "vid_displayfrequency"}


def get_input(prompt, default=None):
    """Prompt for a value, using the default when the response is empty."""
    if default:
        user_input = input(f"{prompt} [{default}]: ")
        return user_input if user_input else default
    return input(f"{prompt}: ")


def get_resolution_windows():
    """Return the primary Windows display's width, height and refresh rate."""
    try:
        user32 = ctypes.windll.user32
        user32.SetProcessDPIAware()

        class DisplayDevice(ctypes.Structure):
            """Store the ANSI DISPLAY_DEVICE fields used by the Windows API."""

            _fields_ = [
                ("cb", ctypes.c_uint32),
                ("DeviceName", ctypes.c_char * 32),
                ("DeviceString", ctypes.c_char * 128),
                ("StateFlags", ctypes.c_uint32),
                ("DeviceID", ctypes.c_char * 128),
                ("DeviceKey", ctypes.c_char * 128),
            ]

        class DevMode(ctypes.Structure):
            """Store the ANSI DEVMODE fields used to read the current display mode."""

            _fields_ = [
                ("dmDeviceName", ctypes.c_char * 32),
                ("dmSpecVersion", ctypes.c_ushort),
                ("dmDriverVersion", ctypes.c_ushort),
                ("dmSize", ctypes.c_ushort),
                ("dmDriverExtra", ctypes.c_ushort),
                ("dmFields", ctypes.c_uint),
                ("dmOrientation", ctypes.c_short),
                ("dmPaperSize", ctypes.c_short),
                ("dmPaperLength", ctypes.c_short),
                ("dmPaperWidth", ctypes.c_short),
                ("dmScale", ctypes.c_short),
                ("dmCopies", ctypes.c_short),
                ("dmDefaultSource", ctypes.c_short),
                ("dmPrintQuality", ctypes.c_short),
                ("dmColor", ctypes.c_short),
                ("dmDuplex", ctypes.c_short),
                ("dmYResolution", ctypes.c_short),
                ("dmTTOption", ctypes.c_short),
                ("dmCollate", ctypes.c_short),
                ("dmFormName", ctypes.c_char * 32),
                ("dmLogPixels", ctypes.c_ushort),
                ("dmBitsPerPel", ctypes.c_uint),
                ("dmPelsWidth", ctypes.c_uint),
                ("dmPelsHeight", ctypes.c_uint),
                ("dmDisplayFlags", ctypes.c_uint),
                ("dmDisplayFrequency", ctypes.c_uint),
            ]

        primary_device = None
        device = DisplayDevice(cb=ctypes.sizeof(DisplayDevice))

        # DISPLAY_DEVICE_PRIMARY_DEVICE is 0x00000004
        idx = 0
        while user32.EnumDisplayDevicesA(None, idx, ctypes.byref(device), 0):
            if device.StateFlags & 4:
                primary_device = device.DeviceName
                break
            idx += 1

        dm = DevMode(dmSize=ctypes.sizeof(DevMode))

        # If we found a primary device, query it. Otherwise default to current (None)
        if user32.EnumDisplaySettingsA(primary_device, -1, ctypes.byref(dm)):
            return dm.dmPelsWidth, dm.dmPelsHeight, dm.dmDisplayFrequency

    except (OSError, TypeError, ValueError, AttributeError) as e:
        print(f"Windows detection failed: {e}")
    return None


def get_resolution_linux():
    """Read the active mode of the primary or first connected xrandr output."""
    try:
        output = subprocess.check_output(["xrandr"]).decode("utf-8")
        lines = output.split('\n')

        primary_block_start_index = -1
        first_connected_index = -1

        # Find start of relevant block
        for i, line in enumerate(lines):
            if " connected " in line:
                if first_connected_index == -1:
                    first_connected_index = i
                if " primary " in line:
                    primary_block_start_index = i
                    break

        start_index = (
            primary_block_start_index
            if primary_block_start_index != -1
            else first_connected_index
        )

        if start_index == -1:
            return None

        # Parse block
        for i in range(start_index, len(lines)):
            line = lines[i]
            # Stop if we hit the next output definition (non-indented line after the header)
            if i > start_index and not line.startswith(" "):
                break

            if '*' in line:
                # Found active mode
                res_match = re.search(r'^\s+(\d+)x(\d+)', line)
                if not res_match:
                    continue

                w = int(res_match.group(1))
                h = int(res_match.group(2))

                rate_match = re.search(r'(\d+(?:\.\d+)?)\*', line)
                r = int(float(rate_match.group(1))) if rate_match else 60
                return w, h, r
    except (
        OSError, subprocess.CalledProcessError, UnicodeDecodeError, ValueError, TypeError
    ) as e:
        print(f"Linux detection failed: {e}")
    return None


def get_resolution_macos():
    """Read the main macOS display's resolution, defaulting its refresh rate to 60 Hz."""
    try:
        output = subprocess.check_output(
            ["system_profiler", "SPDisplaysDataType"], stderr=subprocess.DEVNULL
        ).decode("utf-8")

        # Split into blocks based on indentation or empty lines is tricky with system_profiler
        # But usually "Main Display: Yes" is unique to one block.
        # We can try to find the block that contains "Main Display: Yes"

        lines = output.split('\n')

        # Simple parser: Group by indentation or "Display:" headers?
        # system_profiler SPDisplaysDataType output format:
        # Graphics/Displays:
        #    GPU Name:
        #       ...
        #       Displays:
        #          Display Name:
        #             Resolution: ...
        #             Main Display: Yes

        # We can look for "Main Display: Yes" and then scroll BACKWARDS to find "Resolution:"
        # OR split by "Resolution:" and check the segment? No.

        # Robust way:
        # 1. Capture all "Resolution: ..." lines and their context.
        # 2. But we need to link it to "Main Display: Yes".

        # Let's iterate lines.
        last_resolution = None
        last_refresh = None

        for line in lines:
            line_str = line.strip()

            # Resolution: 2880 x 1800 Retina
            res_match = re.search(r'Resolution:\s+(\d+)\s+x\s+(\d+)', line_str)
            if res_match:
                last_resolution = (int(res_match.group(1)), int(res_match.group(2)))
                # Default to 60 Hz whenever a new display resolution is found.
                last_refresh = 60

            # UI Looks like: 1440 x 900 @ 60 Hz ??
            # Sometimes system_profiler shows "UI Looks like: ..."

            # Use specific "Main Display: Yes"
            if "Main Display: Yes" in line_str and last_resolution:
                return last_resolution[0], last_resolution[1], last_refresh

            # Refresh Rate check - some output has it?
            # If not, 60 is a safe default for config.

    except (
        OSError, subprocess.CalledProcessError, UnicodeDecodeError, ValueError, TypeError
    ) as e:
        print(f"MacOS detection failed: {e}")
    return None


def detect_resolution():
    """Detect the current display mode using the host operating system."""
    print("Detecting display resolution...")
    system = platform.system()
    if system == "Windows":
        return get_resolution_windows()
    elif system == "Linux":
        return get_resolution_linux()
    elif system == "Darwin":
        return get_resolution_macos()
    return None


# -----------------------------
# Refactor of main() into helpers
# -----------------------------

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


def get_detected_video_settings(resolution):
    """Convert a detected display mode into explicit video configuration values."""
    w, h, r = resolution
    return str(w), str(h), str(r), "0"


def print_detected_resolution(resolution):
    """Show the detected display dimensions and refresh rate."""
    w, h, r = resolution
    print(f"Detected Resolution: {w}x{h} @ {r}Hz")


def print_manual_resolution():
    """Report failed detection and ask whether to use the desktop resolution."""
    print("Could not automatically detect resolution.")
    return get_input("Use desktop resolution? (y/n)", "y").lower()


def prompt_video_settings_manual(use_desktop):
    """Return desktop mode settings or prompt for explicit display values."""
    if use_desktop.startswith('y'):
        return None, None, None, "1"

    return (
        get_input("Width", "1920"),
        get_input("Height", "1080"),
        get_input("Refresh Rate", "60"),
        "0",
    )


def get_video_settings_with_detection():
    """Detect video settings or ask for them when detection is unavailable."""
    resolution = detect_resolution()

    if resolution:
        print_detected_resolution(resolution)
        vid_width, vid_height, vid_freq, vid_usedesktop = get_detected_video_settings(resolution)
    else:
        use_desktop = print_manual_resolution()
        vid_width, vid_height, vid_freq, vid_usedesktop = prompt_video_settings_manual(use_desktop)

    return vid_width, vid_height, vid_freq, vid_usedesktop


def build_updates_dict(name, team, sensitivity, vid_width, vid_height, vid_freq, vid_usedesktop):
    """Build configuration updates, omitting unspecified video values."""
    updates = {
        "name": name,
        "team": team,
        "sensitivity": sensitivity,
        "vid_usedesktopres": vid_usedesktop
    }

    if vid_width is not None:
        updates["vid_width"] = vid_width
    if vid_height is not None:
        updates["vid_height"] = vid_height
    if vid_freq is not None:
        updates["vid_displayfrequency"] = vid_freq

    return updates


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


def update_lines_with_keys(lines: list[str], updates: dict[str, str]) -> tuple[list[str], set[str]]:
    """Update existing settings and remove explicit video settings in desktop mode."""
    new_lines: list[str] = []
    keys_found: set[str] = set()

    desktop_mode = updates.get("vid_usedesktopres") == "1"

    for line in lines:
        if is_ignorable_line(line):
            new_lines.append(line)
            continue

        key = get_key_from_line(line)
        if not key:
            new_lines.append(line)
            continue

        # Desktop mode removes explicit video keys; comments and blank lines stay.
        if desktop_mode and key in VIDEO_KEYS:
            # skip this line entirely (delete it)
            continue

        # Normal update path
        new_lines.append(rewrite_line_if_update(line, updates, keys_found))

    return new_lines, keys_found


def append_missing_keys(
    new_lines: list[str], updates: dict[str, str], keys_found: set[str]
) -> list[str]:
    """Append missing settings, excluding explicit video values in desktop mode."""
    added_new = False
    desktop_mode = updates.get("vid_usedesktopres") == "1"

    for key, value in updates.items():
        if key in keys_found:
            continue

        # If desktop mode, don't add explicit video keys at all
        if desktop_mode and key in VIDEO_KEYS:
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
    """Run the interactive setup and save the selected player and video settings."""
    print_banner()

    name, team, sensitivity = prompt_user_settings()
    vid_width, vid_height, vid_freq, vid_usedesktop = get_video_settings_with_detection()

    updates = build_updates_dict(
        name=name,
        team=team,
        sensitivity=sensitivity,
        vid_width=vid_width,
        vid_height=vid_height,
        vid_freq=vid_freq,
        vid_usedesktop=vid_usedesktop
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
