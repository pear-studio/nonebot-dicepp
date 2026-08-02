"""Process-independent private token handling."""

from __future__ import annotations

import hmac
import errno
import os
import secrets
import stat
import tempfile
from pathlib import Path

from filelock import FileLock, Timeout as FileLockTimeout


class TokenSecurityError(RuntimeError):
    """The Manager API token cannot be kept private on this host."""


_MAX_TOKEN_BYTES = 4096
_TOKEN_PUBLICATION_TIMEOUT_SECONDS = 15
_WINDOWS_ACL_STAGES = frozenset(
    {
        "owner-allowlist",
        "dacl-apply",
        "post-owner",
        "inheritance",
        "ace-verification",
    }
)
_WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT = getattr(
    stat,
    "FILE_ATTRIBUTE_REPARSE_POINT",
    0x0400,
)


class _WindowsAclStageError(RuntimeError):
    """A fixed, non-sensitive reason why Windows ACL hardening failed."""

    def __init__(self, stage: str) -> None:
        self.stage = stage
        super().__init__(stage)


def _windows_acl_error(stage: str) -> TokenSecurityError:
    if stage not in _WINDOWS_ACL_STAGES:
        stage = "dacl-apply"
    return TokenSecurityError(
        f"Could not apply Windows ACL to Manager API token ({stage})"
    )


def _windows_regular_token_exists(token_path: Path) -> bool:
    """Return whether a token exists without accepting Windows reparse points."""
    try:
        metadata = os.lstat(token_path)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise TokenSecurityError("Manager API token is unavailable") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or getattr(metadata, "st_file_attributes", 0)
        & _WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise TokenSecurityError("Manager API token must be a regular file")
    return True


def _require_windows_regular_token_file(token_path: Path) -> None:
    if not _windows_regular_token_exists(token_path):
        raise TokenSecurityError("Manager API token is unavailable")


def _decode_token_bytes(raw: bytes) -> str:
    if len(raw) > _MAX_TOKEN_BYTES:
        raise TokenSecurityError("Manager API token is invalid")
    try:
        return raw.decode("utf-8", errors="strict").strip()
    except UnicodeDecodeError as exc:
        raise TokenSecurityError("Manager API token is invalid") from exc


def _read_token_descriptor(descriptor: int) -> str:
    chunks: list[bytes] = []
    size = 0
    try:
        while True:
            chunk = os.read(
                descriptor,
                min(1024, _MAX_TOKEN_BYTES + 1 - size),
            )
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > _MAX_TOKEN_BYTES:
                raise TokenSecurityError("Manager API token is invalid")
    except TokenSecurityError:
        raise
    except OSError as exc:
        raise TokenSecurityError("Manager API token is unavailable") from exc
    return _decode_token_bytes(b"".join(chunks))


