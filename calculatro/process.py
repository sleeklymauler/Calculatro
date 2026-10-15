"""Windows process helpers for when the mod starts the app with the game
(`watch --game-pid`): exiting with the game, and not running twice."""

from __future__ import annotations

import ctypes
from collections.abc import Callable
from ctypes import wintypes

SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0
ERROR_ALREADY_EXISTS = 183
INSTANCE_MUTEX = "Local\\Calculatro"

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.WaitForSingleObject.restype = wintypes.DWORD
_kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_kernel32.CreateMutexW.restype = wintypes.HANDLE
_kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32.CloseHandle.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

_mutexes: list[int] = []  # held until the app exits


def exited(pid: int) -> Callable[[], bool]:
    """A check that's True once process `pid` has exited (at once if it can't be
    found). It holds a handle to the process, so a reused pid can't fool it."""
    handle = _kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        return lambda: True
    return lambda: _kernel32.WaitForSingleObject(handle, 0) == WAIT_OBJECT_0


def claim_instance(name: str = INSTANCE_MUTEX) -> bool:
    """Whether this is the only app holding `name`; if so, it's held until the app
    exits. Otherwise it's let go at once, so the one holding it can't be outlived
    by a second that keeps the next app from claiming it."""
    handle = _kernel32.CreateMutexW(None, False, name)
    if not handle:
        return True  # can't tell; better to run twice than not at all
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        _kernel32.CloseHandle(handle)
        return False
    _mutexes.append(handle)
    return True
