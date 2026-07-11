from __future__ import annotations

import ctypes
import os
from pathlib import Path


def get_process_created_at(pid: int) -> float | None:
    pid = int(pid)
    if pid <= 0:
        return None
    if os.name == "nt":
        return _get_windows_process_created_at(pid)
    if Path(f"/proc/{pid}/stat").exists():
        return _get_linux_process_created_at(pid)
    return None


def process_matches(pid: int, created_at: float, tolerance: float = 0.25) -> bool:
    if pid <= 0 or created_at <= 0:
        return False
    actual = get_process_created_at(pid)
    return actual is not None and abs(actual - float(created_at)) <= tolerance


def _get_windows_process_created_at(pid: int) -> float | None:
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return None

    creation = wintypes.FILETIME()
    exit_time = wintypes.FILETIME()
    kernel_time = wintypes.FILETIME()
    user_time = wintypes.FILETIME()
    try:
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        ):
            return None
        exit_ticks = (exit_time.dwHighDateTime << 32) | exit_time.dwLowDateTime
        if exit_ticks != 0:
            return None
        ticks = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
        return ticks / 10_000_000 - 11_644_473_600
    finally:
        kernel32.CloseHandle(handle)


def _get_linux_process_created_at(pid: int) -> float | None:
    try:
        stat_text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        close_paren = stat_text.rfind(")")
        fields = stat_text[close_paren + 2 :].split()
        start_ticks = int(fields[19])
        boot_time = 0.0
        for line in Path("/proc/stat").read_text(encoding="utf-8").splitlines():
            if line.startswith("btime "):
                boot_time = float(line.split()[1])
                break
        if boot_time <= 0:
            return None
        return boot_time + start_ticks / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError):
        return None
