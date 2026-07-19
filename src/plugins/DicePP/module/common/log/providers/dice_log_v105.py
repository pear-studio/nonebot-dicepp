from __future__ import annotations

import asyncio
import ipaddress
import json
import zlib
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

import aiohttp

from ..projection import LogProjection, ProjectedMessage
from ..publisher import ProviderPublishResult

PROTOCOL_VERSION = 105


class ProviderPublishError(RuntimeError):
    pass


class ProviderUnavailableError(ProviderPublishError):
    pass


class DiceLogV105Provider:
    """Adapter for the legacy Dice Log v105 multipart/zlib wire protocol."""

    name = "dice_log_v105"

    def __init__(
        self,
        endpoint: str,
        *,
        token: str = "",
        timeout_seconds: float = 15.0,
        session_factory: Callable[..., Any] | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("Web publication timeout must be positive")
        self._endpoint = endpoint.strip()
        self._token = token.strip()
        self._timeout_seconds = float(timeout_seconds)
        self._session_factory = session_factory or aiohttp.ClientSession

    async def publish(
        self,
        projection: LogProjection,
        *,
        request_id: str,
        requested_by: str,
    ) -> ProviderPublishResult:
        del request_id  # The v105 wire format has no request-id field.
        if not _is_http_url(self._endpoint):
            raise ProviderUnavailableError("Web publication endpoint is not configured")
        if self._token and not _accepts_bearer_token(self._endpoint):
            raise ProviderUnavailableError(
                "Bearer token requires HTTPS for non-loopback Web publication endpoints"
            )

        form = aiohttp.FormData()
        form.add_field("name", projection.log_name)
        form.add_field("uniform_id", f"QQ:{requested_by.strip()}")
        form.add_field("client", "DicePP")
        form.add_field("version", str(PROTOCOL_VERSION))
        form.add_field(
            "file",
            _compressed_payload(projection),
            filename="log-zlib-compressed",
            content_type="application/octet-stream",
        )
        headers = (
            {"Authorization": f"Bearer {self._token}"}
            if self._token
            else {}
        )
        timeout = aiohttp.ClientTimeout(total=self._timeout_seconds)

        try:
            async with self._session_factory(timeout=timeout) as session:
                async with session.put(
                    self._endpoint,
                    data=form,
                    headers=headers,
                    allow_redirects=False,
                ) as response:
                    body = await response.text()
                    status = int(response.status)
                    reason = str(getattr(response, "reason", "") or "")
        except asyncio.TimeoutError as exc:
            raise ProviderPublishError("Web publication timed out") from exc
        except aiohttp.ClientError as exc:
            raise ProviderPublishError(
                f"Web publication request failed: {type(exc).__name__}"
            ) from exc

        response_data = _parse_response(body)
        if not 200 <= status < 300:
            detail = response_data.get("message") if response_data else None
            safe_detail = _redact_secret(str(detail), self._token) if detail else ""
            safe_reason = _redact_secret(reason, self._token)
            raise ProviderPublishError(
                f"Web provider returned HTTP {status}: "
                f"{safe_detail or safe_reason or 'request failed'}"
            )
        url = response_data.get("url") if response_data else None
        if not isinstance(url, str) or not _is_http_url(url.strip()):
            raise ProviderPublishError(
                "Web provider response did not contain a valid publication URL"
            )
        return ProviderPublishResult(url=url.strip())


def _compressed_payload(projection: LogProjection) -> bytes:
    items = []
    for message in projection.messages:
        user_id = str(message.user_id or "")
        message_id = str(message.message_id or "")
        items.append(
            {
                "nickname": message.nickname,
                "imUserId": user_id,
                "uniformId": f"QQ:{user_id}" if user_id else "",
                "time": int(message.time.timestamp()),
                "message": _render_projected_message(message),
                "isDice": False,
                "commandId": None,
                "commandInfo": None,
                "rawMsgId": message_id,
            }
        )
    raw = json.dumps(
        {"version": PROTOCOL_VERSION, "items": items},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return zlib.compress(raw)


def _render_projected_message(message: ProjectedMessage) -> str:
    """Render only the already-filtered projection, matching TXT/DOCX semantics."""
    lines: list[str] = []
    reply = message.reply
    if reply is not None:
        if reply.author is None:
            lines.append(f"> [回复消息：{reply.message_id}]")
        else:
            lines.append(f"> {reply.author}（消息 {reply.message_id}）")
            lines.extend(f"> {line}" for line in reply.excerpt)
    lines.append(message.readable_text)
    return "\n".join(lines)


def _parse_response(body: str) -> dict[str, Any]:
    try:
        parsed = json.loads(body)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _accepts_bearer_token(value: str) -> bool:
    parsed = urlparse(value)
    if parsed.scheme == "https":
        return True
    if parsed.scheme != "http" or parsed.hostname is None:
        return False
    hostname = parsed.hostname.casefold()
    if hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _redact_secret(value: str, secret: str) -> str:
    if not secret:
        return value
    return value.replace(secret, "[redacted]")
