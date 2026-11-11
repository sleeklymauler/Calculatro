"""Windows process helpers for when the mod starts the app with the game
(`watch --game-pid`): exiting with the game, and not running twice."""

from __future__ import annotations

import ctypes
import hashlib
import os
from collections.abc import Callable
from ctypes import wintypes
from pathlib import Path

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


class _MemoryCounters(ctypes.Structure):  # PROCESS_MEMORY_COUNTERS_EX
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
        (name, ctypes.c_size_t) for name in (
            "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
            "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage",
            "PrivateUsage")]


class _MemoryStatus(ctypes.Structure):  # MEMORYSTATUSEX
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD)] + [
        (name, ctypes.c_ulonglong) for name in (
            "ullTotalPhys", "ullAvailPhys", "ullTotalPageFile", "ullAvailPageFile", "ullTotalVirtual",
            "ullAvailVirtual", "ullAvailExtendedVirtual")]


_kernel32.GetCurrentProcess.restype = wintypes.HANDLE
_kernel32.K32GetProcessMemoryInfo.restype = wintypes.BOOL
_kernel32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MemoryCounters), wintypes.DWORD]
_kernel32.GlobalMemoryStatusEx.restype = wintypes.BOOL
_kernel32.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(_MemoryStatus)]

MEMORY_LIMIT = 2 * 2**30  # bytes the app may use at most, if the computer has twice that


def memory_used() -> int:
    """The bytes this process has committed (its private usage), or 0 if Windows won't say.
    Python gives the memory a cancelled calculation used back to Windows as it unwinds."""
    counters = _MemoryCounters(cb=ctypes.sizeof(_MemoryCounters))
    if not _kernel32.K32GetProcessMemoryInfo(_kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        return 0
    return counters.PrivateUsage


def memory_limit() -> int:
    """The most the app's calculations may make it use: MEMORY_LIMIT, or half the
    computer's memory if that's less."""
    status = _MemoryStatus(dwLength=ctypes.sizeof(_MemoryStatus))
    if not _kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return MEMORY_LIMIT
    return min(MEMORY_LIMIT, status.ullTotalPhys // 2)


def exited(pid: int) -> Callable[[], bool]:
    """A check that's True once process `pid` has exited (at once if it can't be
    found). It holds a handle to the process, so a reused pid can't fool it."""
    handle = _kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        return lambda: True
    return lambda: _kernel32.WaitForSingleObject(handle, 0) == WAIT_OBJECT_0


def instance_name(state_path: Path) -> str:
    """The mutex for the app watching this state file. Instances only clash over
    the same odds file, which sits beside it. Hashed, since a mutex's name can't
    contain a path: CreateMutexW rejects a further backslash after the namespace."""
    resolved = os.path.normcase(state_path.resolve())
    return f"{INSTANCE_MUTEX}-{hashlib.sha256(resolved.encode()).hexdigest()[:16]}"


def claim_instance(name: str) -> bool:
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
