"""Windows process helpers: for when the mod starts the app with the game (`watch --game-pid`), exiting with
the game, not running twice and leaving it the CPU; the memory the app may use; and for its worker processes
(pool.ProcessRunner), how many to run, what each uses, and ending them with the app."""

import ctypes
import functools
import hashlib
import os
from collections.abc import Callable
from pathlib import Path

SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0
ERROR_ALREADY_EXISTS = 183
BELOW_NORMAL_PRIORITY_CLASS = 0x4000
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9  # SetInformationJobObject's class for _JobLimits
INSTANCE_MUTEX = "Local\\Calculatro"

_mutexes: list[int] = []  # held until the app exits


class _MemoryCounters(ctypes.Structure):  # PROCESS_MEMORY_COUNTERS_EX
    _fields_ = [("cb", ctypes.c_uint32), ("PageFaultCount", ctypes.c_uint32)] + [
        (name, ctypes.c_size_t) for name in (
            "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
            "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage",
            "PrivateUsage")]


class _MemoryStatus(ctypes.Structure):  # MEMORYSTATUSEX
    _fields_ = [("dwLength", ctypes.c_uint32), ("dwMemoryLoad", ctypes.c_uint32)] + [
        (name, ctypes.c_ulonglong) for name in (
            "ullTotalPhys", "ullAvailPhys", "ullTotalPageFile", "ullAvailPageFile", "ullTotalVirtual",
            "ullAvailVirtual", "ullAvailExtendedVirtual")]


class _JobBasicLimits(ctypes.Structure):  # JOBOBJECT_BASIC_LIMIT_INFORMATION
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", ctypes.c_uint32), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32)]


class _JobLimits(ctypes.Structure):  # JOBOBJECT_EXTENDED_LIMIT_INFORMATION
    _fields_ = [("BasicLimitInformation", _JobBasicLimits), ("IoInfo", ctypes.c_ulonglong * 6)] + [
        (name, ctypes.c_size_t) for name in (
            "ProcessMemoryLimit", "JobMemoryLimit", "PeakProcessMemoryUsed", "PeakJobMemoryUsed")]


@functools.cache
def _kernel32() -> ctypes.WinDLL:
    """Windows' kernel32, with the functions used here declared. Loaded on first use,
    not on import, so the modules that import this one (and their tests) load on
    other systems too; only calling these functions needs Windows."""
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.SetPriorityClass.restype = wintypes.BOOL
    kernel32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.GetPriorityClass.restype = wintypes.DWORD
    kernel32.GetPriorityClass.argtypes = [wintypes.HANDLE]
    kernel32.K32GetProcessMemoryInfo.restype = wintypes.BOOL
    kernel32.K32GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(_MemoryCounters), wintypes.DWORD]
    kernel32.GlobalMemoryStatusEx.restype = wintypes.BOOL
    kernel32.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(_MemoryStatus)]
    kernel32.GetPhysicallyInstalledSystemMemory.restype = wintypes.BOOL
    kernel32.GetPhysicallyInstalledSystemMemory.argtypes = [ctypes.POINTER(ctypes.c_ulonglong)]
    kernel32.GetProcessAffinityMask.restype = wintypes.BOOL
    kernel32.GetProcessAffinityMask.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_size_t)]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    return kernel32


GB = 2**30
MEMORY_LIMIT = 2 * GB  # bytes the app may use at most by default (the "Memory limit" setting's default)


def memory_used(handle: int | None = None) -> int:
    """The bytes this process has committed (its private usage), or the process `handle` is a handle to (a
    worker's `multiprocessing.Process.sentinel`); 0 if Windows won't say.
    Python gives the memory a cancelled calculation used back to Windows as it unwinds."""
    counters = _MemoryCounters(cb=ctypes.sizeof(_MemoryCounters))
    kernel32 = _kernel32()
    if handle is None:
        handle = kernel32.GetCurrentProcess()
    if not kernel32.K32GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
        return 0
    return counters.PrivateUsage


def installed_memory() -> int | None:
    """The computer's memory in bytes, as installed (16 GB for 16 GB, where Windows
    reports a little less as usable), or what Windows can use if it won't say that;
    None if it says neither. The mod reads it the same way (settings.lua)."""
    kb = ctypes.c_ulonglong()
    if _kernel32().GetPhysicallyInstalledSystemMemory(ctypes.byref(kb)) and kb.value:
        return kb.value * 1024
    status = _MemoryStatus(dwLength=ctypes.sizeof(_MemoryStatus))
    if _kernel32().GlobalMemoryStatusEx(ctypes.byref(status)) and status.ullTotalPhys:
        return status.ullTotalPhys
    return None


