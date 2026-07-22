"""Subprocess contracts for Dashboard Manager runtime backends."""

import asyncio
import sys
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dashboard.src.manager import DockerComposeRuntimeBackend, ManagerService, OperationFailed, ProcessRuntimeBackend, UnavailableRuntimeBackend
from tests.support.dashboard.app import setup_auth
from tests.support.dashboard.manager import (
    _assert_operate_contract,
    _assert_runtime_logs_contract,
    _assert_runtime_statuses_contract,
    _command_arg,
    _fake_docker_command,
    _install_manager_service,
    _known_test_bots,
    _make_docker_compose_runtime_backend,
    _make_process_runtime_backend,
    _read_jsonl,
    _wait_for_text,
    _wait_for_text_sync,
    _write_fake_docker_script,
    _write_sleeping_process_script,
)

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


class TestProcessRuntimeBackendSubprocessContract:
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


class TestManagerSubprocessOperations:
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
