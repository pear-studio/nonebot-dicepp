"""Manager/Dashboard 进程 import 隔离的防回归测试。

RC17 架构回归：``dicepp_manager.control`` / ``dicepp_manager.factory`` 从
``plugins.DicePP.module.dashboard_reporter`` import 控制协议与凭据，导致
Manager 与 Dashboard GUI 进程在 import 阶段拉起整个 Bot 业务包（含 logger
等副作用）。控制协议已迁至无副作用的 ``dicepp_control`` 包，本测试在隔离
子进程内断言 Manager 模块 import 不再引入任何 ``plugins.DicePP*`` 模块。
"""

import os
from pathlib import Path
import subprocess
import sys


def _repository_root() -> Path:
    return next(
        parent
        for parent in Path(__file__).resolve().parents
        if (parent / "pyproject.toml").is_file()
    )


def test_manager_modules_do_not_import_dicepp_bot_package(tmp_path: Path) -> None:
    dicepp_src = _repository_root() / "src"
    script = f"""
import sys
sys.path.insert(0, {str(dicepp_src)!r})

import dicepp_manager.control
import dicepp_manager.factory

offenders = sorted(
    name for name in sys.modules
    if name == "plugins" or name.startswith("plugins.")
)
assert not offenders, f"Manager import pulled in Bot package: {{offenders}}"
"""
    env = os.environ.copy()
    env["DICEPP_PROJECT_ROOT"] = str(tmp_path)
    proc = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_control_package_does_not_import_manager_or_bot_packages(
    tmp_path: Path,
) -> None:
    """Shared control primitives stay independent from process-owned packages."""
    dicepp_src = _repository_root() / "src"
    script = f"""
import sys
sys.path.insert(0, {str(dicepp_src)!r})

import dicepp_control.control_token
import dicepp_control.protocol

offenders = sorted(
    name for name in sys.modules
    if name == "dicepp_manager"
    or name.startswith("dicepp_manager.")
    or name == "plugins"
    or name.startswith("plugins.")
)
assert not offenders, f"Control package pulled in process-owned packages: {{offenders}}"
"""
    env = os.environ.copy()
    env["DICEPP_PROJECT_ROOT"] = str(tmp_path)
    proc = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
