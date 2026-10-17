"""Keep a frozen Python/Qt runtime out of external native executables."""

from __future__ import annotations

import ctypes
import os
import sys
from contextlib import contextmanager


def external_environment():
    environment = os.environ.copy()
    for key in ("LD_LIBRARY_PATH", "LIBPATH"):
        if key + "_ORIG" not in environment and not getattr(sys, "frozen", False):
            continue
        original = environment.pop(key + "_ORIG", None)
        environment.pop(key, None)
        if original is not None:
            environment[key] = original
    for key in ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QML2_IMPORT_PATH"):
        environment.pop(key, None)
    return environment


@contextmanager
def external_libraries():
    """Windows children inherit SetDllDirectory, independently of their env."""
    frozen_windows = os.name == "nt" and getattr(sys, "frozen", False)
    if frozen_windows:
        ctypes.windll.kernel32.SetDllDirectoryW(None)
    try:
        yield
    finally:
        if frozen_windows:
            ctypes.windll.kernel32.SetDllDirectoryW(sys._MEIPASS)
