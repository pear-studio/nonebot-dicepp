from __future__ import annotations

import subprocess

import pytest

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
async def test_docker_socket_adapter_resolves_the_same_label_allowlist(monkeypatch) -> None:
    adapter = DockerSocketRuntimeAdapter(
        socket_path="/fake/docker.sock",
        allowed_runtime_units={"dicepp-runtime"},
    )
    calls: list[tuple[str, str]] = []
    container_id = "b" * 64

    async def fake_request(method, path, *, expected, raw=False):
        calls.append((method, path))
        if path.startswith("/containers/json?"):
            return [{"Id": container_id}]
        return {}

    monkeypatch.setattr(adapter, "_request", fake_request)
    result = await adapter.operate("dicepp-runtime", "stop")

    assert result.runtime_state == "stopped"
    assert calls[1] == ("POST", f"/containers/{container_id}/stop?t=10")
    lookup = calls[0][1]
    assert "io.dicepp.managed" in lookup
    assert "io.dicepp.runtime-unit" in lookup
    assert "io.dicepp.deployment-schema" in lookup


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
