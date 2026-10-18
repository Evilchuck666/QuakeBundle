"""Use the existing engines and configuration helpers without invoking Python."""

from __future__ import annotations

import math
import platform
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from assets.scripts.ezQuake import launcher as ez_launcher
from assets.scripts.vkQuake import launcher as vk_launcher
from quake_installer.config import INSTALL_MULTIPLAYER_DIR, INSTALL_SINGLE_PLAYER_DIR
from quake_installer.runtime import external_environment

ASSETS = Path(__file__).resolve().parent.parent / "assets"


@dataclass(frozen=True)
class Edition:
    key: str
    title: str
    engine: str
    description: str
    default_dir: Path
    config_dir: str
    launcher: object


EDITIONS = {
    "singleplayer": Edition(
        "singleplayer",
        "Campaign",
        "vkQuake",
        "Quake and its expansions",
        INSTALL_SINGLE_PLAYER_DIR,
        "id1/configs",
        vk_launcher,
    ),
    "multiplayer": Edition(
        "multiplayer",
        "Multiplayer",
        "ezQuake",
        "QuakeWorld · nQuake · Addons",
        INSTALL_MULTIPLAYER_DIR,
        "ezquake/configs",
        ez_launcher,
    ),
}


def validate_player(name, sensitivity, team="", fakename=""):
    if not name.strip():
        raise ValueError("Enter a player name.")
    for value in (name, team, fakename):
        if any(ord(char) < 32 or char in '\\"' for char in value):
            raise ValueError(
                "Names cannot contain quotes, backslashes or line breaks."
            )
    if not math.isfinite(float(sensitivity)) or float(sensitivity) <= 0:
        raise ValueError("Mouse sensitivity must be a positive number.")


def read_player(edition, root):
    values = {"name": "Player", "sensitivity": "3", "team": "", "cl_fakename": ""}
    path = root / edition.config_dir / "player.cfg"
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            match = re.match(r'^\s*(\w+)\s+(?:"([^"\r\n]*)"|([^\s/]+))', line)
            if match and match[1] in values:
                values[match[1]] = match[2] if match[2] is not None else match[3]
    return values


def configure(edition, root, name, sensitivity, team="", fakename=""):
    """Recover only missing configs from bundled assets and preserve other settings."""
    validate_player(name, sensitivity, team, fakename)
    directory = root / edition.config_dir
    directory.mkdir(parents=True, exist_ok=True)
    for filename in edition.launcher.CONFIG_FILES:
        target = directory / filename
        if not target.exists():
            shutil.copyfile(ASSETS / "cfg" / edition.engine / filename, target)
    player = directory / "player.cfg"
    updates = {
        "name": name.strip(),
        "hostname": f"{name.strip()} SV",
        "sensitivity": str(sensitivity),
    }
    if edition.key == "singleplayer":
        updates["_cl_name"] = name.strip()
    else:
        updates.update(team=team, cl_fakename=fakename)
    helper = edition.launcher
    helper.save_config_lines(
        player, helper.update_config_lines(helper.load_config_lines(player), updates)
    )
    helper.mark_setup_complete(player)
    helper.prepare_shared_autoexec(root)


def installed(edition, root):
    try:
        edition.launcher.resolve_engine(root)
        files = (
            ("id1/pak0.pak", "id1/pak1.pak")
            if edition.key == "multiplayer"
            else ("id1/pak0.pak",)
        )
        return all((root / name).is_file() for name in files)
    except OSError:
        return False


def launch_command(edition, root, mod=""):
    engine = edition.launcher.resolve_engine(root)
    command = [str(engine), "-basedir", str(root)]
    if edition.key == "multiplayer":
        command.append("-nohome")
    elif platform.system() != "Windows":
        command.extend(["-userdir", str(root)])
    config = (
        "ezquake/configs/autoexec.cfg"
        if edition.key == "multiplayer"
        else "../id1/configs/autoexec.cfg"
    )
    command.extend(["+exec", config])
    if mod:
        if Path(mod).name != mod or mod in (".", "..") or not (root / mod).is_dir():
            raise ValueError("Select a valid expansion folder.")
        command.extend(["-game", mod])
    return command


def game_environment(edition):
    """Undo PyInstaller's library paths before starting an external game."""
    environment = external_environment()
    environment.update(SDL_MOUSE_RELATIVE_MODE_WARP="0", SDL_MOUSE_RELATIVE_SCALING="0")
    if platform.system() == "Linux":
        environment["SDL_VIDEO_X11_NET_WM_BYPASS_COMPOSITOR"] = "1"
        if edition.key == "multiplayer":
            environment.update(__GL_SYNC_TO_VBLANK="0", __GL_MaxFramesAllowed="1")
    return environment
