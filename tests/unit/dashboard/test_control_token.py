"""Dedicated Bot↔Manager credential boundary tests."""

from concurrent.futures import ThreadPoolExecutor
import errno
import os
import stat

import pytest
from dicepp_data import InstanceLayout
import dicepp_manager.auth as manager_auth
from dicepp_manager.auth import TokenSecurityError
from plugins.DicePP.core.data.schema import DicePPDatabase
from dicepp_control.control_token import (
    ensure_token,
    read_token,
    token_path,
)


def test_control_token_uses_manager_boundary_and_ignores_legacy_database(tmp_path):
    layout = InstanceLayout.from_root(tmp_path)
    legacy_token = DicePPDatabase(layout).ensure_local_control_token()

    token = ensure_token(tmp_path)

    assert token != legacy_token
    assert read_token(tmp_path) == token
    assert token_path(tmp_path) == layout.manager_control_token
    assert token_path(tmp_path).is_relative_to(layout.manager_control_dir)
    assert not token_path(tmp_path).is_relative_to(layout.data_root)


def test_control_token_bootstrap_converges_for_bot_and_manager_start_race(tmp_path):
    with ThreadPoolExecutor(max_workers=8) as executor:
        tokens = list(executor.map(lambda _index: ensure_token(tmp_path), range(8)))

    assert len(set(tokens)) == 1
    assert read_token(tmp_path) == tokens[0]


def test_control_token_bootstrap_converges_without_hardlink_support(
    monkeypatch,
    tmp_path,
):
    """An O_EXCL fallback must preserve one token for concurrent starters."""
    monkeypatch.setattr(
        manager_auth.os,
        "link",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError(errno.EOPNOTSUPP, "hardlinks are unsupported")
        ),
    )

    with ThreadPoolExecutor(max_workers=8) as executor:
        tokens = list(executor.map(lambda _index: ensure_token(tmp_path), range(8)))

    assert len(set(tokens)) == 1
    assert read_token(tmp_path) == tokens[0]


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode policy")
def test_control_token_hardens_existing_file_for_deployment_user(tmp_path):
    path = token_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("control-token", encoding="utf-8")
    path.chmod(0o644)

    assert read_token(tmp_path) is None
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
    assert ensure_token(tmp_path) == "control-token"

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor policy")
def test_control_token_reads_safe_existing_file_without_write_access(
    monkeypatch,
    tmp_path,
):
    """A Bot consumer must not chmod or create inside its read-only mount."""
    token = ensure_token(tmp_path)
    original_open = manager_auth.os.open

    def readonly_open(path, flags, *args, **kwargs):
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT):
            raise OSError(errno.EROFS, "read-only file system", str(path))
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(manager_auth.os, "open", readonly_open)
    # The production policy checks this capability before using ``dir_fd``.
    # Keep that declaration true for the wrapper, which transparently forwards
    # its descriptor-relative calls to the real POSIX ``os.open``.
    monkeypatch.setattr(
        manager_auth.os,
        "supports_dir_fd",
        manager_auth.os.supports_dir_fd | {readonly_open},
    )
    monkeypatch.setattr(
        manager_auth.os,
        "fchmod",
        lambda *_args: (_ for _ in ()).throw(
            OSError(errno.EROFS, "read-only file system")
        ),
    )

    assert ensure_token(tmp_path) == token


def test_control_token_rejects_symlink_without_reading_target(tmp_path):
    path = token_path(tmp_path)
    path.parent.mkdir(parents=True)
    target = tmp_path / "target-token"
    target.write_text("do not read", encoding="utf-8")
    try:
        path.symlink_to(target)
    except OSError:
        pytest.skip("this Windows account cannot create symlinks")

    assert read_token(tmp_path) is None
    with pytest.raises(TokenSecurityError):
        ensure_token(tmp_path)

    assert target.read_text(encoding="utf-8") == "do not read"


def test_control_token_rejects_existing_empty_file(tmp_path):
    path = token_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("", encoding="utf-8")

    with pytest.raises(TokenSecurityError, match="^Private token is invalid$"):
        ensure_token(tmp_path)
