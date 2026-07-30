"""Dedicated Bot↔Manager credential boundary tests."""

from concurrent.futures import ThreadPoolExecutor

from dicepp_data import InstanceLayout
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
