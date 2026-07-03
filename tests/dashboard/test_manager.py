"""Tests for the Dashboard-local Manager API."""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import sqlite3
import subprocess
import sys
import textwrap
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest
from fastapi.testclient import TestClient

from dashboard.src.config import ManagerRuntimeSettings
from dashboard.src.app import _compute_bot_statuses
from dashboard.src.manager import (
    DockerComposeRuntimeBackend,
    ManagerService,
    OperationConflict,
    OperationFailed,
    ProcessRuntimeBackend,
    RuntimeOperationUnsupported,
    UnavailableRuntimeBackend,
    UnsupportedRuntimeBackend,
    create_runtime_backend,
)
from dashboard.src.manager.models import (
    MANAGER_API_VERSION,
    OPERATION_SCHEMA_VERSION,
    VALID_ACTIONS,
    BotRuntimeStatus,
    ManagerAction,
    RuntimeLogs,
)
from dashboard.src.manager import models as manager_models
from dashboard.src.manager import store as manager_store
from tests.dashboard.conftest import setup_auth


class BlockingRuntimeBackend:
    """Runtime fake that leaves one operation running until released."""

    def __init__(self) -> None:
        self.entered = Event()
        self.release = Event()

    async def status(self, bot_ids: list[str]) -> dict[str, BotRuntimeStatus]:
        return {
            bot_id: BotRuntimeStatus(
                bot_id=bot_id,
                runtime_state="unknown",
                health="unknown",
                message="blocking fake",
            )
            for bot_id in bot_ids
        }

    async def operate(
        self,
        bot_id: str,
        action: ManagerAction,
        request_detail: dict | None = None,
    ) -> BotRuntimeStatus:
        self.entered.set()
        await asyncio.to_thread(self.release.wait)
        return BotRuntimeStatus(
            bot_id=bot_id,
            runtime_state="running",
            health="healthy",
            message=f"released {action}",
        )


class SuccessfulRuntimeBackend:
    """Runtime fake used only when a test needs a successful lifecycle result."""

    async def status(self, bot_ids: list[str]) -> dict[str, BotRuntimeStatus]:
        return {
            bot_id: BotRuntimeStatus(
                bot_id=bot_id,
                runtime_state="unknown",
                health="unknown",
                message="successful fake idle",
            )
            for bot_id in bot_ids
        }

    async def operate(
        self,
        bot_id: str,
        action: ManagerAction,
        request_detail: dict | None = None,
    ) -> BotRuntimeStatus:
        runtime_state = "stopped" if action == "stop" else "running"
        health = "stopped" if action == "stop" else "healthy"
        return BotRuntimeStatus(
            bot_id=bot_id,
            runtime_state=runtime_state,
            health=health,
            message=f"successful fake {action}",
        )


class FailingRuntimeBackend:
    """Runtime fake that fails lifecycle operations after Manager starts them."""

    async def status(self, bot_ids: list[str]) -> dict[str, BotRuntimeStatus]:
        return {
            bot_id: BotRuntimeStatus(
                bot_id=bot_id,
                runtime_state="unknown",
                health="unknown",
                message="failing fake idle",
            )
            for bot_id in bot_ids
        }

    async def operate(
        self,
        bot_id: str,
        action: ManagerAction,
        request_detail: dict | None = None,
    ) -> BotRuntimeStatus:
        raise RuntimeError(f"failed fake {action}")


class RecordingRuntimeBackend:
    """Runtime fake that records whether Manager tried to touch the backend."""

    def __init__(self) -> None:
        self.status_calls: list[list[str]] = []
        self.operate_calls: list[tuple[str, str, dict | None]] = []
        self.log_calls: list[tuple[str, int]] = []

    async def status(self, bot_ids: list[str]) -> dict[str, BotRuntimeStatus]:
        self.status_calls.append(list(bot_ids))
        return {
            bot_id: BotRuntimeStatus(
                bot_id=bot_id,
                runtime_state="unknown",
                health="unknown",
                message="recording fake idle",
            )
            for bot_id in bot_ids
        }

    async def operate(
        self,
        bot_id: str,
        action: ManagerAction,
        request_detail: dict | None = None,
    ) -> BotRuntimeStatus:
        self.operate_calls.append((
            bot_id,
            action,
            dict(request_detail) if request_detail else None,
        ))
        return BotRuntimeStatus(
            bot_id=bot_id,
            runtime_state="running",
            health="healthy",
            message=f"recording fake {action}",
        )

    async def logs(self, bot_id: str, lines: int) -> RuntimeLogs:
        self.log_calls.append((bot_id, lines))
        return RuntimeLogs(
            bot_id=bot_id,
            text="",
            source="recording",
            lines=lines,
        )




def _install_manager_service(client: TestClient, backend) -> None:
    client.app.state.manager_service = ManagerService(
        bot_status_provider=lambda: _compute_bot_statuses(client.app.state.dashboard_db),
        runtime_backend=backend,
        db_path=client.app.state.dashboard_db,
    )
    client.app.state.manager_db_path = client.app.state.dashboard_db


def _known_test_bots() -> list[dict]:
    return [
        {"bot_id": "test_bot"},
        {"bot_id": "another_bot"},
    ]


def _command_arg(value: str | Path) -> str:
    value = str(value)
    if os.name == "nt":
        return subprocess.list2cmdline([value])
    return shlex.quote(value)


def _write_sleeping_process_script(tmp_path: Path) -> Path:
    script = tmp_path / "process_runtime_child.py"
    script.write_text(
        textwrap.dedent(
            """
            import sys
            import time
            from pathlib import Path

            Path(sys.argv[1]).write_text(sys.argv[2], encoding="utf-8")
            while True:
                time.sleep(0.1)
            """
        ).strip(),
        encoding="utf-8",
    )
    return script


