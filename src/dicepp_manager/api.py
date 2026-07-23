"""Private standalone Manager HTTP API."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import cast

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse

from .auth import ensure_api_token, token_matches
from .config import ManagerSettings
from .factory import create_manager_service
from .models import ManagerAction, VALID_ACTIONS
from .runtime import RuntimeOperationUnsupported
from .service import ManagerService, OperationConflict, OperationFailed, UnknownRuntimeUnit


def create_manager_app(
    settings: ManagerSettings,
    *,
    service: ManagerService | None = None,
    api_token: str | None = None,
) -> FastAPI:
    manager_service = service or create_manager_service(settings)
    expected_token = api_token or ensure_api_token(settings.token_path or settings.layout.manager_token)
    tasks: set[asyncio.Task] = set()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
            if tasks:
                _done, pending = await asyncio.wait(tasks, timeout=5)
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
        finally:
            manager_service.close()

    app = FastAPI(title="DicePP Manager", version="2", lifespan=lifespan)
    app.state.manager_service = manager_service
    app.state.operation_tasks = tasks

    async def require_manager_auth(authorization: str | None = Header(None)) -> None:
        scheme, _, supplied = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not token_matches(expected_token, supplied):
            raise HTTPException(status_code=401, detail="Invalid Manager API token")

    auth = [Depends(require_manager_auth)]

    @app.exception_handler(HTTPException)
    async def http_error(_request, exc: HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"ok": False, "message": str(exc.detail)})

    @app.get("/v1/status", dependencies=auth)
    async def status():
        return {"ok": True, **(await manager_service.status())}

    @app.get("/v1/operations", dependencies=auth)
    async def operations(limit: int = Query(50, ge=1, le=200)):
        return {"ok": True, "operations": manager_service.list_operations(limit)}

    @app.get("/v1/operations/{operation_id}", dependencies=auth)
    async def operation(operation_id: str):
        result = manager_service.get_operation(operation_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Manager operation not found")
        return {"ok": True, "operation": result.to_dict()}

    @app.get("/v1/logs", dependencies=auth)
    async def runtime_logs(lines: int = Query(200, ge=1, le=1000)):
        try:
            result = await manager_service.runtime_logs(lines)
        except RuntimeOperationUnsupported as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        return {"ok": True, "logs": result}

    @app.get("/v1/runtime-units/{runtime_unit_id}/logs", dependencies=auth)
    async def unit_logs(runtime_unit_id: str, lines: int = Query(200, ge=1, le=1000)):
        try:
            result = await manager_service.logs(runtime_unit_id, lines)
        except UnknownRuntimeUnit as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeOperationUnsupported as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        return {"ok": True, "logs": result}

    async def finish_operation(manager_operation) -> None:
        try:
            await manager_service.run(manager_operation)
        except OperationFailed:
            # Durable state is the observable failure channel. Do not emit an
            # unhandled task exception after the caller has received the id.
            return

    @app.post("/v1/runtime-units/{runtime_unit_id}/{action}", dependencies=auth, status_code=202)
    async def operate(runtime_unit_id: str, action: str):
        if action not in VALID_ACTIONS:
            raise HTTPException(status_code=400, detail="Allowed actions: start, stop, restart")
        try:
            manager_operation = manager_service.submit(runtime_unit_id, cast(ManagerAction, action))
        except UnknownRuntimeUnit as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except OperationConflict as exc:
            return JSONResponse(
                status_code=409,
                content={"ok": False, "message": str(exc), "operation": exc.operation.to_dict()},
            )
        except OperationFailed as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={"ok": False, "message": str(exc), "operation": exc.operation.to_dict()},
            )
        task = asyncio.create_task(finish_operation(manager_operation))
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return {"ok": True, "operation": manager_operation.to_dict()}

    return app
