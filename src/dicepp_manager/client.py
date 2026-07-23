"""Dashboard/launcher client for the private Manager API."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .auth import read_api_token
from .config import ManagerClientSettings
from .deployment import DEPLOYMENT_SCHEMA_VERSION, MANAGER_API_VERSION


class ManagerClientError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 502, payload: dict | None = None) -> None:
        self.status_code = status_code
        self.payload = payload or {}
        super().__init__(message)


class ManagerUnavailable(ManagerClientError):
    pass


class ManagerIncompatible(ManagerClientError):
    pass


class ManagerClient:
    def __init__(self, settings: ManagerClientSettings) -> None:
        self.settings = settings

    async def status(self) -> dict:
        return await self._ensure_compatible()

    async def _ensure_compatible(self) -> dict:
        payload = await self._request("GET", "/v1/status")
        health = payload.get("health")
        if not isinstance(health, dict):
            raise ManagerIncompatible("Manager health metadata is missing", status_code=409)
        actual_api = health.get("manager_api_version")
        actual_deployment = health.get("deployment_schema_version")
        if actual_api != MANAGER_API_VERSION or actual_deployment != DEPLOYMENT_SCHEMA_VERSION:
            raise ManagerIncompatible(
                "Manager compatibility mismatch: "
                f"api={actual_api!r}, deployment={actual_deployment!r}; "
                f"expected api={MANAGER_API_VERSION}, deployment={DEPLOYMENT_SCHEMA_VERSION}",
                status_code=409,
            )
        return payload

    async def list_operations(self, limit: int = 50) -> list[dict]:
        await self._ensure_compatible()
        return (await self._request("GET", f"/v1/operations?limit={limit}")).get("operations", [])

    async def get_operation(self, operation_id: str) -> dict:
        await self._ensure_compatible()
        segment = urllib.parse.quote(operation_id, safe="")
        return (await self._request("GET", f"/v1/operations/{segment}")).get("operation", {})

    async def operate(self, runtime_unit_id: str, action: str) -> dict:
        await self._ensure_compatible()
        unit_segment = urllib.parse.quote(runtime_unit_id, safe="")
        action_segment = urllib.parse.quote(action, safe="")
        return (await self._request("POST", f"/v1/runtime-units/{unit_segment}/{action_segment}")).get("operation", {})

    async def logs(self, runtime_unit_id: str, lines: int) -> dict:
        await self._ensure_compatible()
        segment = urllib.parse.quote(runtime_unit_id, safe="")
        return (await self._request("GET", f"/v1/runtime-units/{segment}/logs?lines={lines}")).get("logs", {})

    async def runtime_logs(self, lines: int) -> dict:
        await self._ensure_compatible()
        return (await self._request("GET", f"/v1/logs?lines={lines}")).get("logs", {})

    async def _request(self, method: str, path: str) -> dict[str, Any]:
        try:
            token = read_api_token(self.settings.token_path)
        except (OSError, ValueError) as exc:
            raise ManagerUnavailable(str(exc), status_code=503) from exc
        return await asyncio.to_thread(self._request_sync, method, path, token)

    def _request_sync(self, method: str, path: str, token: str) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.settings.base_url}{path}",
            method=method,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.settings.timeout) as response:
                body = response.read().decode("utf-8")
                status = response.status
        except urllib.error.HTTPError as exc:
            payload = _decode_payload(exc.read())
            raise ManagerClientError(
                str(payload.get("message") or f"Manager API returned HTTP {exc.code}"),
                status_code=exc.code,
                payload=payload,
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ManagerUnavailable(f"Manager is unavailable: {exc}", status_code=503) from exc
        payload = _decode_payload(body.encode("utf-8"))
        if status >= 400 or payload.get("ok") is False:
            raise ManagerClientError(str(payload.get("message") or "Manager request failed"), status_code=status, payload=payload)
        payload.pop("ok", None)
        return payload


def _decode_payload(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}
