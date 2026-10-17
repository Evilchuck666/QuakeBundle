"""Cover download progress and multiplayer game data installation."""

import contextlib
import io
import os
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from tqdm import tqdm

from quake_installer import gamedata


class MultiplayerGameDataTests(unittest.TestCase):
    """Install original Quake data without touching existing user files."""

    def setUp(self):
        """Keep downloaded and installed files in a temporary directory."""
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        root = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="quake data ")))
        self.download = root / "download"
        self.install = root / "multiplayer"
        self.rerelease = self.download / "rerelease"
        self.singleplayer = root / "singleplayer"
        for name, path in {
            "DOWNLOAD_DIR": self.download,
            "INSTALL_MULTIPLAYER_DIR": self.install,
            "RERELEASE_DIR": self.rerelease,
            "INSTALL_SINGLE_PLAYER_DIR": self.singleplayer,
        }.items():
            stack.enter_context(patch.object(gamedata, name, path))
        self.output = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.nquake = stack.enter_context(patch.object(gamedata, "install_nquake_game_data"))

    @staticmethod
    def write(path, content):
        """Create a synthetic game or user file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def test_installs_original_paks_with_lowercase_names_and_preserves_user_files(self):
        """Reinstall archives without replacing directories or unrelated files."""
        preserved = {
            self.install / "id1/config.cfg": b"settings",
            self.install / "id1/save.sav": b"saved game",
            self.install / "qw/config.cfg": b"multiplayer settings",
            self.install / "id1/pak2.pak": b"custom content",
            self.download / "id1/config.cfg": b"downloaded defaults",
            self.rerelease / "id1/pak0.pak": b"re-release data",
            self.singleplayer / "id1/pak0.pak": b"single-player data",
        }
        for path, content in preserved.items():
            self.write(path, content)

        for names in (
            ("PAK0.PAK", "PAK1.PAK"),
            ("pak0.pak", "pak1.pak"),
            ("Pak0.Pak", "pAk1.pAK"),
        ):
            with self.subTest(names=names):
                for name, content in zip(names, (b"original pak0", b"original pak1")):
                    self.write(self.download / "id1" / name, content)
                    self.write(self.install / "id1" / name.lower(), b"old data")

                gamedata.install_multiplayer_game_data()
                self.nquake.assert_called_once_with()
                self.nquake.reset_mock()

                for name, content in zip(names, (b"original pak0", b"original pak1")):
                    self.assertEqual((self.install / "id1" / name.lower()).read_bytes(), content)
                    self.assertFalse((self.download / "id1" / name).exists())
                self.assertEqual(
                    sorted(path.name for path in (self.install / "id1").iterdir()),
                    ["config.cfg", "pak0.pak", "pak1.pak", "pak2.pak", "save.sav"],
                )
                for path, content in preserved.items():
                    self.assertEqual(path.read_bytes(), content)
        self.assertIn(f"2 files, {gamedata.mb(26)} in {self.install}", self.output.getvalue())

    def test_missing_source_reports_all_required_files_without_creating_install(self):
        """Report an absent download without leaving an empty installation."""
        with self.assertRaises(SystemExit) as error:
            gamedata.install_multiplayer_game_data()
        self.assertEqual(
            str(error.exception),
            "Files missing after download:\n  id1/pak0.pak\n  id1/pak1.pak",
        )
        self.assertFalse(self.install.exists())
        self.assertEqual(self.output.getvalue(), "")
        self.nquake.assert_not_called()

    def test_incomplete_download_preserves_source_and_existing_install(self):
        """Validate both archives before moving or overwriting either one."""
        for present, missing in (("pak0.pak", "pak1.pak"), ("pak1.pak", "pak0.pak")):
            with self.subTest(missing=missing):
                source = self.download / "id1" / present.upper()
                self.write(source, b"new data")
                self.write(self.install / "id1" / present, b"old data")
                self.write(self.rerelease / "id1" / missing, b"wrong edition")
                with self.assertRaisesRegex(SystemExit, f"id1/{missing}"):
                    gamedata.install_multiplayer_game_data()
                self.assertEqual(source.read_bytes(), b"new data")
                self.assertEqual((self.install / "id1" / present).read_bytes(), b"old data")
                source.unlink()
        self.assertEqual(self.output.getvalue(), "")
        self.nquake.assert_not_called()

    def test_directory_named_like_a_pak_is_rejected_before_moving_files(self):
        """A directory cannot satisfy the required archive check."""
        self.write(self.download / "id1/PAK0.PAK", b"original data")
        (self.download / "id1/PAK1.PAK").mkdir()
        with self.assertRaisesRegex(SystemExit, "id1/pak1.pak"):
            gamedata.install_multiplayer_game_data()
        self.assertEqual((self.download / "id1/PAK0.PAK").read_bytes(), b"original data")
        self.assertFalse(self.install.exists())
        self.nquake.assert_not_called()

    def test_addon_download_failure_leaves_steam_paks_in_place(self):
        """Keep the Steam files available so a failed addon install can be retried."""
        for name in ("pak0.pak", "pak1.pak"):
            self.write(self.download / "id1" / name, b"Steam data")
            self.write(self.install / "id1" / name, b"installed data")
        self.nquake.side_effect = SystemExit("Download failed")
        with self.assertRaisesRegex(SystemExit, "Download failed"):
            gamedata.install_multiplayer_game_data()
        for name in ("pak0.pak", "pak1.pak"):
            self.assertEqual((self.download / "id1" / name).read_bytes(), b"Steam data")
            self.assertEqual((self.install / "id1" / name).read_bytes(), b"installed data")


class NQuakeGameDataTests(unittest.TestCase):
    """Exercise the automatic nQuake installation using synthetic distribution ZIPs."""

    def setUp(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.root = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="nquake data ")))
        self.install = self.root / "multiplayer"
        self.download = self.root / "download"
        self.distribution = self.root / "distribution"
        self.distribution.mkdir()
        self.mirror = "https://nquake.example/distribution"
        self.manifest = self.distribution / "nquake.ini"
        self.manifest.write_text(
            f'[mirror_addresses]\n1={self.mirror}/\n[mirror_descriptions]\n1="Mirror"\n',
            encoding="utf-8",
        )
        self.packages = {
            "gpl.zip": {
                "LICENSE": b"license",
                "ezquake/configs/config.cfg": b"default settings\r\n",
                "ezquake/ezquake.pk3": b"engine data\r\n",
                "qw/skins/player.png": b"skin",
                "qw/maps/update.bsp": b"updated map",
                "qw/demos/": b"",
                "id1/gpl_maps.pk3": b"shareware replacement maps",
                "id1/readme.txt": b"shareware instructions",
                "ID1/PAK0.PAK": b"shareware data",
                "id1/pak1.pak": b"wrong game data",
                "ezquake.exe": b"old engine",
                "ezquake/sb/wget.exe": b"Windows download helper",
                "x64.zip": b"old engine archive",
            },
            "non-gpl.zip": {
                "readme.txt": b"nQuake instructions\r\n",
                "qw/autoexec.cfg": b"exec nquake_default.cfg\r\n",
                "qw/nquake.pk3": b"base content",
                "qw/maps/custom.bsp": b"map",
                "qw/save.sav": b"bundled save",
            },
            "addon-clanarena.zip": {
                "arena/arena.pk3": b"Clan Arena",
                "prox/prox.pk3": b"Pro-X",
                "prox/configs/config.cfg": b"Clan Arena settings\r\n",
            },
            "addon-fortress.zip": {
                "fortress/pak0.pak": b"Team Fortress",
                "fortress/default.cfg": b"Team Fortress settings\r\n",
            },
            "addon-textures.zip": {
                "qw/qrp_maps_textures_1.pk3": b"high resolution textures\r\n",
            },
        }
        for name, files in self.packages.items():
            self.write_zip(name, files)
        stack.enter_context(patch.object(gamedata, "INSTALL_MULTIPLAYER_DIR", self.install))
        stack.enter_context(patch.object(gamedata, "DOWNLOAD_DIR", self.download))
        self.fetch = stack.enter_context(patch.object(gamedata, "fetch", side_effect=self.fetch_file))
        self.output = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def write_zip(self, name, files):
        with zipfile.ZipFile(self.distribution / name, "w") as archive:
            for path, data in files.items():
                archive.writestr(path, data)

    def fetch_file(self, url, destination):
        if url == gamedata.NQUAKE_INI_URL:
            source = self.manifest
        else:
            name = url.rsplit("/", 1)[-1]
            self.assertIn(name, self.packages)
            self.assertEqual(url, f"{self.mirror}/{name}")
            source = self.distribution / name
        shutil.copyfile(source, destination)

    def installed_files(self):
        if not self.install.exists():
            return {}
        return {p.relative_to(self.install).as_posix(): p.read_bytes()
                for p in self.install.rglob("*") if p.is_file()}

    def test_installs_base_and_all_addons_with_steam_paks_and_preserves_settings(self):
        """Install every package through the public entry point, without engine downloads."""
        preserved = {
            "ezquake.exe": b"current engine",
            "ezQuake.AppImage": b"current Linux engine",
            "ezquake/configs/config.cfg": b"custom settings\r\n",
            "qw/save.sav": b"saved game",
            "qw/config.cfg": b"custom multiplayer settings",
            "id1/config.cfg": b"custom base settings",
        }
        for name, data in preserved.items():
            MultiplayerGameDataTests.write(self.install / name, data)
        MultiplayerGameDataTests.write(self.install / "qw/maps/update.bsp", b"old map")
        for name in ("pak0.pak", "pak1.pak"):
            MultiplayerGameDataTests.write(self.download / "id1" / name.upper(), b"Steam " + name.encode())

        with patch("builtins.input", side_effect=AssertionError("Unexpected prompt")):
            gamedata.install_multiplayer_game_data()

        actual = self.installed_files()
        for name, data in preserved.items():
            self.assertEqual(actual[name], data)
        for name in ("pak0.pak", "pak1.pak"):
            self.assertEqual(actual[f"id1/{name}"], b"Steam " + name.encode())
            self.assertFalse((self.download / "id1" / name.upper()).exists())
        for name in ("id1/gpl_maps.pk3", "id1/readme.txt", "ID1/PAK0.PAK",
                     "ezquake/sb/wget.exe", "x64.zip"):
            self.assertNotIn(name, actual)
        for name in ("ezquake/ezquake.pk3", "qw/skins/player.png", "qw/maps/update.bsp",
                     "qw/nquake.pk3", "qw/maps/custom.bsp", "arena/arena.pk3",
                     "prox/prox.pk3", "fortress/pak0.pak", "qw/qrp_maps_textures_1.pk3"):
            expected = next(files[name] for files in self.packages.values() if name in files)
            self.assertEqual(actual[name], expected)
        for name in ("readme.txt", "qw/autoexec.cfg", "prox/configs/config.cfg", "fortress/default.cfg"):
            expected = next(files[name] for files in self.packages.values() if name in files)
            if os.name != "nt":
                expected = expected.replace(b"\r\n", b"\n")
            self.assertEqual(actual[name], expected)
        self.assertTrue((self.install / "qw/demos").is_dir())
        self.assertEqual([call.args[0] for call in self.fetch.call_args_list],
                         [gamedata.NQUAKE_INI_URL] + [f"{self.mirror}/{p}" for p in self.packages])
        self.assertEqual(list(self.install.glob(".nquake-*")), [])

    def test_invalid_manifest_stops_before_package_downloads(self):
        for manifest, message in (
            ("[versions]\nlinux=2.2\n", "No download mirrors"),
            ("[mirror_addresses]\n1=file:///tmp/packages\n", "No download mirrors"),
            ("[mirror_addresses]\n1=https:///missing-host\n", "No download mirrors"),
            ("not an INI file", "Invalid nQuake package locations"),
        ):
            with self.subTest(manifest=manifest):
                self.manifest.write_text(manifest, encoding="utf-8")
                self.fetch.reset_mock()
                with self.assertRaisesRegex(SystemExit, message):
                    gamedata.install_nquake_game_data()
                self.assertEqual(self.fetch.call_count, 1)
                self.assertEqual(list(self.install.iterdir()), [])

    def test_failed_download_or_corrupt_archive_preserves_install_and_cleans_temporary_files(self):
        MultiplayerGameDataTests.write(self.install / "qw/maps/update.bsp", b"installed map")
        before = self.installed_files()

        def fail_download(url, destination):
            if url.endswith("addon-textures.zip"):
                destination.write_bytes(b"partial download")
                raise SystemExit("Download failed")
            self.fetch_file(url, destination)

        for failure in ("download", "archive"):
            with self.subTest(failure=failure):
                if failure == "download":
                    self.fetch.side_effect = fail_download
                    message = "Download failed"
                else:
                    self.fetch.side_effect = self.fetch_file
                    (self.distribution / "addon-textures.zip").write_bytes(b"not a ZIP")
                    message = "Could not extract nQuake addon-textures.zip"
                with self.assertRaisesRegex(SystemExit, message):
                    gamedata.install_nquake_game_data()
                self.assertEqual(self.installed_files(), before)
                self.assertEqual(list(self.install.glob(".nquake-*")), [])

    def test_rejects_archive_paths_outside_install_directory(self):
        for path in ("../../escaped.cfg", "/escaped.cfg", "C:\\escaped.cfg", "..\\escaped.cfg"):
            with self.subTest(path=path):
                self.write_zip("addon-textures.zip", {path: b"outside"})
                with self.assertRaisesRegex(SystemExit, "Unsafe path in addon-textures.zip"):
                    gamedata.install_nquake_game_data()
                self.assertEqual(list(self.install.iterdir()), [])


class DownloadProgressTests(unittest.TestCase):
    """Cover download progress with visible and disabled progress bars."""

    def setUp(self):
        """Set up a temporary download directory and capture stdout."""
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        directory = stack.enter_context(tempfile.TemporaryDirectory(prefix="quake data "))
        stack.enter_context(patch.object(gamedata, "DOWNLOAD_DIR", Path(directory)))
        self.output = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def test_progress_starts_lazily_and_tracks_state_and_size_changes(self):
        """Ensure the download bar appears only when progress data starts and tracks updates."""
        bars = []

        def make_bar(**kwargs):
            progress_bar = tqdm(file=io.StringIO(), **kwargs)
            self.addCleanup(progress_bar.close)
            bars.append(progress_bar)
            return progress_bar

        def run(_command, on_line):
            on_line("Logging in...")
            self.assertEqual(bars, [])
            for state, downloaded, total in (
                ("preallocating", 10, 0),
                ("preallocating", 20, 0),
                ("downloading", 1, 1048576),
                ("downloading", 4, 2097152),
                ("verifying", 5, 2097152),
            ):
                on_line(
                    f"Update state (0x61) {state}, progress: 0.00 ({downloaded} / {total})"
                )
                self.assertEqual(len(bars), 1)
                self.assertEqual(bars[0].desc, f"    {state}")
                self.assertEqual(bars[0].n, downloaded)
                self.assertEqual(bars[0].total, total or None)
            on_line("Success! App '2310' fully installed.")
            on_line("Update state (0x61) downloading, progress: 0.00 (0 / 0)")
            self.assertEqual(bars[0].desc, "    verifying")
            return 0, []

        with patch.object(gamedata, "byte_bar", side_effect=make_bar), patch.object(
            gamedata, "run_captured", side_effect=run
        ):
            gamedata.download_quake(Path("steamcmd"), "test-user", 1)
        self.assertEqual(self.output.getvalue(), "    ✔ installed (2.0 MB)\n")

    def test_redirected_output_handles_disabled_bars_and_keeps_last_known_size(self):
        """Keep the last known byte count when status updates arrive without a visible bar."""
        def run(_command, on_line):
            for line in (
                "Update state (0x61) downloading, progress: 0.00 (0 / 1048576)",
                "Update state (0x81) verifying, progress: 0.00 (0 / 0)",
                "Success! App '2310' already up to date.",
            ):
                on_line(line)
            return 0, []

        with patch.object(gamedata, "run_captured", side_effect=run):
            gamedata.download_quake(Path("steamcmd"), "test-user", 1)
        self.assertEqual(self.output.getvalue(), "    ✔ up to date (1.0 MB)\n")


if __name__ == "__main__":
    unittest.main()
