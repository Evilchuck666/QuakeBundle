"""Fake SteamCMD that refuses pipes and uses actual Windows console APIs."""

import ctypes
import sys
import time
from ctypes import wintypes

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
assert sys.argv[1:] == ["+login", "account", "+quit"]


def write(value):
    count = wintypes.DWORD()
    assert kernel.WriteConsoleW(stdout, value, len(value), ctypes.byref(count), None)


def response():
    count = wintypes.DWORD()
    value = ctypes.create_unicode_buffer(256)
    assert kernel.ReadConsoleW(stdin, value, 255, ctypes.byref(count), None)
    return value.value.rstrip("\r\n")


write("Pass")
time.sleep(0.05)
write("word:")
assert response() == "test-password"
write("\r\nSteam Guard code:")
assert response() == "ABCDE"
write("\r\nLogged in OK\r\n")
