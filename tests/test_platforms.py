"""Offline checks for platform paths and vkQuake release installation."""

import contextlib
import io
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from quake_installer import config, ezquake, vkquake


def release_asset(name):
    """Return a minimal GitHub release asset payload for testing."""
    return {"name": name, "browser_download_url": "https://example.invalid/" + name}


class UserDataDirectoryTests(unittest.TestCase):
    """Verify platform-specific user-data directory selection."""

    def test_platform_paths_and_environment_overrides(self):
        """Verify the default user-data directory and environment overrides."""
        home = Path(tempfile.gettempdir()) / "quake-installer-test-home"
        custom = home / "custom data"
        cases = [
            ("Windows", {}, home / "AppData" / "Local"),
            ("Windows", {"LOCALAPPDATA": str(custom)}, custom),
            ("Darwin", {}, home / "Library" / "Application Support"),
            ("Darwin", {"XDG_DATA_HOME": str(custom)}, home / "Library" / "Application Support"),
            ("Linux", {}, home / ".local" / "share"),
            ("Linux", {"XDG_DATA_HOME": ""}, home / ".local" / "share"),
            ("Linux", {"XDG_DATA_HOME": str(custom)}, custom),
        ]
        for system, environment, expected in cases:
            with (
                self.subTest(system=system, environment=environment),
                mock.patch.object(config.platform, "system", return_value=system),
                mock.patch.dict(os.environ, environment, clear=True),
                mock.patch.object(config.Path, "home", return_value=home),
            ):
                self.assertEqual(config.user_data_dir(), expected)


class VkQuakeReleaseTests(unittest.TestCase):
    """Verify vkQuake release selection for supported platforms."""

    def test_selects_asset_for_linux_and_windows_architectures(self):
        """Verify the supported vkQuake asset is selected for Linux and Windows."""
        assets = [
            release_asset("vkQuake-1.32-x86_64.AppImage"),
            release_asset("vkQuake-1.32_windows_x64.zip"),
            release_asset("vkQuake-1.32_windows_arm64.zip"),
        ]

        release = {"tag_name": "1.32", "assets": assets}

        cases = [
            ("Linux", "x86_64", assets[0]),
            ("Linux", "AMD64", assets[0]),
            ("Windows", "AMD64", assets[1]),
            ("Windows", "x86_64", assets[1]),
            ("Windows", "ARM64", assets[2]),
        ]

        for system, machine, expected in cases:
            with (
                self.subTest(system=system, machine=machine),
                mock.patch.object(vkquake.platform, "system", return_value=system),
                mock.patch.object(vkquake.platform, "machine", return_value=machine),
                mock.patch.object(vkquake, "fetch_json", return_value=release) as fetch,
            ):
                self.assertEqual(vkquake.vkquake_release(), ("1.32", expected))
                fetch.assert_called_once_with(config.VKQUAKE_RELEASE_URL)

    def test_unsupported_platform_does_not_fetch_a_release(self):
        """Ensure unsupported architectures do not trigger a release lookup."""
        for system, machine in [("Linux", "aarch64"), ("Windows", "i686"), ("FreeBSD", "amd64")]:
            with (
                self.subTest(system=system, machine=machine),
                mock.patch.object(vkquake.platform, "system", return_value=system),
                mock.patch.object(vkquake.platform, "machine", return_value=machine),
                mock.patch.object(vkquake, "fetch_json") as fetch,
            ):
                self.assertIsNone(vkquake.vkquake_release())
                fetch.assert_not_called()

    def test_missing_platform_asset_reports_the_required_build(self):
        """Confirm the message names the exact build that is missing."""
        with (
            mock.patch.object(vkquake.platform, "system", return_value="Linux"),
            mock.patch.object(vkquake.platform, "machine", return_value="x86_64"),
            mock.patch.object(
                vkquake, "fetch_json", return_value={"tag_name": "1.32", "assets": []}
            ),
            self.assertRaisesRegex(SystemExit, r"1\.32 has no \*-x86_64\.AppImage build"),
        ):
            vkquake.vkquake_release()

    def test_mac_uses_newest_available_dmg_across_pages_and_tag_separators(self):
        """Verify the newest Mac release is picked across pagination and tag formats."""
        older_asset = release_asset("vkQuake-old.dmg")
        newest_asset = release_asset("vkQuake-current.dmg")
        for machine, separator in [("x86_64", "-"), ("arm64", "_")]:
            pages = [
                [
                    {
                        "tag_name": "vkQuake-9.0",
                        "published_at": "2024-01-01T00:00:00Z",
                        "assets": [older_asset]
                    },
                    {
                        "tag_name": "OtherGame-10",
                        "published_at": "2026-01-01T00:00:00Z",
                        "assets": [older_asset]
                    },
                ],
                [
                    {
                        "tag_name": "vkQuake-3.0",
                        "published_at": "2026-01-01T00:00:00Z",
                        "assets": []
                    },
                    {
                        "tag_name": "vkQuake" + separator + "2.0",
                        "published_at": "2025-01-01T00:00:00Z",
                        "assets": [release_asset("source.zip"), newest_asset]
                    },
                ],
                [],
            ]

            with (
                self.subTest(machine=machine, separator=separator),
                mock.patch.object(vkquake.platform, "system", return_value="Darwin"),
                mock.patch.object(vkquake.platform, "machine", return_value=machine),
                mock.patch.object(vkquake, "fetch_json", side_effect=pages) as fetch,
            ):
                self.assertEqual(vkquake.vkquake_release(), ("2.0", newest_asset))
                self.assertEqual(fetch.call_args_list, [
                    mock.call(
                        config.VKQUAKE_MAC_RELEASES_URL + "?per_page=100&page=" + str(page)
                    )
                    for page in (1, 2, 3)
                ])

    def test_mac_missing_dmg_reports_an_error(self):
        """Ensure a missing Mac .dmg raises a clear install error."""
        with (
            mock.patch.object(vkquake.platform, "system", return_value="Darwin"),
            mock.patch.object(vkquake, "fetch_json", return_value=[]),
            self.assertRaisesRegex(SystemExit, "No vkQuake build found on Mac Source Ports"),
        ):
            vkquake.vkquake_release()


