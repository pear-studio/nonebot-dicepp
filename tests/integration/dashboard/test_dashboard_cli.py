"""Tests for Dashboard administrator commands."""

from dashboard import __main__ as dashboard_main
from dashboard.src.auth import verify_password_db
from dashboard.src.config import DashboardPaths


def test_admin_init_sets_password_without_starting_server(
    tmp_dashboard_paths, monkeypatch
):
    answers = iter(["secure-password", "secure-password"])
    monkeypatch.setattr(dashboard_main.getpass, "getpass", lambda _prompt: next(answers))

    result = dashboard_main._admin_init()

    assert result == 0
    assert verify_password_db(str(DashboardPaths.DASHBOARD_DB), "secure-password")


def test_admin_init_rejects_existing_password(tmp_dashboard_paths, monkeypatch):
    answers = iter(["secure-password", "secure-password"])
    monkeypatch.setattr(dashboard_main.getpass, "getpass", lambda _prompt: next(answers))
    assert dashboard_main._admin_init() == 0

    assert dashboard_main._admin_init() == 1


def test_ensure_dirs_does_not_write_config(tmp_dashboard_paths):
    """Dashboard 启动准备只创建自己的管理数据目录，不改写 config/。"""
    user_config = DashboardPaths.CONFIG_USER
    assert not user_config.exists()

    dashboard_main.ensure_dirs()

    assert not user_config.exists()
    assert DashboardPaths.DATA_DIR.is_dir()