def _read_token_windows(token_path: Path) -> str:
    """Read a token through a no-reparse Win32 handle, never a path reopen."""
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class FileInformation(ctypes.Structure):
        _fields_ = [
            ("file_attributes", wintypes.DWORD),
            ("creation_time", wintypes.FILETIME),
            ("last_access_time", wintypes.FILETIME),
            ("last_write_time", wintypes.FILETIME),
            ("volume_serial_number", wintypes.DWORD),
            ("file_size_high", wintypes.DWORD),
            ("file_size_low", wintypes.DWORD),
            ("number_of_links", wintypes.DWORD),
            ("file_index_high", wintypes.DWORD),
            ("file_index_low", wintypes.DWORD),
        ]

    generic_read = 0x80000000
    file_share_read = 0x00000001
    open_existing = 3
    file_attribute_normal = 0x00000080
    file_attribute_directory = 0x00000010
    file_flag_open_reparse_point = 0x00200000
    file_type_disk = 0x0001
    error_file_not_found = 2
    error_path_not_found = 3
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    get_file_information = kernel32.GetFileInformationByHandle
    get_file_information.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(FileInformation),
    ]
    get_file_information.restype = wintypes.BOOL
    get_file_type = kernel32.GetFileType
    get_file_type.argtypes = [wintypes.HANDLE]
    get_file_type.restype = wintypes.DWORD
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    handle = create_file(
        str(token_path),
        generic_read,
        file_share_read,
        None,
        open_existing,
        file_attribute_normal | file_flag_open_reparse_point,
        None,
    )
    invalid_handle = wintypes.HANDLE(-1).value
    if handle == invalid_handle:
        error = ctypes.get_last_error()
        if error in {error_file_not_found, error_path_not_found}:
            raise FileNotFoundError("Manager API token is unavailable")
        raise TokenSecurityError("Manager API token is unavailable")

    descriptor = -1
    try:
        information = FileInformation()
        if not get_file_information(handle, ctypes.byref(information)):
            raise TokenSecurityError("Manager API token is unavailable")
        if get_file_type(handle) != file_type_disk or information.file_attributes & (
            _WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT | file_attribute_directory
        ):
            raise TokenSecurityError("Manager API token must be a regular file")
        try:
            descriptor = msvcrt.open_osfhandle(
                handle,
                os.O_RDONLY | getattr(os, "O_BINARY", 0),
            )
        except OSError as exc:
            raise TokenSecurityError("Manager API token is unavailable") from exc
        handle = None
        try:
            return _read_token_descriptor(descriptor)
        finally:
            os.close(descriptor)
            descriptor = -1
    finally:
        if handle is not None:
            close_handle(handle)


def _posix_open_flags() -> int:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise TokenSecurityError(
            "POSIX token policy requires no-follow file descriptors"
        )
    return os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0)


def _open_posix_parent(token_path: Path) -> tuple[int, os.stat_result]:
    flags = _posix_open_flags() | getattr(os, "O_DIRECTORY", 0)
    descriptor = -1
    try:
        descriptor = os.open(token_path.parent, flags)
        metadata = os.fstat(descriptor)
    except FileNotFoundError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise TokenSecurityError("Manager API token directory is unavailable") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        os.close(descriptor)
        raise TokenSecurityError("Manager API token directory is invalid")
    return descriptor, metadata


def _open_posix_token(
    parent_descriptor: int,
    token_path: Path,
) -> int:
    if os.open not in os.supports_dir_fd:
        raise TokenSecurityError(
            "POSIX token policy requires descriptor-relative file access"
        )
    try:
        return os.open(
            token_path.name,
            _posix_open_flags(),
            dir_fd=parent_descriptor,
        )
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise TokenSecurityError("Manager API token is unavailable") from exc


def _secure_token_descriptor_posix(
    descriptor: int,
    parent: os.stat_result,
) -> None:
    """Apply the ownership and mode policy through one no-follow descriptor."""
    metadata = _regular_token_metadata_posix(descriptor)

    try:
        effective_uid = os.geteuid()
    except AttributeError as exc:  # pragma: no cover - guarded by os.name
        raise TokenSecurityError("POSIX token policy is unavailable") from exc

    try:
        if effective_uid == 0:
            os.fchown(descriptor, parent.st_uid, parent.st_gid)
        elif metadata.st_uid != effective_uid:
            raise TokenSecurityError(
                "Manager API token must be owned by the current user"
            )
        os.fchmod(descriptor, 0o600)
        secured = os.fstat(descriptor)
    except TokenSecurityError:
        raise
    except OSError as exc:
        raise TokenSecurityError(
            "Could not secure Manager API token permissions"
        ) from exc

    expected_uid = parent.st_uid if effective_uid == 0 else effective_uid
    expected_gid = parent.st_gid if effective_uid == 0 else secured.st_gid
    if (
        secured.st_uid != expected_uid
        or secured.st_gid != expected_gid
        or stat.S_IMODE(secured.st_mode) != 0o600
    ):
        raise TokenSecurityError("Manager API token permissions are not secure")