class VkQuakeInstallTests(unittest.TestCase):
    """Verify the install steps for each supported vkQuake package format."""

    def setUp(self):
        """Create a fresh temporary install root for each test."""
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.install_dir = self.root / "game files" / "singleplayer"
        stack.enter_context(
            mock.patch.object(vkquake, "INSTALL_SINGLE_PLAYER_DIR", self.install_dir)
        )
        self.output = io.StringIO()
        stack.enter_context(contextlib.redirect_stdout(self.output))

    def test_appimage_replaces_previous_version_and_is_executable(self):
        """Verify AppImage updates replace the prior binary and keep it executable."""
        self.install_dir.mkdir(parents=True)
        destination = self.install_dir / "vkQuake.AppImage"
        destination.write_bytes(b"previous version")
        asset = release_asset("vkQuake-1.32-x86_64.AppImage")
        download_paths = []

        def download(url, path):
            self.assertEqual(url, asset["browser_download_url"])
            self.assertEqual(path.parent.parent, self.install_dir)
            download_paths.append(path)
            path.write_bytes(b"new appimage")

        original_chmod = Path.chmod

        with (
            mock.patch.object(vkquake, "vkquake_release", return_value=("1.32", asset)),
            mock.patch.object(vkquake.platform, "system", return_value="Linux"),
            mock.patch.object(vkquake, "fetch", side_effect=download),
            mock.patch.object(Path, "chmod", autospec=True, side_effect=original_chmod) as chmod,
        ):
            vkquake.install_vkquake()
            chmod.assert_called_once_with(download_paths[0], 0o755)

        self.assertEqual(destination.read_bytes(), b"new appimage")

        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o755)

        self.assertEqual(list(self.install_dir.iterdir()), [destination])

        self.assertEqual(self.output.getvalue(),
                         f"    vkQuake 1.32 ({asset['name']})\n"
                         f"    ✔ Installed {destination}\n"
                         "    Run it with:\n"
                         f'      "{destination}" -basedir "{self.install_dir}"\n')

    def test_windows_zip_installs_game_files_without_debug_symbols(self):
        """Confirm Windows zip installs only supported game files and drops symbols."""
        asset = release_asset("vkQuake-1.32_windows_x64.zip")

        def download(url, path):
            self.assertEqual(url, asset["browser_download_url"])
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("vkQuake.exe", b"executable")
                archive.writestr("vkQuake.pdb", b"symbols")
                archive.writestr("support/config.cfg", b"configuration")
                archive.writestr("support/library.pdb", b"more symbols")

        with (
            mock.patch.object(vkquake, "vkquake_release", return_value=("1.32", asset)),
            mock.patch.object(vkquake.platform, "system", return_value="Windows"),
            mock.patch.object(vkquake, "fetch", side_effect=download),
            mock.patch.object(vkquake, "INSTALL_SINGLE_PLAYER_DIR", self.root / "unused default"),
        ):
            vkquake.install_vkquake(self.install_dir)

        files = {path.relative_to(self.install_dir).as_posix(): path.read_bytes()
                 for path in self.install_dir.rglob("*") if path.is_file()}

        self.assertEqual(
            files,
            {"vkQuake.exe": b"executable", "support/config.cfg": b"configuration"}
        )

        self.assertEqual(
            self.output.getvalue(),
            f"    vkQuake 1.32 ({asset['name']})\n"
            f"    ✔ Installed {self.install_dir / 'vkQuake.exe'}\n"
            "    Run it from its folder; it uses the current directory as the game folder.\n"
        )

    def test_unsupported_platform_skips_installation(self):
        """Unsupported targets should not download or install anything."""
        with  (
            mock.patch.object(vkquake, "vkquake_release", return_value=None),
            mock.patch.object(vkquake, "fetch") as fetch
        ):
            vkquake.install_vkquake()
            fetch.assert_not_called()

        self.assertFalse(self.install_dir.exists())
        self.assertIn("skipping", self.output.getvalue())

    def test_download_failure_keeps_previous_appimage_and_cleans_temporary_files(self):
        """A failed download must leave the previous AppImage intact."""
        self.install_dir.mkdir(parents=True)
        destination = self.install_dir / "vkQuake.AppImage"
        destination.write_bytes(b"working version")

        def fail_download(_url, path):
            path.write_bytes(b"incomplete")
            raise OSError("download interrupted")

        asset = release_asset("vkQuake-1.32-x86_64.AppImage")

        with (
            mock.patch.object(vkquake, "vkquake_release", return_value=("1.32", asset)),
            mock.patch.object(vkquake.platform, "system", return_value="Linux"),
            mock.patch.object(vkquake, "fetch", side_effect=fail_download),
            self.assertRaisesRegex(OSError, "download interrupted"),
        ):
            vkquake.install_vkquake()

        self.assertEqual(destination.read_bytes(), b"working version")
        self.assertEqual(list(self.install_dir.iterdir()), [destination])

    def test_mac_install_mounts_copies_replaces_and_detaches_app(self):
        """Install the DMG app into place and detach it cleanly afterward."""
        destination = self.install_dir / "vkQuake.app"
        destination.mkdir(parents=True)
        (destination / "obsolete").write_text("old bundle")
        asset = release_asset("vkQuake.dmg")

        def run(command, **_kwargs):
            if command[:2] == ["hdiutil", "attach"]:
                app = command[-2] / "vkQuake.app"
                app.mkdir()
                (app / "binary").write_bytes(b"new bundle")
                self.assertTrue(command[-1].is_file())
            elif command[0] == "ditto":
                self.assertFalse(destination.exists())
                shutil.copytree(command[1], command[2])
            return subprocess.CompletedProcess(command, 0)

        with (
            mock.patch.object(vkquake, "vkquake_release", return_value=("1.32", asset)),
            mock.patch.object(vkquake.platform, "system", return_value="Darwin"),
            mock.patch.object(
                vkquake, "fetch", side_effect=lambda url, path: path.write_bytes(b"disk image")
            ) as fetch,
            mock.patch.object(vkquake.subprocess, "run", side_effect=run) as commands,
            mock.patch.object(vkquake, "INSTALL_SINGLE_PLAYER_DIR", self.root / "unused default"),
        ):
            vkquake.install_vkquake(self.install_dir)
        image = fetch.call_args.args[1]
        mountpoint = commands.call_args_list[0].args[0][-2]
        self.assertEqual(commands.call_args_list, [
            mock.call(
                ["hdiutil", "attach", "-nobrowse", "-readonly", "-mountpoint", mountpoint, image],
                check=True,
                stdout=subprocess.DEVNULL
            ),
            mock.call(["ditto", mountpoint / "vkQuake.app", destination], check=True),
            mock.call(["hdiutil", "detach", mountpoint], check=False, stdout=subprocess.DEVNULL),
        ])
        self.assertEqual((destination / "binary").read_bytes(), b"new bundle")
        self.assertFalse((destination / "obsolete").exists())
        self.assertFalse(mountpoint.parent.exists())
        self.assertFalse(image.parent.exists())
        self.assertEqual(list(self.install_dir.iterdir()), [destination])
        self.assertEqual(self.output.getvalue(),
                         f"    vkQuake 1.32 ({asset['name']})\n"
                         f"    ✔ Installed {destination}\n"
                         "    Run it by opening vkQuake.app; it uses the folder it is in.\n")

    def test_mac_copy_failure_still_detaches_and_cleans_mount_directory(self):
        """Even a copy failure should clean up the mounted DMG directory."""
        self.install_dir.mkdir(parents=True)
        image = self.root / "vkQuake.dmg"
        image.write_bytes(b"disk image")

        def run(command, **_kwargs):
            if command[:2] == ["hdiutil", "attach"]:
                (command[-2] / "vkQuake.app").mkdir()
            elif command[0] == "ditto":
                raise subprocess.CalledProcessError(1, command)
            return subprocess.CompletedProcess(command, 0)

        with (
            mock.patch.object(vkquake.subprocess, "run", side_effect=run) as commands,
            self.assertRaises(subprocess.CalledProcessError)
        ):
            vkquake.install_vkquake_mac(image)

        mountpoint = commands.call_args_list[0].args[0][-2]

        self.assertEqual(commands.call_count, 3)

        self.assertEqual(commands.call_args, mock.call(
            ["hdiutil", "detach", mountpoint], check=False, stdout=subprocess.DEVNULL
        ))

        self.assertFalse(mountpoint.parent.exists())


