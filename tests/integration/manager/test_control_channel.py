"""Manager-owned Bot control channel contracts."""

from __future__ import annotations

import asyncio
import errno
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import dicepp_security.private_token as manager_auth
from dicepp_data import InstanceLayout
from dicepp_manager.api import create_manager_app
from dicepp_manager.config import ManagerSettings
from dicepp_manager.service import ManagerService
from dicepp_manager.store import ManagerOperationStore
from plugins.DicePP.core.data.schema import DicePPDatabase
from dicepp_control.control_token import ensure_token, token_path
from dicepp_control.protocol import (
    auth,
    decode,
    encode,
    reload_result,
    status,
)


def _client(tmp_path: Path) -> TestClient:
    layout = InstanceLayout.from_root(tmp_path)
    service = ManagerService(
        store=ManagerOperationStore(layout.manager_db),
        state_dir=layout.manager_state_dir,
    )
    app = create_manager_app(
        ManagerSettings(layout=layout),
        service=service,
        api_token="manager-api-token",
    )
    return TestClient(app)


def _api_headers() -> dict[str, str]:
    return {"Authorization": "Bearer manager-api-token"}


def _authenticate(ws, root: Path) -> None:
    ws.send_text(encode(auth("bot-1", ensure_token(root))))
    reply = ws.receive_json()
    assert reply["type"] == "auth_result"
    assert reply["payload"]["ok"] is True


def _send_status(ws) -> None:
    ws.send_text(encode(status("bot-1", "3.0.0")))


class _FakeControlSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.closed = False

    async def send_text(self, payload: str) -> None:
        self.sent.append(decode(payload))

    async def close(self, **_kwargs) -> None:
        self.closed = True


class _ReloadCallbackSocket(_FakeControlSocket):
    def __init__(self, service) -> None:
        super().__init__()
        self.service = service
        self.active_callbacks = 0
        self.max_concurrent_callbacks = 0
        self.callback_tasks: list[asyncio.Task] = []

    async def send_text(self, payload: str) -> None:
        await super().send_text(payload)
        request = self.sent[-1]
        if request.get("type") != "reload":
            return

        async def complete_reload() -> None:
            self.active_callbacks += 1
            self.max_concurrent_callbacks = max(
                self.max_concurrent_callbacks,
                self.active_callbacks,
            )
            await asyncio.sleep(0.01)
            await self.service._handle_message(
                "bot-1",
                self,
                reload_result("bot-1", True, [], reply_to=request["id"]),
            )
            self.active_callbacks -= 1

        self.callback_tasks.append(asyncio.create_task(complete_reload()))


class _PingFailureSocket:
    def __init__(self, root: Path) -> None:
        self._auth = encode(auth("bot-1", ensure_token(root)))
        self._authenticated = False
        self._blocked_receive = asyncio.Event()

    async def accept(self) -> None:
        return None

    async def receive_text(self) -> str:
        if not self._authenticated:
            self._authenticated = True
            return self._auth
        await self._blocked_receive.wait()
        raise AssertionError("blocked receive unexpectedly resumed")

    async def send_text(self, payload: str) -> None:
        if decode(payload)["type"] == "ping":
            raise RuntimeError("injected ping transport failure")

    async def close(self, **_kwargs) -> None:
        self._blocked_receive.set()


