"""Cross-platform no-follow file and trusted-path primitives."""

from __future__ import annotations

import os
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Iterator

_WINDOWS_FILE_ATTRIBUTE_DIRECTORY = getattr(stat, "FILE_ATTRIBUTE_DIRECTORY", 0x0010)
_WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT = getattr(
    stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400
)

if os.name == "nt":
    import ctypes
    import msvcrt
    from ctypes import wintypes

    _GENERIC_READ = 0x80000000
    _DELETE = 0x00010000
    _FILE_READ_ATTRIBUTES = 0x0080
    _FILE_SHARE_READ = 0x00000001
    _OPEN_EXISTING = 3
    _FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    _FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class _BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    _CreateFileW = ctypes.WinDLL("kernel32", use_last_error=True).CreateFileW
    _CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _CreateFileW.restype = wintypes.HANDLE
    _GetFileInformationByHandle = (
        ctypes.WinDLL("kernel32", use_last_error=True).GetFileInformationByHandle
    )
    _GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_BY_HANDLE_FILE_INFORMATION),
    ]
    _GetFileInformationByHandle.restype = wintypes.BOOL
    _CloseHandle = ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle
    _CloseHandle.argtypes = [wintypes.HANDLE]
    _CloseHandle.restype = wintypes.BOOL
    _SetFileInformationByHandle = (
        ctypes.WinDLL(
            "kernel32",
            use_last_error=True,
        ).SetFileInformationByHandle
    )
    _SetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    _SetFileInformationByHandle.restype = wintypes.BOOL
    _FILE_DISPOSITION_INFO_CLASS = 4

    class _FILE_DISPOSITION_INFO(ctypes.Structure):
        _fields_ = [("DeleteFile", wintypes.BOOL)]


class UnsafePathError(OSError):
    """A filesystem path crossed a symbolic-link or reparse boundary."""


def _windows_open_handle(path: Path, *, access: int) -> tuple[int, int]:
    handle = _CreateFileW(
        str(path),
        access,
        _FILE_SHARE_READ,
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_OPEN_REPARSE_POINT | _FILE_FLAG_BACKUP_SEMANTICS,
        None,
    )
    raw_handle = int(handle)
    if raw_handle == _INVALID_HANDLE_VALUE:
        raise ctypes.WinError(ctypes.get_last_error(), str(path))
    try:
        information = _BY_HANDLE_FILE_INFORMATION()
        if not _GetFileInformationByHandle(
            handle,
            ctypes.byref(information),
        ):
            raise ctypes.WinError(ctypes.get_last_error(), str(path))
        return raw_handle, int(information.dwFileAttributes)
    except Exception:
        _CloseHandle(handle)
        raise


def path_attributes_no_follow(path: Path) -> int:
    """Return platform file attributes without following the final component."""

    if os.name == "nt":
        handle, attributes = _windows_open_handle(
            path,
            access=_FILE_READ_ATTRIBUTES,
        )
        _CloseHandle(handle)
        return attributes
    metadata = os.lstat(path)
    attributes = 0
    if stat.S_ISDIR(metadata.st_mode):
        attributes |= _WINDOWS_FILE_ATTRIBUTE_DIRECTORY
    if stat.S_ISLNK(metadata.st_mode):
        attributes |= _WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT
    return attributes


def is_reparse_point(path: Path) -> bool:
    """Return whether the final component is a symlink or Windows reparse point."""

    try:
        return bool(
            path_attributes_no_follow(path)
            & _WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT
        )
    except OSError:
        return False


def assert_directory_no_reparse(path: Path) -> Path:
    """Require an existing directory whose final component is not redirected."""

    attributes = path_attributes_no_follow(path)
    if attributes & _WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT:
        raise UnsafePathError(f"Directory is a symbolic link or reparse point: {path}")
    if os.name == "nt":
        if not attributes & _WINDOWS_FILE_ATTRIBUTE_DIRECTORY:
            raise UnsafePathError(f"Path is not a directory: {path}")
    else:
        metadata = os.lstat(path)
        if not stat.S_ISDIR(metadata.st_mode):
            raise UnsafePathError(f"Path is not a directory: {path}")
    return path


def assert_contained_no_reparse(
    path: Path,
    *,
    root: Path,
    allow_missing: bool = False,
) -> Path:
    """Require lexical containment and non-reparse existing components."""

    root_absolute = Path(os.path.abspath(root))
    path_absolute = Path(os.path.abspath(path))
    try:
        relative = path_absolute.relative_to(root_absolute)
    except ValueError as exc:
        raise UnsafePathError(
            f"Path escapes the trusted root {root_absolute}: {path_absolute}"
        ) from exc
    candidates = [root_absolute]
    current = root_absolute
    for part in relative.parts:
        current = current / part
        candidates.append(current)
    missing_seen = False
    for candidate in candidates:
        if os.path.lexists(candidate):
            if missing_seen:
                raise UnsafePathError(
                    f"Path has an existing child below a missing parent: {candidate}"
                )
            attributes = path_attributes_no_follow(candidate)
            if attributes & _WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT:
                raise UnsafePathError(
                    f"Path contains a symbolic link or reparse point: {candidate}"
                )
        else:
            missing_seen = True
            if not allow_missing:
                raise FileNotFoundError(candidate)
    if not missing_seen:
        resolved_root = root_absolute.resolve(strict=True)
        resolved_path = path_absolute.resolve(strict=True)
        if not resolved_path.is_relative_to(resolved_root):
            raise UnsafePathError(
                f"Resolved path escapes the trusted root: {resolved_path}"
            )
    return path_absolute


