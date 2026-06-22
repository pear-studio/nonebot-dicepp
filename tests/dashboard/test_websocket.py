"""Tests for the WebSocket Control Channel endpoint."""

import json
import sqlite3
import time as _time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from plugins.DicePP.module.dashboard_reporter.control_token import ensure_token
from plugins.DicePP.module.dashboard_reporter.protocol import (
    auth,
    encode,
    ping as ping_msg,
    pong as pong_msg,
    reload_result,
    status,
)


def _db_path(client: TestClient) -> str:
    return client.app.state.dashboard_db


def _wait_until(condition_func, timeout=2.0, interval=0.05):
    """Poll *condition_func* every *interval* until it returns truthy, or *timeout* elapses."""
    deadline = _time.time() + timeout
    while _time.time() < deadline:
        result = condition_func()
        if result:
            return result
        _time.sleep(interval)
    return condition_func()  # final attempt


def _db_has_version(db_path: str, bot_id: str, expected_version: str) -> bool:
    """Return True if *bot_id* has *expected_version* in bots_meta."""
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT version FROM bots_meta WHERE bot_id = ?",
            (bot_id,),
        ).fetchone()
        return row is not None and row[0] == expected_version
    finally:
        conn.close()


class TestWebSocketAuth:
    """Authentication phase of the WebSocket endpoint."""

    def test_valid_token(self, tmp_dashboard_paths: Path, test_client: TestClient):
        """Bot with correct control token + bot_id passes authentication."""
        token = ensure_token(tmp_dashboard_paths)
        with test_client.websocket_connect("/ws/control") as ws:
            ws.send_text(encode(auth("test_bot", token)))
            reply = ws.receive_json()
            assert reply["type"] == "auth_result"
            assert reply["payload"]["ok"] is True
            ws.close()

    def test_invalid_token(self, tmp_dashboard_paths: Path, test_client: TestClient):
        """Incorrect token is rejected."""
        ensure_token(tmp_dashboard_paths)
        with test_client.websocket_connect("/ws/control") as ws:
            ws.send_text(encode(auth("bad_bot", "wrong-token")))
            reply = ws.receive_json()
            assert reply["type"] == "auth_result"
            assert reply["payload"]["ok"] is False

    def test_empty_token(self, tmp_dashboard_paths: Path, test_client: TestClient):
        """Empty token is rejected."""
        ensure_token(tmp_dashboard_paths)
        with test_client.websocket_connect("/ws/control") as ws:
            ws.send_text(encode(auth("bot", "")))
            reply = ws.receive_json()
            assert reply["type"] == "auth_result"
            assert reply["payload"]["ok"] is False

    def test_missing_protocol_version(self, tmp_dashboard_paths: Path, test_client: TestClient):
        """Message without 'dicepp-control-v1' protocol field is rejected."""
        token = ensure_token(tmp_dashboard_paths)
        with test_client.websocket_connect("/ws/control") as ws:
            ws.send_text(json.dumps({
                "type": "auth",
                "payload": {"bot_id": "bot", "token": token},
            }))
            reply = ws.receive_json()
            assert reply["type"] == "auth_result"
            assert reply["payload"]["ok"] is False

    def test_auth_timeout(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_dashboard_paths: Path,
        test_client: TestClient,
    ):
        """An auth timeout reports the reason, then closes with code 4001."""
        from dashboard.src import websocket as websocket_module

        monkeypatch.setattr(websocket_module, "_AUTH_TIMEOUT", 0.01)
        ensure_token(tmp_dashboard_paths)
        with test_client.websocket_connect("/ws/control") as ws:
            reply = ws.receive_json()
            assert reply["type"] == "auth_result"
            assert reply["payload"] == {"ok": False, "reason": "auth timeout"}
            with pytest.raises(WebSocketDisconnect) as exc_info:
                ws.receive_text()
            assert exc_info.value.code == 4001


