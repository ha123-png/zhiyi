"""Read a verification process's Windows working set without extra dependencies."""

import ctypes
from ctypes import wintypes


def process_memory(pid):
    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (name, ctypes.c_size_t)
            for name in (
                "PeakWorkingSetSize",
                "WorkingSetSize",
                "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage",
                "QuotaPeakNonPagedPoolUsage",
                "QuotaNonPagedPoolUsage",
                "PagefileUsage",
                "PeakPagefileUsage",
            )
        ]

    kernel = ctypes.windll.kernel32
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x410, False, pid)
    info = Counters()
    info.cb = ctypes.sizeof(info)
    ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(Counters),
        wintypes.DWORD,
    ]
    ok = ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(info), info.cb)
    kernel.CloseHandle(handle)
    if not ok:
        raise OSError("Cannot sample verification process memory")
    return info.WorkingSetSize
