"""Validate portable archives and prevent publishing an unverified build."""

import subprocess
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import build


class BuildScriptsTests(unittest.TestCase):
    def test_windows_verification_uses_uppercase_environment_keys(self):
        environment = {
            "SYSTEMROOT": r"D:\Windows",
            "PATH": r"C:\Python313;C:\build-tools",
            "PYTHONHOME": r"C:\Python313",
            "PYTHONPATH": r"C:\checkout",
            "VIRTUAL_ENV": r"C:\build-env",
        }
        windows_os = SimpleNamespace(name="nt", pathsep=";", environ=environment)

        def render_preview(command, *, cwd, env, check, timeout):
            self.assertEqual(Path(command[0]).read_bytes(), b"synthetic executable")
            self.assertEqual(command[1:], ["--smoke-test", "--screenshot", "preview.png"])
            self.assertEqual(env["SYSTEMROOT"], r"D:\Windows")
            self.assertEqual(
                env["PATH"], str(Path(r"D:\Windows") / "System32") + r";D:\Windows"
            )
            self.assertEqual(env["QT_QPA_PLATFORM"], "offscreen")
            for key in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
                self.assertNotIn(key, env)
            (Path(cwd) / "preview.png").write_bytes(b"synthetic preview")

        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "QuakeBundle.exe"
            executable.write_bytes(b"synthetic executable")
            with (
                patch.object(build, "os", windows_os),
                patch.object(build.subprocess, "run", side_effect=render_preview) as run,
            ):
                build.verify(executable)
            run.assert_called_once()
        self.assertEqual(environment["PATH"], r"C:\Python313;C:\build-tools")

    def test_archives_contain_only_the_executable(self):
        with tempfile.TemporaryDirectory(prefix="build package ") as directory:
            for system in ("linux", "windows", "macos"):
                with self.subTest(system=system):
                    executable = Path(directory) / (
                        "QuakeBundle.exe" if system == "windows" else "QuakeBundle"
                    )
                    executable.write_bytes(b"synthetic executable")
                    # Packaging must supply executable permissions even when the
                    # host filesystem cannot represent POSIX execute bits.
                    executable.chmod(0o666)
                    archive = build.archive(executable, system, "x64")
                    if system == "linux":
                        with tarfile.open(archive) as bundle:
                            self.assertEqual(bundle.getnames(), [executable.name])
                            self.assertEqual(
                                bundle.extractfile(executable.name).read(),
                                executable.read_bytes(),
                            )
                            self.assertEqual(
                                bundle.getmember(executable.name).mode & 0o777, 0o755
                            )
                    else:
                        with zipfile.ZipFile(archive) as bundle:
                            self.assertEqual(bundle.namelist(), [executable.name])
                            self.assertEqual(
                                bundle.read(executable.name), executable.read_bytes()
                            )

    def test_failed_smoke_test_does_not_publish_package(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "dist").mkdir()
            (root / "dist/QuakeBundle").write_bytes(b"binary")
            with (
                patch.object(build, "ROOT", root),
                patch.object(build.platform, "system", return_value="Linux"),
                patch.object(build.platform, "machine", return_value="x86_64"),
                patch.object(build.subprocess, "run"),
                patch.object(
                    build, "verify", side_effect=RuntimeError("failed smoke test")
                ),
                patch.object(build, "archive") as archive,
                self.assertRaisesRegex(RuntimeError, "failed smoke test"),
            ):
                build.main(["--platform", "linux"])
            archive.assert_not_called()

    def test_failed_compilation_does_not_verify_an_old_binary(self):
        with (
            patch.object(build.platform, "system", return_value="Windows"),
            patch.object(build.platform, "machine", return_value="AMD64"),
            patch.object(
                build.subprocess,
                "run",
                side_effect=subprocess.CalledProcessError(1, "build"),
            ),
            patch.object(build, "verify") as verify,
            self.assertRaises(subprocess.CalledProcessError),
        ):
            build.main(["--platform", "windows"])
        verify.assert_not_called()

    def test_target_os_mismatch_stops_before_building(self):
        with (
            patch.object(build.platform, "system", return_value="Linux"),
            patch.object(build.subprocess, "run") as run,
            self.assertRaises(SystemExit),
        ):
            build.main(["--platform", "windows"])
        run.assert_not_called()