def _write_fake_docker_script(tmp_path: Path) -> Path:
    script = tmp_path / "fake_docker.py"
    script.write_text(
        textwrap.dedent(
            """
            import json
            import os
            import sys
            from pathlib import Path

            log_path = Path(os.environ["FAKE_DOCKER_LOG"])
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"argv": sys.argv[1:]}) + "\\n")

            fail_stage = os.environ.get("FAKE_DOCKER_FAIL_STAGE")
            if fail_stage and len(sys.argv) > 2 and sys.argv[2] == fail_stage:
                print(f"fake docker {fail_stage} failed", file=sys.stderr)
                raise SystemExit(7)

            if os.environ.get("FAKE_DOCKER_FAIL") == "1":
                print("fake docker failed", file=sys.stderr)
                raise SystemExit(7)

            if sys.argv[1:3] == ["compose", "ps"]:
                print(os.environ.get("FAKE_DOCKER_STATUS", "running"))
            elif sys.argv[1:3] == ["compose", "logs"]:
                print(os.environ.get("FAKE_DOCKER_LOGS", "fake log line"))
            else:
                print("ok")
            """
        ).strip(),
        encoding="utf-8",
    )
    return script


def _fake_docker_command(script: Path) -> str:
    return f"{_command_arg(sys.executable)} {_command_arg(script)}"


def _read_fake_docker_records(path: Path) -> list[dict]:
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if isinstance(item, list):
            item = {"argv": item, "env": {}}
        records.append(item)
    return records


def _read_jsonl(path: Path) -> list[list[str]]:
    return [record["argv"] for record in _read_fake_docker_records(path)]


async def _wait_for_text(path: Path, expected: str) -> None:
    for _ in range(50):
        if path.exists() and path.read_text(encoding="utf-8") == expected:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"{path} did not contain {expected!r}")


def _wait_for_text_sync(path: Path, expected: str) -> None:
    for _ in range(50):
        if path.exists() and path.read_text(encoding="utf-8") == expected:
            return
        time.sleep(0.05)
    raise AssertionError(f"{path} did not contain {expected!r}")


def _make_process_runtime_backend(
    tmp_path: Path,
    marker_name: str = "test_bot.txt",
) -> tuple[ProcessRuntimeBackend, Path]:
    script = _write_sleeping_process_script(tmp_path)
    marker = tmp_path / marker_name
    command = (
        f"{_command_arg(sys.executable)} {_command_arg(script)} "
        f"{_command_arg(marker)} {{bot_id}}"
    )
    return (
        ProcessRuntimeBackend(
            command=command,
            cwd=tmp_path,
            stop_timeout=1.0,
        ),
        marker,
    )


def _make_docker_compose_runtime_backend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    status: str = "running",
    logs: str = "fake log line",
) -> tuple[DockerComposeRuntimeBackend, Path]:
    script = _write_fake_docker_script(tmp_path)
    log_path = tmp_path / "docker.log"
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(log_path))
    monkeypatch.setenv("FAKE_DOCKER_STATUS", status)
    monkeypatch.setenv("FAKE_DOCKER_LOGS", logs)
    return (
        DockerComposeRuntimeBackend(
            command=_fake_docker_command(script),
            service_template="dicepp-{bot_id}",
            cwd=tmp_path,
            timeout=1.0,
        ),
        log_path,
    )


def _assert_runtime_statuses_contract(
    statuses: dict[str, BotRuntimeStatus],
    bot_ids: list[str],
) -> None:
    assert set(statuses) == set(bot_ids)
    for bot_id in bot_ids:
        status = statuses[bot_id]
        assert isinstance(status, BotRuntimeStatus)
        assert status.bot_id == bot_id

        data = status.to_dict()
        assert set(data) == {"bot_id", "runtime_state", "health", "message", "detail"}
        assert data["bot_id"] == bot_id
        assert isinstance(data["runtime_state"], str)
        assert isinstance(data["health"], str)
        assert isinstance(data["message"], str)
        assert isinstance(data["detail"], dict)


async def _assert_operate_contract(
    backend,
    bot_id: str,
    action: ManagerAction,
) -> BotRuntimeStatus:
    status = await backend.operate(bot_id, action)
    assert isinstance(status, BotRuntimeStatus)
    assert status.bot_id == bot_id
    assert status.to_dict()["bot_id"] == bot_id
    return status


def _assert_runtime_logs_contract(
    logs: RuntimeLogs,
    *,
    bot_id: str,
    source: str,
    lines: int,
    text: str,
    truncated: bool,
) -> None:
    assert isinstance(logs, RuntimeLogs)
    assert logs.bot_id == bot_id
    assert logs.source == source
    assert logs.lines == lines
    assert logs.text == text
    assert logs.truncated is truncated
    assert logs.to_dict() == {
        "bot_id": bot_id,
        "text": text,
        "source": source,
        "lines": lines,
        "truncated": truncated,
    }