def memory_limit(gb: int | None = None, installed: int | None = None) -> int:
    """The most the app's calculations may make it use: the player's "Memory limit"
    setting (`gb`, or MEMORY_LIMIT without one), or half the computer's memory if
    that's less. `installed` is the computer's (installed_memory() if not given)."""
    limit = gb * GB if gb else MEMORY_LIMIT
    if installed is None:
        installed = installed_memory()
    return limit if installed is None else min(limit, installed // 2)


WORKER_MEMORY = GB // 2  # of the memory limit, what Auto allows each worker process (`auto_cores`)
AUTO_CORES_MOST = 4
CORES_MOST = 8  # the most the "Cores used" setting offers (settings.lua)


def usable_cores() -> int:
    """The logical cores this process may run on: those in its affinity mask, which the game's launcher may
    have narrowed (`start /affinity`), and which Windows hands on to the app. `os.process_cpu_count()` doesn't
    look at it on Windows. (A PC with more than 64 has processor groups, and the mask is for this
    process's group only.) The computer's cores, or 1, if Windows won't say."""
    ours, system = ctypes.c_size_t(), ctypes.c_size_t()
    kernel32 = _kernel32()
    known = kernel32.GetProcessAffinityMask(kernel32.GetCurrentProcess(), ctypes.byref(ours), ctypes.byref(system))
    if known and ours.value:
        return ours.value.bit_count()
    return os.cpu_count() or 1


def auto_cores(usable: int, limit: int) -> int:
    """How many cores the "Cores used" setting's Auto uses, on a PC with `usable` logical cores
    (`usable_cores`) and a memory limit of `limit` bytes (`memory_limit`): a quarter of the cores, so the game
    and the rest of the PC keep most (1, and so no worker processes, with fewer than 8), up to
    AUTO_CORES_MOST, and no more than the limit has WORKER_MEMORY for each."""
    return max(1, min(usable // 4, AUTO_CORES_MOST, limit // WORKER_MEMORY))


def cores_used(setting: int, limit: int, usable: int | None = None) -> int:
    """How many cores the calculations use: the player's "Cores used" setting, or Auto's choice
    (`auto_cores`) for 0, and always leaving a core free of them (but using 1 at least). 1 is the app on its
    own; more are that many worker processes."""
    if usable is None:
        usable = usable_cores()
    if setting <= 0:
        return auto_cores(usable, limit)
    return max(1, min(setting, CORES_MOST, usable - 1))


def kill_on_close_job() -> int | None:
    """A new Windows job object that ends its processes when its last handle is closed, which Windows does
    when the app exits, however it does: the handle is kept open (it's never closed here) and isn't
    inherited. None if Windows won't make one."""
    kernel32 = _kernel32()
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None
    limits = _JobLimits()
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel32.SetInformationJobObject(job, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(limits),
                                            ctypes.sizeof(limits)):
        kernel32.CloseHandle(job)
        return None
    return job


def add_to_job(job: int, handle: int) -> bool:
    """Put the process `handle` is a handle to in `job` (`kill_on_close_job`). Whether Windows did."""
    return bool(_kernel32().AssignProcessToJobObject(job, handle))


def set_low_priority() -> bool:
    """Put this process below normal priority, so the game always gets the CPU before the
    app's calculations do. Whether Windows did. Processes it starts afterwards get the same
    priority (Windows hands a below-normal parent's class on)."""
    kernel32 = _kernel32()
    return bool(kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS))


def priority_class() -> int:
    """This process's priority class (BELOW_NORMAL_PRIORITY_CLASS once `set_low_priority` has
    worked), or 0 if Windows won't say."""
    kernel32 = _kernel32()
    return kernel32.GetPriorityClass(kernel32.GetCurrentProcess())


def exited(pid: int) -> Callable[[], bool]:
    """A check that's True once process `pid` has exited (at once if it can't be
    found). It holds a handle to the process, so a reused pid can't fool it."""
    handle = _kernel32().OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        return lambda: True
    return lambda: _kernel32().WaitForSingleObject(handle, 0) == WAIT_OBJECT_0


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
    handle = _kernel32().CreateMutexW(None, False, name)
    if not handle:
        return True  # can't tell; better to run twice than not at all
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        _kernel32().CloseHandle(handle)
        return False
    _mutexes.append(handle)
    return True
