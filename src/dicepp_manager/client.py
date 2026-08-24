"""Dashboard/launcher client for the private Manager API."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from typing import Any

from .auth import TokenSecurityError, read_api_token
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


def _read_manager_token(token_path) -> str:
    """Map every local credential failure to the stable Manager boundary."""
    try:
        return read_api_token(token_path)
    except (OSError, TokenSecurityError, ValueError) as exc:
        raise ManagerUnavailable(
            "Manager credentials are unavailable",
            status_code=503,
        ) from exc


class ManagerClient:
    def __init__(self, settings: ManagerClientSettings) -> None:
        self.settings = settings

    async def status(self) -> dict:
        return await self._ensure_compatible()

    async def health(self) -> dict:
        """Read API readiness without requiring the status handshake first."""
        return await self._request("GET", "/v1/health")

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

    async def control_bots(self) -> list[dict]:
        await self._ensure_control_capable()
        result = (await self._request("GET", "/v1/control/bots")).get("bots", [])
        return result if isinstance(result, list) else []

    async def reload_bots(self, bot_id: str | None = None) -> list[dict]:
        await self._ensure_control_capable()
        result = await self._request(
            "POST",
            "/v1/control/reload",
            json_body={"bot_id": bot_id} if bot_id is not None else {},
        )
        rows = result.get("results", [])
        return rows if isinstance(rows, list) else []

    async def _ensure_control_capable(self) -> dict:
        payload = await self._ensure_compatible()
        control = payload.get("control")
        if (
            not isinstance(control, dict)
            or control.get("available") is not True
            or control.get("protocol") != "dicepp-control-v1"
        ):
            raise ManagerIncompatible(
                "Manager control channel capability is unavailable; "
                "restart Manager before using this Dashboard",
                status_code=409,
            )
        return payload

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        token = _read_manager_token(self.settings.token_path)
        return await asyncio.to_thread(
            self._request_sync,
            method,
            path,
            token,
            json_body,
        )

    def _request_sync(
        self,
        method: str,
        path: str,
        token: str,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = (
            json.dumps(json_body, ensure_ascii=False).encode("utf-8")
            if json_body is not None
            else None
        )
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.settings.base_url}{path}",
            method=method,
            headers=headers,
            data=data,
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