class TestRuntimeBackendFactory:
    def test_default_factory_returns_unavailable_backend(self, monkeypatch: pytest.MonkeyPatch):
        """Manager defaults to the placeholder runtime backend."""
        monkeypatch.delenv("DICEPP_MANAGER_RUNTIME", raising=False)

        backend = create_runtime_backend()

        assert isinstance(backend, UnavailableRuntimeBackend)

    @pytest.mark.parametrize("runtime", [None, "unavailable"])
    def test_unavailable_runtime_ignores_bad_process_env(
        self, monkeypatch: pytest.MonkeyPatch, runtime: str | None
    ):
        """Process-only env validation must not break the default unavailable backend."""
        if runtime is None:
            monkeypatch.delenv("DICEPP_MANAGER_RUNTIME", raising=False)
        else:
            monkeypatch.setenv("DICEPP_MANAGER_RUNTIME", runtime)
        monkeypatch.setenv("DICEPP_MANAGER_PROCESS_STOP_TIMEOUT", "bad")
        monkeypatch.setenv("DICEPP_MANAGER_DOCKER_TIMEOUT", "bad")

        backend = create_runtime_backend()

        assert isinstance(backend, UnavailableRuntimeBackend)

    def test_process_runtime_with_missing_command_fails_fast(self):
        """Process runtime opt-in requires an explicit command."""
        settings = ManagerRuntimeSettings(runtime="process")

        with pytest.raises(
            UnsupportedRuntimeBackend,
            match="requires DICEPP_MANAGER_PROCESS_COMMAND",
        ):
            create_runtime_backend(settings)

    def test_process_runtime_factory_uses_explicit_command(self, tmp_path: Path):
        """Factory creates ProcessRuntimeBackend only when command config is complete."""
        script = _write_sleeping_process_script(tmp_path)
        marker = tmp_path / "factory_bot.txt"
        command = (
            f"{_command_arg(sys.executable)} {_command_arg(script)} "
            f"{_command_arg(marker)} {{bot_id}}"
        )
        settings = ManagerRuntimeSettings(
            runtime="process",
            process_command=command,
            process_cwd=str(tmp_path),
        )

        backend = create_runtime_backend(settings)

        assert isinstance(backend, ProcessRuntimeBackend)

    def test_process_runtime_factory_reads_env(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """Env opt-in creates the process backend without changing defaults."""
        script = _write_sleeping_process_script(tmp_path)
        marker = tmp_path / "env_bot.txt"
        command = (
            f"{_command_arg(sys.executable)} {_command_arg(script)} "
            f"{_command_arg(marker)} {{bot_id}}"
        )
        monkeypatch.setenv("DICEPP_MANAGER_RUNTIME", "process")
        monkeypatch.setenv("DICEPP_MANAGER_PROCESS_COMMAND", command)
        monkeypatch.setenv("DICEPP_MANAGER_PROCESS_CWD", str(tmp_path))
        monkeypatch.setenv("DICEPP_MANAGER_PROCESS_STOP_TIMEOUT", "1.0")

        backend = create_runtime_backend()

        assert isinstance(backend, ProcessRuntimeBackend)

    @pytest.mark.parametrize(
        ("settings", "message"),
        [
            (
                ManagerRuntimeSettings(
                    runtime="docker-compose",
                    docker_service_template="dicepp-{bot_id}",
                ),
                "requires DICEPP_MANAGER_DOCKER_COMMAND",
            ),
            (
                ManagerRuntimeSettings(
                    runtime="docker-compose",
                    docker_command="fake-docker",
                ),
                "requires DICEPP_MANAGER_DOCKER_SERVICE",
            ),
        ],
    )
    def test_docker_compose_runtime_with_missing_config_fails_fast(
        self, settings: ManagerRuntimeSettings, message: str
    ):
        """Docker Compose runtime opt-in requires explicit command and service config."""
        with pytest.raises(UnsupportedRuntimeBackend, match=message):
            create_runtime_backend(settings)

    def test_docker_compose_runtime_factory_reads_env(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """Env opt-in creates DockerComposeRuntimeBackend without changing defaults."""
        script = _write_fake_docker_script(tmp_path)
        monkeypatch.setenv("FAKE_DOCKER_LOG", str(tmp_path / "docker.log"))
        monkeypatch.setenv("DICEPP_MANAGER_RUNTIME", "docker-compose")
        monkeypatch.setenv("DICEPP_MANAGER_DOCKER_COMMAND", _fake_docker_command(script))
        monkeypatch.setenv("DICEPP_MANAGER_DOCKER_SERVICE", "dicepp-{bot_id}")
        monkeypatch.setenv("DICEPP_MANAGER_DOCKER_CWD", str(tmp_path))
        monkeypatch.setenv("DICEPP_MANAGER_DOCKER_TIMEOUT", "1.0")

        backend = create_runtime_backend()

        assert isinstance(backend, DockerComposeRuntimeBackend)

    def test_unknown_runtime_type_fails_clearly(self):
        """Unsupported configured runtimes fail instead of pretending to operate."""
        settings = ManagerRuntimeSettings(runtime="docker")

        with pytest.raises(
            UnsupportedRuntimeBackend,
            match="Unsupported manager runtime backend: 'docker'",
        ):
            create_runtime_backend(settings)


class TestRuntimeBackendSharedContract:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "backend_name",
        ["unavailable", "process", "docker-compose"],
    )
    async def test_status_matches_requested_bot_ids_and_dict_shape(
        self,
        backend_name: str,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ):
        """Every runtime backend reports one BotRuntimeStatus per requested bot."""
        if backend_name == "unavailable":
            backend = UnavailableRuntimeBackend()
        elif backend_name == "process":
            backend, _marker = _make_process_runtime_backend(tmp_path)
        else:
            backend, _log_path = _make_docker_compose_runtime_backend(
                monkeypatch, tmp_path
            )
        bot_ids = ["test_bot", "another_bot"]

        statuses = await backend.status(bot_ids)

        _assert_runtime_statuses_contract(statuses, bot_ids)


class TestUnavailableRuntimeBackendContract:
    @pytest.mark.asyncio
    async def test_status_reports_every_bot_as_unavailable(self):
        """Unavailable backend returns stable placeholder status for requested bots."""
        backend = UnavailableRuntimeBackend()
        bot_ids = ["test_bot", "another_bot"]

        statuses = await backend.status(bot_ids)

        _assert_runtime_statuses_contract(statuses, bot_ids)
        assert statuses["test_bot"].to_dict() == {
            "bot_id": "test_bot",
            "runtime_state": "unknown",
            "health": "unavailable",
            "message": "Current runtime backend not connected / not implemented",
            "detail": {},
        }

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "action", ["start", "stop", "restart"]
    )
    async def test_operate_is_unsupported(self, action: ManagerAction):
        """Unavailable backend never fakes lifecycle success."""
        backend = UnavailableRuntimeBackend()

        with pytest.raises(
            RuntimeOperationUnsupported,
            match="Current runtime backend not connected / not implemented",
        ):
            await backend.operate("test_bot", action)

    @pytest.mark.asyncio
    async def test_logs_are_unsupported(self):
        """Unavailable backend never fakes diagnostic logs."""
        backend = UnavailableRuntimeBackend()

        with pytest.raises(RuntimeOperationUnsupported, match="does not support logs"):
            await backend.logs("test_bot", 200)