class EzQuakeReleaseTests(unittest.TestCase):
    """Verify ezQuake release selection for supported platforms."""

    def test_selects_asset(self):
        """Verify the supported ezQuake asset."""
        assets = [
            release_asset("ezQuake-linux-x86_64.zip"),
            release_asset("ezQuake-macOS-universal.zip"),
            release_asset("ezQuake-windows-x64.zip"),
        ]

        release = {"tag_name": "3.6.9", "assets": assets}

        cases = [
            ("Linux", "x86_64", assets[0]),
            ("Linux", "AMD64", assets[0]),
            ("Darwin", "arm64", assets[1]),
            ("Darwin", "x86_64", assets[1]),
            ("Windows", "AMD64", assets[2]),
            ("Windows", "x86_64", assets[2]),
        ]

        for system, machine, expected in cases:
            with (
                self.subTest(system=system, machine=machine),
                mock.patch.object(ezquake.platform, "system", return_value=system),
                mock.patch.object(ezquake.platform, "machine", return_value=machine),
                mock.patch.object(ezquake, "fetch_json", return_value=release) as fetch,
            ):
                self.assertEqual(ezquake.ezquake_release(), ("3.6.9", expected))
                fetch.assert_called_once_with(config.EZQUAKE_RELEASE_URL)


    def test_unsupported_platform_does_not_fetch_a_release(self):
        """Ensure unsupported architectures do not trigger a release lookup."""
        for system, machine in [("Linux", "aarch64"), ("Windows", "i686"), ("FreeBSD", "amd64")]:
            with (
                self.subTest(system=system, machine=machine),
                mock.patch.object(ezquake.platform, "system", return_value=system),
                mock.patch.object(ezquake.platform, "machine", return_value=machine),
                mock.patch.object(ezquake, "fetch_json") as fetch,
            ):
                self.assertIsNone(ezquake.ezquake_release())
                fetch.assert_not_called()


    def test_missing_platform_asset_reports_the_required_build(self):
        """Confirm the message names the exact build that is missing."""
        with (
            mock.patch.object(ezquake.platform, "system", return_value="Linux"),
            mock.patch.object(ezquake.platform, "machine", return_value="x86_64"),
            mock.patch.object(
                ezquake, "fetch_json", return_value={"tag_name": "3.6.9", "assets": []}
            ),
            self.assertRaisesRegex(SystemExit, r"3\.6\.9 has no \*linux-x86_64\.zip build"),
        ):
            ezquake.ezquake_release()


    def test_mac_missing_dmg_reports_an_error(self):
        """Ensure a missing Mac .dmg raises a clear install error."""
        with (
            mock.patch.object(vkquake.platform, "system", return_value="Darwin"),
            mock.patch.object(vkquake, "fetch_json", return_value=[]),
            self.assertRaisesRegex(SystemExit, "No vkQuake build found on Mac Source Ports"),
        ):
            vkquake.vkquake_release()