def _regular_token_metadata_posix(descriptor: int) -> os.stat_result:
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode):
        raise TokenSecurityError("Manager API token must be a regular file")
    return metadata


def _validate_readonly_token_descriptor_posix(descriptor: int) -> None:
    metadata = _regular_token_metadata_posix(descriptor)
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        raise TokenSecurityError("Manager API token permissions are not secure")
    try:
        effective_uid = os.geteuid()
    except AttributeError as exc:  # pragma: no cover - guarded by os.name
        raise TokenSecurityError("POSIX token policy is unavailable") from exc
    if effective_uid != 0 and metadata.st_uid != effective_uid:
        raise TokenSecurityError("Manager API token must be owned by the current user")


def _secure_token_file_posix(token_path: Path) -> None:
    """Secure an existing token without following a replaced file path."""
    parent_descriptor, parent = _open_posix_parent(token_path)
    try:
        token_descriptor = _open_posix_token(parent_descriptor, token_path)
        try:
            _secure_token_descriptor_posix(token_descriptor, parent)
        finally:
            os.close(token_descriptor)
    finally:
        os.close(parent_descriptor)


def _read_secured_token_posix(token_path: Path) -> str | None:
    parent_descriptor, parent = _open_posix_parent(token_path)
    try:
        try:
            token_descriptor = _open_posix_token(parent_descriptor, token_path)
        except FileNotFoundError:
            return None
        try:
            _secure_token_descriptor_posix(token_descriptor, parent)
            return _read_token_descriptor(token_descriptor)
        finally:
            if token_descriptor >= 0:
                os.close(token_descriptor)
    finally:
        os.close(parent_descriptor)


def _read_token_posix_readonly(token_path: Path) -> str:
    parent_descriptor, _parent = _open_posix_parent(token_path)
    try:
        token_descriptor = _open_posix_token(parent_descriptor, token_path)
        try:
            _validate_readonly_token_descriptor_posix(token_descriptor)
            return _read_token_descriptor(token_descriptor)
        finally:
            os.close(token_descriptor)
    finally:
        os.close(parent_descriptor)


