from __future__ import annotations

import subprocess

import pytest

import dicepp_manager.docker_runtime as docker_runtime_module
from dicepp_manager.docker_runtime import (
    DockerRuntimeAdapter,
    DockerRuntimeError,
    DockerSocketRuntimeAdapter,
    _decode_docker_logs,
)

@pytest.mark.asyncio
async def test_docker_cli_uses_only_fixed_label_lookup_and_container_id(monkeypatch) -> None:
    calls: list[list[str]] = []
    container_id = "a" * 64

    def fake_run(argv, **kwargs):
        calls.append(argv)
        stdout = container_id + "\n" if argv[1] == "ps" else ""
        return subprocess.CompletedProcess(argv, 0, stdout, "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = DockerRuntimeAdapter(docker_command="docker", allowed_runtime_units={"dicepp-runtime"})

    await adapter.operate("dicepp-runtime", "restart")

    assert calls[0] == [
        "docker", "ps", "-a",
        "--filter", "label=io.dicepp.managed=true",
        "--filter", "label=io.dicepp.runtime-unit=dicepp-runtime",
        "--filter", "label=io.dicepp.deployment-schema=2",
        "--format", "{{.ID}}",
    ]
    assert calls[1] == ["docker", "restart", container_id]


def test_docker_cli_rejects_templates_and_unknown_units() -> None:
    with pytest.raises(ValueError, match="one executable"):
        DockerRuntimeAdapter(docker_command="docker compose", allowed_runtime_units={"dicepp-runtime"})
    adapter = DockerRuntimeAdapter(docker_command="docker", allowed_runtime_units={"dicepp-runtime"})
    with pytest.raises(DockerRuntimeError, match="allowlist"):
        import asyncio
        asyncio.run(adapter.operate("attacker", "start"))


def test_docker_multiplexed_logs_are_decoded() -> None:
    payload = b"hello\n"
    raw = b"\x01\x00\x00\x00" + len(payload).to_bytes(4, "big") + payload
    assert _decode_docker_logs(raw) == "hello\n"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "expected_detail"),
    [
        (
            TimeoutError("timed out"),
            {"timeout": True, "timeout_seconds": 60.0},
        ),
        (ConnectionRefusedError("refused"), {}),
    ],
)
async def test_docker_socket_marks_only_transport_timeouts_for_reconciliation(
    monkeypatch,
    failure: OSError,
    expected_detail: dict,
) -> None:
    class FailingConnection:
        def __init__(self, _socket_path: str, timeout: float) -> None:
            assert timeout == 60.0

        def request(self, *_args, **_kwargs) -> None:
            raise failure

        def close(self) -> None:
            pass

    monkeypatch.setattr(
        docker_runtime_module,
        "_UnixSocketConnection",
        FailingConnection,
    )
    adapter = DockerSocketRuntimeAdapter(
        socket_path="/fake/docker.sock",
        allowed_runtime_units={"dicepp-runtime"},
    )

    with pytest.raises(DockerRuntimeError) as raised:
        await adapter._request(
            "POST",
            "/containers/" + ("a" * 64) + "/stop?t=30",
            expected={204},
            timeout=60.0,
        )

    assert raised.value.detail == expected_detail


