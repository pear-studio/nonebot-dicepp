"""Private standalone Manager HTTP API."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import tempfile
import urllib.parse
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, WebSocket
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from dicepp_data import (
    QueryDatabaseStateError,
    is_query_database_name,
    load_query_database_state,
    set_query_database_enabled,
)
from dicepp_meta import get_version

from .archive import (
    MAX_ARCHIVE_BYTES,
    ArchiveError,
    ArchiveInvalidError,
    ArchiveNameError,
    ArchiveNotFoundError,
)
from .archive_coordinator import ArchiveCoordinator
from .auth import ensure_api_token, token_matches
from .config import ManagerSettings
from .control import ControlChannelService
from .config_validation import (
    ConfigurationValidationError,
    read_config_object,
    validate_bot_candidate,
    validate_user_candidate,
)
from .factory import create_manager_service
from .maintenance import MaintenanceConflict
from .service import ManagerService


def _maintenance_conflict_response(exc: MaintenanceConflict) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={
            "ok": False,
            "message": str(exc),
            "code": "maintenance_conflict",
        },
    )


def _invalid_configuration_response(exc: ConfigurationValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "ok": False,
            "code": "invalid_configuration",
            "message": "Configuration validation failed",
            "errors": exc.errors,
        },
    )


def _stored_configuration_response() -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={
            "ok": False,
            "code": "stored_configuration_invalid",
            "message": "Stored configuration is unreadable",
        },
    )


def _write_managed_config(path: Path, payload: dict) -> None:
    """Atomically persist a Manager-authorized config document."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _query_database_path(settings: ManagerSettings, database: str) -> Path:
    if not is_query_database_name(database):
        raise HTTPException(status_code=400, detail="Invalid query database name")
    directory = (settings.layout.content_dir / "queries").resolve()
    candidate = directory / f"{database}.db"
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(directory)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Query database not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Query database escapes content directory") from exc
    if not resolved.is_file():
        raise HTTPException(status_code=400, detail="Query database is not a file")
    return resolved


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
    if manager_service.archive_coordinator is None:
        manager_service.archive_coordinator = ArchiveCoordinator(
            layout=settings.layout,
            service=manager_service,
        )
    archive_coordinator = manager_service.archive_coordinator
    expected_token = api_token or ensure_api_token(settings.token_path or settings.layout.manager_token)
    tasks: set[asyncio.Task] = set()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            if tasks:
                _done, pending = await asyncio.wait(tasks, timeout=5)
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
            await control_service.close()
            manager_service.close()

    app = FastAPI(title="DicePP Manager", version="2", lifespan=lifespan)
    app.state.manager_service = manager_service
    app.state.operation_tasks = tasks
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

    @app.get("/v1/operations", dependencies=auth)
    async def operations(limit: int = Query(50, ge=1, le=200)):
        return {"ok": True, "operations": manager_service.list_operations(limit)}

    @app.get("/v1/operations/{operation_id}", dependencies=auth)
    async def operation(operation_id: str):
        result = manager_service.get_operation(operation_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Manager operation not found")
        return {"ok": True, "operation": result.to_dict()}

    def track_task(coroutine) -> asyncio.Task:
        task = asyncio.create_task(coroutine)
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return task

    @app.get("/v1/config/user", dependencies=auth)
    async def config_user_get():
        try:
            config = read_config_object(settings.layout.config_user)
        except ConfigurationValidationError:
            return _stored_configuration_response()
        return {"ok": True, "config": config}

    @app.put("/v1/config/user", dependencies=auth)
    async def config_user_save(request: Request):
        body = await _json_body(request)
        try:
            with manager_service.maintenance():
                canonical = validate_user_candidate(settings.layout, body)
                _write_managed_config(settings.layout.config_user, canonical)
        except MaintenanceConflict as exc:
            return _maintenance_conflict_response(exc)
        except ConfigurationValidationError as exc:
            return _invalid_configuration_response(exc)
        return {
            "ok": True,
            "saved": True,
            "application": "deferred",
            "restart_required": True,
        }

    @app.get("/v1/config/bots/{bot_id}", dependencies=auth)
    async def config_bot_get(bot_id: str):
        try:
            path = settings.layout.bot_config_path(bot_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not path.exists():
            raise HTTPException(status_code=404, detail="Bot configuration not found")
        try:
            config = read_config_object(path, missing_is_empty=False)
        except ConfigurationValidationError:
            return _stored_configuration_response()
        return {"ok": True, "config": config}

    @app.put("/v1/config/bots/{bot_id}", dependencies=auth)
    async def config_bot_save(bot_id: str, request: Request):
        body = await _json_body(request)
        try:
            path = settings.layout.bot_config_path(bot_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            with manager_service.maintenance():
                canonical = validate_bot_candidate(settings.layout, bot_id, body)
                _write_managed_config(path, canonical)
        except MaintenanceConflict as exc:
            return _maintenance_conflict_response(exc)
        except ConfigurationValidationError as exc:
            return _invalid_configuration_response(exc)
        return {
            "ok": True,
            "saved": True,
            "application": "deferred",
            "restart_required": True,
        }

    @app.get("/v1/content/query-databases", dependencies=auth)
    async def query_databases_list():
        directory = settings.layout.content_dir / "queries"
        try:
            state = load_query_database_state(directory)
        except QueryDatabaseStateError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        databases = []
        if directory.exists():
            for path in sorted(directory.glob("*.db"), key=lambda item: item.name.casefold()):
                try:
                    resolved = _query_database_path(settings, path.stem)
                except HTTPException:
                    continue
                stat = resolved.stat()
                databases.append({
                    "name": path.stem,
                    "enabled": state.is_enabled(path.stem),
                    "size": stat.st_size,
                    "modified": stat.st_mtime,
                })
        return {"ok": True, "databases": databases}

    @app.put("/v1/content/query-databases/{database}/enabled", dependencies=auth)
    async def query_database_enabled(database: str, request: Request):
        _query_database_path(settings, database)
        body = await _json_body(request)
        enabled = body.get("enabled")
        if not isinstance(enabled, bool):
            raise HTTPException(status_code=400, detail="enabled must be a boolean")
        try:
            with manager_service.maintenance():
                state = set_query_database_enabled(
                    settings.layout.content_dir / "queries",
                    database,
                    enabled,
                )
        except MaintenanceConflict as exc:
            return _maintenance_conflict_response(exc)
        except QueryDatabaseStateError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "ok": True,
            "database": database,
            "enabled": state.is_enabled(database),
            "application": "immediate",
            "restart_required": False,
        }

    @app.get("/v1/archives", dependencies=auth)
    async def archives_list():
        return {"ok": True, "archives": archive_coordinator.list()}

    @app.get("/v1/archives/{filename}", dependencies=auth)
    async def archives_detail(filename: str):
        try:
            archive, manifest = archive_coordinator.detail(filename)
        except ArchiveNameError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ArchiveNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ArchiveInvalidError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"ok": True, "archive": archive, "manifest": manifest}

    @app.post("/v1/archives/{filename}/verify", dependencies=auth)
    async def archives_verify(filename: str):
        try:
            verification = archive_coordinator.verify(filename)
        except ArchiveNameError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ArchiveNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ArchiveInvalidError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"ok": True, "verification": verification}

    @app.delete("/v1/archives/{filename}", dependencies=auth)
    async def archives_delete(filename: str):
        try:
            archive = archive_coordinator.delete(filename)
        except ArchiveNameError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ArchiveNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ArchiveError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True, "deleted": filename, "archive": archive}

    @app.get("/v1/archives/{filename}/export", dependencies=auth)
    async def archives_export(filename: str):
        try:
            path = archive_coordinator.export_path(filename)
        except ArchiveNameError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ArchiveNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ArchiveInvalidError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return FileResponse(
            path,
            media_type="application/zip",
            filename=path.name,
        )

    @app.post("/v1/archives/import", dependencies=auth)
    async def archives_import(request: Request, x_archive_filename: str | None = Header(None)):
        if not x_archive_filename:
            raise HTTPException(status_code=400, detail="X-Archive-Filename is required")
        total = 0
        with tempfile.SpooledTemporaryFile(max_size=8 * 1024**2) as upload:
            async for chunk in request.stream():
                total += len(chunk)
                if total > MAX_ARCHIVE_BYTES:
                    raise HTTPException(status_code=413, detail="Archive upload is too large")
                upload.write(chunk)
            upload.seek(0)
            try:
                result = await asyncio.to_thread(
                    archive_coordinator.import_stream,
                    urllib.parse.unquote(x_archive_filename),
                    upload,
                )
            except ArchiveNameError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except ArchiveInvalidError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"ok": True, "import": result}

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
