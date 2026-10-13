"""Exercise SteamCMD installation and native process capture without Steam."""

import io
import os
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from quakebundle import steamcmd


def steamcmd_archive(system, filename):
    """Build the same archive formats as Valve, with a harmless payload."""
    payload = b"synthetic steamcmd executable\n"
    buffer = io.BytesIO()
    if system == "Windows":
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(filename, payload)
    else:
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            entry = tarfile.TarInfo(filename)
            entry.size = len(payload)
            entry.mode = 0o644
            archive.addfile(entry, io.BytesIO(payload))
    return buffer.getvalue(), payload


class SteamcmdInstallationTests(unittest.TestCase):
    """Exercise SteamCMD installation and archive extraction behavior."""

    def setUp(self):
        """Create a temporary workspace for each test."""
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.root = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="quake steamcmd ")))

    def test_installs_platform_archives_and_reuses_existing_executable(self):
        """Install a platform archive once and reuse the extracted executable."""
        for system in ("Linux", "Darwin", "Windows"):
            with self.subTest(system=system):
                directory = self.root / system / "steamcmd"
                filename = "steamcmd.exe" if system == "Windows" else "steamcmd.sh"
                archive, payload = steamcmd_archive(system, filename)

                def download(_url, destination, archive=archive):
                    destination.write_bytes(archive)

                with patch.object(steamcmd, "STEAMCMD_DIR", directory), \
                     patch.object(steamcmd.platform, "system", return_value=system), \
                     patch.object(steamcmd, "fetch", side_effect=download) as fetch, \
                     redirect_stdout(io.StringIO()):
                    executable = steamcmd.install_steamcmd()
                    self.assertEqual(executable, directory / filename)
                    self.assertEqual(executable.read_bytes(), payload)
                    self.assertEqual(fetch.call_count, 1)
                    self.assertEqual(fetch.call_args.args[0], steamcmd.STEAMCMD_URLS[system])
                    if os.name == "posix" and system != "Windows":
                        self.assertEqual(stat.S_IMODE(executable.stat().st_mode), 0o755)

                    self.assertEqual(steamcmd.install_steamcmd(), executable)
                    self.assertEqual(fetch.call_count, 1)

    def test_rejects_unsupported_platform_without_downloading(self):
        """Reject unsupported OS targets before attempting a download."""
        with patch.object(steamcmd, "STEAMCMD_DIR", self.root / "steamcmd"), \
             patch.object(steamcmd.platform, "system", return_value="Plan9"), \
             patch.object(steamcmd, "fetch") as fetch:
            with self.assertRaisesRegex(SystemExit, "Unsupported operating system: Plan9"):
                steamcmd.install_steamcmd()
            fetch.assert_not_called()

    def test_rejects_archive_without_expected_executable(self):
        """Fail if the archive does not contain the expected SteamCMD executable."""
        for system in ("Linux", "Darwin", "Windows"):
            with self.subTest(system=system):
                archive, _ = steamcmd_archive(system, "README.txt")
                with (
                    patch.object(steamcmd, "STEAMCMD_DIR", self.root / system),
                    patch.object(steamcmd.platform, "system", return_value=system),
                    patch.object(
                        steamcmd,
                        "fetch",
                        side_effect=lambda _url, path, archive=archive: path.write_bytes(archive),
                    ),
                    redirect_stdout(io.StringIO()),
                    self.assertRaisesRegex(
                        SystemExit,
                        "not found after extracting steamcmd",
                    ),
                ):
                    steamcmd.install_steamcmd()


