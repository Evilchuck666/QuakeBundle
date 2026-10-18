"""Run the installer off the Qt event loop; communicate only through signals."""

from __future__ import annotations

import io
import platform
import queue
import shutil
import threading
from contextlib import redirect_stderr, redirect_stdout

from PySide6.QtCore import QThread, Signal

from quake_installer import ezquake, gamedata, interactive, steamcmd, vkquake
from quake_installer.config import STEAMCMD_DIR, parse_install_dir
from quake_installer.events import Cancelled, Observer, observe

from .library import ASSETS, configure, create_shortcut


class LogStream(io.TextIOBase):
    def __init__(self, emit):
        self.emit = emit
        self.pending = ""

    def write(self, text):
        self.pending += text
        while "\n" in self.pending:
            line, self.pending = self.pending.split("\n", 1)
            if line.strip():
                self.emit(line.strip())
        return len(text)

    def flush(self):
        if self.pending.strip():
            self.emit(self.pending.strip())
        self.pending = ""


class InstallWorker(QThread):
    stage = Signal(int, str)
    progress = Signal(float, object, str)
    log = Signal(str)
    challenge = Signal(str)
    status = Signal(str)
    completed = Signal(str)  # success, cancelled, error
    failed = Signal(str)

    def __init__(self, edition, root, username, player, parent=None):
        super().__init__(parent)
        self.edition, self.root, self.username, self.player = (
            edition,
            root,
            username,
            player,
        )
        self.cancelled = threading.Event()
        self.answers = queue.Queue()

    def checkpoint(self):
        if self.cancelled.is_set():
            raise Cancelled()

    def cancel(self):
        self.cancelled.set()

    def answer(self, value):
        self.answers.put(value)

    def ask(self, prompt):
        self.challenge.emit(prompt)
        while True:
            self.checkpoint()
            try:
                return self.answers.get(timeout=0.1)
            except queue.Empty:
                continue

    def next_stage(self, number, title):
        self.checkpoint()
        self.stage.emit(number, title)
        self.log.emit(title)

    def auth_status(self, message):
        self.log.emit(message)
        self.status.emit(message)

    def install(self):
        self.root = parse_install_dir(str(self.root))
        machine = platform.machine().lower()
        system = platform.system()
        supported = (
            system == "Linux"
            and machine in ("x86_64", "amd64")
            or system == "Darwin"
            and machine in ("x86_64", "arm64")
            or system == "Windows"
            and (
                machine in ("x86_64", "amd64")
                or machine == "arm64"
                and self.edition.key == "singleplayer"
            )
        )
        if not supported:
            raise RuntimeError(
                f"No compatible engine is available for {system} {machine} for this edition."
            )
        original_configs = {
            name
            for name in self.edition.launcher.CONFIG_FILES
            if (self.root / self.edition.config_dir / name).exists()
        }
        self.next_stage(0, "Preparing SteamCMD")
        steamcmd.check_rosetta()
        executable = steamcmd.install_steamcmd()
        steamcmd.update_steamcmd(executable)
        self.next_stage(1, "Logging in to Steam")
        interactive.login(
            executable,
            self.username,
            STEAMCMD_DIR,
            self.ask,
            self.checkpoint,
            self.auth_status,
        )
        self.next_stage(2, "Downloading Quake")
        gamedata.download_quake(executable, self.username, 3)
        self.next_stage(3, "Installing the game")
        if self.edition.key == "singleplayer":
            gamedata.install_single_player_game_data(self.root)
        else:
            gamedata.install_multiplayer_game_data(self.root)
        self.next_stage(4, f"Installing {self.edition.engine}")
        if self.edition.key == "singleplayer":
            vkquake.install_vkquake(self.root)
        else:
            ezquake.install_ezquake(self.root)
        self.edition.launcher.resolve_engine(
            self.root
        )  # No false success if a build is missing.
        self.next_stage(5, "Preparing your configuration")
        icon = f"{self.edition.engine}.svg"
        shutil.copyfile(ASSETS / "icons" / icon, self.root / icon)
        launcher = self.root / "launcher.py"
        shutil.copyfile(
            ASSETS / "scripts" / self.edition.engine / "launcher.py", launcher
        )
        if system != "Windows":
            launcher.chmod(0o755)
        directory = self.root / self.edition.config_dir
        directory.mkdir(parents=True, exist_ok=True)
        for name in self.edition.launcher.CONFIG_FILES:
            if name not in original_configs:
                shutil.copyfile(
                    ASSETS / "cfg" / self.edition.engine / name, directory / name
                )
        configure(self.edition, self.root, **self.player)
        self.checkpoint()
        self.log.emit("Creating game shortcut")
        destination = create_shortcut(self.edition, self.root)
        self.log.emit(f"Created shortcut: {destination}")
        gamedata.clean_up()

    def run(self):
        stream = LogStream(self.log.emit)
        outcome = "success"
        try:
            with (
                observe(Observer(self.progress.emit, self.checkpoint)),
                redirect_stdout(stream),
                redirect_stderr(stream),
            ):
                self.install()
        except Cancelled:
            outcome = "cancelled"
        except (Exception, SystemExit) as error:  # noqa: BLE001 - contain all backend failures at the thread boundary.
            outcome = "error"
            self.failed.emit(str(error) or type(error).__name__)
        finally:
            stream.flush()
            self.completed.emit(outcome)
