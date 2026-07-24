from __future__ import annotations

from pathlib import Path

import pytest

from dicepp_manager.client import ManagerClient, ManagerIncompatible
from dicepp_manager.config import ManagerClientSettings
from dicepp_manager.deployment import DEPLOYMENT_SCHEMA_VERSION, MANAGER_API_VERSION


def _client() -> ManagerClient:
    return ManagerClient(
        ManagerClientSettings(
            base_url="http://127.0.0.1:4091",
            token_path=Path("unused-token"),
        )
    )


def _compatible_status() -> dict:
    return {
        "health": {
            "manager_api_version": MANAGER_API_VERSION,
            "deployment_schema_version": DEPLOYMENT_SCHEMA_VERSION,
        }
    }


@pytest.mark.asyncio
async def test_every_manager_client_entry_performs_compatibility_handshake(
    monkeypatch,
) -> None:
    client = _client()
    calls: list[tuple[str, str]] = []

    async def request(method: str, path: str, **_kwargs):
        calls.append((method, path))
        if path == "/v1/status":
            return _compatible_status()
        if path.startswith("/v1/operations/"):
            return {"operation": {}}
        if path.startswith("/v1/operations"):
            return {"operations": []}
        if path.endswith("/logs?lines=20") or path == "/v1/logs?lines=20":
            return {"logs": {}}
        return {"operation": {}}

    monkeypatch.setattr(client, "_request", request)
    entries = [
        lambda: client.list_operations(10),
        lambda: client.get_operation("op/id"),
        lambda: client.operate("unit/id", "start/now"),
        lambda: client.logs("unit/id", 20),
        lambda: client.runtime_logs(20),
        lambda: client.release_status(),
        lambda: client.check_releases(),
        lambda: client.download_release("portable"),
        lambda: client.upgrade_preview(),
        lambda: client.confirm_upgrade(
            version="3.1.0",
            confirmation_token="confirmation-token",
        ),
        lambda: client.upgrade_status(),
    ]
    for entry in entries:
        calls.clear()
        await entry()
        assert calls[0] == ("GET", "/v1/status")
        assert len(calls) == 2

    calls.clear()
    await client.get_operation("op/id with space")
    assert calls[1] == ("GET", "/v1/operations/op%2Fid%20with%20space")
    calls.clear()
    await client.operate("unit/id with space", "start/now")
    assert calls[1] == (
        "POST",
        "/v1/runtime-units/unit%2Fid%20with%20space/start%2Fnow",
    )


@pytest.mark.asyncio
async def test_incompatible_manager_blocks_mutating_request(monkeypatch) -> None:
    client = _client()
    calls: list[tuple[str, str]] = []

    async def request(method: str, path: str):
        calls.append((method, path))
        return {
            "health": {
                "manager_api_version": "future",
                "deployment_schema_version": DEPLOYMENT_SCHEMA_VERSION,
            }
        }

    monkeypatch.setattr(client, "_request", request)
    with pytest.raises(ManagerIncompatible, match="compatibility mismatch"):
        await client.operate("dicepp-runtime", "restart")
    assert calls == [("GET", "/v1/status")]


@pytest.mark.asyncio
async def test_upgrade_confirmation_forwards_version_and_one_time_token(
    monkeypatch,
) -> None:
    client = _client()
    calls: list[tuple[str, str, dict | None]] = []

    async def request(method: str, path: str, *, json_body=None):
        calls.append((method, path, json_body))
        if path == "/v1/status":
            return _compatible_status()
        return {"operation": {"operation_id": "upgrade-1", "status": "queued"}}

    monkeypatch.setattr(client, "_request", request)

    operation = await client.confirm_upgrade(
        version="3.1.0",
        confirmation_token="confirmation-token",
    )

    assert operation == {"operation_id": "upgrade-1", "status": "queued"}
    assert calls == [
        ("GET", "/v1/status", None),
        (
            "POST",
            "/v1/upgrades/confirm",
            {
                "version": "3.1.0",
                "confirmation_token": "confirmation-token",
            },
        ),
    ]
