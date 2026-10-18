"""Build, verify and archive the standalone application on the current OS."""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SYSTEMS = {"Linux": "linux", "Darwin": "macos", "Windows": "windows"}


def verify(executable):
    """Run only the copied executable, outside the checkout and build environment."""
    with tempfile.TemporaryDirectory(prefix="QuakeBundle standalone ") as directory:
        directory = Path(directory)
        copied = directory / executable.name
        shutil.copy2(executable, copied)
        environment = os.environ.copy()
        for key in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
            environment.pop(key, None)
        environment["QT_QPA_PLATFORM"] = "offscreen"
        if os.name == "nt":
            windows = environment["SystemRoot"]
            environment["PATH"] = os.pathsep.join(
                (str(Path(windows) / "System32"), windows)
            )
        subprocess.run(
            [str(copied), "--smoke-test", "--screenshot", "preview.png"],
            cwd=directory,
            env=environment,
            check=True,
            timeout=120,
        )
        preview = directory / "preview.png"
        if not preview.is_file() or preview.stat().st_size == 0:
            raise RuntimeError("The standalone application did not render its window.")


def archive(executable, system, architecture):
    """Publish an archive only after it is complete; keep the executable at its root."""
    suffix = ".tar.gz" if system == "linux" else ".zip"
    destination = executable.parent / f"QuakeBundle-{system}-{architecture}{suffix}"
    with tempfile.TemporaryDirectory(dir=executable.parent) as temporary:
        staged = Path(temporary) / destination.name
        if system == "linux":
            with tarfile.open(staged, "w:gz") as bundle:
                bundle.add(executable, arcname=executable.name)
        else:
            with zipfile.ZipFile(
                staged, "w", compression=zipfile.ZIP_DEFLATED
            ) as bundle:
                bundle.write(executable, arcname=executable.name)
        staged.replace(destination)
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", required=True, choices=SYSTEMS.values())
    args = parser.parse_args(argv)
    system = SYSTEMS.get(platform.system())
    if args.platform != system:
        parser.error(
            "Build on the target operating system; cross-compilation is not supported."
        )
    machine = platform.machine().lower()
    architecture = {
        "x86_64": "x64",
        "amd64": "x64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }.get(machine)
    if architecture is None:
        parser.error(f"Unsupported architecture: {machine}")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--clean",
            "--noconfirm",
            "scripts/QuakeBundle.spec",
        ],
        cwd=ROOT,
        check=True,
    )
    executable = (
        ROOT / "dist" / ("QuakeBundle.exe" if system == "windows" else "QuakeBundle")
    )
    if not executable.is_file():
        raise RuntimeError(f"Missing standalone executable: {executable}")
    verify(executable)
    package = archive(executable, system, architecture)
    print(f"\nExecutable: {executable}\nVerified package: {package}")


if __name__ == "__main__":
    main()
