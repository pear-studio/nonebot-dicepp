"""Dashboard/launcher client for the private Manager API."""

from __future__ import annotations

import asyncio
import http.client
import io
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterator

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


class ArchiveDownload:
    def __init__(self, response) -> None:
        self._response = response

    def __iter__(self) -> Iterator[bytes]:
        try:
            while True:
                chunk = self._response.read(1024 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            self.close()

    def close(self) -> None:
        response, self._response = self._response, None
        if response is not None:
            response.close()


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

    async def save_user_config(self, config: dict) -> dict:
        await self._ensure_compatible()
        return await self._request("PUT", "/v1/config/user", json_body=config)

    async def save_bot_config(self, bot_id: str, config: dict) -> dict:
        await self._ensure_compatible()
        segment = urllib.parse.quote(bot_id, safe="")
        return await self._request(
            "PUT",
            f"/v1/config/bots/{segment}",
            json_body=config,
        )

    async def list_archives(self) -> list[dict]:
        await self._ensure_compatible()
        return (await self._request("GET", "/v1/archives")).get("archives", [])

    async def estimate_archive(self, profile: str) -> dict:
        await self._ensure_compatible()
        return (
            await self._request(
                "POST",
                "/v1/archives/estimate",
                json_body={"profile": profile},
            )
        ).get("estimate", {})

    async def create_archive(
        self,
        *,
        description: str | None = None,
        profile: str = "regular",
    ) -> dict:
        await self._ensure_compatible()
        payload = await self._request(
            "POST",
            "/v1/archives",
            json_body={"description": description, "profile": profile},
        )
        return payload.get("operation", {})

    async def archive_detail(self, filename: str) -> dict:
        await self._ensure_compatible()
        segment = urllib.parse.quote(filename, safe="")
        return await self._request("GET", f"/v1/archives/{segment}")

    async def verify_archive(self, filename: str) -> dict:
        await self._ensure_compatible()
        segment = urllib.parse.quote(filename, safe="")
        return await self._request("POST", f"/v1/archives/{segment}/verify")

    async def plan_archive_restore(self, filename: str) -> dict:
        await self._ensure_compatible()
        segment = urllib.parse.quote(filename, safe="")
        return await self._request("POST", f"/v1/archives/{segment}/restore-plan")

    async def restore_archive(
        self,
        filename: str,
        *,
        confirm_restore: bool,
        description: str | None = None,
    ) -> dict:
        await self._ensure_compatible()
        segment = urllib.parse.quote(filename, safe="")
        payload = await self._request(
            "POST",
            f"/v1/archives/{segment}/restore",
            json_body={
                "confirm_restore": confirm_restore,
                "description": description,
            },
        )
        return payload.get("operation", {})

    async def delete_archive(self, filename: str) -> dict:
        await self._ensure_compatible()
        segment = urllib.parse.quote(filename, safe="")
        return await self._request("DELETE", f"/v1/archives/{segment}")

    async def export_archive(self, filename: str) -> bytes:
        await self._ensure_compatible()
        segment = urllib.parse.quote(filename, safe="")
        return await self._request_bytes("GET", f"/v1/archives/{segment}/export")

    async def open_archive_download(self, filename: str) -> ArchiveDownload:
        await self._ensure_compatible()
        token = _read_manager_token(self.settings.token_path)
        segment = urllib.parse.quote(filename, safe="")
        return await asyncio.to_thread(
            self._open_archive_download_sync,
            f"/v1/archives/{segment}/export",
            token,
        )

    async def import_archive(self, filename: str, source) -> dict:
        await self._ensure_compatible()
        token = _read_manager_token(self.settings.token_path)
        return await asyncio.to_thread(self._upload_sync, filename, source, token)

    async def release_status(self) -> dict:
        await self._ensure_compatible()
        return await self._request("GET", "/v1/releases/status")

    async def check_releases(self) -> dict:
        await self._ensure_compatible()
        return await self._request("POST", "/v1/releases/check")

    async def download_release(self, purpose: str | None = None) -> dict:
        await self._ensure_compatible()
        return await self._request(
            "POST",
            "/v1/releases/download",
            json_body={"purpose": purpose},
        )

    async def upgrade_preview(self) -> dict:
        await self._ensure_compatible()
        return await self._request("GET", "/v1/upgrades/preview")

    async def confirm_upgrade(
        self,
        *,
        version: str,
        confirmation_token: str,
    ) -> dict:
        await self._ensure_compatible()
        payload = await self._request(
            "POST",
            "/v1/upgrades/confirm",
            json_body={
                "version": version,
                "confirmation_token": confirmation_token,
            },
        )
        operation = payload.get("operation")
        return operation if isinstance(operation, dict) else payload

    async def upgrade_status(self) -> dict:
        await self._ensure_compatible()
        return await self._request("GET", "/v1/upgrades/status")

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

    async def _request_bytes(self, method: str, path: str) -> bytes:
        token = _read_manager_token(self.settings.token_path)
        return await asyncio.to_thread(self._request_bytes_sync, method, path, token)

    def _request_bytes_sync(self, method: str, path: str, token: str) -> bytes:
        request = urllib.request.Request(
            f"{self.settings.base_url}{path}",
            method=method,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/zip"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.settings.timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            payload = _decode_payload(exc.read())
            raise ManagerClientError(
                str(payload.get("message") or f"Manager API returned HTTP {exc.code}"),
                status_code=exc.code,
                payload=payload,
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ManagerUnavailable(f"Manager is unavailable: {exc}", status_code=503) from exc

    def _open_archive_download_sync(self, path: str, token: str) -> ArchiveDownload:
        request = urllib.request.Request(
            f"{self.settings.base_url}{path}",
            method="GET",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/zip"},
        )
        try:
            response = urllib.request.urlopen(request, timeout=self.settings.timeout)
        except urllib.error.HTTPError as exc:
            payload = _decode_payload(exc.read())
            raise ManagerClientError(
                str(payload.get("message") or f"Manager API returned HTTP {exc.code}"),
                status_code=exc.code,
                payload=payload,
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ManagerUnavailable(f"Manager is unavailable: {exc}", status_code=503) from exc
        return ArchiveDownload(response)

    def _upload_sync(self, filename: str, source, token: str) -> dict[str, Any]:
        parsed = urllib.parse.urlsplit(self.settings.base_url)
        connection_class = (
            http.client.HTTPSConnection
            if parsed.scheme == "https"
            else http.client.HTTPConnection
        )
        connection = connection_class(
            parsed.hostname,
            parsed.port,
            timeout=self.settings.timeout,
        )
        try:
            source.seek(0, io.SEEK_END)
            length = source.tell()
            source.seek(0)
            path = f"{parsed.path.rstrip('/')}/v1/archives/import"
            connection.putrequest("POST", path)
            connection.putheader("Authorization", f"Bearer {token}")
            connection.putheader("Accept", "application/json")
            connection.putheader(
                "X-Archive-Filename",
                urllib.parse.quote(filename, safe=""),
            )
            connection.putheader("Content-Type", "application/zip")
            connection.putheader("Content-Length", str(length))
            connection.endheaders()
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                connection.send(chunk)
            response = connection.getresponse()
            raw = response.read()
        except (OSError, TimeoutError, UnicodeError, http.client.HTTPException) as exc:
            raise ManagerUnavailable(f"Manager is unavailable: {exc}", status_code=503) from exc
        finally:
            connection.close()
        payload = _decode_payload(raw)
        if response.status >= 400 or payload.get("ok") is False:
            raise ManagerClientError(
                str(payload.get("message") or "Manager import failed"),
                status_code=response.status,
                payload=payload,
            )
        payload.pop("ok", None)
        return payload


def _decode_payload(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}
