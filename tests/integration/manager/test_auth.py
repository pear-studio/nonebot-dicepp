"""Filesystem and platform contracts for the private Manager API token."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import dicepp_manager.auth as manager_auth
from dicepp_manager.auth import read_api_token
from dicepp_manager.client import ManagerClient, ManagerUnavailable
from dicepp_manager.config import ManagerClientSettings


@pytest.mark.skipif(os.name == "nt", reason="POSIX ownership policy")
def test_ensure_api_token_hardens_existing_file_for_deployment_user(tmp_path):
    token_path = tmp_path / "manager" / "state" / "api-token"
    token_path.parent.mkdir(parents=True)
    token_path.write_text("x" * 48 + "\n", encoding="utf-8")
    token_path.chmod(0o644)

    token = manager_auth.ensure_api_token(token_path)

    secured = token_path.stat()
    parent = token_path.parent.stat()
    assert token == "x" * 48
    assert stat.S_IMODE(secured.st_mode) == 0o600
    if os.geteuid() == 0:
        assert (secured.st_uid, secured.st_gid) == (parent.st_uid, parent.st_gid)
    else:
        assert secured.st_uid == os.geteuid()


def test_read_api_token_uses_platform_safe_reader_for_regular_token(tmp_path):
    token_path = tmp_path / "manager" / "state" / "api-token"

    token = manager_auth.ensure_api_token(token_path)

    assert read_api_token(token_path) == token


def test_read_api_token_maps_missing_token_to_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError, match="Manager API token is unavailable"):
        read_api_token(tmp_path / "missing" / "api-token")


@pytest.mark.skipif(os.name == "nt", reason="POSIX readonly descriptor policy")
def test_posix_read_api_token_never_changes_token_permissions(monkeypatch, tmp_path):
    token_path = tmp_path / "manager" / "state" / "api-token"
    token = manager_auth.ensure_api_token(token_path)
    monkeypatch.setattr(
        manager_auth.os,
        "fchown",
        lambda *_args: pytest.fail("read-only token access must not change owner"),
    )
    monkeypatch.setattr(
        manager_auth.os,
        "fchmod",
        lambda *_args: pytest.fail("read-only token access must not change mode"),
    )

    assert read_api_token(token_path) == token


@pytest.mark.skipif(os.name == "nt", reason="POSIX ownership policy")
def test_posix_root_policy_uses_manager_state_owner_and_group(monkeypatch, tmp_path):
    token_path = tmp_path / "manager" / "state" / "api-token"
    token_path.parent.mkdir(parents=True)
    token_path.write_text("x" * 48 + "\n", encoding="utf-8")
    owner = token_path.parent.stat()
    chown_calls = []

    monkeypatch.setattr(manager_auth.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(
        manager_auth.os,
        "fchown",
        lambda descriptor, uid, gid: chown_calls.append((descriptor, uid, gid)),
        raising=False,
    )

    manager_auth._secure_token_file_posix(token_path)

    assert len(chown_calls) == 1
    assert chown_calls[0][1:] == (owner.st_uid, owner.st_gid)
    assert stat.S_IMODE(token_path.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX ownership policy")
def test_posix_policy_rejects_token_descriptor_owned_by_another_user(monkeypatch):
    foreign_owner = SimpleNamespace(
        st_mode=stat.S_IFREG | 0o600,
        st_uid=2001,
        st_gid=2001,
    )

    monkeypatch.setattr(manager_auth.os, "geteuid", lambda: 1001, raising=False)
    monkeypatch.setattr(manager_auth.os, "fstat", lambda _descriptor: foreign_owner)

    with pytest.raises(
        manager_auth.TokenSecurityError,
        match="owned by the current user",
    ):
        manager_auth._secure_token_descriptor_posix(
            42,
            SimpleNamespace(st_uid=1001, st_gid=1001),
        )


@pytest.mark.skipif(os.name == "nt", reason="POSIX ownership policy")
def test_posix_token_symlink_is_rejected_without_following_target(tmp_path):
    token_path = tmp_path / "manager" / "state" / "api-token"
    token_path.parent.mkdir(parents=True)
    target = tmp_path / "other-user-token"
    target.write_text("do not touch", encoding="utf-8")
    target.chmod(0o644)
    token_path.symlink_to(target)

    with pytest.raises(manager_auth.TokenSecurityError):
        manager_auth.ensure_api_token(token_path)

    assert target.read_text(encoding="utf-8") == "do not touch"
    assert stat.S_IMODE(target.stat().st_mode) == 0o644


@pytest.mark.skipif(os.name == "nt", reason="POSIX ownership policy")
def test_posix_existing_token_permissions_are_changed_only_through_its_descriptor(
    monkeypatch,
    tmp_path,
):
    token_path = tmp_path / "manager" / "state" / "api-token"
    token_path.parent.mkdir(parents=True)
    token_path.write_text("x" * 48 + "\n", encoding="utf-8")
    token_path.chmod(0o644)
    fchown_calls = []
    fchmod_calls = []
    original_fchown = manager_auth.os.fchown
    original_fchmod = manager_auth.os.fchmod

    def fchown(descriptor, uid, gid):
        fchown_calls.append((descriptor, uid, gid))
        original_fchown(descriptor, uid, gid)

    def fchmod(descriptor, mode):
        fchmod_calls.append((descriptor, mode))
        original_fchmod(descriptor, mode)

    monkeypatch.setattr(manager_auth.os, "fchown", fchown)
    monkeypatch.setattr(manager_auth.os, "fchmod", fchmod)
    monkeypatch.setattr(
        manager_auth.os,
        "chown",
        lambda *_args: pytest.fail("path-based chown must not be used"),
    )
    monkeypatch.setattr(
        manager_auth.os,
        "chmod",
        lambda *_args: pytest.fail("path-based chmod must not be used"),
    )

    assert manager_auth.ensure_api_token(token_path) == "x" * 48

    assert fchown_calls or os.geteuid() != 0
    assert fchmod_calls and fchmod_calls[0][1] == 0o600
    assert stat.S_IMODE(token_path.stat().st_mode) == 0o600


def test_windows_acl_hardening_replaces_inherited_permissions(monkeypatch, tmp_path):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    expected_environment = {
        "SystemRoot": r"C:\Windows",
        "WINDIR": r"C:\Windows",
        "DICEPP_MANAGER_TOKEN_ACL_PATH": str(tmp_path / "api-token"),
    }
    hidden_options = {"creationflags": 42, "startupinfo": object()}
    monkeypatch.setattr(
        manager_auth,
        "_windows_powershell_path",
        lambda: Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"),
    )
    monkeypatch.setattr(
        manager_auth,
        "_windows_acl_environment",
        lambda _path: expected_environment,
    )
    monkeypatch.setattr(
        manager_auth,
        "_windows_hidden_subprocess_options",
        lambda: hidden_options,
    )
    monkeypatch.setattr(
        manager_auth,
        "_require_windows_regular_token_file",
        lambda _path: None,
    )
    monkeypatch.setattr(manager_auth.subprocess, "run", run)

    manager_auth._secure_token_file_windows(tmp_path / "api-token")

    command, kwargs = calls[0]
    assert command[:4] == [
        r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
    ]
    script = command[4]
    assert "$acl.SetAccessRuleProtection($true, $false)" in script
    assert "$existingAcl.GetOwner" in script
    assert "$trustedOwnerSids" in script
    assert "$trustedOwnerSids -notcontains $owner.Value" in script
    assert "$trustedOwnerSids -notcontains $verifiedOwner.Value" in script
    assert "S-1-5-18" in script
    assert "S-1-5-32-544" in script
    assert "FileSystemRights]::Modify" in script
    assert command[-1] == script
    assert kwargs["check"] is False
    assert kwargs["stdout"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.DEVNULL
    assert kwargs["env"] == expected_environment
    assert kwargs["timeout"] == manager_auth._WINDOWS_ACL_TIMEOUT_SECONDS
    assert kwargs["shell"] is False
    assert kwargs["creationflags"] == 42
    assert kwargs["startupinfo"] is hidden_options["startupinfo"]


def test_windows_acl_hardening_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(
        manager_auth,
        "_windows_powershell_path",
        lambda: Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"),
    )
    monkeypatch.setattr(
        manager_auth,
        "_windows_acl_environment",
        lambda _path: {"DICEPP_MANAGER_TOKEN_ACL_PATH": str(_path)},
    )
    monkeypatch.setattr(
        manager_auth,
        "_windows_hidden_subprocess_options",
        lambda: {},
    )
    monkeypatch.setattr(
        manager_auth,
        "_require_windows_regular_token_file",
        lambda _path: None,
    )
    monkeypatch.setattr(
        manager_auth.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="access denied",
        ),
    )

    with pytest.raises(
        manager_auth.TokenSecurityError,
        match="^Could not apply Windows ACL to Manager API token$",
    ):
        manager_auth._secure_token_file_windows(tmp_path / "api-token")


def test_windows_acl_hardening_timeout_fails_closed_without_output(monkeypatch, tmp_path):
    monkeypatch.setattr(
        manager_auth,
        "_windows_powershell_path",
        lambda: Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"),
    )
    monkeypatch.setattr(
        manager_auth,
        "_windows_acl_environment",
        lambda _path: {"DICEPP_MANAGER_TOKEN_ACL_PATH": str(_path)},
    )
    monkeypatch.setattr(
        manager_auth,
        "_windows_hidden_subprocess_options",
        lambda: {},
    )
    monkeypatch.setattr(
        manager_auth,
        "_require_windows_regular_token_file",
        lambda _path: None,
    )
    monkeypatch.setattr(
        manager_auth.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired("powershell.exe", 1)
        ),
    )

    with pytest.raises(
        manager_auth.TokenSecurityError,
        match="^Could not apply Windows ACL to Manager API token$",
    ):
        manager_auth._secure_token_file_windows(tmp_path / "api-token")


def test_windows_acl_hardening_unexpected_subprocess_failure_fails_closed(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        manager_auth,
        "_windows_powershell_path",
        lambda: Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"),
    )
    monkeypatch.setattr(
        manager_auth,
        "_windows_acl_environment",
        lambda _path: {"DICEPP_MANAGER_TOKEN_ACL_PATH": str(_path)},
    )
    monkeypatch.setattr(
        manager_auth,
        "_windows_hidden_subprocess_options",
        lambda: {},
    )
    monkeypatch.setattr(
        manager_auth,
        "_require_windows_regular_token_file",
        lambda _path: None,
    )
    monkeypatch.setattr(
        manager_auth.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("untrusted subprocess failure")
        ),
    )

    with pytest.raises(
        manager_auth.TokenSecurityError,
        match="^Could not apply Windows ACL to Manager API token$",
    ):
        manager_auth._secure_token_file_windows(tmp_path / "api-token")


@pytest.mark.skipif(os.name != "nt", reason="Windows reparse-point policy")
def test_windows_reparse_point_is_rejected_before_acl_or_token_read(
    monkeypatch,
    tmp_path,
):
    token_path = tmp_path / "manager" / "state" / "api-token"
    target = tmp_path / "target-token"
    target.write_text("target must not be read", encoding="utf-8")
    original_lstat = manager_auth.os.lstat
    reparse_point = SimpleNamespace(
        st_mode=stat.S_IFREG | 0o600,
        st_file_attributes=manager_auth._WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT,
    )

    def lstat(path):
        if Path(path) == token_path:
            return reparse_point
        return original_lstat(path)

    monkeypatch.setattr(manager_auth.os, "lstat", lstat)
    monkeypatch.setattr(
        manager_auth,
        "_secure_token_file_windows",
        lambda _path: pytest.fail("ACL must not run for a reparse point"),
    )

    with pytest.raises(
        manager_auth.TokenSecurityError,
        match="must be a regular file",
    ):
        manager_auth.ensure_api_token(token_path)

    assert target.read_text(encoding="utf-8") == "target must not be read"


@pytest.mark.skipif(os.name != "nt", reason="Windows reparse-point policy")
def test_windows_dangling_symlink_is_rejected_before_acl_or_token_read(
    monkeypatch,
    tmp_path,
):
    token_path = tmp_path / "manager" / "state" / "api-token"
    token_path.parent.mkdir(parents=True)
    try:
        token_path.symlink_to(tmp_path / "missing-target")
    except OSError:
        pytest.skip("this Windows account cannot create symlinks")
    monkeypatch.setattr(
        manager_auth,
        "_secure_token_file_windows",
        lambda _path: pytest.fail("ACL must not run for a dangling symlink"),
    )

    with pytest.raises(
        manager_auth.TokenSecurityError,
        match="must be a regular file",
    ):
        manager_auth.ensure_api_token(token_path)


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL post-replace policy")
def test_windows_post_replace_acl_failure_keeps_presecured_token(monkeypatch, tmp_path):
    token_path = tmp_path / "manager" / "state" / "api-token"
    calls = []
    unlink_calls = []
    original_unlink = Path.unlink
    original_secure = manager_auth._secure_token_file_windows

    def secure(path):
        calls.append(Path(path))
        if len(calls) == 1:
            return original_secure(path)
        raise manager_auth.TokenSecurityError("post-replace verification failed")

    def unlink(path, *args, **kwargs):
        unlink_calls.append(Path(path))
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(manager_auth, "_secure_token_file_windows", secure)
    monkeypatch.setattr(Path, "unlink", unlink)

    with pytest.raises(
        manager_auth.TokenSecurityError,
        match="^post-replace verification failed$",
    ):
        manager_auth.ensure_api_token(token_path)

    assert len(calls) == 2
    assert token_path.exists()
    assert token_path.read_text(encoding="utf-8").strip()
    assert list(token_path.parent.iterdir()) == [token_path]
    assert unlink_calls == []


@pytest.mark.asyncio
async def test_manager_client_rejects_token_symlink_without_reading_target(tmp_path):
    token_path = tmp_path / "manager" / "state" / "api-token"
    token_path.parent.mkdir(parents=True)
    target = tmp_path / "target-token"
    target.write_text("target must not be read", encoding="utf-8")
    try:
        token_path.symlink_to(target)
    except OSError:
        pytest.skip("this Windows account cannot create symlinks")
    client = ManagerClient(
        ManagerClientSettings(
            base_url="http://127.0.0.1:4091",
            token_path=token_path,
        )
    )

    with pytest.raises(
        ManagerUnavailable,
        match="^Manager credentials are unavailable$",
    ):
        await client.status()

    assert target.read_text(encoding="utf-8") == "target must not be read"