def _secure_token_file_windows_native(token_path: Path) -> None:
    """Apply and verify the token ACL through the Win32 security APIs."""
    import ctypes
    from ctypes import wintypes

    dword = wintypes.DWORD
    psid = ctypes.c_void_p
    pacl = ctypes.c_void_p
    security_descriptor = ctypes.c_void_p

    class SidAndAttributes(ctypes.Structure):
        _fields_ = [("Sid", psid), ("Attributes", dword)]

    class TokenUser(ctypes.Structure):
        _fields_ = [("User", SidAndAttributes)]

    class Trustee(ctypes.Structure):
        _fields_ = [
            ("pMultipleTrustee", ctypes.c_void_p),
            ("MultipleTrusteeOperation", ctypes.c_uint),
            ("TrusteeForm", ctypes.c_uint),
            ("TrusteeType", ctypes.c_uint),
            ("ptstrName", ctypes.c_void_p),
        ]

    class ExplicitAccess(ctypes.Structure):
        _fields_ = [
            ("grfAccessPermissions", dword),
            ("grfAccessMode", ctypes.c_uint),
            ("grfInheritance", dword),
            ("Trustee", Trustee),
        ]

    class AclSizeInformation(ctypes.Structure):
        _fields_ = [
            ("AceCount", dword),
            ("AclBytesInUse", dword),
            ("AclBytesFree", dword),
        ]

    class AceHeader(ctypes.Structure):
        _fields_ = [
            ("AceType", ctypes.c_ubyte),
            ("AceFlags", ctypes.c_ubyte),
            ("AceSize", ctypes.c_ushort),
        ]

    class AccessAllowedAce(ctypes.Structure):
        _fields_ = [
            ("Header", AceHeader),
            ("Mask", dword),
            ("SidStart", dword),
        ]

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        dword,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_uint,
        ctypes.c_void_p,
        dword,
        ctypes.POINTER(dword),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.ConvertStringSidToSidW.argtypes = [
        wintypes.LPCWSTR,
        ctypes.POINTER(psid),
    ]
    advapi32.ConvertStringSidToSidW.restype = wintypes.BOOL
    advapi32.EqualSid.argtypes = [psid, psid]
    advapi32.EqualSid.restype = wintypes.BOOL
    advapi32.GetLengthSid.argtypes = [psid]
    advapi32.GetLengthSid.restype = dword
    advapi32.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR,
        ctypes.c_uint,
        dword,
        ctypes.POINTER(psid),
        ctypes.c_void_p,
        ctypes.POINTER(pacl),
        ctypes.c_void_p,
        ctypes.POINTER(security_descriptor),
    ]
    advapi32.GetNamedSecurityInfoW.restype = dword
    advapi32.SetEntriesInAclW.argtypes = [
        ctypes.c_uint,
        ctypes.c_void_p,
        pacl,
        ctypes.POINTER(pacl),
    ]
    advapi32.SetEntriesInAclW.restype = dword
    advapi32.SetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR,
        ctypes.c_uint,
        dword,
        psid,
        psid,
        pacl,
        pacl,
    ]
    advapi32.SetNamedSecurityInfoW.restype = dword
    advapi32.GetSecurityDescriptorControl.argtypes = [
        security_descriptor,
        ctypes.POINTER(ctypes.c_ushort),
        ctypes.POINTER(dword),
    ]
    advapi32.GetSecurityDescriptorControl.restype = wintypes.BOOL
    advapi32.GetAclInformation.argtypes = [
        pacl,
        ctypes.c_void_p,
        dword,
        ctypes.c_uint,
    ]
    advapi32.GetAclInformation.restype = wintypes.BOOL
    advapi32.GetAce.argtypes = [pacl, dword, ctypes.POINTER(ctypes.c_void_p)]
    advapi32.GetAce.restype = wintypes.BOOL
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    token_query = 0x0008
    token_user_information = 1
    se_file_object = 1
    owner_security_information = 0x00000001
    dacl_security_information = 0x00000004
    protected_dacl_security_information = 0x80000000
    se_dacl_protected = 0x1000
    set_access = 2
    no_inheritance = 0
    trustee_is_sid = 0
    trustee_is_unknown = 0
    acl_size_information = 2
    access_allowed_ace_type = 0
    modify_with_synchronize = 0x001301BF
    full_control = 0x001F01FF

    def fail(stage: str) -> None:
        raise _WindowsAclStageError(stage)

    def free_local(pointer: ctypes.c_void_p | None) -> None:
        if pointer:
            kernel32.LocalFree(pointer)

    def same_sid(left: ctypes.c_void_p, right: ctypes.c_void_p) -> bool:
        return bool(advapi32.EqualSid(left, right))

    def read_security(stage: str):
        owner = psid()
        dacl = pacl()
        descriptor = security_descriptor()
        result = advapi32.GetNamedSecurityInfoW(
            str(token_path),
            se_file_object,
            owner_security_information | dacl_security_information,
            ctypes.byref(owner),
            None,
            ctypes.byref(dacl),
            None,
            ctypes.byref(descriptor),
        )
        if result != 0 or not descriptor:
            fail(stage)
        return owner, dacl, descriptor

    def sid_from_text(value: str) -> ctypes.c_void_p:
        sid = psid()
        if not advapi32.ConvertStringSidToSidW(value, ctypes.byref(sid)):
            fail("owner-allowlist")
        return sid

    token_handle = wintypes.HANDLE()
    try:
        if not advapi32.OpenProcessToken(
            kernel32.GetCurrentProcess(),
            token_query,
            ctypes.byref(token_handle),
        ):
            fail("owner-allowlist")
        required_size = dword()
        advapi32.GetTokenInformation(
            token_handle,
            token_user_information,
            None,
            0,
            ctypes.byref(required_size),
        )
        if required_size.value == 0:
            fail("owner-allowlist")
        token_information = ctypes.create_string_buffer(required_size.value)
        if not advapi32.GetTokenInformation(
            token_handle,
            token_user_information,
            token_information,
            required_size,
            ctypes.byref(required_size),
        ):
            fail("owner-allowlist")
        current_sid = ctypes.cast(
            token_information,
            ctypes.POINTER(TokenUser),
        ).contents.User.Sid
    except _WindowsAclStageError:
        raise
    except Exception:
        fail("owner-allowlist")
    finally:
        if token_handle:
            kernel32.CloseHandle(token_handle)

    system_sid = psid()
    administrators_sid = psid()
    try:
        system_sid = sid_from_text("S-1-5-18")
        administrators_sid = sid_from_text("S-1-5-32-544")
        trusted_owner_sids = (current_sid, system_sid, administrators_sid)
        try:
            owner, _dacl, descriptor = read_security("owner-allowlist")
            try:
                if not owner or not any(
                    same_sid(owner, trusted) for trusted in trusted_owner_sids
                ):
                    fail("owner-allowlist")
            finally:
                free_local(descriptor)
        except _WindowsAclStageError:
            raise
        except Exception:
            fail("owner-allowlist")

        rules: list[tuple[ctypes.c_void_p, int]] = []
        for principal, rights in (
            (current_sid, modify_with_synchronize),
            (system_sid, full_control),
            (administrators_sid, full_control),
        ):
            for index, (known_principal, known_rights) in enumerate(rules):
                if same_sid(principal, known_principal):
                    rules[index] = (known_principal, known_rights | rights)
                    break
            else:
                rules.append((principal, rights))

        new_dacl = pacl()
        try:
            entries = (ExplicitAccess * len(rules))()
            for index, (principal, rights) in enumerate(rules):
                entry = entries[index]
                entry.grfAccessPermissions = rights
                entry.grfAccessMode = set_access
                entry.grfInheritance = no_inheritance
                entry.Trustee.TrusteeForm = trustee_is_sid
                entry.Trustee.TrusteeType = trustee_is_unknown
                entry.Trustee.ptstrName = principal
            if (
                advapi32.SetEntriesInAclW(
                    len(entries),
                    entries,
                    None,
                    ctypes.byref(new_dacl),
                )
                != 0
            ):
                fail("dacl-apply")
            if (
                advapi32.SetNamedSecurityInfoW(
                    str(token_path),
                    se_file_object,
                    dacl_security_information | protected_dacl_security_information,
                    None,
                    None,
                    new_dacl,
                    None,
                )
                != 0
            ):
                fail("dacl-apply")
        except _WindowsAclStageError:
            raise
        except Exception:
            fail("dacl-apply")
        finally:
            free_local(new_dacl)

        descriptor = None
        try:
            owner, dacl, descriptor = read_security("post-owner")
            if not owner or not any(
                same_sid(owner, trusted) for trusted in trusted_owner_sids
            ):
                fail("post-owner")

            control = ctypes.c_ushort()
            revision = dword()
            if (
                not advapi32.GetSecurityDescriptorControl(
                    descriptor,
                    ctypes.byref(control),
                    ctypes.byref(revision),
                )
                or not control.value & se_dacl_protected
            ):
                fail("inheritance")

            if not dacl:
                fail("ace-verification")
            information = AclSizeInformation()
            if not advapi32.GetAclInformation(
                dacl,
                ctypes.byref(information),
                ctypes.sizeof(information),
                acl_size_information,
            ) or information.AceCount != len(rules):
                fail("ace-verification")
            seen = [False] * len(rules)
            for index in range(information.AceCount):
                raw_ace = ctypes.c_void_p()
                if (
                    not advapi32.GetAce(dacl, index, ctypes.byref(raw_ace))
                    or not raw_ace
                ):
                    fail("ace-verification")
                ace = ctypes.cast(raw_ace, ctypes.POINTER(AccessAllowedAce)).contents
                if (
                    ace.Header.AceType != access_allowed_ace_type
                    or ace.Header.AceFlags != 0
                    or ace.Header.AceSize < ctypes.sizeof(AccessAllowedAce)
                ):
                    fail("ace-verification")
                ace_sid = psid(raw_ace.value + AccessAllowedAce.SidStart.offset)
                sid_length = advapi32.GetLengthSid(ace_sid)
                if (
                    sid_length == 0
                    or AccessAllowedAce.SidStart.offset + sid_length
                    > ace.Header.AceSize
                ):
                    fail("ace-verification")
                for rule_index, (principal, rights) in enumerate(rules):
                    if same_sid(ace_sid, principal):
                        if seen[rule_index] or ace.Mask != rights:
                            fail("ace-verification")
                        seen[rule_index] = True
                        break
                else:
                    fail("ace-verification")
            if not all(seen):
                fail("ace-verification")
        except _WindowsAclStageError:
            raise
        except Exception:
            fail("ace-verification")
        finally:
            free_local(descriptor)
    finally:
        free_local(system_sid)
        free_local(administrators_sid)