class TestProcessRuntimeBackendContract:
    @pytest.mark.asyncio
    async def test_logs_read_shared_runtime_log(self, tmp_path: Path):
        """Process backend exposes the shared runtime log tail."""
        log_path = tmp_path / "data" / "logs" / "dicepp-runtime.log"
        log_path.parent.mkdir(parents=True)
        log_path.write_text("one\ntwo\nthree\n", encoding="utf-8")
        backend, _marker = _make_process_runtime_backend(tmp_path)
        backend._log_path = log_path

        logs = await backend.logs("test_bot", 2)

        _assert_runtime_logs_contract(
            logs,
            bot_id="test_bot",
            source=str(log_path),
            lines=2,
            text="two\nthree",
            truncated=True,
        )

    @pytest.mark.asyncio
    async def test_start_status_stop_and_restart_use_temp_process(
        self, tmp_path: Path
    ):
        """Process backend manages only subprocesses it starts from temp fixtures."""
        backend, marker = _make_process_runtime_backend(tmp_path)

        try:
            initial = await backend.status(["test_bot"])
            _assert_runtime_statuses_contract(initial, ["test_bot"])
            assert initial["test_bot"].runtime_state == "stopped"

            started = await _assert_operate_contract(backend, "test_bot", "start")
            await _wait_for_text(marker, "test_bot")
            assert started.runtime_state == "running"
            first_pid = started.detail["pid"]

            duplicate_start = await _assert_operate_contract(
                backend, "test_bot", "start"
            )
            assert duplicate_start.runtime_state == "running"
            assert duplicate_start.detail["pid"] == first_pid

            running = await backend.status(["test_bot"])
            _assert_runtime_statuses_contract(running, ["test_bot"])
            assert running["test_bot"].runtime_state == "running"
            assert running["test_bot"].health == "healthy"

            restarted = await _assert_operate_contract(backend, "test_bot", "restart")
            assert restarted.runtime_state == "running"
            assert restarted.detail["pid"] != first_pid

            stopped = await _assert_operate_contract(backend, "test_bot", "stop")
            assert stopped.runtime_state == "stopped"
            assert stopped.health == "stopped"

            stopped_again = await _assert_operate_contract(backend, "test_bot", "stop")
            assert stopped_again.runtime_state == "stopped"
        finally:
            await backend.operate("test_bot", "stop")

    @pytest.mark.asyncio
    async def test_stop_timeout_message_does_not_depend_on_returncode_sign(
        self, tmp_path: Path
    ):
        """Stop timeout reports kill timeout even on platforms with positive returncode."""
        backend = ProcessRuntimeBackend(
            command=_command_arg(sys.executable),
            cwd=tmp_path,
            stop_timeout=0.01,
            log_path=tmp_path / "runtime.log",
        )

        class TimeoutProcess:
            pid = 1234
            returncode = None

            def poll(self):
                return None

            def terminate(self):
                return None

            def kill(self):
                self.returncode = 1

            def wait(self, timeout=None):
                if timeout is not None:
                    raise subprocess.TimeoutExpired("fake", timeout)
                return self.returncode

        backend._processes["test_bot"] = TimeoutProcess()  # type: ignore[assignment]

        stopped = await backend.operate("test_bot", "stop")

        assert stopped.runtime_state == "stopped"
        assert stopped.message == "Process killed after stop timeout"
        assert stopped.detail == {"returncode": 1}

    @pytest.mark.asyncio
    async def test_stdout_and_stderr_are_redirected_to_runtime_log(
        self, tmp_path: Path
    ):
        """Started child process output lands in the shared runtime log."""
        script = tmp_path / "process_runtime_output.py"
        script.write_text(
            textwrap.dedent(
                """
                import sys
                print("stdout line", flush=True)
                print("stderr line", file=sys.stderr, flush=True)
                """
            ).strip(),
            encoding="utf-8",
        )
        log_path = tmp_path / "data" / "logs" / "dicepp-runtime.log"
        command = f"{_command_arg(sys.executable)} {_command_arg(script)}"
        backend = ProcessRuntimeBackend(
            command=command,
            cwd=tmp_path,
            stop_timeout=1.0,
            log_path=log_path,
        )

        await _assert_operate_contract(backend, "test_bot", "start")
        for _ in range(50):
            status = await backend.status(["test_bot"])
            if status["test_bot"].runtime_state == "stopped":
                break
            await asyncio.sleep(0.05)

        text = log_path.read_text(encoding="utf-8")
        assert "stdout line" in text
        assert "stderr line" in text

    @pytest.mark.asyncio
    async def test_python_child_writes_utf8_runtime_log_when_output_is_redirected(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """Redirected Python child output keeps non-ASCII runtime logs readable."""
        monkeypatch.delenv("PYTHONIOENCODING", raising=False)
        script = tmp_path / "process_runtime_utf8_output.py"
        script.write_text(
            textwrap.dedent(
                """
                import sys
                print(f"stdout encoding={sys.stdout.encoding}", flush=True)
                print(f"stderr encoding={sys.stderr.encoding}", file=sys.stderr, flush=True)
                print("stdout 中文输出：骰娘启动", flush=True)
                print("stderr 中文输出：运行正常", file=sys.stderr, flush=True)
                """
            ).strip(),
            encoding="utf-8",
        )
        log_path = tmp_path / "data" / "logs" / "dicepp-runtime.log"
        command = f"{_command_arg(sys.executable)} {_command_arg(script)}"
        backend = ProcessRuntimeBackend(
            command=command,
            cwd=tmp_path,
            stop_timeout=1.0,
            log_path=log_path,
        )

        await _assert_operate_contract(backend, "test_bot", "start")
        for _ in range(50):
            status = await backend.status(["test_bot"])
            if status["test_bot"].runtime_state == "stopped":
                break
            await asyncio.sleep(0.05)

        text = log_path.read_text(encoding="utf-8")
        assert "stdout encoding=utf-8" in text.lower()
        assert "stderr encoding=utf-8" in text.lower()
        assert "stdout 中文输出：骰娘启动" in text
        assert "stderr 中文输出：运行正常" in text
        assert "涓" not in text
        assert "锟" not in text
        assert "\ufffd" not in text

    @pytest.mark.asyncio
    async def test_start_respects_existing_pythonioencoding(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """Explicit user Python stdio encoding is passed through unchanged."""
        import dashboard.src.manager.process_runtime as process_runtime

        monkeypatch.setenv("PYTHONIOENCODING", "gbk")
        backend = ProcessRuntimeBackend(
            command=_command_arg(sys.executable),
            cwd=tmp_path,
            stop_timeout=1.0,
            log_path=tmp_path / "runtime.log",
        )
        captured: dict[str, object] = {}

        class FakeProcess:
            pid = 1234

            def poll(self):
                return None

        def fake_popen(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return FakeProcess()

        monkeypatch.setattr(process_runtime.subprocess, "Popen", fake_popen)

        try:
            await _assert_operate_contract(backend, "test_bot", "start")
        finally:
            backend._close_log_handle("test_bot")

        env = captured["kwargs"]["env"]
        assert env["PYTHONIOENCODING"] == "gbk"

    @pytest.mark.asyncio
    async def test_windows_start_uses_create_no_window(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """Windows process launches hide the runtime console window."""
        import dashboard.src.manager.process_runtime as process_runtime

        backend = ProcessRuntimeBackend(
            command=_command_arg(sys.executable),
            cwd=tmp_path,
            stop_timeout=1.0,
            log_path=tmp_path / "runtime.log",
        )
        captured: dict[str, object] = {}

        class FakeProcess:
            pid = 1234

            def poll(self):
                return None

        def fake_popen(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return FakeProcess()

        monkeypatch.setattr(
            process_runtime.subprocess,
            "CREATE_NO_WINDOW",
            0x08000000,
            raising=False,
        )
        assert process_runtime._creationflags("nt") == 0x08000000
        monkeypatch.setattr(process_runtime, "_creationflags", lambda: 0x08000000)
        monkeypatch.setattr(process_runtime.subprocess, "Popen", fake_popen)

        try:
            started = await _assert_operate_contract(backend, "test_bot", "start")
        finally:
            backend._close_log_handle("test_bot")

        assert started.runtime_state == "running"
        assert captured["kwargs"]["creationflags"] == 0x08000000


class TestDockerComposeRuntimeBackendContract:
    @pytest.mark.asyncio
    async def test_start_stop_restart_and_status_use_explicit_compose_argv(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """Docker Compose backend shells out only to the explicitly configured command."""
        backend, log_path = _make_docker_compose_runtime_backend(monkeypatch, tmp_path)

        started = await _assert_operate_contract(backend, "test_bot", "start")
        stopped = await _assert_operate_contract(backend, "test_bot", "stop")
        restarted = await _assert_operate_contract(backend, "test_bot", "restart")
        statuses = await backend.status(["test_bot"])

        _assert_runtime_statuses_contract(statuses, ["test_bot"])
        assert started.runtime_state == "running"
        assert stopped.runtime_state == "stopped"
        assert restarted.runtime_state == "running"
        assert statuses["test_bot"].runtime_state == "running"
        assert _read_jsonl(log_path) == [
            ["compose", "up", "-d", "dicepp-test_bot"],
            ["compose", "stop", "dicepp-test_bot"],
            ["compose", "restart", "dicepp-test_bot"],
            ["compose", "ps", "dicepp-test_bot"],
        ]

    @pytest.mark.asyncio
    async def test_command_failure_raises(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """A failing compose command is surfaced instead of reported as success."""
        backend, _log_path = _make_docker_compose_runtime_backend(
            monkeypatch, tmp_path
        )
        monkeypatch.setenv("FAKE_DOCKER_FAIL", "1")

        with pytest.raises(RuntimeError, match="exit code 7"):
            await backend.operate("test_bot", "start")

    @pytest.mark.asyncio
    async def test_logs_use_explicit_compose_logs_argv(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """Docker Compose logs call uses the configured command, tail, and service."""
        backend, log_path = _make_docker_compose_runtime_backend(
            monkeypatch, tmp_path, logs="line 1\nline 2"
        )

        logs = await backend.logs("test_bot", 25)

        _assert_runtime_logs_contract(
            logs,
            bot_id="test_bot",
            source="docker-compose:dicepp-test_bot",
            lines=25,
            text="line 1\nline 2",
            truncated=False,
        )
        assert _read_jsonl(log_path) == [
            ["compose", "logs", "--tail", "25", "dicepp-test_bot"],
        ]

    @pytest.mark.asyncio
    async def test_logs_command_failure_raises(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """A failing compose logs command is surfaced as a runtime failure."""
        backend, _log_path = _make_docker_compose_runtime_backend(
            monkeypatch, tmp_path
        )
        monkeypatch.setenv("FAKE_DOCKER_FAIL", "1")

        with pytest.raises(RuntimeError, match="exit code 7"):
            await backend.logs("test_bot", 25)

    @pytest.mark.asyncio
    async def test_docker_compose_failure_detail_preserved_in_operation(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """Docker Compose failure detail (returncode, stdout, stderr) is stored
        in the failed Manager operation record."""
        db_path = str(tmp_path / "dashboard.db")
        backend, _log_path = _make_docker_compose_runtime_backend(
            monkeypatch, tmp_path
        )
        monkeypatch.setenv("FAKE_DOCKER_FAIL", "1")
        service = ManagerService(
            bot_status_provider=_known_test_bots,
            runtime_backend=backend,
            db_path=db_path,
        )

        with pytest.raises(OperationFailed) as exc_info:
            await service.operate("test_bot", "start")

        failed = exc_info.value.operation
        assert failed.status == "failed"
        assert failed.message is not None and len(failed.message) > 0
        assert isinstance(failed.detail, dict)
        assert failed.detail.get("returncode") == 7
        assert isinstance(failed.detail.get("stdout"), str)
        assert isinstance(failed.detail.get("stderr"), str)


class TestManagerOperationPersistence:
    @pytest.mark.asyncio
    async def test_persistent_operations_are_trimmed_to_max_operations(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """Persistent operation history keeps only the newest configured records."""
        db_path = str(tmp_path / "dashboard.db")
        timestamp_index = 0

        def fake_utc_now() -> str:
            nonlocal timestamp_index
            timestamp_index += 1
            return f"2026-01-01T00:00:{timestamp_index:02d}+00:00"

        monkeypatch.setattr(manager_models, "utc_now", fake_utc_now)
        monkeypatch.setattr(manager_store, "utc_now", fake_utc_now)
        service = ManagerService(
            bot_status_provider=_known_test_bots,
            runtime_backend=SuccessfulRuntimeBackend(),
            db_path=db_path,
            max_operations=2,
        )

        first = await service.operate("test_bot", "start")
        second = await service.operate("test_bot", "stop")
        third = await service.operate("test_bot", "restart")
        fourth = await service.operate("test_bot", "start")

        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(
                """SELECT operation_id
                   FROM manager_operations
                   ORDER BY created_at DESC, rowid DESC"""
            ).fetchall()
            count = conn.execute("SELECT COUNT(*) FROM manager_operations").fetchone()[0]
        finally:
            conn.close()

        expected_ids = [fourth.operation_id, third.operation_id]
        assert count == 2
        assert [row[0] for row in rows] == expected_ids
        assert [
            operation["operation_id"]
            for operation in service.list_operations(limit=10)
        ] == expected_ids
        assert first.operation_id not in expected_ids
        assert second.operation_id not in expected_ids

    @pytest.mark.parametrize("max_operations", [0, -1])
    @pytest.mark.parametrize("db_path_value", [None, "dashboard.db"])
    def test_invalid_max_operations_is_rejected(
        self,
        db_path_value: str | None,
        max_operations: int,
        tmp_path: Path,
    ):
        """ManagerService rejects unclear operation retention limits."""
        db_path = str(tmp_path / db_path_value) if db_path_value else None

        with pytest.raises(ValueError, match="max_operations must be greater than 0"):
            ManagerService(
                bot_status_provider=_known_test_bots,
                runtime_backend=SuccessfulRuntimeBackend(),
                db_path=db_path,
                max_operations=max_operations,
            )

    @pytest.mark.asyncio
    async def test_operations_are_persisted_and_reloaded(self, tmp_path: Path):
        """Succeeded, failed and rejected operations survive a ManagerService rebuild."""
        db_path = str(tmp_path / "dashboard.db")

        success_service = ManagerService(
            bot_status_provider=_known_test_bots,
            runtime_backend=SuccessfulRuntimeBackend(),
            db_path=db_path,
        )
        succeeded = await success_service.operate("test_bot", "start")

        failing_service = ManagerService(
            bot_status_provider=_known_test_bots,
            runtime_backend=FailingRuntimeBackend(),
            db_path=db_path,
        )
        with pytest.raises(OperationFailed) as failed_exc:
            await failing_service.operate("test_bot", "stop")
        failed = failed_exc.value.operation

        blocking_backend = BlockingRuntimeBackend()
        blocking_service = ManagerService(
            bot_status_provider=_known_test_bots,
            runtime_backend=blocking_backend,
            db_path=db_path,
        )
        running_task = asyncio.create_task(blocking_service.operate("test_bot", "restart"))
        assert await asyncio.to_thread(blocking_backend.entered.wait, 5)

        with pytest.raises(OperationConflict) as rejected_exc:
            await blocking_service.operate("test_bot", "stop")
        rejected = rejected_exc.value.operation

        blocking_backend.release.set()
        restarted = await running_task

        reloaded = ManagerService(
            bot_status_provider=_known_test_bots,
            runtime_backend=SuccessfulRuntimeBackend(),
            db_path=db_path,
        )
        operations = {
            operation["operation_id"]: operation
            for operation in reloaded.list_operations(limit=20)
        }

        assert operations[succeeded.operation_id]["status"] == "succeeded"
        assert (
            operations[succeeded.operation_id]["detail"]["runtime"]["runtime_state"]
            == "running"
        )
        assert operations[failed.operation_id]["status"] == "failed"
        assert operations[failed.operation_id]["message"] == "failed fake stop"
        assert operations[rejected.operation_id]["status"] == "rejected"
        assert (
            operations[rejected.operation_id]["detail"]["running_operation_id"]
            == restarted.operation_id
        )
        assert operations[restarted.operation_id]["status"] == "succeeded"

    def test_incomplete_operations_are_recovered_as_failed(self, tmp_path: Path):
        """A Manager restart fails stale queued/running operations instead of leaving them stuck."""
        db_path = str(tmp_path / "dashboard.db")
        ManagerService(
            bot_status_provider=_known_test_bots,
            runtime_backend=SuccessfulRuntimeBackend(),
            db_path=db_path,
        )
        conn = sqlite3.connect(db_path)
        try:
            rows = [
                (
                    "old-running-operation",
                    "test_bot",
                    "restart",
                    "running",
                    "2026-01-01T00:00:00+00:00",
                    "2026-01-01T00:00:01+00:00",
                    "2026-01-01T00:00:01+00:00",
                    None,
                    "",
                    "{}",
                ),
                (
                    "old-queued-operation",
                    "another_bot",
                    "start",
                    "queued",
                    "2026-01-01T00:01:00+00:00",
                    "2026-01-01T00:01:00+00:00",
                    None,
                    None,
                    "",
                    "{}",
                ),
            ]
            conn.executemany(
                """INSERT INTO manager_operations (
                    operation_id, bot_id, action, status, created_at, updated_at,
                    started_at, finished_at, message, detail
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
            conn.commit()
        finally:
            conn.close()

        reloaded = ManagerService(
            bot_status_provider=_known_test_bots,
            runtime_backend=SuccessfulRuntimeBackend(),
            db_path=db_path,
        )

        recovered = {
            operation["operation_id"]: operation
            for operation in reloaded.list_operations(limit=2)
        }
        for operation_id in ["old-running-operation", "old-queued-operation"]:
            assert recovered[operation_id]["status"] == "failed"
            assert (
                recovered[operation_id]["message"]
                == "Operation interrupted by Manager restart"
            )
            assert recovered[operation_id]["finished_at"] is not None
            assert recovered[operation_id]["detail"] == {
                "recovered": True,
                "reason": "manager_restart",
            }


class TestManagerAuth:
    def test_manager_endpoints_require_auth(self, test_client: TestClient):
        """Manager endpoints keep using Dashboard session authentication."""
        for method, path in [
            ("get", "/api/manager/status"),
            ("get", "/api/manager/operations"),
            ("get", "/api/manager/logs"),
            ("get", "/api/manager/bots/test_bot/logs"),
            ("post", "/api/manager/bots/test_bot/start"),
        ]:
            resp = getattr(test_client, method)(path)
            assert resp.status_code == 401
            assert resp.json()["message"] == "Not authenticated"


class TestManagerStatus:
    def test_status_returns_discovered_bots_with_manager_and_runtime_state(
        self, test_client: TestClient
    ):
        """Status includes discovered bots plus Manager idle and unavailable runtime state."""
        setup_auth(test_client)

        resp = test_client.get("/api/manager/status")

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["health"]["status"] == "ok"
        assert data["health"]["runtime_backend"] == "UnavailableRuntimeBackend"
        assert data["health"]["manager_api_version"] == MANAGER_API_VERSION
        assert data["health"]["operation_schema_version"] == OPERATION_SCHEMA_VERSION
        assert isinstance(data["health"]["manager_api_version"], int)
        assert isinstance(data["health"]["operation_schema_version"], int)
        assert isinstance(data["health"]["dicepp_version"], str)
        assert data["health"]["dicepp_version"]
        bots = {bot["bot_id"]: bot for bot in data["bots"]}
        assert set(bots) >= {"test_bot", "another_bot"}
        assert bots["test_bot"]["manager"] == {
            "operation_status": "idle",
            "operation_id": None,
            "action": None,
        }
        assert bots["test_bot"]["runtime"]["runtime_state"] == "unknown"
        assert bots["test_bot"]["runtime"]["health"] == "unavailable"
        assert (
            bots["test_bot"]["runtime"]["message"]
            == "Current runtime backend not connected / not implemented"
        )

    def test_status_falls_back_when_dicepp_version_metadata_is_missing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        test_client: TestClient,
    ):
        """Manager health keeps a stable version field when package metadata is unavailable."""
        setup_auth(test_client)

        def missing_version(_package_name: str) -> str:
            raise manager_models.importlib_metadata.PackageNotFoundError

        monkeypatch.setattr(manager_models.importlib_metadata, "version", missing_version)

        resp = test_client.get("/api/manager/status")

        assert resp.status_code == 200
        assert resp.json()["health"]["dicepp_version"] == "unknown"


class TestManagerOperations:
    def test_default_backend_logs_are_unsupported_not_faked(
        self, test_client: TestClient
    ):
        """Default Dashboard runtime refuses logs instead of returning placeholder text."""
        setup_auth(test_client)

        resp = test_client.get("/api/manager/bots/test_bot/logs")

        assert resp.status_code == 501
        assert resp.json() == {
            "ok": False,
            "message": "Current runtime backend does not support logs",
        }

    def test_default_backend_runtime_logs_are_unsupported_not_faked(
        self, test_client: TestClient
    ):
        """Default Dashboard runtime refuses global logs instead of faking text."""
        setup_auth(test_client)

        resp = test_client.get("/api/manager/logs")

        assert resp.status_code == 501
        assert resp.json() == {
            "ok": False,
            "message": "Current runtime backend does not support runtime logs",
        }

    def test_process_runtime_global_logs_endpoint_reads_shared_log(
        self,
        test_client: TestClient,
        tmp_path: Path,
    ):
        """The global logs endpoint exposes the shared ProcessRuntime log tail."""
        setup_auth(test_client)
        log_path = tmp_path / "data" / "logs" / "dicepp-runtime.log"
        log_path.parent.mkdir(parents=True)
        log_path.write_text("old line\nruntime log line\n", encoding="utf-8")
        backend = ProcessRuntimeBackend(
            command=f"{_command_arg(sys.executable)} -c pass",
            cwd=tmp_path,
            log_path=log_path,
        )
        _install_manager_service(test_client, backend)

        resp = test_client.get("/api/manager/logs?lines=1")

        assert resp.status_code == 200
        assert resp.json()["logs"] == {
            "bot_id": "runtime",
            "text": "runtime log line",
            "source": str(log_path),
            "lines": 1,
            "truncated": True,
        }

    def test_unknown_bot_logs_are_rejected_with_404(self, test_client: TestClient):
        """Logs for undiscovered bots have a stable 404 message."""
        setup_auth(test_client)

        resp = test_client.get("/api/manager/bots/missing_bot/logs")

        assert resp.status_code == 404
        assert resp.json() == {"ok": False, "message": "Bot not found: missing_bot"}

    def test_docker_compose_logs_endpoint_returns_runtime_text(
        self,
        monkeypatch: pytest.MonkeyPatch,
        test_client: TestClient,
        tmp_path: Path,
    ):
        """Manager API exposes Docker Compose logs through the backend contract."""
        setup_auth(test_client)
        script = _write_fake_docker_script(tmp_path)
        log_path = tmp_path / "docker.log"
        monkeypatch.setenv("FAKE_DOCKER_LOG", str(log_path))
        monkeypatch.setenv("FAKE_DOCKER_LOGS", "api line")
        backend = DockerComposeRuntimeBackend(
            command=_fake_docker_command(script),
            service_template="dicepp-{bot_id}",
            cwd=tmp_path,
            timeout=1.0,
        )
        _install_manager_service(test_client, backend)

        resp = test_client.get("/api/manager/bots/test_bot/logs?lines=12")

        assert resp.status_code == 200
        assert resp.json()["logs"] == {
            "bot_id": "test_bot",
            "text": "api line",
            "source": "docker-compose:dicepp-test_bot",
            "lines": 12,
            "truncated": False,
        }
        assert _read_jsonl(log_path) == [
            ["compose", "logs", "--tail", "12", "dicepp-test_bot"],
        ]

    def test_docker_compose_logs_failure_returns_500(
        self,
        monkeypatch: pytest.MonkeyPatch,
        test_client: TestClient,
        tmp_path: Path,
    ):
        """Manager API reports compose logs failures with failed/error semantics."""
        setup_auth(test_client)
        script = _write_fake_docker_script(tmp_path)
        monkeypatch.setenv("FAKE_DOCKER_LOG", str(tmp_path / "docker.log"))
        monkeypatch.setenv("FAKE_DOCKER_FAIL", "1")
        backend = DockerComposeRuntimeBackend(
            command=_fake_docker_command(script),
            service_template="dicepp-{bot_id}",
            cwd=tmp_path,
            timeout=1.0,
        )
        _install_manager_service(test_client, backend)

        resp = test_client.get("/api/manager/bots/test_bot/logs?lines=12")

        assert resp.status_code == 500
        assert resp.json()["ok"] is False
        assert "Manager logs failed: Docker Compose command failed with exit code 7" in (
            resp.json()["message"]
        )

    def test_default_backend_operation_is_unsupported_not_succeeded(
        self, test_client: TestClient
    ):
        """Default Dashboard runtime refuses lifecycle operations instead of faking success."""
        setup_auth(test_client)

        resp = test_client.post("/api/manager/bots/test_bot/start")

        assert resp.status_code == 501
        assert resp.json() == {
            "ok": False,
            "message": (
                "Manager operation failed: "
                "Current runtime backend not connected / not implemented"
            ),
        }

        operations = test_client.get("/api/manager/operations").json()["operations"]
        operation = operations[0]
        assert operation["bot_id"] == "test_bot"
        assert operation["action"] == "start"
        assert operation["status"] == "failed"
        assert operation["message"] == "Current runtime backend not connected / not implemented"
        assert operation["detail"] == {"error": "unsupported"}

        status = test_client.get("/api/manager/status").json()
        bots = {bot["bot_id"]: bot for bot in status["bots"]}
        assert bots["test_bot"]["runtime"]["runtime_state"] == "unknown"
        assert bots["test_bot"]["runtime"]["health"] == "unavailable"

    def test_successful_operation_records_operation_and_audit(self, test_client: TestClient):
        """An explicit fake backend can return success and write audit."""
        setup_auth(test_client)
        _install_manager_service(test_client, SuccessfulRuntimeBackend())

        resp = test_client.post("/api/manager/bots/test_bot/start")

        assert resp.status_code == 200
        operation = resp.json()["operation"]
        assert operation["bot_id"] == "test_bot"
        assert operation["action"] == "start"
        assert operation["status"] == "succeeded"
        assert operation["started_at"] is not None
        assert operation["finished_at"] is not None
        assert operation["detail"]["runtime"]["runtime_state"] == "running"

        operations_resp = test_client.get("/api/manager/operations")
        operations = operations_resp.json()["operations"]
        assert operations[0]["operation_id"] == operation["operation_id"]
        assert operations[0]["status"] == "succeeded"

        audit_resp = test_client.get("/api/audit")
        entries = audit_resp.json()["entries"]
        manager_entries = [entry for entry in entries if entry["action"] == "manager.start"]
        assert manager_entries
        assert manager_entries[0]["target"] == "test_bot"
        assert operation["operation_id"] in manager_entries[0]["detail"]

    def test_process_backend_operation_returns_succeeded(
        self, test_client: TestClient, tmp_path: Path
    ):
        """Manager API can operate through an injected process backend."""
        setup_auth(test_client)
        script = _write_sleeping_process_script(tmp_path)
        marker = tmp_path / "api_bot.txt"
        command = (
            f"{_command_arg(sys.executable)} {_command_arg(script)} "
            f"{_command_arg(marker)} {{bot_id}}"
        )
        backend = ProcessRuntimeBackend(
            command=command,
            cwd=tmp_path,
            stop_timeout=1.0,
        )
        _install_manager_service(test_client, backend)

        try:
            resp = test_client.post("/api/manager/bots/test_bot/start")

            assert resp.status_code == 200
            operation = resp.json()["operation"]
            assert operation["status"] == "succeeded"
            assert operation["detail"]["runtime"]["runtime_state"] == "running"
            _wait_for_text_sync(marker, "test_bot")
        finally:
            stop_resp = test_client.post("/api/manager/bots/test_bot/stop")
            assert stop_resp.status_code == 200

    def test_docker_compose_backend_failure_returns_failed_operation(
        self,
        monkeypatch: pytest.MonkeyPatch,
        test_client: TestClient,
        tmp_path: Path,
    ):
        """Manager API records failed when the opt-in Docker Compose command fails."""
        setup_auth(test_client)
        script = _write_fake_docker_script(tmp_path)
        monkeypatch.setenv("FAKE_DOCKER_LOG", str(tmp_path / "docker.log"))
        monkeypatch.setenv("FAKE_DOCKER_FAIL", "1")
        backend = DockerComposeRuntimeBackend(
            command=_fake_docker_command(script),
            service_template="dicepp-{bot_id}",
            cwd=tmp_path,
            timeout=1.0,
        )
        _install_manager_service(test_client, backend)

        resp = test_client.post("/api/manager/bots/test_bot/start")

        assert resp.status_code == 500
        data = resp.json()
        assert data["ok"] is False
        assert "Docker Compose command failed with exit code 7" in data["message"]
        operations = test_client.get("/api/manager/operations").json()["operations"]
        assert operations[0]["status"] == "failed"
        assert "fake docker failed" in operations[0]["message"]

    def test_same_bot_running_operation_is_rejected_with_409(self, test_client: TestClient):
        """A bot can have only one in-flight Manager operation."""
        setup_auth(test_client)
        backend = BlockingRuntimeBackend()
        _install_manager_service(test_client, backend)

        with ThreadPoolExecutor(max_workers=1) as executor:
            first = executor.submit(test_client.post, "/api/manager/bots/test_bot/restart")
            assert backend.entered.wait(timeout=5), "first operation did not start"

            rejected = test_client.post("/api/manager/bots/test_bot/stop")

            backend.release.set()
            first_resp = first.result(timeout=5)

        assert first_resp.status_code == 200
        assert first_resp.json()["operation"]["status"] == "succeeded"
        assert rejected.status_code == 409
        rejected_data = rejected.json()
        assert rejected_data["ok"] is False
        assert rejected_data["message"] == "Bot test_bot already has a running operation"
        assert rejected_data["operation"]["status"] == "rejected"
        assert rejected_data["operation"]["detail"]["running_action"] == "restart"

        operations = test_client.get("/api/manager/operations").json()["operations"]
        statuses = [operation["status"] for operation in operations[:2]]
        assert statuses == ["rejected", "succeeded"]

    def test_unknown_bot_is_rejected_with_404(self, test_client: TestClient):
        """Operations for undiscovered bots have a stable 404 message."""
        setup_auth(test_client)

        resp = test_client.post("/api/manager/bots/missing_bot/start")

        assert resp.status_code == 404
        assert resp.json() == {"ok": False, "message": "Bot not found: missing_bot"}

    @pytest.mark.parametrize("action", ["pause", "update", "rollback"])
    def test_invalid_action_is_rejected_with_400(
        self,
        action: str,
        test_client: TestClient,
    ):
        """Only known Manager actions are accepted."""
        setup_auth(test_client)
        backend = RecordingRuntimeBackend()
        _install_manager_service(test_client, backend)

        resp = test_client.post(f"/api/manager/bots/test_bot/{action}")

        assert resp.status_code == 400
        assert resp.json() == {
            "ok": False,
            "message": (
                "Invalid manager action. Allowed: "
                "start, stop, restart"
            ),
        }
        assert test_client.get("/api/manager/operations").json()["operations"] == []
        assert backend.status_calls == []
        assert backend.operate_calls == []
        assert backend.log_calls == []

        audit_entries = test_client.get("/api/audit").json()["entries"]
        manager_entries = [
            entry for entry in audit_entries
            if isinstance(entry.get("action"), str)
            and entry["action"].startswith("manager.")
        ]
        assert manager_entries == []