@pytest.mark.asyncio
async def test_docker_socket_adapter_resolves_the_same_label_allowlist(monkeypatch) -> None:
    adapter = DockerSocketRuntimeAdapter(
        socket_path="/fake/docker.sock",
        allowed_runtime_units={"dicepp-runtime"},
    )
    calls: list[tuple[str, str, float | None]] = []
    container_id = "b" * 64

    async def fake_request(method, path, *, expected, raw=False, timeout=None):
        calls.append((method, path, timeout))
        if path.startswith("/containers/json?"):
            return [{"Id": container_id}]
        return {}

    monkeypatch.setattr(adapter, "_request", fake_request)
    result = await adapter.operate("dicepp-runtime", "stop")

    assert result.runtime_state == "stopped"
    assert result.detail == {"container_id": container_id}
    assert calls[1] == (
        "POST",
        f"/containers/{container_id}/stop?t=10",
        40.0,
    )
    lookup = calls[0][1]
    assert "io.dicepp.managed" in lookup
    assert "io.dicepp.runtime-unit" in lookup
    assert "io.dicepp.deployment-schema" in lookup


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["stopped", "removed"])
async def test_stop_timeout_accepts_exact_container_already_stopped_or_removed(
    monkeypatch,
    outcome: str,
) -> None:
    adapter = DockerSocketRuntimeAdapter(
        socket_path="/fake/docker.sock",
        allowed_runtime_units={"dicepp-runtime"},
        timeout=0.01,
    )
    container_id = "c" * 64
    calls: list[tuple[str, str]] = []

    async def fake_request(method, path, **_kwargs):
        calls.append((method, path))
        if path.startswith("/containers/json?"):
            return [{"Id": container_id}]
        if method == "POST":
            raise DockerRuntimeError(
                "Docker socket request failed: timed out",
                detail={"timeout": True, "timeout_seconds": 40.0},
            )
        if outcome == "removed":
            raise DockerRuntimeError(
                "Docker API returned HTTP 404",
                detail={"status_code": 404},
            )
        return {"Id": container_id, "State": {"Running": False}}

    monkeypatch.setattr(adapter, "_request", fake_request)

    result = await adapter.operate("dicepp-runtime", "stop")

    assert result.runtime_state == "stopped"
    assert result.detail == {"container_id": container_id}
    assert calls[0][0] == "GET"
    assert calls[0][1].startswith("/containers/json?")
    assert calls[1:] == [
        ("POST", f"/containers/{container_id}/stop?t=10"),
        ("GET", f"/containers/{container_id}/json"),
    ]
    assert sum(method == "POST" for method, _path in calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["running", "foreign", "unavailable"])
async def test_stop_timeout_fails_closed_when_exact_state_is_not_stopped(
    monkeypatch,
    outcome: str,
) -> None:
    adapter = DockerSocketRuntimeAdapter(
        socket_path="/fake/docker.sock",
        allowed_runtime_units={"dicepp-runtime"},
        timeout=0.01,
    )
    container_id = "d" * 64
    calls: list[tuple[str, str]] = []

    async def fake_request(method, path, **_kwargs):
        calls.append((method, path))
        if path.startswith("/containers/json?"):
            return [{"Id": container_id}]
        if method == "POST":
            raise DockerRuntimeError(
                "Docker socket request failed: timed out",
                detail={"timeout": True},
            )
        if outcome == "unavailable":
            raise DockerRuntimeError("Docker inspect failed")
        return {
            "Id": "e" * 64 if outcome == "foreign" else container_id,
            "State": {"Running": outcome == "running"},
        }

    monkeypatch.setattr(adapter, "_request", fake_request)

    with pytest.raises(DockerRuntimeError, match="timed out"):
        await adapter.operate("dicepp-runtime", "stop")

    assert calls[-1] == ("GET", f"/containers/{container_id}/json")
    assert sum(method == "POST" for method, _path in calls) == 1


def test_docker_log_decoder_falls_back_for_residual_or_truncated_frame() -> None:
    payload = b"hello"
    complete = b"\x01\x00\x00\x00" + len(payload).to_bytes(4, "big") + payload
    assert _decode_docker_logs(complete + b"tail") == (complete + b"tail").decode(
        "utf-8",
        errors="replace",
    )
    assert _decode_docker_logs(complete[:-1]) == complete[:-1].decode(
        "utf-8",
        errors="replace",
    )


def test_docker_socket_rejects_non_positive_timeout() -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        DockerSocketRuntimeAdapter(
            socket_path="/fake/docker.sock",
            allowed_runtime_units={"dicepp-runtime"},
            timeout=0,
        )
