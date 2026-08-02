"""Subprocess contract tests for the packaged Dashboard launcher entry."""

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tests.support.dashboard.paths import repo_root


def test_windowed_frozen_entry_imports_without_console_streams(tmp_path: Path) -> None:
    """模拟 PyInstaller console=False 从 Explorer 双击启动：frozen 且无标准流。

    RC17 阻断回归：GUI 程序从 Explorer 启动时 sys.stdout/sys.stderr 为 None,
    入口 import 链拉起 Bot logger 后在 Loguru None sink 上崩溃, 进程在 import
    阶段弹窗退出。修复后入口 import 必须在 None 流下安静完成。
    """
    app_dir = tmp_path / "DicePP"
    app_dir.mkdir()
    marker = tmp_path / "entry-ok.txt"
    project_root = repo_root()
    code = textwrap.dedent(
        f"""
        import os
        import runpy
        import sys

        for key in (
            "DICEPP_PROJECT_ROOT",
            "DASHBOARD_HOST",
            "DASHBOARD_PORT",
            "DICEPP_MANAGER_RUNTIME",
            "DICEPP_MANAGER_PROCESS_COMMAND",
            "DICEPP_MANAGER_PROCESS_CWD",
        ):
            os.environ.pop(key, None)

        sys.frozen = True
        sys.executable = {str(app_dir / "DicePP.exe")!r}
        sys.argv = [sys.executable]
        # PyInstaller windowed（console=False）进程没有控制台流
        sys.stdout = None
        sys.stderr = None
        runpy.run_path(
            {str(project_root / "scripts" / "build" / "dashboard_entry.py")!r},
            run_name="dashboard_entry_test",
        )
        with open({str(marker)!r}, "w", encoding="utf-8") as output:
            output.write("ok")
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert marker.read_text(encoding="utf-8") == "ok"


@pytest.mark.parametrize(
    "hook",
    ["--veloapp-install", "--veloapp-updated", "--veloapp-obsolete", "--veloapp-uninstall"],
)
def test_velopack_hook_exits_zero_before_launcher_import(hook: str) -> None:
    """Velopack 安装/更新/卸载钩子必须快速退出 0, 不得照常启动常驻进程。

    RC17 回归：安装器以 --veloapp-* 参数调用主 exe 并期望其快速返回,
    当前入口无视参数照常启动, 安装器 30 秒超时后强杀。这些钩子对 DicePP
    全是 no-op（快捷方式等由安装器负责）。
    """
    project_root = repo_root()
    code = textwrap.dedent(
        f"""
        import json
        import runpy
        import sys

        sys.argv = ["DicePP.exe", {hook!r}, "3.0.0-rc.18"]
        try:
            runpy.run_path(
                {str(project_root / "scripts" / "build" / "dashboard_entry.py")!r},
                run_name="dashboard_entry_test",
            )
        except SystemExit as exc:
            print(json.dumps({{
                "exit_code": exc.code,
                "launcher_imported": "dashboard.src.launcher" in sys.modules,
            }}))
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=True,
    )

    payload = json.loads(result.stdout)
    assert payload["exit_code"] == 0
    assert payload["launcher_imported"] is False


