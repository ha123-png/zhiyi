"""Stop only executable images belonging to an explicitly selected installation."""

import ctypes
from ctypes import wintypes
from pathlib import Path
import sys


def _open_live_process(kernel, pid):
    process = kernel.OpenProcess(0x1000 | 0x100000 | 0x1, False, pid)
    if process:
        return process
    error = ctypes.get_last_error()
    if error == 87:
        return None
    if error == 5:
        # A terminated child can remain in the snapshot while its parent or
        # WebView releases handles. Windows denies TERMINATE on that object.
        observer = kernel.OpenProcess(0x1000 | 0x100000, False, pid)
        if observer:
            try:
                if kernel.WaitForSingleObject(observer, 0) == 0:
                    return None
            finally:
                kernel.CloseHandle(observer)
        elif ctypes.get_last_error() == 87:
            return None
    raise ctypes.WinError(error)


def stop_installation(directory: Path) -> int:
    if sys.platform != "win32":
        raise RuntimeError("This operation is only available on Windows.")
    target = (directory.resolve() / "Zhiyi.exe").resolve()
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)

    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("size", wintypes.DWORD), ("usage", wintypes.DWORD),
            ("pid", wintypes.DWORD), ("heap", ctypes.c_size_t),
            ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
            ("parent", wintypes.DWORD), ("priority", wintypes.LONG),
            ("flags", wintypes.DWORD), ("name", wintypes.WCHAR * 260),
        ]

    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    for method in (kernel.Process32FirstW, kernel.Process32NextW):
        method.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
        method.restype = wintypes.BOOL
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
    ]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateProcess.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL

    snapshot = kernel.CreateToolhelp32Snapshot(0x2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    stopped = 0
    try:
        entry = ProcessEntry()
        entry.size = ctypes.sizeof(entry)
        present = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while present:
            if entry.name.casefold() == "zhiyi.exe":
                # Validate and terminate through the same handle, so PID reuse
                # cannot redirect the operation to a different process.
                process = _open_live_process(kernel, entry.pid)
                if process:
                    try:
                        buffer = ctypes.create_unicode_buffer(32768)
                        length = wintypes.DWORD(len(buffer))
                        if not kernel.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(length)):
                            error = ctypes.get_last_error()
                            if kernel.WaitForSingleObject(process, 0) != 0:
                                raise ctypes.WinError(error)
                        if buffer.value and Path(buffer.value).resolve() == target:
                            if not kernel.TerminateProcess(process, 0):
                                error = ctypes.get_last_error()
                                if kernel.WaitForSingleObject(process, 0) != 0:
                                    raise ctypes.WinError(error)
                            if kernel.WaitForSingleObject(process, 10000) != 0:
                                raise RuntimeError("知意进程未能退出，请关闭该安装目录中的知意后重试。")
                            stopped += 1
                    finally:
                        kernel.CloseHandle(process)
            present = kernel.Process32NextW(snapshot, ctypes.byref(entry))
        if ctypes.get_last_error() != 18:  # ERROR_NO_MORE_FILES
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.CloseHandle(snapshot)
    return stopped
