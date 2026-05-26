"""为 src/dicepp_admin/* 模块测试设置 sys.path。

admin 跟 DicePP plugin 是平级的独立 package。父 conftest 只加了
src/plugins/DicePP 到 sys.path（α 历史包结构），admin 需要再加
src/ 让 `import dicepp_admin` 工作。
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


@pytest.fixture()
def tmp_admin_paths(monkeypatch, tmp_path):
    """把 AdminPaths 重定向到一个临时目录，保证测试不污染真实 data/admin/。

    同时 patch 各模块在 import 时已经绑定的常量（_LL_CONFIG_FILE 等），
    避免它们仍指向真实 admin 目录。
    """
    from dicepp_admin import config as cfg

    base = tmp_path / "admin_test"
    base.mkdir(parents=True, exist_ok=True)

    data_dir = base
    admin_dir = data_dir / "admin"
    instances_dir = data_dir / "instances"
    llonebot_dir = data_dir / "llonebot"
    for d in (admin_dir, instances_dir, llonebot_dir):
        d.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(cfg.AdminPaths, "PROJECT_ROOT", base)
    monkeypatch.setattr(cfg.AdminPaths, "DATA_DIR", data_dir)
    monkeypatch.setattr(cfg.AdminPaths, "ADMIN_DIR", admin_dir)
    monkeypatch.setattr(cfg.AdminPaths, "INSTANCES_DIR", instances_dir)
    monkeypatch.setattr(cfg.AdminPaths, "LLONEBOT_DIR", llonebot_dir)
    monkeypatch.setattr(cfg.AdminPaths, "AUTH_FILE", admin_dir / "auth.json")
    monkeypatch.setattr(cfg.AdminPaths, "SESSION_FILE", admin_dir / "sessions.json")
    monkeypatch.setattr(cfg.AdminPaths, "INSTANCES_FILE", admin_dir / "instances.json")
    monkeypatch.setattr(cfg.AdminPaths, "AUDIT_DB", admin_dir / "audit.db")

    # llonebot_manager 在模块加载时绑定 _LL_CONFIG_FILE / _BUNDLE_DIR_DEFAULT
    # 到真实 admin 目录，需要重 patch
    try:
        from dicepp_admin import llonebot_manager as lm
        monkeypatch.setattr(lm, "_LL_CONFIG_FILE", llonebot_dir / "config.json")
        monkeypatch.setattr(lm, "_BUNDLE_DIR_DEFAULT", base / "bin" / "llonebot")
    except ImportError:
        pass

    return base
