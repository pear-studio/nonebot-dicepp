"""Local HTTP client for a running DicePP Shell session."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .session import RuntimeInfo


class ShellRuntimeRequestError(RuntimeError):
    pass


def send_message(runtime: RuntimeInfo, payload: dict[str, Any]) -> dict[str, Any]:
    return _request_json(
        f"{runtime.base_url}/v1/messages",
        method="POST",
        payload=payload,
        timeout=35,
    )


def fetch_status(runtime: RuntimeInfo) -> dict[str, Any]:
    return _request_json(
        f"{runtime.base_url}/v1/status",
        method="GET",
        timeout=5,
    )


def request_stop(runtime: RuntimeInfo) -> dict[str, Any]:
    return _request_json(
        f"{runtime.base_url}/v1/runtime/stop",
        method="POST",
        payload={},
        timeout=5,
    )


def _request_json(
    url: str,
    *,
    method: str,
    payload: dict[str, Any] | None = None,
    timeout: float,
) -> dict[str, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ShellRuntimeRequestError(
            f"Shell runtime request failed ({exc.code}): {detail}"
        ) from exc
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise ShellRuntimeRequestError(f"Shell runtime is unavailable: {exc}") from exc
    if not isinstance(body, dict):
        raise ShellRuntimeRequestError("Shell runtime returned a non-object response")
    return body
