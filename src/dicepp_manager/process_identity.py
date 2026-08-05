"""Exact process identity helpers shared by managed Runtime processes."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import signal
import time
from pathlib import Path
from typing import Any, Protocol

_QUERY_IMAGE_BUFFER_SIZE = 32768


class ProcessIdentityError(RuntimeError):
    pass


class ProcessIdentityHandle(Protocol):
    identity: dict[str, Any]

    def wait(self, timeout: float) -> bool: ...
    def terminate(self, timeout: float) -> bool: ...
    def close(self) -> None: ...


def inspect_process_identity(pid: int) -> dict[str, Any] | None:
    if pid <= 0:
        return None
    if os.name == "nt":
        return _inspect_windows_process(pid)
    return _inspect_proc_process(pid)


def open_process_identity_handle(
    identity: dict[str, Any],
) -> ProcessIdentityHandle | None:
    expected = _validate_identity(identity)
    if os.name == "nt":
        return _open_windows_process_handle(expected)
    if _inspect_proc_process(expected["pid"]) != expected:
        return None
    return _PosixProcessIdentityHandle(expected)


def _validate_identity(value: dict[str, Any]) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or type(value.get("pid")) is not int
        or value["pid"] <= 0
        or not isinstance(value.get("started_at"), str)
        or not value["started_at"]
        or not isinstance(value.get("executable"), str)
        or not Path(value["executable"]).is_absolute()
    ):
        raise ProcessIdentityError("Process identity is invalid")
    return {
        "pid": value["pid"],
        "started_at": value["started_at"],
        "executable": value["executable"],
    }


class _PosixProcessIdentityHandle:
    def __init__(self, identity: dict[str, Any]) -> None:
        self.identity = identity

    def wait(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if _inspect_proc_process(self.identity["pid"]) != self.identity:
                return True
            time.sleep(0.05)
        return False

    def terminate(self, timeout: float) -> bool:
        if _inspect_proc_process(self.identity["pid"]) != self.identity:
            return True
        os.kill(self.identity["pid"], signal.SIGTERM)
        if self.wait(timeout):
            return True
        if _inspect_proc_process(self.identity["pid"]) != self.identity:
            return True
        os.kill(self.identity["pid"], getattr(signal, "SIGKILL", signal.SIGTERM))
        return self.wait(timeout)

    def close(self) -> None:
        return None


class _WindowsProcessIdentityHandle:
    def __init__(self, handle: int, identity: dict[str, Any]) -> None:
        self._handle = handle
        self.identity = identity

    def wait(self, timeout: float) -> bool:
        milliseconds = max(0, min(int(timeout * 1000), 0xFFFFFFFE))
        result = ctypes.windll.kernel32.WaitForSingleObject(
            self._handle,
            milliseconds,
        )
        if result == 0:
            return True
        if result == 0x102:
            return False
        raise ProcessIdentityError("Waiting for the process handle failed")

    def terminate(self, timeout: float) -> bool:
        if self.wait(0):
            return True
        if not ctypes.windll.kernel32.TerminateProcess(self._handle, 1):
            raise ProcessIdentityError("Terminating the process handle failed")
        return self.wait(timeout)

    def close(self) -> None:
        if self._handle:
            ctypes.windll.kernel32.CloseHandle(self._handle)
            self._handle = 0


def _open_windows_process_handle(
    expected: dict[str, Any],
) -> _WindowsProcessIdentityHandle | None:
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.argtypes = [
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(0x1000 | 0x00100000 | 0x0001, False, expected["pid"])
    if not handle:
        return None
    try:
        actual = _identity_from_windows_handle(handle, expected["pid"])
        if actual != expected:
            kernel32.CloseHandle(handle)
            return None
        return _WindowsProcessIdentityHandle(handle, actual)
    except BaseException:
        kernel32.CloseHandle(handle)
        raise


def _inspect_proc_process(pid: int) -> dict[str, Any] | None:
    proc = Path("/proc") / str(pid)
    try:
        executable = str((proc / "exe").resolve(strict=True))
        fields = (proc / "stat").read_text(encoding="utf-8").split()
        started_at = fields[21]
    except (FileNotFoundError, ProcessLookupError, PermissionError, IndexError, OSError):
        return None
    return {"pid": pid, "started_at": started_at, "executable": executable}


def _inspect_windows_process(pid: int) -> dict[str, Any] | None:
    kernel32 = ctypes.windll.kernel32
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
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        return _identity_from_windows_handle(handle, pid)
    finally:
        kernel32.CloseHandle(handle)


def _identity_from_windows_handle(handle: int, pid: int) -> dict[str, Any] | None:
    kernel32 = ctypes.windll.kernel32
    creation = wintypes.FILETIME()
    exit_time = wintypes.FILETIME()
    kernel = wintypes.FILETIME()
    user = wintypes.FILETIME()
    if not kernel32.GetProcessTimes(
        handle,
        ctypes.byref(creation),
        ctypes.byref(exit_time),
        ctypes.byref(kernel),
        ctypes.byref(user),
    ):
        return None
    size = ctypes.c_ulong(_QUERY_IMAGE_BUFFER_SIZE)
    buffer = ctypes.create_unicode_buffer(size.value)
    if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
        return None
    return {
        "pid": pid,
        "started_at": str((creation.dwHighDateTime << 32) | creation.dwLowDateTime),
        "executable": str(Path(buffer.value).resolve()),
    }


__all__ = ["inspect_process_identity", "open_process_identity_handle"]
