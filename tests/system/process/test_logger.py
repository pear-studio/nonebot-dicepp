import os
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest



def test_bot_import_logs_tolerate_nonebot_reconfigure(tmp_path: Path):
    """NoneBot startup logs should not break handlers that include request_id."""
    env = os.environ.copy()
    env["DICEPP_PROJECT_ROOT"] = str(tmp_path)
    proc = subprocess.run(
        [sys.executable, "-c", "import bot; print('imported')"],
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "imported" in proc.stdout
    assert "\x1b[" not in proc.stdout
    assert "\x1b[" not in proc.stderr
    assert "--- Logging error" not in proc.stderr
    assert "KeyError: 'request_id'" not in proc.stderr


def test_redirected_stderr_logger_outputs_utf8_without_ansi(
    tmp_path: Path,
) -> None:
    """Redirected runtime stderr remains UTF-8 even from an ANSI stdio process."""
    script = tmp_path / "logger_redirected_stderr.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import json
            import sys

            sys.path.insert(0, {str(Path.cwd() / "src")!r})
            from plugins.DicePP.utils.logger import logger

            logger.info("DicePP 骰子机器人已启动")
            logger.info("等待聊天客户端连接")
            print(json.dumps({{
                "stdout_encoding": sys.stdout.encoding,
                "stderr_encoding": sys.stderr.encoding,
                "stderr_isatty": sys.stderr.isatty(),
            }}))
            """
        ).strip(),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["DICEPP_PROJECT_ROOT"] = str(tmp_path)
    env["PYTHONIOENCODING"] = "gbk"

    proc = subprocess.run(
        [sys.executable, str(script)],
        cwd=os.getcwd(),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )

    assert proc.returncode == 0, proc.stderr.decode("utf-8", errors="replace")
    payload = json.loads(proc.stdout.decode("utf-8"))
    stderr_text = proc.stderr.decode("utf-8")
    assert payload["stdout_encoding"].lower().replace("_", "-") == "utf-8"
    assert payload["stderr_encoding"].lower().replace("_", "-") == "utf-8"
    assert payload["stderr_isatty"] is False
    assert "DicePP 骰子机器人已启动" in stderr_text
    assert "等待聊天客户端连接" in stderr_text
    assert b"\x1b[" not in proc.stderr
    assert "���" not in stderr_text
    assert "�ȴ" not in stderr_text
    assert "\ufffd" not in stderr_text


def test_windowed_launcher_logging_without_console_streams(tmp_path: Path) -> None:
    """PyInstaller console=False 从 Explorer 启动时 sys.stdout/sys.stderr 为 None。

    RC17 回归：logger 模块 import 即崩溃（Loguru 拒绝 None sink），整个 GUI
    进程在 import 阶段弹窗退出。修复后应跳过 console handler，文件日志照常。
    """
    marker = tmp_path / "boot-ok.txt"
    script = tmp_path / "logger_windowed.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import sys

            # 模拟 PyInstaller windowed（console=False）进程：无控制台流
            sys.stdout = None
            sys.stderr = None

            sys.path.insert(0, {str(Path.cwd() / "src")!r})
            from plugins.DicePP.utils.logger import (
                configure_log_level,
                logger,
                restore_runtime_logging,
            )

            restore_runtime_logging()
            configure_log_level("INFO")
            logger.info("windowed launcher boot")

            with open({str(marker)!r}, "w", encoding="utf-8") as output:
                output.write("ok")
            """
        ).strip(),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["DICEPP_PROJECT_ROOT"] = str(tmp_path)

    proc = subprocess.run(
        [sys.executable, str(script)],
        cwd=os.getcwd(),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )

    assert proc.returncode == 0, proc.stderr.decode("utf-8", errors="replace")
    assert marker.read_text(encoding="utf-8") == "ok"
    log_file = tmp_path / "data" / "logs" / "dicepp.log"
    assert "windowed launcher boot" in log_file.read_text(encoding="utf-8")


def test_windowed_launcher_schema_migration_tolerates_nonebot_logging(
    tmp_path: Path,
) -> None:
    """Startup recovery may import NoneBot schema modules without a console."""
    marker = tmp_path / "schema-ok.txt"
    script = tmp_path / "schema_windowed.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import sys
            from pathlib import Path

            sys.stdout = None
            sys.stderr = None
            sys.path.insert(0, {str(Path.cwd())!r})
            sys.path.insert(0, {str(Path.cwd() / "src")!r})

            import scripts.build.dashboard_entry
            from dicepp_data import InstanceLayout
            from dicepp_manager.maintenance_runtime import MaintenanceRuntimeSupport

            support = object.__new__(MaintenanceRuntimeSupport)
            support.layout = InstanceLayout.from_root({str(tmp_path)!r})
            support.migrate_and_validate_schema()
            Path({str(marker)!r}).write_text("ok", encoding="utf-8")
            """
        ).strip(),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["DICEPP_PROJECT_ROOT"] = str(tmp_path)

    proc = subprocess.run(
        [sys.executable, str(script)],
        cwd=os.getcwd(),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )

    assert proc.returncode == 0, proc.stderr.decode("utf-8", errors="replace")
    assert marker.read_text(encoding="utf-8") == "ok"


def test_restore_runtime_logging_replaces_colored_redirected_handler(
    tmp_path: Path,
) -> None:
    """Framework logger reconfiguration must not leave colored runtime stderr."""
    script = tmp_path / "logger_restore_runtime.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import sys

            sys.path.insert(0, {str(Path.cwd() / "src")!r})
            from loguru import logger as loguru_logger
            from plugins.DicePP.utils.logger import restore_runtime_logging

            loguru_logger.remove()
            loguru_logger.add(sys.stderr, format="<red>{{message}}</red>", colorize=True)
            restore_runtime_logging()
            loguru_logger.info("等待聊天客户端连接")
            """
        ).strip(),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["DICEPP_PROJECT_ROOT"] = str(tmp_path)
    env["PYTHONIOENCODING"] = "gbk"

    proc = subprocess.run(
        [sys.executable, str(script)],
        cwd=os.getcwd(),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )

    assert proc.returncode == 0, proc.stderr.decode("utf-8", errors="replace")
    stderr_text = proc.stderr.decode("utf-8")
    assert "等待聊天客户端连接" in stderr_text
    assert b"\x1b[" not in proc.stderr
    assert "�ȴ" not in stderr_text
    assert "\ufffd" not in stderr_text