class RosettaTests(unittest.TestCase):
    """Exercise Rosetta detection and installation guidance for Apple Silicon."""

    def test_apple_silicon_probes_rosetta(self):
        """Probe Rosetta on Apple Silicon systems when needed."""
        with (
            patch.object(steamcmd.platform, "system", return_value="Darwin"),
            patch.object(steamcmd.platform, "machine", return_value="arm64"),
            patch.object(
                steamcmd.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0)
            ) as run
        ):
            steamcmd.check_rosetta()
            run.assert_called_once_with(
                ["arch", "-x86_64", "/usr/bin/true"],
                check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )

    def test_missing_rosetta_reports_install_command(self):
        """Explain how to install Rosetta when it is missing."""
        with (
            patch.object(steamcmd.platform, "system", return_value="Darwin"),
            patch.object(steamcmd.platform, "machine", return_value="arm64"),
            patch.object(
                steamcmd.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 1),
            ),
            self.assertRaisesRegex(SystemExit, "softwareupdate --install-rosetta"),
        ):
            steamcmd.check_rosetta()

    def test_other_platforms_do_not_probe_rosetta(self):
        """Skip Rosetta checks on non-Apple Silicon systems."""
        for system, machine in (("Darwin", "x86_64"), ("Linux", "aarch64"), ("Windows", "AMD64")):
            with self.subTest(system=system, machine=machine), \
                 patch.object(steamcmd.platform, "system", return_value=system), \
                 patch.object(steamcmd.platform, "machine", return_value=machine), \
                 patch.object(steamcmd.subprocess, "run") as run:
                steamcmd.check_rosetta()
                run.assert_not_called()


class CapturedProcessTests(unittest.TestCase):
    """Exercise native process capture and output tail handling."""

    def setUp(self):
        """Create a temporary directory and patch the SteamCMD install path."""
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.directory = Path(stack.enter_context(
            tempfile.TemporaryDirectory(prefix="quake capture ")
        ))
        stack.enter_context(patch.object(steamcmd, "STEAMCMD_DIR", self.directory))

    def test_native_capture_combines_streams_strips_ansi_and_keeps_tail(self):
        """Capture stdout/stderr, strip ANSI codes, and retain the tail."""
        count = steamcmd.TAIL_LINES + 5
        code = (
            "import os, sys\n"
            "assert os.path.samefile(os.getcwd(), sys.argv[1])\n"
            "assert sys.stdin.read() == ''\n"
            "print('\\x1b[32mstdout\\x1b[0m', flush=True)\n"
            "print('stderr', file=sys.stderr, flush=True)\n"
            "print('  \\n', flush=True)\n"
            f"for index in range({count}): print('line ' + str(index), flush=True)\n"
            "sys.stdout.write('final unterminated line')\n"
            "sys.stdout.flush()\n"
            "sys.exit(7)\n"
        )
        lines = []
        returncode, tail = steamcmd.run_captured(
            [sys.executable, "-u", "-c", code, str(self.directory)], lines.append
        )
        expected = ["stdout", "stderr"] + [f"line {index}" for index in range(count)]
        expected.append("final unterminated line")
        self.assertEqual(returncode, 7)
        self.assertEqual(lines, expected)
        self.assertEqual(tail, expected[-steamcmd.TAIL_LINES:])

    def test_successful_process_with_no_output(self):
        """Return an empty tail when a process exits cleanly without emitting output."""
        lines = []
        self.assertEqual(
            steamcmd.run_captured([sys.executable, "-c", "pass"], lines.append),
            (0, []),
        )
        self.assertEqual(lines, [])

    def test_pipe_capture_keeps_carriage_returns_within_lines(self):
        """Preserve carriage returns safely when splitting a Windows-style pipe stream."""
        code = (
            "import os\n"
            "os.write(1, b'first\\rsecond\\nthird\\r\\n')\n"
            "os.write(2, b'stderr\\n')\n"
            "os.write(1, b'last')\n"
        )
        lines = []
        with patch.object(steamcmd, "os") as system_os:
            system_os.name = "nt"
            result = steamcmd.run_captured([sys.executable, "-c", code], lines.append)
        expected = ["first\rsecond", "third", "stderr", "last"]
        self.assertEqual(lines, expected)
        self.assertEqual(result, (0, expected))

    @unittest.skipUnless(os.name == "posix", "PTY capture is only available on POSIX")
    def test_pty_capture_splits_carriage_returns_and_newlines(self):
        """Split carriage returns and line breaks correctly in PTY-captured output."""
        code = "import os; os.write(1, b'first\\rsecond\\nthird\\r\\nlast')"
        lines = []
        result = steamcmd.run_captured([sys.executable, "-c", code], lines.append)
        expected = ["first", "second", "third", "last"]
        self.assertEqual(lines, expected)
        self.assertEqual(result, (0, expected))


if __name__ == "__main__":
    unittest.main()