@pytest.mark.parametrize("argument", ["--veloapp", "--veloapplication"])
def test_non_hook_velopack_prefix_continues_to_launcher_import(argument: str) -> None:
    """Similar user arguments must not be mistaken for lifecycle hooks."""
    project_root = repo_root()
    code = textwrap.dedent(
        f"""
        import runpy
        import sys

        sys.argv = ["DicePP.exe", {argument!r}]
        runpy.run_path(
            {str(project_root / "scripts" / "build" / "dashboard_entry.py")!r},
            run_name="dashboard_entry_test",
        )
        assert "dashboard.src.launcher" in sys.modules
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_dashboard_entry_preconfigures_env_before_dashboard_import(
    tmp_path: Path,
) -> None:
    app_dir = tmp_path / "DicePP"
    app_dir.mkdir()
    project_root = repo_root()
    code = textwrap.dedent(
        f"""
        import json
        import os
        import runpy
        import sys

        for key in (
            "DICEPP_PROJECT_ROOT",
            "DASHBOARD_HOST",
            "DASHBOARD_PORT",
            "DICEPP_MANAGER_RUNTIME",
            "DICEPP_MANAGER_PROCESS_COMMAND",
            "DICEPP_MANAGER_PROCESS_CWD",
        ):
            os.environ.pop(key, None)

        sys.frozen = True
        sys.executable = {str(app_dir / "DicePP.exe")!r}
        runpy.run_path(
            {str(project_root / "scripts" / "build" / "dashboard_entry.py")!r},
            run_name="dashboard_entry_test",
        )

        from dashboard.src.config import DashboardPaths

        print(json.dumps({{
            "project_root": str(DashboardPaths.PROJECT_ROOT),
            "runtime_log": str(DashboardPaths.RUNTIME_LOG),
            "env_root": os.environ["DICEPP_PROJECT_ROOT"],
            "runtime_command": os.environ["DICEPP_MANAGER_PROCESS_COMMAND"],
        }}))
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=True,
    )

    payload = json.loads(result.stdout)
    assert Path(payload["project_root"]) == app_dir
    assert Path(payload["runtime_log"]) == app_dir / "data" / "logs" / "dicepp-runtime.log"
    assert Path(payload["env_root"]) == app_dir
    assert "DicePP-Runtime.exe" in payload["runtime_command"]


@pytest.mark.parametrize("flag", ["--background", "--manager-tray"])
def test_background_entry_logs_bootstrap_failure_without_traceback_modal(
    tmp_path: Path,
    flag: str,
) -> None:
    install_root = tmp_path / "DicePP"
    program_dir = install_root / "current"
    source_config = program_dir / "config" / "global.json"
    source_config.parent.mkdir(parents=True)
    source_config.write_text("{}", encoding="utf-8")
    # A directory at the destination makes bootstrap fail before Dashboard's
    # regular logger imports, which is the PyInstaller-modal regression path.
    (install_root / "config" / "global.json").mkdir(parents=True)
    project_root = repo_root()
    code = textwrap.dedent(
        f"""
        import runpy
        import sys

        sys.frozen = True
        sys.executable = {str(program_dir / "DicePP.exe")!r}
        sys.argv = [sys.executable, {flag!r}]
        runpy.run_path(
            {str(project_root / "scripts" / "build" / "dashboard_entry.py")!r},
            run_name="__main__",
        )
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr
    log = install_root / "data" / "logs" / "dicepp-runtime.log"
    assert "bootstrap fatal error: RuntimeError" in log.read_text(encoding="utf-8")


@pytest.mark.parametrize("flag", ["--background", "--manager-tray"])
def test_background_entry_silently_converts_launcher_runtime_failure(
    tmp_path: Path,
    flag: str,
) -> None:
    app_dir = tmp_path / "DicePP"
    app_dir.mkdir()
    runtime_log = app_dir / "data" / "logs" / "dicepp-runtime.log"
    project_root = repo_root()
    code = textwrap.dedent(
        f"""
        import os
        import runpy
        import sys

        sys.frozen = True
        sys.executable = {str(app_dir / "DicePP.exe")!r}
        os.environ["DICEPP_RUNTIME_LOG"] = {str(runtime_log)!r}

        from dashboard.src import launcher

        def fail_log_rotation():
            raise RuntimeError("injected launcher runtime failure")

        launcher.rotate_runtime_log = fail_log_rotation
        sys.argv = [sys.executable, {flag!r}]
        runpy.run_path(
            {str(project_root / "scripts" / "build" / "dashboard_entry.py")!r},
            run_name="__main__",
        )
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr
    assert "fatal error: RuntimeError: injected launcher runtime failure" in (
        runtime_log.read_text(encoding="utf-8")
    )