@contextmanager
def open_regular_binary_no_follow(path: Path) -> Iterator[BinaryIO]:
    """Open one stable regular-file handle without following the final component."""

    if os.name == "nt":
        handle, attributes = _windows_open_handle(path, access=_GENERIC_READ)
        if attributes & (
            _WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT
            | _WINDOWS_FILE_ATTRIBUTE_DIRECTORY
        ):
            _CloseHandle(handle)
            raise UnsafePathError(
                f"File is a symbolic link, reparse point, or directory: {path}"
            )
        try:
            descriptor = msvcrt.open_osfhandle(
                handle,
                os.O_RDONLY | getattr(os, "O_BINARY", 0),
            )
        except Exception:
            _CloseHandle(handle)
            raise
    else:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        no_follow = getattr(os, "O_NOFOLLOW", 0)
        if not no_follow:
            raise UnsafePathError("O_NOFOLLOW is unavailable on this platform")
        descriptor = os.open(path, flags | no_follow)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise UnsafePathError(f"Path is not a regular file: {path}")
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            descriptor = -1
            yield handle
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def delete_path_entry_no_follow(
    path: Path,
    *,
    expected_identity: tuple[int, int] | None = None,
) -> bool:
    """Delete the exact authorized path entry without following redirects.

    Windows commits deletion through the still-open no-follow handle. POSIX
    first atomically quarantines the entry inside its parent dirfd, then
    verifies the quarantined object before unlinking it.
    """

    if os.name == "nt":
        return _windows_delete_path_entry(
            path,
            expected_identity=expected_identity,
        )
    return _posix_delete_path_entry(
        path,
        expected_identity=expected_identity,
    )


def _windows_delete_path_entry(
    path: Path,
    *,
    expected_identity: tuple[int, int] | None,
) -> bool:
    try:
        raw_handle, attributes = _windows_open_handle(
            path,
            access=_GENERIC_READ | _DELETE,
        )
    except OSError:
        return False
    descriptor = -1
    try:
        descriptor = msvcrt.open_osfhandle(
            raw_handle,
            os.O_RDONLY | getattr(os, "O_BINARY", 0),
        )
        raw_handle = -1
        opened = os.fstat(descriptor)
        if expected_identity is not None and (
            opened.st_dev,
            opened.st_ino,
        ) != expected_identity:
            return False
        is_reparse = bool(
            attributes & _WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT
        )
        if (
            attributes & _WINDOWS_FILE_ATTRIBUTE_DIRECTORY
            and not is_reparse
        ):
            return False
        if not is_reparse and not stat.S_ISREG(opened.st_mode):
            return False
        disposition = _FILE_DISPOSITION_INFO(True)
        handle = msvcrt.get_osfhandle(descriptor)
        if not _SetFileInformationByHandle(
            handle,
            _FILE_DISPOSITION_INFO_CLASS,
            ctypes.byref(disposition),
            ctypes.sizeof(disposition),
        ):
            return False
        return True
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        elif raw_handle != -1:
            _CloseHandle(raw_handle)


def _posix_delete_path_entry(
    path: Path,
    *,
    expected_identity: tuple[int, int] | None,
) -> bool:
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        parent_fd = os.open(path.parent, directory_flags)
    except OSError:
        return False
    opened_fd = -1
    tombstone: str | None = None
    try:
        try:
            before = os.stat(
                path.name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
        except OSError:
            return False
        if expected_identity is not None and (
            before.st_dev,
            before.st_ino,
        ) != expected_identity:
            return False
        if stat.S_ISDIR(before.st_mode) and not stat.S_ISLNK(before.st_mode):
            return False
        open_flags = (
            getattr(os, "O_PATH", os.O_RDONLY)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        try:
            opened_fd = os.open(path.name, open_flags, dir_fd=parent_fd)
            opened = os.fstat(opened_fd)
        except OSError:
            return False
        if (opened.st_dev, opened.st_ino) != (
            before.st_dev,
            before.st_ino,
        ):
            return False
        tombstone = f".delete-{os.urandom(16).hex()}"
        try:
            os.rename(
                path.name,
                tombstone,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
        except OSError:
            return False
        try:
            quarantined = os.stat(
                tombstone,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
        except OSError:
            return False
        if (quarantined.st_dev, quarantined.st_ino) != (
            opened.st_dev,
            opened.st_ino,
        ):
            _restore_quarantined_entry(
                parent_fd,
                tombstone,
                path.name,
            )
            tombstone = None
            return False
        os.unlink(tombstone, dir_fd=parent_fd)
        tombstone = None
        return True
    except OSError:
        return False
    finally:
        if tombstone is not None:
            _restore_quarantined_entry(
                parent_fd,
                tombstone,
                path.name,
            )
        if opened_fd >= 0:
            os.close(opened_fd)
        os.close(parent_fd)


def _restore_quarantined_entry(
    parent_fd: int,
    tombstone: str,
    original: str,
) -> None:
    """Restore a quarantined replacement without overwriting another entry."""

    try:
        os.link(
            tombstone,
            original,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
            follow_symlinks=False,
        )
    except OSError:
        return
    try:
        os.unlink(tombstone, dir_fd=parent_fd)
    except OSError:
        pass
