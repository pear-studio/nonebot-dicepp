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
import time
import warnings
from pathlib import Path

import psutil
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
    # Startup probes allow two bounded 30-second phases and cleanup can take
    # another 35 seconds on a failing onefile process tree.
    pytest.mark.timeout(120),
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
    while manager_port == port:
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


def _owned_listener_pids(exe: Path, ports: set[int]) -> set[int]:
    """Return DicePP listeners owned by the package root under test."""
    package_root = exe.parent.resolve()
    pids: set[int] = set()
    for connection in psutil.net_connections(kind="tcp"):
        if (
            connection.status != psutil.CONN_LISTEN
            or connection.pid is None
            or not connection.laddr
            or connection.laddr.port not in ports
        ):
            continue
        try:
            process_exe = Path(psutil.Process(connection.pid).exe()).resolve()
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            continue
        if (
            process_exe.name.casefold() == "dicepp.exe"
            and process_exe.is_relative_to(package_root)
        ):
            pids.add(connection.pid)
    return pids


def _stop_process_tree(
    proc: subprocess.Popen,
    exe: Path,
    ports: set[int],
) -> None:
    """Terminate the stable stub, onefile parent and actual service process."""
    targets = _owned_listener_pids(exe, ports)
    if proc.poll() is None:
        targets.add(proc.pid)

    errors: list[str] = []
    for pid in sorted(targets):
        try:
            result = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except subprocess.TimeoutExpired:
            try:
                psutil.Process(pid).kill()
            except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
                pass
            errors.append(f"taskkill timed out for pid {pid}")
            continue
        if result.returncode != 0 and psutil.pid_exists(pid):
            errors.append(
                f"taskkill failed for pid {pid}: "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )

    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)
        errors.append(f"launcher process {proc.pid} required direct kill")

    remaining = _owned_listener_pids(exe, ports)
    if remaining:
        errors.append(f"DicePP listeners remain after cleanup: {sorted(remaining)}")
    if errors:
        raise RuntimeError("; ".join(errors))


def _stop_process_tree_preserving_failure(
    proc: subprocess.Popen,
    exe: Path,
    ports: set[int],
) -> None:
    """Do not let a teardown failure hide the startup assertion that preceded it."""
    primary_error = sys.exc_info()[1]
    try:
        _stop_process_tree(proc, exe, ports)
    except Exception as cleanup_error:
        if primary_error is None:
            raise
        warnings.warn(
            f"Packaged process cleanup also failed: {cleanup_error}",
            RuntimeWarning,
            stacklevel=2,
        )


def _wait_for_startup_complete(
    log_path: Path,
    *,
    timeout: float,
) -> str:
    """Wait for the launcher stage after Manager, runtime and tray setup."""
    deadline = time.monotonic() + timeout
    latest = ""
    while time.monotonic() < deadline:
        if log_path.is_file():
            latest = log_path.read_text(encoding="utf-8", errors="replace")
            if "launcher | startup complete" in latest:
                return latest
        time.sleep(0.1)
    raise TimeoutError(
        "launcher did not record startup completion; latest runtime log:\n"
        f"{latest or '<missing>'}"
    )


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
    ports = {
        int(env["DASHBOARD_PORT"]),
        int(env["DICEPP_MANAGER_PORT"]),
    }
    runtime_log = (
        Path(env["DICEPP_PROJECT_ROOT"])
        / "data"
        / "logs"
        / "dicepp-runtime.log"
    )

    proc = _launch_detached(exe, args, env)
    dashboard_ready = False
    try:
        wait_for_server(f"{base_url}/api/auth/status", timeout=30)
        dashboard_ready = True
        log_text = _wait_for_startup_complete(runtime_log, timeout=30)
        listener_pids = _owned_listener_pids(exe, ports)
        assert listener_pids, "no DicePP process owns the ready service ports"
        assert "launcher | DicePPDashboard server started" in log_text
        assert "launcher | DicePPManager server started" in log_text
        assert "bootstrap fatal error" not in log_text
    except Exception as exc:
        exit_state = (
            f"process exited with code {proc.returncode}"
            if proc.poll() is not None
            else (
                "process still running but startup completion was not recorded"
                if dashboard_ready
                else "process still running but Dashboard port never became ready"
            )
        )
        pytest.fail(
            "Packaged Dashboard executable did not become ready without "
            f"console streams ({exit_state}): {exc}"
        )
    finally:
        _stop_process_tree_preserving_failure(proc, exe, ports)


@pytest.mark.parametrize(
    "hook",
    ["--veloapp-install", "--veloapp-updated", "--veloapp-obsolete", "--veloapp-uninstall"],
)
def test_packaged_velopack_hooks_exit_quickly_without_starting_services(
    tmp_path: Path,
    hook: str,
) -> None:
    """The actual windowed executable must honour Velopack lifecycle hooks."""
    exe = _dashboard_exe()
    env, _base_url = _launch_env(tmp_path)
    ports = {
        int(env["DASHBOARD_PORT"]),
        int(env["DICEPP_MANAGER_PORT"]),
    }
    proc = _launch_detached(exe, [hook, "3.0.0-rc.18"], env)

    try:
        assert proc.wait(timeout=10) == 0
    except subprocess.TimeoutExpired:
        _stop_process_tree(proc, exe, ports)
        pytest.fail(f"Packaged Dashboard did not exit promptly for {hook}")

    assert not (
        Path(env["DICEPP_PROJECT_ROOT"])
        / "data"
        / "logs"
        / "dicepp-runtime.log"
    ).exists()
