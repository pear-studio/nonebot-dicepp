"""Private standalone Manager HTTP API."""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from dicepp_meta import get_version

from .auth import ensure_api_token, token_matches
from .config import ManagerSettings
from .control import ControlChannelService
from .factory import create_manager_service
from .service import ManagerService


def create_manager_app(
    settings: ManagerSettings,
    *,
    service: ManagerService | None = None,
    api_token: str | None = None,
) -> FastAPI:
    manager_service = service or create_manager_service(settings)
    if manager_service.control_service is None:
        manager_service.control_service = ControlChannelService(
            project_root=settings.layout.root,
            known_bot_ids=lambda: {
                path.stem
                for path in settings.layout.config_bots_dir.glob("*.json")
                if path.is_file() and path.stem != "_template"
            },
            heartbeat_timeout=settings.control_heartbeat_timeout,
            reload_timeout=settings.control_reload_timeout,
        )
    control_service = manager_service.control_service
    expected_token = api_token or ensure_api_token(settings.token_path or settings.layout.manager_token)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            await control_service.close()
            manager_service.close()

    app = FastAPI(title="DicePP Manager", version="2", lifespan=lifespan)
    app.state.manager_service = manager_service
    app.state.control_service = control_service

    manager_bearer = HTTPBearer(
        auto_error=False,
        scheme_name="ManagerBearerAuth",
        description="Private local Manager API token.",
    )

    async def require_manager_auth(
        credentials: HTTPAuthorizationCredentials | None = Depends(manager_bearer),
    ) -> None:
        if (
            credentials is None
            or credentials.scheme.lower() != "bearer"
            or not token_matches(expected_token, credentials.credentials)
        ):
            raise HTTPException(
                status_code=401,
                detail="Invalid Manager API token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    auth = [Depends(require_manager_auth)]

    @app.exception_handler(HTTPException)
    async def http_error(_request, exc: HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"ok": False, "message": str(exc.detail)},
            headers=exc.headers,
        )

    @app.get("/v1/status", dependencies=auth)
    async def status():
        return {
            "ok": True,
            **(await manager_service.status()),
            "control": control_service.capability(),
        }

    @app.websocket("/v1/control/ws")
    async def control_websocket(ws: WebSocket):
        """Bot-only v1 control endpoint; Dashboard never accepts this traffic."""
        await control_service.websocket_endpoint(ws)

    @app.get("/v1/control/bots", dependencies=auth)
    async def control_bots():
        return {"ok": True, "bots": control_service.bot_statuses()}

    @app.post("/v1/control/reload", dependencies=auth)
    async def control_reload(request: Request):
        body = await _json_body(request)
        bot_id = body.get("bot_id")
        if bot_id is not None and (not isinstance(bot_id, str) or not bot_id):
            raise HTTPException(status_code=400, detail="bot_id must be a non-empty string")
        return {"ok": True, "results": await control_service.reload(bot_id)}

    @app.get("/v1/health", dependencies=auth)
    async def health():
        return {
            "ok": True,
            "dicepp_version": get_version(),
        }

    return app


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Request body must be JSON") from exc
    if body is None:
        return {}
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Request body must be an object")
    return body