class TestWebSocketControl:
    """Message exchange after successful authentication."""

    def _auth(self, ws, tmp_dashboard_paths: Path):
        token = ensure_token(tmp_dashboard_paths)
        ws.send_text(encode(auth("ws_bot", token)))
        reply = ws.receive_json()
        assert reply["payload"]["ok"] is True

    def test_ping_pong_exchange(self, tmp_dashboard_paths: Path, test_client: TestClient):
        """Server receives ping + pong without errors."""
        with test_client.websocket_connect("/ws/control") as ws:
            self._auth(ws, tmp_dashboard_paths)
            ws.send_text(encode(ping_msg()))
            ws.send_text(encode(pong_msg("ws_bot")))
            ws.close()

    def test_status_updates_bots_meta(self, tmp_dashboard_paths: Path, test_client: TestClient):
        """Status message updates the heartbeat timestamp in dashboard.db."""
        db_path = _db_path(test_client)
        # Pre-insert bot row
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT OR REPLACE INTO bots_meta (bot_id, version, last_heartbeat) VALUES (?,?,?)",
            ("ws_bot", "", str(_time.time() - 60)),
        )
        conn.commit()
        conn.close()

        with test_client.websocket_connect("/ws/control") as ws:
            self._auth(ws, tmp_dashboard_paths)
            ws.send_text(encode(status("ws_bot", "2.0.0")))

        # Poll for the async handler to process the status update
        def _check_status():
            conn = sqlite3.connect(db_path)
            try:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT last_heartbeat, version FROM bots_meta WHERE bot_id = ?",
                    ("ws_bot",),
                ).fetchone()
                if row is None or row["version"] != "2.0.0":
                    return False
                return float(row["last_heartbeat"]) > _time.time() - 10
            finally:
                conn.close()

        assert _wait_until(_check_status), "Status update not reflected in DB within 2s"

    def test_unknown_message_type_ignored(self, tmp_dashboard_paths: Path, test_client: TestClient):
        """Unknown message types are silently ignored (no crash)."""
        with test_client.websocket_connect("/ws/control") as ws:
            self._auth(ws, tmp_dashboard_paths)
            ws.send_text(json.dumps({
                "protocol": "dicepp-control-v1",
                "id": "xxx",
                "reply_to": None,
                "type": "unknown_type",
                "timestamp": 0.0,
                "payload": {},
            }))
            ws.close()

    # ── Issue #11: missing test cases ─────────────────────────────────

    def test_reload_request_reply(self, tmp_dashboard_paths: Path, test_client: TestClient):
        """Dashboard sends reload request → Bot returns reload_result."""
        import asyncio
        from dashboard.src.websocket import send_reload_to_bot

        with test_client.websocket_connect("/ws/control") as ws:
            self._auth(ws, tmp_dashboard_paths)

            # Dashboard sends a reload request via the WS pool
            asyncio.run(send_reload_to_bot("ws_bot", "req_001"))

            # Bot receives the reload request
            reply = ws.receive_json()
            assert reply["type"] == "reload"
            assert reply["payload"]["request_id"] == "req_001"

            # Bot sends back reload_result
            ws.send_text(encode(reload_result("ws_bot", True, reply_to="req_001")))

        # Poll for the reload result to be stored on the dashboard side
        pending = getattr(test_client.app.state, "pending_reload_results", {})
        assert _wait_until(
            lambda: pending.get("req_001") is not None
        ), "reload_result was not stored within 2s"

        rr = pending.pop("req_001", None)
        assert rr is not None
        assert rr["bot_id"] == "ws_bot"
        assert rr["success"] is True

    def test_disconnect_clears_pool(self, tmp_dashboard_paths: Path, test_client: TestClient):
        """Bot disconnects → Dashboard clears it from the connection pool."""
        from dashboard.src.websocket import get_ws

        with test_client.websocket_connect("/ws/control") as ws:
            self._auth(ws, tmp_dashboard_paths)
            assert get_ws("ws_bot") is not None, "bot should be in pool after auth"

        # After the WS context exits, the bot should be removed from the pool
        assert get_ws("ws_bot") is None, "bot should be removed from pool on disconnect"

    def test_reconnect_resumes_communication(self, tmp_dashboard_paths: Path, test_client: TestClient):
        """Bot reconnects → communication resumes (status update still works)."""
        db_path = _db_path(test_client)

        # First connection: send status v1
        with test_client.websocket_connect("/ws/control") as ws:
            self._auth(ws, tmp_dashboard_paths)
            ws.send_text(encode(status("ws_bot", "1.0.0")))

        # Poll for v1 to be recorded before reconnecting
        assert _wait_until(
            lambda: _db_has_version(db_path, "ws_bot", "1.0.0")
        ), "v1 status not recorded within 2s"

        # Reconnect: send status v2
        with test_client.websocket_connect("/ws/control") as ws:
            self._auth(ws, tmp_dashboard_paths)
            ws.send_text(encode(status("ws_bot", "2.0.0")))

        # Poll for v2 to be recorded
        assert _wait_until(
            lambda: _db_has_version(db_path, "ws_bot", "2.0.0")
        ), "v2 status not recorded within 2s"

    def test_replacement_connection_survives_old_connection_cleanup(
        self, tmp_dashboard_paths: Path, test_client: TestClient
    ):
        """Cleaning up a replaced connection must not evict its replacement."""
        from dashboard.src.websocket import get_ws

        token = ensure_token(tmp_dashboard_paths)
        first_context = test_client.websocket_connect("/ws/control")
        first = first_context.__enter__()
        second_context = None
        try:
            first.send_text(encode(auth("same_bot", token)))
            assert first.receive_json()["payload"]["ok"] is True

            second_context = test_client.websocket_connect("/ws/control")
            second = second_context.__enter__()
            second.send_text(encode(auth("same_bot", token)))
            assert second.receive_json()["payload"]["ok"] is True

            with pytest.raises(WebSocketDisconnect) as exc_info:
                first.receive_text()
            assert exc_info.value.code == 4000

            first_context.__exit__(None, None, None)
            first_context = None
            _wait_until(lambda: get_ws("same_bot") is not None)

            assert get_ws("same_bot") is not None
        finally:
            if second_context is not None:
                second_context.__exit__(None, None, None)
            if first_context is not None:
                first_context.__exit__(None, None, None)

    @pytest.mark.asyncio
    async def test_notify_reload_returns_online_websocket_result(
        self,
        monkeypatch: pytest.MonkeyPatch,
        test_client: TestClient,
    ):
        """A received WebSocket reload result is returned to the API layer."""
        from dashboard.src import websocket as websocket_module
        from dashboard.src.app import _notify_reload, app

        conn = sqlite3.connect(_db_path(test_client))
        try:
            conn.execute(
                "INSERT OR REPLACE INTO bots_meta (bot_id, version, last_heartbeat) "
                "VALUES (?, ?, ?)",
                ("ws_bot", "2.0.0", str(_time.time())),
            )
            conn.commit()
        finally:
            conn.close()

        async def fake_send_reload(bot_id: str, request_id: str) -> bool:
            assert bot_id == "ws_bot"
            app.state.pending_reload_results = {
                request_id: {
                    "bot_id": bot_id,
                    "success": True,
                    "errors": [],
                    "_ts": _time.time(),
                }
            }
            return True

        monkeypatch.setattr(
            websocket_module, "send_reload_to_bot", fake_send_reload
        )

        results = await _notify_reload(_db_path(test_client), "ws_bot")

        assert results == [
            {"bot_id": "ws_bot", "status": "ok", "error": None}
        ]

    def test_auth_missing_bot_id_rejected(self, tmp_dashboard_paths: Path, test_client: TestClient):
        """Missing bot_id in auth message is rejected."""
        token = ensure_token(tmp_dashboard_paths)
        with test_client.websocket_connect("/ws/control") as ws:
            ws.send_text(encode(auth("", token)))
            reply = ws.receive_json()
            assert reply["type"] == "auth_result"
            assert reply["payload"]["ok"] is False
