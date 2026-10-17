"""Check cleanup specific to the multiplayer installer."""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import multiplayer
import test_singleplayer


class MultiplayerCommandLineTests(test_singleplayer.CommandLineTests):
    """Exercise the multiplayer CLI with the same options as single-player."""

    installer = multiplayer


class MultiplayerCleanupTests(unittest.TestCase):
    """Remove only the requested ezQuake files, including on repeated cleanup."""

    def test_cleanup_removes_configs_and_documents_and_preserves_other_files(self):
        """Remove ezQuake configs and documents while preserving other files."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "multiplayer"
            removed = (
                "ezquake/configs/player.cfg",
                "ezquake/configs/nested/default.cfg",
                "ezquake/gnu.txt",
                "ezquake/Online Manual.url",
            )
            preserved = (
                "ezquake/ezquake.pk3", "ezquake/other.cfg",
                "qw/config.cfg", "id1/pak0.pak", "launcher.py",
                "configs/player.cfg", "gnu.txt", "Online Manual.url",
            )
            for name in removed + preserved:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"keep contents")

            with (
                patch.object(multiplayer, "INSTALL_MULTIPLAYER_DIR", root),
                patch.object(multiplayer, "_clean_up_downloads") as downloads,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                for _ in range(2):
                    multiplayer.clean_up()
                    self.assertFalse((root / "ezquake/configs").exists())
                    for name in removed:
                        self.assertFalse((root / name).exists())
                    for name in preserved:
                        self.assertEqual((root / name).read_bytes(), b"keep contents")
                self.assertEqual(downloads.call_count, 2)

    def test_cleanup_without_ezquake_does_not_create_directories(self):
        """Leave the installation directory absent when ezQuake is missing."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "multiplayer"
            with (
                patch.object(multiplayer, "INSTALL_MULTIPLAYER_DIR", root),
                patch.object(multiplayer, "_clean_up_downloads") as downloads,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                multiplayer.clean_up()
            downloads.assert_called_once_with()
            self.assertFalse(root.exists())
