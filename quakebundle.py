#!/usr/bin/env python3
"""Start the Qt6 application, from source or from its self-contained bundle."""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Check the UI and bundled assets, then exit",
    )
    parser.add_argument(
        "--screenshot", type=Path, help="Save a preview during --smoke-test"
    )
    args = parser.parse_args(argv)

    if sys.version_info < (3, 10):  # noqa: UP036 - source may be launched with an older system Python.
        raise RuntimeError("QuakeBundle requires Python 3.10 or newer.")

    from PySide6.QtCore import QLockFile, QSettings, QTemporaryDir, QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox

    from quake_gui.library import ASSETS, EDITIONS, configure, read_player
    from quake_gui.window import MainWindow
    from quake_installer.config import BASE_DIR

    app = QApplication([sys.argv[0]])
    app.setApplicationName("QuakeBundle")
    app.setOrganizationName("QuakeBundle")
    app.setStyle("Fusion")
    temporary = None
    lock = None
    settings = None
    if args.smoke_test:
        temporary = QTemporaryDir()
        settings = QSettings(
            str(Path(temporary.path()) / "preview.ini"), QSettings.Format.IniFormat
        )
        for key in EDITIONS:
            settings.setValue(f"directory/{key}", str(Path(temporary.path()) / key))
    else:
        # Both editions share SteamCMD and its download directory.
        BASE_DIR.mkdir(parents=True, exist_ok=True)
        lock = QLockFile(str(BASE_DIR / "quakebundle.lock"))
        lock.setStaleLockTime(0)
        if not lock.tryLock(100):
            QMessageBox.information(
                None,
                "QuakeBundle",
                "QuakeBundle is already open. Use the existing window.",
            )
            return 1
    window = MainWindow(settings)
    window.show()
    if args.smoke_test:

        def check():
            try:
                import certifi

                assert Path(certifi.where()).is_file(), (
                    "Missing bundled TLS certificates"
                )
                if sys.platform == "win32":
                    # Exercise the bundled native extension from the windowed
                    # executable, without relying on an external Python.
                    from quake_installer.windows_console import smoke_test

                    smoke_test(temporary.path())
                for key, edition in EDITIONS.items():
                    window.select_edition(key)
                    assert (ASSETS / f"icons/{edition.engine}.png").is_file()
                    for filename in edition.launcher.CONFIG_FILES:
                        assert (ASSETS / "cfg" / edition.engine / filename).is_file()
                    root = Path(temporary.path()) / key
                    configure(edition, root, "Ranger", 3)
                    assert read_player(edition, root)["name"] == "Ranger"
                window.select_edition("singleplayer")
                if args.screenshot:
                    args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                    assert window.grab().save(str(args.screenshot))
            except Exception as error:  # noqa: BLE001 - convert Qt callback failures to a failing process status.
                if sys.stderr:
                    print(error, file=sys.stderr)
                app.exit(1)
            else:
                app.exit(0)

        QTimer.singleShot(250, check)
    result = app.exec()
    if lock is not None:
        lock.unlock()
    return result


def run(argv=None):
    """Report startup failures even when Windows uses pythonw (no console)."""
    try:
        return main(argv)
    except Exception as error:  # noqa: BLE001 - surface startup failures when no console exists.
        message = "QuakeBundle could not start.\n\n" + traceback.format_exc()
        if isinstance(error, ImportError) and not getattr(sys, "frozen", False):
            message += (
                "\nThis is the source code. To use QuakeBundle without installing "
                "Python or Qt, extract the standalone package for your system "
                "and open QuakeBundle.exe (Windows) or QuakeBundle (Linux/macOS).\n"
            )
        if sys.stderr is not None:
            print(message, file=sys.stderr)
        elif sys.platform == "win32":
            # Qt itself may have failed to import, so use a native dialog.
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, "QuakeBundle", 0x10)
        return 1


if __name__ == "__main__":
    raise SystemExit(run())
