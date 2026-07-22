"""Reusable Manager backend helpers for Dashboard tests."""

import asyncio
import json
import os
import shlex
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dashboard.src.app import _compute_bot_statuses
from dashboard.src.manager import DockerComposeRuntimeBackend, ManagerService, ProcessRuntimeBackend
from dashboard.src.manager.models import BotRuntimeStatus, ManagerAction, RuntimeLogs

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
            timeout=5.0,
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
