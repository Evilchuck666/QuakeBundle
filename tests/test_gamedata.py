"""Cover download progress with visible and disabled progress bars."""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tqdm import tqdm

from quake_installer import gamedata


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
