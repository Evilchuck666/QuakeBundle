"""Fake SteamCMD that refuses pipes and uses actual Windows console APIs."""

import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

kernel = ctypes.WinDLL("kernel32", use_last_error=True)
kernel.GetStdHandle.argtypes = [wintypes.DWORD]
kernel.GetStdHandle.restype = wintypes.HANDLE
kernel.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
kernel.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel.WriteConsoleW.argtypes = [
    wintypes.HANDLE,
    wintypes.LPCWSTR,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    wintypes.LPVOID,
]
kernel.ReadConsoleW.argtypes = [
    wintypes.HANDLE,
    wintypes.LPVOID,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    wintypes.LPVOID,
]
stdin = kernel.GetStdHandle(-10)
stdout = kernel.GetStdHandle(-11)
mode = wintypes.DWORD()
assert kernel.GetConsoleMode(stdout, ctypes.byref(mode)), "stdout is not a console"
assert kernel.GetConsoleMode(stdin, ctypes.byref(mode)), "stdin is not a console"
assert kernel.SetConsoleMode(stdin, (mode.value | 2) & ~4), "Cannot disable echo"


def write(value):
    count = wintypes.DWORD()
    assert kernel.WriteConsoleW(stdout, value, len(value), ctypes.byref(count), None)


def response():
    count = wintypes.DWORD()
    value = ctypes.create_unicode_buffer(256)
    assert kernel.ReadConsoleW(stdin, value, 255, ctypes.byref(count), None)
    return value.value.rstrip("\r\n")


if sys.argv[1] == "--download-progress":
    # Require a live progress callback before this process can finish. This
    # output intentionally uses a console API and has no trailing newline.
    acknowledged = Path(sys.argv[2])
    write("Update state (0x61) downloading, progress: 25.00 (25 / 100)")
    deadline = time.monotonic() + 10
    while not acknowledged.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert acknowledged.exists(), "Progress was not received while downloading"
    write("\r\nUpdate state (0x61) downloading, progress: 75.00 (75 / 100)\r\n")
    write("Success! App '2310' fully installed.\r\n")
else:
    assert sys.argv[1:] == ["+login", "account", "+quit"]
    write("Pass")
    time.sleep(0.05)
    write("word:")
    assert response() == "test-password"
    write("\r\nSteam Guard code:")
    assert response() == "ABCDE"
    write("\r\nLogged in OK\r\n")
