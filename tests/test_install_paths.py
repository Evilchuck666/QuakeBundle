"""Exercise configurable destinations through both complete installation flows."""

import argparse
import contextlib
import errno
import importlib.util
import io
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from quake_installer import config, ezquake, gamedata, shortcuts, vkquake

HAS_QT = importlib.util.find_spec("PySide6") is not None
if HAS_QT:
    from quake_gui.library import EDITIONS, launch_command
    from quake_gui.worker import InstallWorker
    from quake_installer import interactive, steamcmd


@unittest.skipUnless(
    HAS_QT, "Install scripts/requirements-gui.txt to run GUI installation tests"
)
class InstallationPathTests(unittest.TestCase):
    """Keep Steam and release downloads offline while installing real fixture files."""

    def setUp(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.home = Path(stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        previous_directory = Path.cwd()
        os.chdir(self.home)
        self.addCleanup(os.chdir, previous_directory)
        self.download = self.home / "cache/download"
        self.steam = self.home / "cache/steamcmd"
        self.assets = Path(__file__).resolve().parents[1] / "assets"
        stack.enter_context(patch.object(Path, "home", return_value=self.home))
        stack.enter_context(
            patch.dict(os.environ, HOME=str(self.home), USERPROFILE=str(self.home))
        )
        stack.enter_context(patch.object(config, "DOWNLOAD_DIR", self.download))
        stack.enter_context(patch.object(config, "STEAMCMD_DIR", self.steam))
        stack.enter_context(patch.object(gamedata, "DOWNLOAD_DIR", self.download))
        stack.enter_context(
            patch.object(gamedata, "RERELEASE_DIR", self.download / "rerelease")
        )
        stack.enter_context(patch.object(gamedata, "STEAMCMD_DIR", self.steam))
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    @staticmethod
    def write(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def download_game(self, *_args):
        for relative in gamedata.EXPECTED_FILES:
            self.write(self.download / "rerelease" / relative, b"re-release data")
        for name in ("PAK0.PAK", "PAK1.PAK"):
            self.write(self.download / "id1" / name, b"original data")
        self.write(self.steam / "cached-login", b"Steam cache")

    def fetch_nquake(self, url, destination):
        if url == config.NQUAKE_INI_URL:
            destination.write_text(
                "[mirror_addresses]\n1=https://example.test/nquake\n"
            )
        else:
            with zipfile.ZipFile(destination, "w") as archive:
                archive.writestr("qw/" + url.rsplit("/", 1)[-1] + ".pk3", b"addon")
                archive.writestr("ezquake/configs/default.cfg", b"bundled config")

    def run_installer(self, edition, argument, expected, cross_device=False):
        campaign = edition.key == "singleplayer"
        engine = vkquake if campaign else ezquake
        name = edition.engine
        release_function = "vkquake_release" if campaign else "ezquake_release"
        binary = "vkQuake.AppImage" if campaign else "ezQuake-x86_64.AppImage"
        worker = InstallWorker(
            edition, argument, "user", {"name": "Ranger", "sensitivity": 3}
        )

        def fetch_engine(_url, destination):
            if campaign:
                destination.write_bytes(b"engine")
            else:
                with zipfile.ZipFile(destination, "w") as archive:
                    archive.writestr(binary, b"engine")

        original_replace, original_rename = os.replace, os.rename

        def cross_device_move(operation, source, destination):
            if Path(source).is_relative_to(self.download):
                raise OSError(errno.EXDEV, "Cross-device link")
            return operation(source, destination)

        self.write(expected / "id1/save.sav", b"saved game")
        self.write(expected / "id1/pak0.pak", b"previous data")
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch("platform.system", return_value="Linux"))
            stack.enter_context(patch("platform.machine", return_value="x86_64"))
            for function in ("check_rosetta", "install_steamcmd", "update_steamcmd"):
                stack.enter_context(patch.object(steamcmd, function))
            stack.enter_context(patch.object(interactive, "login"))
            stack.enter_context(
                patch.object(gamedata, "download_quake", self.download_game)
            )
            stack.enter_context(patch.object(gamedata, "fetch", self.fetch_nquake))
            stack.enter_context(
                patch.object(
                    engine,
                    release_function,
                    return_value=(
                        "test",
                        {
                            "name": binary,
                            "browser_download_url": "https://example.test/engine",
                        },
                    ),
                )
            )
            stack.enter_context(patch.object(engine, "fetch", fetch_engine))
            if cross_device:
                stack.enter_context(
                    patch.object(
                        os,
                        "replace",
                        side_effect=lambda s, d: cross_device_move(
                            original_replace, s, d
                        ),
                    )
                )
                stack.enter_context(
                    patch.object(
                        os,
                        "rename",
                        side_effect=lambda s, d: cross_device_move(
                            original_rename, s, d
                        ),
                    )
                )
            worker.install()
            expected_command = launch_command(edition, expected)

        data = b"re-release data" if campaign else b"original data"
        self.assertEqual((expected / "id1/pak0.pak").read_bytes(), data)
        self.assertEqual((expected / "id1/save.sav").read_bytes(), b"saved game")
        self.assertEqual((expected / binary).read_bytes(), b"engine")
        self.assertFalse((expected / "launcher.py").exists())
        self.assertEqual(
            (expected / (name + ".svg")).read_bytes(),
            (self.assets / "icons" / (name + ".svg")).read_bytes(),
        )
        self.assertFalse(self.download.exists())
        self.assertEqual((self.steam / "cached-login").read_bytes(), b"Steam cache")
        if not campaign:
            self.assertEqual((expected / "id1/pak1.pak").read_bytes(), data)
            self.assertEqual(len(list((expected / "qw").glob("*.pk3"))), 5)
            self.assertTrue((expected / "ezquake/configs/player.cfg").is_file())
        desktop = self.home / ".local/share/applications" / (name + ".desktop")
        entries = dict(
            line.split("=", 1)
            for line in desktop.read_text(encoding="utf-8").splitlines()
            if "=" in line and not line.startswith("#")
        )
        self.assertEqual(entries["Path"], shortcuts.desktop_value(expected))
        self.assertEqual(
            entries["Icon"], shortcuts.desktop_value(expected / (name + ".svg"))
        )
        self.assertEqual(
            entries["Exec"],
            " ".join(shortcuts.desktop_argument(arg) for arg in expected_command),
        )
        self.assertEqual(entries["Terminal"], "false")

    def test_custom_paths_install_everything_and_update_shortcuts(self):
        for edition in EDITIONS.values():
            for kind in ("absolute", "relative", "home"):
                with self.subTest(edition=edition.key, kind=kind):
                    destination = self.home / f"{edition.key} {kind} é $game 100%"
                    if kind == "relative":
                        argument = os.path.relpath(destination)
                    elif kind == "home":
                        argument = "~/" + destination.name
                    else:
                        argument = str(destination)
                    self.run_installer(edition, argument, destination)
                    self.assertFalse((self.home / "defaults").exists())

    def test_game_data_can_move_to_another_filesystem(self):
        for edition in EDITIONS.values():
            with self.subTest(edition=edition.key):
                destination = self.home / edition.key
                self.run_installer(
                    edition, str(destination), destination, cross_device=True
                )

    def test_invalid_destinations_fail_before_contacting_steam(self):
        file = self.home / "existing-file"
        file.write_bytes(b"keep")
        for edition in EDITIONS.values():
            for destination in (
                "",
                str(file),
                str(self.download),
                str(self.download / "game"),
                str(self.steam / "game"),
            ):
                with (
                    self.subTest(edition=edition.key, destination=destination),
                    patch.object(steamcmd, "install_steamcmd") as steam,
                    self.assertRaises(argparse.ArgumentTypeError),
                ):
                    InstallWorker(edition, destination, "user", {}).install()
                steam.assert_not_called()
        self.assertEqual(file.read_bytes(), b"keep")

    def test_symlink_into_download_directory_is_rejected(self):
        self.download.mkdir(parents=True)
        link = self.home / "linked game"
        try:
            link.symlink_to(self.download, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"Directory symlinks unavailable: {error}")
        with self.assertRaises(argparse.ArgumentTypeError):
            config.parse_install_dir(str(link / "game"))
