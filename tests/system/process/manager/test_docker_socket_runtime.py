from __future__ import annotations

import json
import socket
import threading
import time
import urllib.parse
from contextlib import contextmanager
from pathlib import Path

import pytest

from dicepp_manager.docker_runtime import (
    DockerRuntimeError,
    DockerSocketRuntimeAdapter,
)
from dicepp_manager.docker_handoff import DockerHandoffExecutor


@contextmanager
def _fake_docker_socket(
    path: Path,
    responses: list[tuple[int, bytes] | tuple[int, bytes, float]],
):
    requests: list[str] = []
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    server.listen()

    def serve() -> None:
        try:
            for response in responses:
                status, body = response[:2]
                delay = response[2] if len(response) == 3 else 0.0
                connection, _ = server.accept()
                with connection:
                    raw = b""
                    while b"\r\n\r\n" not in raw:
                        chunk = connection.recv(4096)
                        if not chunk:
                            break
                        raw += chunk
                    requests.append(raw.decode("ascii").split("\r\n", 1)[0])
                    if delay:
                        time.sleep(delay)
                    reason = {
                        200: "OK",
                        204: "No Content",
                        304: "Not Modified",
                        404: "Not Found",
                        500: "Error",
                    }[status]
                    connection.sendall(
                        f"HTTP/1.1 {status} {reason}\r\n".encode("ascii")
                        + f"Content-Length: {len(body)}\r\n".encode("ascii")
                        + b"Content-Type: application/json\r\nConnection: close\r\n\r\n"
                        + body
                    )
        finally:
            server.close()

    thread = threading.Thread(target=serve, name="FakeDockerSocket", daemon=False)
    thread.start()
    try:
        yield requests
    finally:
        thread.join(timeout=5)
        assert not thread.is_alive()


@pytest.mark.asyncio
@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="Unix sockets are unavailable")
@pytest.mark.parametrize(
    ("action", "expected_path", "status"),
    [
        ("start", "/containers/" + "c" * 64 + "/start", 304),
        ("stop", "/containers/" + "c" * 64 + "/stop?t=10", 204),
        ("restart", "/containers/" + "c" * 64 + "/restart?t=10", 204),
    ],
)
async def test_docker_socket_real_http_filters_and_fixed_action_endpoints(
    tmp_path: Path,
    action: str,
    expected_path: str,
    status: int,
) -> None:
    socket_path = tmp_path / f"{action}.sock"
    container_id = "c" * 64
    lookup_body = json.dumps([{"Id": container_id}]).encode()
    with _fake_docker_socket(socket_path, [(200, lookup_body), (status, b"")]) as requests:
        adapter = DockerSocketRuntimeAdapter(
            socket_path=str(socket_path),
            allowed_runtime_units={"dicepp-runtime"},
        )
        await adapter.operate("dicepp-runtime", action)

    method, target, _protocol = requests[0].split(" ")
    assert method == "GET"
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(target).query)
    filters = json.loads(query["filters"][0])
    assert filters == {
        "label": [
            "io.dicepp.managed=true",
            "io.dicepp.runtime-unit=dicepp-runtime",
            "io.dicepp.deployment-schema=2",
        ]
    }
    assert requests[1] == f"POST {expected_path} HTTP/1.1"


@pytest.mark.asyncio
@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="Unix sockets are unavailable")
async def test_handoff_stop_allows_response_beyond_default_socket_timeout(
    tmp_path: Path,
) -> None:
    socket_path = tmp_path / "slow-stop.sock"
    container_id = "f" * 64
    with _fake_docker_socket(socket_path, [(204, b"", 0.15)]) as requests:
        adapter = DockerSocketRuntimeAdapter(
            socket_path=str(socket_path),
            allowed_runtime_units={"manager-helper"},
            timeout=0.05,
        )
        await DockerHandoffExecutor(adapter).stop(container_id)

    assert requests == [
        f"POST /containers/{container_id}/stop?t=30 HTTP/1.1"
    ]


@pytest.mark.asyncio
@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="Unix sockets are unavailable")
async def test_handoff_bound_delete_accepts_exact_container_already_absent(
    tmp_path: Path,
) -> None:
    socket_path = tmp_path / "missing-delete.sock"
    container_id = "f" * 64
    with _fake_docker_socket(
        socket_path,
        [(404, b'{"message":"No such container"}')],
    ) as requests:
        adapter = DockerSocketRuntimeAdapter(
            socket_path=str(socket_path),
            allowed_runtime_units={"manager-helper"},
        )
        await DockerHandoffExecutor(adapter).delete(container_id, missing_ok=True)

    assert requests == [
        f"DELETE /containers/{container_id}?v=0&force=0 HTTP/1.1"
    ]


@pytest.mark.asyncio
@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="Unix sockets are unavailable")
@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ([], "No labelled"),
        ([{"Id": "a" * 64}, {"Id": "b" * 64}], "exactly one"),
        ([{"Id": "../not-an-id"}], "invalid container id"),
    ],
)
async def test_docker_socket_rejects_missing_multiple_and_invalid_ids(
    tmp_path: Path,
    payload: list[dict],
    message: str,
) -> None:
    socket_path = tmp_path / "lookup.sock"
    with _fake_docker_socket(socket_path, [(200, json.dumps(payload).encode())]):
        adapter = DockerSocketRuntimeAdapter(
            socket_path=str(socket_path),
            allowed_runtime_units={"dicepp-runtime"},
        )
        with pytest.raises(DockerRuntimeError, match=message):
            await adapter.operate("dicepp-runtime", "start")


@pytest.mark.asyncio
@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="Unix sockets are unavailable")
async def test_docker_socket_preserves_non_success_http_detail(tmp_path: Path) -> None:
    socket_path = tmp_path / "failure.sock"
    container_id = "d" * 64
    with _fake_docker_socket(
        socket_path,
        [
            (200, json.dumps([{"Id": container_id}]).encode()),
            (500, b'{"message":"daemon exploded"}'),
        ],
    ):
        adapter = DockerSocketRuntimeAdapter(
            socket_path=str(socket_path),
            allowed_runtime_units={"dicepp-runtime"},
        )
        with pytest.raises(DockerRuntimeError, match="daemon exploded") as raised:
            await adapter.operate("dicepp-runtime", "restart")
    assert raised.value.detail == {"status_code": 500}


@pytest.mark.asyncio
@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="Unix sockets are unavailable")
async def test_docker_socket_decodes_multiple_log_frames_over_http(tmp_path: Path) -> None:
    socket_path = tmp_path / "logs.sock"
    container_id = "e" * 64
    stdout = b"stdout\n"
    stderr = "错误\n".encode()
    frames = (
        b"\x01\x00\x00\x00" + len(stdout).to_bytes(4, "big") + stdout
        + b"\x02\x00\x00\x00" + len(stderr).to_bytes(4, "big") + stderr
    )
    with _fake_docker_socket(
        socket_path,
        [(200, json.dumps([{"Id": container_id}]).encode()), (200, frames)],
    ) as requests:
        adapter = DockerSocketRuntimeAdapter(
            socket_path=str(socket_path),
            allowed_runtime_units={"dicepp-runtime"},
        )
        logs = await adapter.logs("dicepp-runtime", 20)
    assert logs.text == "stdout\n错误"
    assert requests[1] == (
        f"GET /containers/{container_id}/logs?stdout=1&stderr=1&tail=20 HTTP/1.1"
    )