class EzQuakeInstallTests(unittest.TestCase):
    """Verify the install steps for each supported ezQuake package format."""

    def setUp(self):
        """Create a fresh temporary install root for each test."""
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.install_dir = self.root / "game files" / "multiplayer"
        stack.enter_context(
            mock.patch.object(ezquake, "INSTALL_MULTIPLAYER_DIR", self.install_dir)
        )
        self.output = io.StringIO()
        stack.enter_context(contextlib.redirect_stdout(self.output))


    def test_appimage_replaces_previous_version_and_is_executable(self):
        """Verify AppImage updates replace the prior binary and keep it executable."""
        self.install_dir.mkdir(parents=True)
        destination = self.install_dir / "ezQuake-x86_64.AppImage"
        destination.write_bytes(b"previous version")
        asset = release_asset("ezQuake-linux-x86_64.zip")
        download_paths = []

        def download(url, path):
            self.assertEqual(url, asset["browser_download_url"])
            self.assertEqual(path.parent.parent, self.install_dir)
            download_paths.append(path)
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("ezQuake-x86_64.AppImage", b"new appimage")

        original_chmod = Path.chmod

        with (
            mock.patch.object(ezquake, "ezquake_release", return_value=("3.6.9", asset)),
            mock.patch.object(ezquake.platform, "system", return_value="Linux"),
            mock.patch.object(ezquake, "fetch", side_effect=download),
            mock.patch.object(Path, "chmod", autospec=True, side_effect=original_chmod) as chmod,
        ):
            ezquake.install_ezquake()
            chmod.assert_called_once_with(
                download_paths[0].parent / "ezQuake-x86_64.AppImage", 0o755
            )

        self.assertEqual(destination.read_bytes(), b"new appimage")

        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o755)

        self.assertEqual(list(self.install_dir.iterdir()), [destination])

        self.assertEqual(self.output.getvalue(),
                         f"    ezQuake 3.6.9 ({asset['name']})\n"
                         f"    ✔ Installed {destination}\n"
                         "    Run it with:\n"
                         f'      "{destination}" -basedir "{self.install_dir}"\n')


    def test_windows_zip_installs_game_files_without_debug_symbols(self):
        """Confirm Windows zip installs only supported game files and drops symbols."""
        asset = release_asset("ezQuake-windows-x64.zip")

        def download(url, path):
            self.assertEqual(url, asset["browser_download_url"])
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("ezQuake.exe", b"executable")
                archive.writestr("ezQuake.pdb", b"symbols")
                archive.writestr("support/config.cfg", b"configuration")
                archive.writestr("support/library.pdb", b"more symbols")

        with (
            mock.patch.object(ezquake, "ezquake_release", return_value=("3.6.9", asset)),
            mock.patch.object(ezquake.platform, "system", return_value="Windows"),
            mock.patch.object(ezquake, "fetch", side_effect=download),
            mock.patch.object(ezquake, "INSTALL_MULTIPLAYER_DIR", self.root / "unused default"),
        ):
            ezquake.install_ezquake(self.install_dir)

        files = {path.relative_to(self.install_dir).as_posix(): path.read_bytes()
                 for path in self.install_dir.rglob("*") if path.is_file()}

        self.assertEqual(
            files,
            {"ezQuake.exe": b"executable", "support/config.cfg": b"configuration"}
        )

        self.assertEqual(
            self.output.getvalue(),
            f"    ezQuake 3.6.9 ({asset['name']})\n"
            f"    ✔ Installed {self.install_dir / 'ezQuake.exe'}\n"
            "    Run it from its folder; it uses the current directory as the game folder.\n"
        )


    def test_unsupported_platform_skips_installation(self):
        """Unsupported targets should not download or install anything."""
        with  (
            mock.patch.object(ezquake, "ezquake_release", return_value=None),
            mock.patch.object(ezquake, "fetch") as fetch
        ):
            ezquake.install_ezquake()
            fetch.assert_not_called()

        self.assertFalse(self.install_dir.exists())
        self.assertIn("skipping", self.output.getvalue())


    def test_download_failure_keeps_previous_appimage_and_cleans_temporary_files(self):
        """A failed download must leave the previous AppImage intact."""
        self.install_dir.mkdir(parents=True)
        destination = self.install_dir / "ezQuake-x86_64.AppImage"
        destination.write_bytes(b"working version")

        def fail_download(_url, path):
            path.write_bytes(b"incomplete")
            raise OSError("download interrupted")

        asset = release_asset("ezQuake-linux-x86_64.zip")

        with (
            mock.patch.object(ezquake, "ezquake_release", return_value=("3.6.9", asset)),
            mock.patch.object(ezquake.platform, "system", return_value="Linux"),
            mock.patch.object(ezquake, "fetch", side_effect=fail_download),
            self.assertRaisesRegex(OSError, "download interrupted"),
        ):
            ezquake.install_ezquake()

        self.assertEqual(destination.read_bytes(), b"working version")
        self.assertEqual(list(self.install_dir.iterdir()), [destination])


    def test_mac_zip_replaces_app_and_cleans_temporary_files(self):
        """Extract a real ZIP through ditto before replacing the installed app."""
        destination = self.install_dir / "ezQuake.app"
        destination.mkdir(parents=True)
        (destination / "obsolete").write_text("old bundle")
        asset = release_asset("ezQuake-macOS-universal.zip")

        def download(_url, path):
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("ezQuake.app/Contents/MacOS/ezQuake", b"new bundle")

        def run(command, **_kwargs):
            if command[:3] == ["ditto", "-x", "-k"]:
                with zipfile.ZipFile(command[3]) as archive:
                    archive.extractall(command[4])
            else:
                self.assertFalse(destination.exists())
                shutil.copytree(command[1], command[2])
            return subprocess.CompletedProcess(command, 0)

        with (
            mock.patch.object(ezquake, "ezquake_release", return_value=("3.6.9", asset)),
            mock.patch.object(ezquake.platform, "system", return_value="Darwin"),
            mock.patch.object(ezquake, "fetch", side_effect=download) as fetch,
            mock.patch.object(ezquake.subprocess, "run", side_effect=run) as commands,
            mock.patch.object(ezquake, "INSTALL_MULTIPLAYER_DIR", self.root / "unused default"),
        ):
            ezquake.install_ezquake(self.install_dir)
        download = fetch.call_args.args[1]
        extracted = commands.call_args_list[0].args[0][-1]
        self.assertEqual(commands.call_args_list, [
            mock.call(["ditto", "-x", "-k", download, extracted], check=True),
            mock.call(["ditto", extracted / "ezQuake.app", destination], check=True),
        ])
        self.assertEqual((destination / "Contents/MacOS/ezQuake").read_bytes(), b"new bundle")
        self.assertFalse((destination / "obsolete").exists())
        self.assertFalse(extracted.exists())
        self.assertFalse(download.parent.exists())
        self.assertEqual(list(self.install_dir.iterdir()), [destination])
        self.assertIn(f"✔ Installed {destination}", self.output.getvalue())

    def test_mac_extraction_failure_preserves_previous_app_and_cleans_temporary_files(self):
        """A damaged ZIP must not remove the working installation."""
        destination = self.install_dir / "ezQuake.app"
        destination.mkdir(parents=True)
        (destination / "binary").write_bytes(b"working version")
        download = self.root / "ezQuake.zip"
        download.write_bytes(b"invalid zip")

        with (
            mock.patch.object(ezquake.subprocess, "run", side_effect=
                              subprocess.CalledProcessError(1, "ditto")) as commands,
            self.assertRaises(subprocess.CalledProcessError),
        ):
            ezquake.install_ezquake_mac(download)

        extracted = commands.call_args.args[0][-1]
        self.assertEqual(commands.call_count, 1)
        self.assertFalse(extracted.exists())
        self.assertEqual((destination / "binary").read_bytes(), b"working version")

    def test_mac_zip_without_app_preserves_previous_installation(self):
        """Reject an unexpected archive before replacing the existing app."""
        destination = self.install_dir / "ezQuake.app"
        destination.mkdir(parents=True)
        (destination / "binary").write_bytes(b"working version")
        with (
            mock.patch.object(ezquake.subprocess, "run") as commands,
            self.assertRaisesRegex(OSError, "ezQuake.app not found"),
        ):
            ezquake.install_ezquake_mac(self.root / "ezQuake.zip")
        self.assertEqual(commands.call_count, 1)
        self.assertFalse(commands.call_args.args[0][-1].exists())
        self.assertEqual((destination / "binary").read_bytes(), b"working version")

    def test_invalid_linux_zip_preserves_previous_appimage(self):
        """Neither corrupt ZIPs nor missing binaries replace the working engine."""
        self.install_dir.mkdir(parents=True)
        destination = self.install_dir / "ezQuake-x86_64.AppImage"
        destination.write_bytes(b"working version")
        asset = release_asset("ezQuake-linux-x86_64.zip")

        for corrupt in (True, False):
            def download(_url, path, corrupt=corrupt):
                if corrupt:
                    path.write_bytes(b"invalid zip")
                else:
                    with zipfile.ZipFile(path, "w") as archive:
                        archive.writestr("README.txt", "no engine")

            with (
                self.subTest(corrupt=corrupt),
                mock.patch.object(ezquake, "ezquake_release", return_value=("3.6.9", asset)),
                mock.patch.object(ezquake.platform, "system", return_value="Linux"),
                mock.patch.object(ezquake, "fetch", side_effect=download),
                self.assertRaises(zipfile.BadZipFile if corrupt else KeyError),
            ):
                ezquake.install_ezquake()
            self.assertEqual(destination.read_bytes(), b"working version")
            self.assertEqual(list(self.install_dir.iterdir()), [destination])


if __name__ == "__main__":
    unittest.main()
