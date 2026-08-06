"""Windows package acceptance: DicePP.exe launched without any console.

RC17 阻断回归的防回归：PyInstaller ``console=False`` 的主程序从 Explorer
双击启动时 ``sys.stdout``/``sys.stderr`` 为 None, RC17 在该形态下于 import
阶段崩溃（Loguru None sink + Manager import 拉起 Bot 业务包）。本文件以
``DETACHED_PROCESS`` 启动真实打包产物, 不继承控制台、不重定向任何标准流
（禁止 ``capture_output``/``stdout=log_file``/shell 调用）, 覆盖无参数、
``--background``、``--manager-tray`` 三种入口模式, 通过 Dashboard 端口
就绪判定进程成功存活。仅在 Windows 验收侧运行；Linux 下正确 skip。
"""

import json
import os
import subprocess
import sys
import warnings
from pathlib import Path

import pytest

from tests.support.dashboard.playwright import find_free_port, wait_for_server
from tests.support.dashboard.paths import repo_root


_package_smoke_enabled = os.environ.get("DICEPP_WINDOWS_PACKAGE_SMOKE") == "1"
_running_on_windows = sys.platform == "win32"

pytestmark = [
    pytest.mark.skipif(
        not _package_smoke_enabled,
        reason="Windows package smoke is opt-in and requires built executables",
    ),
    pytest.mark.skipif(
        not _running_on_windows,
        reason="Windows package smoke only runs on Windows",
    ),
    # Onefile extraction and local Manager startup need more than the
    # suite-wide 30-second budget, while remaining strictly bounded.
    pytest.mark.timeout(60),
]

# Win32 process creation flags: 子进程不继承父进程控制台, 对 GUI 子系统程序
# 等价于 Explorer 双击——sys.stdout/sys.stderr/stdin 均为 None。
_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200


def _dashboard_exe() -> Path:
    configured_exe = os.environ.get("DICEPP_DASHBOARD_EXE")
    exe = (
        Path(configured_exe).resolve()
        if configured_exe
        else repo_root() / "dist" / "DicePP" / "DicePP.exe"
    )
    if not exe.exists():
        pytest.fail(f"Dashboard executable does not exist: {exe}")
    return exe


def _launch_env(tmp_path: Path) -> tuple[dict[str, str], str]:
    """Mirror the package-smoke environment, minus any stdio redirection."""
    project_root = tmp_path / "dicepp-project"
    (project_root / "config" / "bots").mkdir(parents=True, exist_ok=True)
    (project_root / "dashboard" / "data").mkdir(parents=True, exist_ok=True)
    (project_root / "config" / "global.json").write_text(
        json.dumps({"app": {"name": "dicepp-windows-detached", "version": "1.0.0"}})
    )

    port = find_free_port()
    manager_port = find_free_port()
    env = os.environ.copy()
    for key in (
        "DICEPP_MANAGER_HOST",
        "DICEPP_MANAGER_PORT",
        "DICEPP_MANAGER_URL",
        "DICEPP_MANAGER_TOKEN_FILE",
        "DICEPP_MANAGER_RUNTIME",
        "DICEPP_MANAGER_PROCESS_COMMAND",
        "DICEPP_MANAGER_PROCESS_CWD",
        "DICEPP_MANAGER_PROCESS_STOP_TIMEOUT",
        "DICEPP_MANAGER_RELEASE_SCHEDULER",
    ):
        env.pop(key, None)
    env["DICEPP_PROJECT_ROOT"] = str(project_root)
    env["DASHBOARD_HOST"] = "127.0.0.1"
    env["DASHBOARD_PORT"] = str(port)
    env["DICEPP_DASHBOARD_OPEN_BROWSER"] = "0"
    env["DICEPP_MANAGER_HOST"] = "127.0.0.1"
    env["DICEPP_MANAGER_PORT"] = str(manager_port)
    env["DICEPP_MANAGER_URL"] = f"http://127.0.0.1:{manager_port}"
    env["DICEPP_MANAGER_RUNTIME"] = "unavailable"
    # Package smoke must stay offline; Manager itself still starts and owns
    # the config write, while the unavailable runtime prevents Bot startup.
    env["DICEPP_MANAGER_RELEASE_SCHEDULER"] = "0"
    return env, f"http://127.0.0.1:{port}"


def _launch_detached(exe: Path, args: list[str], env: dict[str, str]) -> subprocess.Popen:
    """Start the GUI executable the way Explorer does: no console, no stdio.

    不得传 stdout/stderr（捕获或日志文件都会给子进程提供有效流, 掩盖
    None-stream 崩溃）；也不得经由 shell/PowerShell 启动。
    """
    return subprocess.Popen(
        [str(exe), *args],
        cwd=str(exe.parent),
        env=env,
        creationflags=_DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP,
    )


def _stop_process_tree(proc: subprocess.Popen) -> None:
    """Terminate the onefile parent and extracted child before the next test."""
    result = subprocess.run(
        ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        warnings.warn(
            "Packaged Dashboard 进程树等待 10 秒后仍未退出："
            f"{result.stderr.strip() or result.stdout.strip()}",
            RuntimeWarning,
            stacklevel=2,
        )
        proc.kill()
        proc.wait()


@pytest.mark.parametrize(
    "args",
    [[], ["--background"], ["--manager-tray"]],
    ids=["no-args", "background", "manager-tray"],
)
def test_detached_exe_boots_without_console_streams(
    tmp_path: Path,
    args: list[str],
) -> None:
    """Explorer 双击形态（无控制台/标准流）下三种入口模式都必须正常起服务。"""
    exe = _dashboard_exe()
    env, base_url = _launch_env(tmp_path)

    proc = _launch_detached(exe, args, env)
    try:
        wait_for_server(f"{base_url}/api/auth/status", timeout=30)
        assert proc.poll() is None, (
            f"packaged executable exited early with code {proc.returncode}"
        )
    except Exception as exc:
        exit_state = (
            f"process exited with code {proc.returncode}"
            if proc.poll() is not None
            else "process still running but Dashboard port never became ready"
        )
        pytest.fail(
            "Packaged Dashboard executable did not become ready without "
            f"console streams ({exit_state}): {exc}"
        )
    finally:
        _stop_process_tree(proc)
