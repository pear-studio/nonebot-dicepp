"""
Dashboard heartbeat reporter for DicePP.

Periodically reports bot status to the DicePP admin dashboard so it can
display online/offline state and provide the bot HTTP URL for web-based
configuration reloads.
"""
import os
import asyncio
from typing import Optional

import aiohttp

from importlib.metadata import version as _get_pkg_version

from utils.logger import logger

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = "4090"
_DEFAULT_HTTP_URL = "http://127.0.0.1:8080"
_HEARTBEAT_INTERVAL_SECONDS = 5
_RETRY_COUNT = 3


class DashboardReporter:
    """Sends periodic heartbeats to the DicePP admin dashboard.

    Usage:
        reporter = DashboardReporter(bot_id="123456789")
        reporter.start()
        # ... in a periodic loop ...
        reporter.tick()
        # ... on shutdown ...
        await reporter.stop()
    """

    def __init__(self, bot_id: str) -> None:
        self._bot_id = bot_id

        host = os.environ.get("DPP_ADMIN_HOST", _DEFAULT_HOST)
        port = os.environ.get("DPP_ADMIN_PORT", _DEFAULT_PORT)
        self._dashboard_url = f"http://{host}:{port}/api/bots/heartbeat"

        self._http_url = os.environ.get("DPP_BOT_HTTP_URL", _DEFAULT_HTTP_URL)
        self._version = _get_pkg_version("dicepp")

        self._session: Optional[aiohttp.ClientSession] = None
        self._last_heartbeat_time: float = 0.0

    # ── public API ──────────────────────────────────────────────────────────

    def start(self) -> None:
        """Create an aiohttp client session for dashboard communication."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            )

    async def stop(self) -> None:
        """Close the aiohttp client session."""
        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None

    def tick(self) -> None:
        """Check elapsed time and send heartbeat if interval has passed.

        Safe to call every tick (sub-second); only sends when >= 5 seconds
        have elapsed since the last successful heartbeat.
        """
        now = asyncio.get_event_loop().time()
        if now - self._last_heartbeat_time >= _HEARTBEAT_INTERVAL_SECONDS:
            asyncio.create_task(self._send_heartbeat())

    async def send_immediate(self) -> None:
        """Send a heartbeat immediately (used on bot connect)."""
        await self._send_heartbeat()

    # ── internals ───────────────────────────────────────────────────────────

    async def _send_heartbeat(self) -> None:
        """POST heartbeat payload to the dashboard, with retry/backoff.

        Retries up to 3 times with 1s/2s/3s backoff on ClientError or
        TimeoutError.  HTTP error responses (4xx/5xx) return immediately
        and are retried by the tick loop after 5 seconds.  Never raises.
        """
        try:
            self.start()  # ensure session exists
        except Exception as exc:
            logger.warning(f"[DashboardReporter] cannot create session: {exc}")
            return

        payload = {
            "bot_id": self._bot_id,
            "version": self._version,
            "http_url": self._http_url,
        }

        last_error: Optional[Exception] = None
        for attempt in range(_RETRY_COUNT):
            try:
                async with self._session.post(
                    self._dashboard_url, json=payload,
                ) as response:
                    if response.status >= 400:
                        text = await response.text()
                        logger.warning(
                            f"[DashboardReporter] heartbeat rejected "
                            f"(attempt {attempt + 1}/{_RETRY_COUNT}): "
                            f"status={response.status} body={text}"
                        )
                        return
                    # Success
                    await response.read()  # consume payload
                    self._last_heartbeat_time = asyncio.get_event_loop().time()
                    logger.debug(
                        f"[DashboardReporter] heartbeat sent "
                        f"bot_id={self._bot_id} version={self._version}"
                    )
                    return
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_error = exc
                logger.warning(
                    f"[DashboardReporter] heartbeat failed "
                    f"(attempt {attempt + 1}/{_RETRY_COUNT}): {exc}"
                )
                if attempt < _RETRY_COUNT - 1:
                    await asyncio.sleep(1 * (attempt + 1))
                continue

        if last_error is not None:
            logger.warning(
                f"[DashboardReporter] heartbeat failed after "
                f"{_RETRY_COUNT} attempts: {last_error}"
            )
