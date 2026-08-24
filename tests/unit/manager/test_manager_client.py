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
        },
        "control": {"available": True, "protocol": "dicepp-control-v1"},
    }


@pytest.mark.asyncio
async def test_health_reads_readiness_without_a_status_handshake(monkeypatch) -> None:
    client = _client()
    calls: list[tuple[str, str]] = []
    expected = {"dicepp_version": "3.0.0rc20"}

    async def request(method: str, path: str):
        calls.append((method, path))
        return expected

    monkeypatch.setattr(client, "_request", request)

    assert await client.health() == expected
    assert calls == [("GET", "/v1/health")]


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
        return {"results": []}

    monkeypatch.setattr(client, "_request", request)
    entries = [
        lambda: client.control_bots(),
        lambda: client.reload_bots("bot/id"),
    ]
    for entry in entries:
        calls.clear()
        await entry()
        assert calls[0] == ("GET", "/v1/status")
        assert len(calls) == 2

@pytest.mark.asyncio
async def test_control_client_refuses_legacy_manager_without_control_capability(monkeypatch) -> None:
    client = _client()

    async def request(method: str, path: str, **_kwargs):
        assert (method, path) == ("GET", "/v1/status")
        return {
            "health": {
                "manager_api_version": MANAGER_API_VERSION,
                "deployment_schema_version": DEPLOYMENT_SCHEMA_VERSION,
            }
        }

    monkeypatch.setattr(client, "_request", request)

    with pytest.raises(ManagerIncompatible, match="control channel capability"):
        await client.control_bots()


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
        await client.reload_bots()
    assert calls == [("GET", "/v1/status")]
