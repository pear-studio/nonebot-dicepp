from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from dicepp_manager import process_runtime
from dicepp_manager.process_runtime import ProcessRuntimeAdapter


def _command(parts: list[str]) -> str:
    return subprocess.list2cmdline(parts) if os.name == "nt" else shlex.join(parts)


@pytest.mark.asyncio
async def test_process_runtime_real_lifecycle_utf8_logs_and_environment(tmp_path: Path) -> None:
    script = tmp_path / "runtime_child.py"
    env_file = tmp_path / "encoding.txt"
    script.write_text(
        "\n".join([
            "import os, pathlib, sys, time",
            "pathlib.Path(sys.argv[1]).write_text(os.environ.get('PYTHONIOENCODING', ''), encoding='utf-8')",
            "print('标准输出', flush=True)",
            "print('错误输出', file=sys.stderr, flush=True)",
            "while True: time.sleep(0.05)",
        ]),
        encoding="utf-8",
    )
    log_path = tmp_path / "runtime.log"
    adapter = ProcessRuntimeAdapter(
        runtime_unit_id="dicepp-runtime",
        command=_command([sys.executable, "-u", str(script), str(env_file)]),
        cwd=tmp_path,
        stop_timeout=2,
        log_path=log_path,
    )

    try:
        started = await adapter.operate("dicepp-runtime", "start")
        first_pid = started.detail["pid"]
        assert (await adapter.status(["dicepp-runtime"]))["dicepp-runtime"].runtime_state == "running"

        for _ in range(100):
            logs = await adapter.logs("dicepp-runtime", 20)
            if "标准输出" in logs.text and "错误输出" in logs.text and env_file.exists():
                break
            await asyncio.sleep(0.02)
        assert "标准输出" in logs.text
        assert "错误输出" in logs.text
        assert env_file.read_text(encoding="utf-8").lower() == "utf-8"

        restarted = await adapter.operate("dicepp-runtime", "restart")
        assert restarted.runtime_state == "running"
        assert restarted.detail["pid"] != first_pid
        stopped = await adapter.operate("dicepp-runtime", "stop")
        assert stopped.runtime_state == "stopped"
        assert (await adapter.status(["dicepp-runtime"]))["dicepp-runtime"].runtime_state == "stopped"
    finally:
        await adapter.operate("dicepp-runtime", "stop")


@pytest.mark.asyncio
async def test_process_runtime_kills_after_stop_timeout(tmp_path: Path) -> None:
    class StubbornProcess:
        pid = 42
        returncode = None

        def __init__(self) -> None:
            self.terminated = False
            self.killed = False

        def poll(self):
            return self.returncode

        def terminate(self) -> None:
            self.terminated = True

        def kill(self) -> None:
            self.killed = True
            self.returncode = -9

        def wait(self, timeout=None):
            if timeout is not None and not self.killed:
                raise subprocess.TimeoutExpired("runtime", timeout)
            return self.returncode

    adapter = ProcessRuntimeAdapter(
        runtime_unit_id="dicepp-runtime",
        command=_command([sys.executable, "-V"]),
        stop_timeout=0.01,
        log_path=tmp_path / "runtime.log",
    )
    process = StubbornProcess()
    adapter._process = process

    result = await adapter.operate("dicepp-runtime", "stop")

    assert process.terminated is True
    assert process.killed is True
    assert result.message == "Process killed after stop timeout"
    assert result.detail["returncode"] == -9


@pytest.mark.skipif(os.name != "nt", reason="Windows creation flags contract")
def test_process_runtime_uses_create_no_window_and_windows_command_parsing(
    monkeypatch,
    tmp_path: Path,
) -> None:
    captured: dict = {}

    class FakeProcess:
        pid = 99
        returncode = None

        def poll(self):
            return None

    def fake_popen(argv, **kwargs):
        captured["argv"] = argv
        captured.update(kwargs)
        return FakeProcess()

    monkeypatch.setattr(process_runtime.subprocess, "Popen", fake_popen)
    adapter = ProcessRuntimeAdapter(
        runtime_unit_id="dicepp-runtime",
        command='"C:\\Program Files\\DicePP\\DicePP-Runtime.exe" --flag',
        cwd=tmp_path,
        log_path=tmp_path / "runtime.log",
    )

    adapter._start("dicepp-runtime")
    adapter._cleanup()

    assert captured["argv"] == [
        "C:\\Program Files\\DicePP\\DicePP-Runtime.exe",
        "--flag",
    ]
    assert captured["creationflags"] == subprocess.CREATE_NO_WINDOW
    assert captured["env"]["PYTHONIOENCODING"] == "utf-8"
