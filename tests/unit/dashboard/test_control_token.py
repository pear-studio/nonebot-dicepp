"""Dedicated Bot↔Manager credential boundary tests."""

from concurrent.futures import ThreadPoolExecutor
import os
import stat

import pytest
from dicepp_data import InstanceLayout
from dicepp_manager.auth import TokenSecurityError
from plugins.DicePP.core.data.schema import DicePPDatabase
from plugins.DicePP.module.dashboard_reporter.control_token import (
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