def _secure_token_file_windows(token_path: Path) -> None:
    """Replace the token ACL with an explicit local-user allowlist."""
    _require_windows_regular_token_file(token_path)
    try:
        _secure_token_file_windows_native(token_path)
    except _WindowsAclStageError as exc:
        raise _windows_acl_error(exc.stage) from None
    except Exception:
        raise _windows_acl_error("dacl-apply") from None


def _secure_token_file(token_path: Path) -> None:
    if os.name == "nt":
        _secure_token_file_windows(token_path)
    else:
        _secure_token_file_posix(token_path)


def _prepare_new_token(token_path: Path, token: str) -> Path:
    """Write a private token to a hardened staging file in its final directory."""
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{token_path.name}.",
        suffix=".tmp",
        dir=token_path.parent,
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        # Harden the empty temporary file before its secret is written.  On
        # Windows this removes any directory-inherited read permissions.
        if os.name == "nt":
            _secure_token_file_windows(temporary)
        else:
            parent_descriptor, parent = _open_posix_parent(token_path)
            try:
                _secure_token_descriptor_posix(descriptor, parent)
            finally:
                os.close(parent_descriptor)
        handle = os.fdopen(descriptor, "w", encoding="utf-8")
        descriptor = -1
        with handle:
            handle.write(token + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return temporary
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise


def _write_new_token(token_path: Path, token: str) -> None:
    temporary = _prepare_new_token(token_path, token)
    replaced = False
    try:
        os.replace(temporary, token_path)
        replaced = True
        _secure_token_file(token_path)
    except Exception:
        if not replaced:
            temporary.unlink(missing_ok=True)
        # The temporary file is hardened before the token is written, so an
        # atomic replacement already leaves a private token behind.  Never
        # perform a check-then-delete against this path after replacement:
        # an attacker could swap its identity between those operations.
        raise


def _create_new_token_exclusively(token_path: Path, token: str) -> bool:
    """Publish a staged token only when no peer has created one first.

    ``os.link`` is an atomic create-if-absent operation on both supported
    platforms.  It lets two independently started Bot/Manager processes
    converge on one control credential without ever replacing a peer's token.
    The staging inode was secured before its secret was written, and the
    destination is verified again after publication.
    """
    temporary = _prepare_new_token(token_path, token)
    published = False
    try:
        try:
            os.link(temporary, token_path)
        except FileExistsError:
            return False
        except OSError as exc:
            if _hardlinks_unsupported(exc):
                return _publish_staged_token_with_lock(token_path, temporary)
            raise TokenSecurityError(
                "Could not atomically create private token"
            ) from exc
        published = True
        temporary.unlink()
        _secure_token_file(token_path)
        return True
    finally:
        if not published:
            temporary.unlink(missing_ok=True)


def _hardlinks_unsupported(exc: OSError) -> bool:
    """Return whether this filesystem cannot publish a token via hardlink."""
    unsupported_errnos = {errno.EOPNOTSUPP, errno.ENOTSUP}
    return (
        exc.errno in unsupported_errnos
        or getattr(exc, "winerror", None) == 50  # ERROR_NOT_SUPPORTED
    )


def _token_publication_lock_path(token_path: Path) -> Path:
    """Return the lock that serializes no-hardlink token publication."""
    return token_path.with_name(f".{token_path.name}.publish.lock")


def _regular_token_exists_without_following(token_path: Path) -> bool:
    """Check whether a final token already exists without accepting links."""
    try:
        metadata = os.lstat(token_path)
    except FileExistsError:
        # ``lstat`` does not normally use this, but keep the publication
        # decision fail-closed on platforms that surface it this way.
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise TokenSecurityError("Private token is unavailable") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or getattr(metadata, "st_file_attributes", 0)
        & _WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise TokenSecurityError("Private token must be a regular file")
    return True


def _publish_staged_token_with_lock(token_path: Path, temporary: Path) -> bool:
    """Publish a complete staged token when hardlinks are unsupported.

    An OS-backed lock elects one publisher.  That publisher writes and fsyncs
    the secret in a hardened temporary file *before* replacing the final path;
    other processes wait for the lock and then re-read the fully published
    token.  No consumer can observe a non-empty prefix at ``token_path``.
    """
    lock = FileLock(str(_token_publication_lock_path(token_path)))
    try:
        lock.acquire(timeout=_TOKEN_PUBLICATION_TIMEOUT_SECONDS)
    except FileLockTimeout as exc:
        raise TokenSecurityError(
            "Timed out waiting for private token publication"
        ) from exc

    try:
        if _regular_token_exists_without_following(token_path):
            return False
        os.replace(temporary, token_path)
        _secure_token_file(token_path)
        return True
    finally:
        lock.release()


def ensure_private_token(
    path: str | os.PathLike[str],
    *,
    token_bytes: int = 48,
    min_length: int = 32,
    exclusive_create: bool = False,
) -> str:
    """Read or create a private token using the platform-safe file policy.

    Callers that share a credential across independently launched processes
    set ``exclusive_create`` so startup order cannot make them return different
    newly generated tokens.
    """
    token_path = Path(path)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    while True:
        existing_token = False
        if os.name != "nt":
            token = _read_secured_token_posix(token_path)
            if token is not None and len(token) >= min_length:
                return token
            existing_token = token is not None
        elif _windows_regular_token_exists(token_path):
            _secure_token_file(token_path)
            try:
                token = _read_token_windows(token_path)
            except FileNotFoundError as exc:
                raise TokenSecurityError("Manager API token is unavailable") from exc
            if len(token) >= min_length:
                return token
            existing_token = True

        if exclusive_create and existing_token:
            raise TokenSecurityError("Private token is invalid")

        token = secrets.token_urlsafe(token_bytes)
        if not exclusive_create:
            _write_new_token(token_path, token)
            return token
        if _create_new_token_exclusively(token_path, token):
            return token


def read_private_token(path: str | os.PathLike[str]) -> str:
    """Read a private token through the platform-safe no-follow reader."""
    try:
        token_path = Path(path)
        return (
            _read_token_windows(token_path)
            if os.name == "nt"
            else _read_token_posix_readonly(token_path)
        )
    except FileNotFoundError as exc:
        raise FileNotFoundError("Manager API token is unavailable") from exc


def ensure_api_token(path: str | os.PathLike[str]) -> str:
    return ensure_private_token(path)


def read_api_token(path: str | os.PathLike[str]) -> str:
    token = read_private_token(path)
    if not token:
        raise ValueError("Manager API token is empty")
    return token


def token_matches(expected: str, supplied: str) -> bool:
    return bool(supplied) and hmac.compare_digest(expected, supplied)