def test_control_auth_uses_local_token_not_manager_api_token(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        assert client.get("/v1/control/bots").status_code == 401
        legacy_db_token = DicePPDatabase(InstanceLayout.from_root(tmp_path)).ensure_local_control_token()
        with client.websocket_connect("/v1/control/ws") as ws:
            ws.send_text(encode(auth("bot-1", "manager-api-token")))
            reply = ws.receive_json()
            assert reply["payload"] == {"ok": False, "reason": "bad token"}
            with pytest.raises(WebSocketDisconnect) as exc:
                ws.receive_text()
            assert exc.value.code == 4002

        with client.websocket_connect("/v1/control/ws") as ws:
            ws.send_text(encode(auth("bot-1", legacy_db_token)))
            reply = ws.receive_json()
            assert reply["payload"] == {"ok": False, "reason": "bad token"}
            with pytest.raises(WebSocketDisconnect) as exc:
                ws.receive_text()
            assert exc.value.code == 4002

        with client.websocket_connect("/v1/control/ws") as ws:
            _authenticate(ws, tmp_path)

        response = client.get("/v1/control/bots", headers=_api_headers())
        assert response.status_code == 200
        assert response.json()["bots"][0]["bot_id"] == "bot-1"


def test_no_hardlink_token_publish_hides_staged_secret_until_ws_auth_ready(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Contenders cannot authenticate with a prefix while fallback publishing."""
    path = token_path(tmp_path)
    staged = threading.Event()
    release_publish = threading.Event()
    staged_tokens: list[str] = []
    results: list[str] = []
    failures: list[BaseException] = []
    original_replace = manager_auth.os.replace

    def no_hardlinks(*_args, **_kwargs):
        raise OSError(errno.EOPNOTSUPP, "hardlinks are unsupported")

    def pause_before_final_publish(source, destination):
        if Path(destination) == path:
            staged_token = Path(source).read_text(encoding="utf-8").strip()
            assert staged_token
            staged_tokens.append(staged_token)
            staged.set()
            assert release_publish.wait(timeout=2)
        return original_replace(source, destination)

    def bootstrap() -> None:
        try:
            results.append(ensure_token(tmp_path))
        except BaseException as exc:  # keep thread errors visible to this test
            failures.append(exc)

    monkeypatch.setattr(manager_auth.os, "link", no_hardlinks)
    monkeypatch.setattr(manager_auth.os, "replace", pause_before_final_publish)
    winner = threading.Thread(target=bootstrap)
    consumers = [threading.Thread(target=bootstrap) for _ in range(2)]
    winner.start()
    try:
        assert staged.wait(timeout=2)
        for consumer in consumers:
            consumer.start()
        for consumer in consumers:
            consumer.join(timeout=0.1)
            assert consumer.is_alive(), "consumer must wait for the final publish"
        assert results == [], "no caller may receive a staged token prefix"
    finally:
        release_publish.set()
        winner.join(timeout=2)
        for consumer in consumers:
            consumer.join(timeout=2)

    assert not winner.is_alive()
    assert all(not consumer.is_alive() for consumer in consumers)
    assert failures == []
    assert len(staged_tokens) == 1
    assert results == [staged_tokens[0]] * 3

    with _client(tmp_path) as client:
        with client.websocket_connect("/v1/control/ws") as ws:
            ws.send_text(encode(auth("bot-1", results[1])))
            reply = ws.receive_json()

    assert reply["type"] == "auth_result"
    assert reply["payload"]["ok"] is True


def test_duplicate_bot_session_replaces_only_the_old_connection(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        token = ensure_token(tmp_path)
        first_context = client.websocket_connect("/v1/control/ws")
        first = first_context.__enter__()
        second_context = None
        try:
            first.send_text(encode(auth("bot-1", token)))
            assert first.receive_json()["payload"]["ok"] is True
            second_context = client.websocket_connect("/v1/control/ws")
            second = second_context.__enter__()
            second.send_text(encode(auth("bot-1", token)))
            assert second.receive_json()["payload"]["ok"] is True
            with pytest.raises(WebSocketDisconnect) as exc:
                first.receive_text()
            assert exc.value.code == 4000
            _send_status(second)
            statuses = client.get("/v1/control/bots", headers=_api_headers()).json()["bots"]
            assert len(statuses) == 1
            assert statuses[0]["bot_id"] == "bot-1"
            assert statuses[0]["version"] == "3.0.0"
            assert statuses[0]["online"] is True
            assert statuses[0]["last_heartbeat_ts"] > 0
        finally:
            if second_context is not None:
                second_context.__exit__(None, None, None)
            first_context.__exit__(None, None, None)


def test_heartbeat_timeout_is_reported_by_manager_status(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        client.app.state.control_service.heartbeat_timeout = 0.01
        with client.websocket_connect("/v1/control/ws") as ws:
            _authenticate(ws, tmp_path)
            _send_status(ws)
            time.sleep(0.02)
            response = client.get("/v1/control/bots", headers=_api_headers())

    bot = response.json()["bots"][0]
    assert bot["online"] is False
    assert bot["last_heartbeat_ts"]


def test_reload_success_and_failure_are_manager_api_results(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        with client.websocket_connect("/v1/control/ws") as ws:
            _authenticate(ws, tmp_path)
            _send_status(ws)
            outcomes = []
            for success, errors in ((True, []), (False, ["validation failed"])):
                reply: dict = {}

                def request_reload() -> None:
                    reply["response"] = client.post(
                        "/v1/control/reload",
                        headers=_api_headers(),
                        json={"bot_id": "bot-1"},
                    )

                thread = threading.Thread(target=request_reload)
                thread.start()
                request = ws.receive_json()
                assert request["type"] == "reload"
                ws.send_text(
                    encode(reload_result("bot-1", success, errors, reply_to=request["id"]))
                )
                thread.join(timeout=2)
                assert not thread.is_alive()
                outcomes.append(reply["response"].json()["results"][0])

    assert outcomes == [
        {"bot_id": "bot-1", "status": "ok", "error": None},
        {"bot_id": "bot-1", "status": "error", "error": "validation failed"},
    ]


def test_reload_timeout_and_offline_are_explicit(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        service = client.app.state.control_service
        service.reload_timeout = 0.01
        with client.websocket_connect("/v1/control/ws") as ws:
            _authenticate(ws, tmp_path)
            _send_status(ws)
            timeout = client.post(
                "/v1/control/reload",
                headers=_api_headers(),
                json={"bot_id": "bot-1"},
            )
            assert timeout.json()["results"] == [{
                "bot_id": "bot-1",
                "status": "timeout",
                "error": "reload timed out",
            }]

        offline = client.post(
            "/v1/control/reload",
            headers=_api_headers(),
            json={"bot_id": "bot-1"},
        )
    assert offline.json()["results"] == [{
        "bot_id": "bot-1",
        "status": "offline",
        "error": "Bot offline",
    }]


@pytest.mark.asyncio
async def test_same_bot_reload_requests_are_serial_and_each_receives_result(
    tmp_path: Path,
) -> None:
    """Bot reload is non-thread-safe, but concurrent callers remain stable."""
    from dicepp_manager.control import ControlChannelService

    service = ControlChannelService(
        project_root=tmp_path,
        known_bot_ids=lambda: {"bot-1"},
        reload_timeout=1,
    )
    socket = _ReloadCallbackSocket(service)
    await service._replace_session("bot-1", socket)
    await service._handle_message("bot-1", socket, status("bot-1", "3.0.0"))

    first, second = await asyncio.gather(
        service.reload("bot-1"),
        service.reload("bot-1"),
    )
    await asyncio.gather(*socket.callback_tasks)

    assert socket.max_concurrent_callbacks == 1
    assert [message["type"] for message in socket.sent] == ["reload", "reload"]
    assert first == [{"bot_id": "bot-1", "status": "ok", "error": None}]
    assert second == [{"bot_id": "bot-1", "status": "ok", "error": None}]


@pytest.mark.asyncio
async def test_replaced_session_cannot_update_status_or_complete_reload(
    tmp_path: Path,
) -> None:
    from dicepp_manager.control import ControlChannelService

    service = ControlChannelService(
        project_root=tmp_path,
        known_bot_ids=lambda: {"bot-1"},
    )
    old_socket = _FakeControlSocket()
    current_socket = _FakeControlSocket()
    await service._replace_session("bot-1", old_socket)
    await service._replace_session("bot-1", current_socket)
    pending = asyncio.get_running_loop().create_future()
    service._pending_reload["late-reply"] = pending

    await service._handle_message("bot-1", old_socket, status("bot-1", "old"))
    await service._handle_message(
        "bot-1",
        old_socket,
        reload_result("bot-1", True, [], reply_to="late-reply"),
    )

    stale = service.bot_statuses()[0]
    assert old_socket.closed is True
    assert stale["version"] == ""
    assert stale["last_heartbeat_ts"] == ""
    assert pending.done() is False

    await service._handle_message("bot-1", current_socket, status("bot-1", "new"))
    await service._handle_message(
        "bot-1",
        current_socket,
        reload_result("bot-1", True, [], reply_to="late-reply"),
    )

    assert service.bot_statuses()[0]["version"] == "new"
    assert pending.result()["success"] is True


@pytest.mark.asyncio
async def test_probe_drops_fresh_heartbeat_when_session_disconnects(
    tmp_path: Path,
) -> None:
    """A fresh historical heartbeat cannot keep a disconnected Bot healthy."""
    from dicepp_manager.control import ControlChannelService

    service = ControlChannelService(
        project_root=tmp_path,
        known_bot_ids=lambda: {"bot-1"},
    )
    socket = _FakeControlSocket()
    await service._replace_session("bot-1", socket)
    await service._handle_message("bot-1", socket, status("bot-1", "3.0.0"))

    assert service.probe()["ok"] is True

    await service._remove_if_current("bot-1", socket)

    assert service.probe() == {
        "ok": False,
        "status": "failed",
        "message": "No Bot control heartbeat",
        "active_authenticated_sessions": 0,
    }


@pytest.mark.asyncio
async def test_probe_ignores_disconnected_bots_when_selecting_latest_heartbeat(
    tmp_path: Path,
) -> None:
    """A disconnected Bot's newer heartbeat cannot hide another active Bot."""
    from dicepp_manager.control import ControlChannelService

    service = ControlChannelService(
        project_root=tmp_path,
        known_bot_ids=lambda: {"bot-a", "bot-b"},
    )
    bot_a = _FakeControlSocket()
    bot_b = _FakeControlSocket()
    await service._replace_session("bot-a", bot_a)
    await service._handle_message("bot-a", bot_a, status("bot-a", "3.0.0"))
    bot_a_heartbeat = service.probe()["heartbeat"]
    await service._replace_session("bot-b", bot_b)
    await service._handle_message("bot-b", bot_b, status("bot-b", "3.0.0"))

    await service._remove_if_current("bot-b", bot_b)

    probe = service.probe()
    assert probe["active_authenticated_sessions"] == 1
    assert probe["heartbeat"] == bot_a_heartbeat


@pytest.mark.asyncio
async def test_reconnected_session_must_publish_a_new_heartbeat(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Replacing a transport clears its heartbeat until the new Bot reports."""
    import dicepp_manager.control as manager_control

    now = [1_700_000_000.0]
    monkeypatch.setattr(manager_control.time, "time", lambda: now[0])

    service = manager_control.ControlChannelService(
        project_root=tmp_path,
        known_bot_ids=lambda: {"bot-1"},
    )
    old_socket = _FakeControlSocket()
    new_socket = _FakeControlSocket()
    await service._replace_session("bot-1", old_socket)
    await service._handle_message("bot-1", old_socket, status("bot-1", "3.0.0"))
    previous = service.probe()["heartbeat"]

    await service._replace_session("bot-1", new_socket)

    assert service.probe()["active_authenticated_sessions"] == 1
    assert service.probe()["ok"] is False

    now[0] += 1
    await service._handle_message("bot-1", new_socket, status("bot-1", "3.1.0"))

    assert service.probe()["ok"] is True
    assert service.probe()["heartbeat"] > previous


@pytest.mark.asyncio
async def test_ping_failure_revokes_session_while_receive_is_blocked(
    tmp_path: Path,
) -> None:
    """A failed ping must terminate the paired receive loop and session."""
    from dicepp_manager.control import ControlChannelService

    service = ControlChannelService(
        project_root=tmp_path,
        known_bot_ids=lambda: {"bot-1"},
        ping_interval=0,
    )

    await asyncio.wait_for(
        service.websocket_endpoint(_PingFailureSocket(tmp_path)),
        timeout=0.5,
    )

    assert service.probe()["active_authenticated_sessions"] == 0


@pytest.mark.asyncio
async def test_replaced_session_ping_failure_cannot_revoke_successor(
    tmp_path: Path,
) -> None:
    """Late transport failure cleanup is scoped to the exact old socket."""
    from dicepp_manager.control import ControlChannelService

    service = ControlChannelService(
        project_root=tmp_path,
        known_bot_ids=lambda: {"bot-1"},
        ping_interval=0,
    )
    old_socket = _PingFailureSocket(tmp_path)
    current_socket = _FakeControlSocket()
    await service._replace_session("bot-1", old_socket)
    await service._replace_session("bot-1", current_socket)

    await service._ping_loop("bot-1", old_socket)

    assert service.probe()["active_authenticated_sessions"] == 1
