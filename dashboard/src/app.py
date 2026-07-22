import asyncio
import ipaddress
import json
import os
import re
import sqlite3
import sys
import time
import uuid
from contextlib import asynccontextmanager, closing
from pathlib import Path
from typing import Optional, cast
from datetime import datetime, timezone

import logging

from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from dicepp_data import PERSONA_DB_ASSET
from dicepp_meta import get_project_info, get_version

from .auth import (
    create_session,
    get_session,
    is_initialized,
    require_auth,
    revoke_session,
    set_password_db,
    validate_password,
    verify_password_db,
)
from .audit import get_recent as audit_get_recent
from .audit import log as audit_log
from .archives import (
    ArchiveInvalidError,
    ArchiveNameError,
    ArchiveNotFoundError,
    ArchiveRestorePlanBlockedError,
    ArchiveRestorePlanVerificationError,
    create_archive,
    delete_archive,
    list_archives,
    plan_archive_restore,
    read_archive_detail,
    restore_archive,
    verify_archive,
)
from .config import DashboardPaths
from .manager import (
    ManagerService,
    OperationConflict,
    OperationFailed,
    RuntimeOperationUnsupported,
    UnknownBot,
)
from .manager.runtime import UnavailableRuntimeBackend
from .manager.models import VALID_ACTIONS, ManagerAction
from .manager.store import MANAGER_OPERATIONS_TABLE_SQL

logger = logging.getLogger("dashboard")

# ── FastAPI app ───────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: initialize DB, state, and ManagerService."""
    db_path = str(DashboardPaths.DASHBOARD_DB)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    _init_db(db_path)
    app.state.dashboard_db = db_path
    app.state.dashboard_paths = DashboardPaths
    app.state.login_failures = {}
    # SSE 订阅者列表是进程内结构。
    # 多 worker 部署 (uvicorn --workers > 1) 时，
    # broadcast_status 仅推送给同一 worker 上的 SSE 客户端。
    app.state.status_subscribers = []  # list[asyncio.Queue] for SSE push
    app.state.manager_service = ManagerService(
        bot_status_provider=lambda: _compute_bot_statuses(app.state.dashboard_db),
        db_path=db_path,
    )
    app.state.manager_db_path = db_path
    yield


app = FastAPI(title="DicePP Dashboard", version=get_version(), lifespan=lifespan)

_LOGIN_FAILURE_LIMIT = 5
_LOGIN_FAILURE_COOLDOWN_SECONDS = 30
_LOGIN_FAILURE_STALE_SECONDS = 10 * 60


# ── Exception handler ─────────────────────────────────────────────────────────


@app.exception_handler(HTTPException)
async def _http_exception_handler(request: Request, exc: HTTPException):
    """Return error responses in the standard format {"ok": false, "message": "..."}."""
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.detail if isinstance(exc.detail, dict) and "ok" in exc.detail
               else {"ok": False, "message": str(exc.detail)},
        headers=exc.headers,
    )


# ── Database init ─────────────────────────────────────────────────────────────


def _init_db(db_path: str) -> None:
    """Initialize dashboard.db with required tables and WAL mode."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")

        conn.execute(
            """CREATE TABLE IF NOT EXISTS auth (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL
            )"""
        )

        conn.execute(
            """CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                expires_at TEXT NOT NULL
            )"""
        )

        conn.execute(
            """CREATE TABLE IF NOT EXISTS bots_meta (
                bot_id TEXT PRIMARY KEY,
                last_heartbeat TEXT,
                version TEXT DEFAULT ''
            )"""
        )

        conn.execute(
            """CREATE TABLE IF NOT EXISTS audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                operator TEXT NOT NULL,
                action TEXT NOT NULL,
                target TEXT NOT NULL,
                detail TEXT DEFAULT '',
                ip TEXT DEFAULT ''
            )"""
        )

        conn.execute(MANAGER_OPERATIONS_TABLE_SQL)

        conn.commit()
    finally:
        conn.close()


# ── Helpers ───────────────────────────────────────────────────────────────────


def _ok(data: dict = None) -> dict:
    """Wrap success response."""
    result = {"ok": True}
    if data:
        result.update(data)
    return result


def _err(message: str, status_code: int = 400) -> HTTPException:
    """Raise an error response."""
    raise HTTPException(status_code=status_code, detail={"ok": False, "message": message})


def _read_json_safe(path: Path) -> dict:
    """Read a JSON file, return empty dict if missing or corrupted."""
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        logger.warning(f"Skipping unreadable config file: {path}")
        return {}


# Config files that the dashboard must never overwrite (git-managed).
_READONLY_CONFIG_NAMES: set[str] = {"global.json"}


def _write_json_atomic(path: Path, data: dict) -> None:
    """Atomically write a dict to a JSON file using .tmp + os.replace.

    Refuses to write to protected files (global.json) which
    is git-managed and should only be changed via code review.
    """
    if path.name in _READONLY_CONFIG_NAMES:
        raise HTTPException(
            status_code=403,
            detail={"ok": False, "message": f"对 {path.name} 的写入被拒绝：此文件由版本库管理，不可通过 Web 接口修改"},
        )
    # Pre-validate serializability so we don't leave a half-written .tmp
    try:
        json.dumps(data, ensure_ascii=False)
    except (TypeError, ValueError) as e:
        raise HTTPException(
            status_code=400,
            detail={"ok": False, "message": f"数据无法序列化为 JSON: {e}"},
        )
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _is_path_traversal(path: str, base: Path) -> bool:
    """Check if the resolved path escapes the given base directory."""
    if not path:
        return True
    try:
        resolved = (base / path).resolve()
        return not resolved.is_relative_to(base.resolve())
    except (ValueError, OSError):
        return True


_ID_PATTERN = re.compile(r'^[a-zA-Z0-9_-]{1,64}$')


def _validate_identifier(value: str, name: str) -> None:
    """Validate identifier: 1-64 chars, alphanumeric + underscore + hyphen."""
    if not value or not _ID_PATTERN.match(value):
        _err(f"{name} 格式无效：仅允许 1~64 位字母、数字、下划线或连字符", 400)


def _apply_deep(target: dict, path: str, value) -> None:
    """Set a value at a dotted path in a nested dict, creating intermediate keys."""
    parts = path.split(".")
    d = target
    for part in parts[:-1]:
        if part not in d or not isinstance(d[part], dict):
            d[part] = {}
        d = d[part]
    d[parts[-1]] = value


def _remove_deep(target: dict, path: str) -> bool:
    """Remove a key at a dotted path in a nested dict. Returns True if removed."""
    parts = path.split(".")
    d = target
    for part in parts[:-1]:
        if part not in d or not isinstance(d[part], dict):
            return False
        d = d[part]
    if parts[-1] in d:
        del d[parts[-1]]
        return True
    return False


_CONTENT_SUBDIRS = {"decks", "random", "queries", "characters", "excel"}

_XLSX_MAGIC = b"\x50\x4b\x03\x04"


def _is_xlsx(path: Path) -> bool:
    """Check if a file is an xlsx by its magic bytes."""
    try:
        with open(path, "rb") as f:
            header = f.read(4)
            return header == _XLSX_MAGIC
    except OSError:
        return False


async def _notify_reload(db_path: str, bot_id: Optional[str] = None) -> list[dict]:
    """Notify bot(s) to reload config via WebSocket Control Channel."""
    from .websocket import send_reload_to_bot

    # Fetch bot IDs and close the DB connection immediately — the async
    # polling loop below may run for seconds per bot and must not hold
    # the connection open.
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if bot_id:
            cursor = conn.execute(
                "SELECT bot_id FROM bots_meta WHERE bot_id = ?", (bot_id,)
            )
        else:
            cursor = conn.execute("SELECT bot_id FROM bots_meta")
        bids = sorted({row["bot_id"] for row in cursor.fetchall()})
    finally:
        conn.close()

    results = []
    for bid in bids:
        request_id = uuid.uuid4().hex
        if await send_reload_to_bot(bid, request_id):
            # Wait up to 5 s for a reload_result
            for _ in range(50):
                await asyncio.sleep(0.1)
                pending = getattr(app.state, "pending_reload_results", {})
                rr = pending.pop(request_id, None)
                if rr is not None:
                    results.append({
                        "bot_id": bid,
                        "status": "ok" if rr["success"] else "error",
                        "error": "; ".join(rr.get("errors", [])) or None,
                    })
                    break
            else:
                results.append({"bot_id": bid, "status": "error", "error": "reload timed out"})
            continue
        results.append({"bot_id": bid, "status": "error", "error": "Bot offline"})

    return results


# ── Auth endpoints ────────────────────────────────────────────────────────────


def _is_windows_runtime() -> bool:
    return sys.platform == "win32"


def _is_local_or_private_address(value: Optional[str]) -> bool:
    """Return whether *value* is localhost or an actual LAN address."""
    if not value:
        return False
    value = value.strip().strip("[]").lower()
    if value == "localhost":
        return True
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if address.is_loopback or address.is_link_local:
        return True
    if isinstance(address, ipaddress.IPv4Address):
        private_v4 = (
            ipaddress.ip_network("10.0.0.0/8"),
            ipaddress.ip_network("172.16.0.0/12"),
            ipaddress.ip_network("192.168.0.0/16"),
        )
        return any(address in network for network in private_v4)
    return address in ipaddress.ip_network("fc00::/7")


def _web_setup_allowed(request: Request) -> bool:
    """Allow initial web setup only from a direct Windows local/LAN URL."""
    if not _is_windows_runtime():
        return False
    client_host = request.client.host if request.client else None
    return _is_local_or_private_address(
        client_host
    ) and _is_local_or_private_address(request.url.hostname)


def _setup_denied_message() -> str:
    if not _is_windows_runtime():
        return "Linux 请先通过命令行执行 Dashboard 管理员初始化"
    return "首次初始化请通过本机或局域网 IP 直接访问，不能通过公网域名或反向代理初始化"


def _login_failure_key(request: Request) -> str:
    return request.client.host if request.client and request.client.host else "unknown"


def _login_failure_entries(request: Request) -> dict[str, dict[str, float]]:
    entries = getattr(request.app.state, "login_failures", None)
    if entries is None:
        entries = {}
        request.app.state.login_failures = entries
    return entries


def _raise_if_login_rate_limited(request: Request) -> None:
    entries = _login_failure_entries(request)
    key = _login_failure_key(request)
    entry = entries.get(key)
    if not entry:
        return

    now = time.monotonic()
    if now - entry.get("last_failed_at", 0) > _LOGIN_FAILURE_STALE_SECONDS:
        entries.pop(key, None)
        return

    blocked_until = entry.get("blocked_until", 0)
    if blocked_until > now:
        retry_after = max(1, int(blocked_until - now))
        raise HTTPException(
            status_code=429,
            detail={"ok": False, "message": f"登录失败次数过多，请 {retry_after} 秒后再试"},
            headers={"Retry-After": str(retry_after)},
        )
    if blocked_until:
        entries.pop(key, None)


def _record_login_failure(request: Request) -> None:
    entries = _login_failure_entries(request)
    key = _login_failure_key(request)
    now = time.monotonic()
    entry = entries.get(key)
    if not entry or now - entry.get("last_failed_at", 0) > _LOGIN_FAILURE_STALE_SECONDS:
        entry = {"count": 0, "blocked_until": 0}

    entry["count"] = entry.get("count", 0) + 1
    entry["last_failed_at"] = now
    if entry["count"] >= _LOGIN_FAILURE_LIMIT:
        entry["blocked_until"] = now + _LOGIN_FAILURE_COOLDOWN_SECONDS
    entries[key] = entry


def _clear_login_failures(request: Request) -> None:
    _login_failure_entries(request).pop(_login_failure_key(request), None)


@app.post("/api/auth/setup")
async def auth_setup(request: Request):
    """Set initial password. Returns 403 if already initialized."""
    db_path = request.app.state.dashboard_db
    if is_initialized(db_path):
        raise HTTPException(status_code=403, detail={"ok": False, "message": "Already initialized"})
    if not _web_setup_allowed(request):
        raise HTTPException(
            status_code=403,
            detail={"ok": False, "message": _setup_denied_message()},
        )

    body = await request.json()
    password = body.get("password", "")

    err = validate_password(password)
    if err:
        raise HTTPException(status_code=400, detail={"ok": False, "message": err})

    set_password_db(db_path, password)
    audit_log(db_path, "auth.setup", "auth", "Initial password set", ip=request.client.host if request.client else "")

    # Auto-login: create session
    token = create_session(db_path)
    response = JSONResponse(_ok())
    response.set_cookie(key="session", value=token, httponly=True, samesite="strict", max_age=7 * 86400)
    return response


@app.post("/api/auth/login")
async def auth_login(request: Request):
    """Login with password, set session cookie."""
    _raise_if_login_rate_limited(request)

    body = await request.json()
    password = body.get("password", "")

    db_path = request.app.state.dashboard_db
    if not verify_password_db(db_path, password):
        _record_login_failure(request)
        raise HTTPException(status_code=401, detail={"ok": False, "message": "Invalid password"})

    _clear_login_failures(request)
    token = create_session(db_path)
    audit_log(db_path, "auth.login", "auth", "Login", ip=request.client.host if request.client else "")

    response = JSONResponse(_ok())
    response.set_cookie(key="session", value=token, httponly=True, samesite="strict", max_age=7 * 86400)
    return response


@app.post("/api/auth/logout", dependencies=[Depends(require_auth)])
async def auth_logout(request: Request):
    """Revoke session."""
    db_path = request.app.state.dashboard_db
    token = request.cookies.get("session")
    if token:
        revoke_session(db_path, token)

    response = JSONResponse(_ok())
    response.delete_cookie(key="session")
    return response


@app.post("/api/auth/change_password", dependencies=[Depends(require_auth)])
async def auth_change_password(request: Request):
    """Change password (verify old first)."""
    body = await request.json()
    old_pwd = body.get("old_password", "")
    new_pwd = body.get("new_password", "")

    db_path = request.app.state.dashboard_db
    if not verify_password_db(db_path, old_pwd):
        raise HTTPException(status_code=401, detail={"ok": False, "message": "Old password is incorrect"})

    err = validate_password(new_pwd)
    if err:
        raise HTTPException(status_code=400, detail={"ok": False, "message": err})

    new_token = set_password_db(db_path, new_pwd, rotate_session=True)
    audit_log(db_path, "auth.change_password", "auth", "Password changed", ip=request.client.host if request.client else "")

    response = JSONResponse(_ok())
    response.set_cookie(key="session", value=new_token, httponly=True, samesite="strict", max_age=7 * 86400)
    return response


@app.get("/api/auth/status")
async def auth_status(request: Request):
    """Return initialization and authentication status."""
    db_path = request.app.state.dashboard_db
    initialized = is_initialized(db_path)

    authenticated = False
    token = request.cookies.get("session")
    if token:
        authenticated = get_session(db_path, token) is not None

    setup_allowed = not initialized and _web_setup_allowed(request)
    return _ok({
        "initialized": initialized,
        "authenticated": authenticated,
        "setup_allowed": setup_allowed,
        "setup_message": "" if setup_allowed else _setup_denied_message(),
        "project": get_project_info(),
    })


# ── Save archives ────────────────────────────────────────────────────────────


def _archive_restore_plan_is_blocked(plan: dict) -> bool:
    return bool(plan.get("problems")) or any(
        entry.get("action") == "blocked"
        for entry in plan.get("entries", [])
        if isinstance(entry, dict)
    )


def _archive_runtime_quiesce_report() -> dict:
    return {
        "enabled": True,
        "bots": [],
        "stop_operations": [],
        "start_operations": [],
        "failed_stage": None,
        "restore_started": False,
        "restart_attempted": False,
        "start_failed": False,
    }


def _archive_runtime_request_detail(
    filename: str | None,
    phase: str,
    *,
    source: str = "archive_restore",
) -> dict:
    detail = {
        "source": source,
        "phase": phase,
    }
    if filename:
        detail["archive_filename"] = filename
    return detail


def _archive_runtime_operation_summary(operation: dict) -> dict:
    summary_keys = (
        "operation_id",
        "bot_id",
        "action",
        "status",
        "message",
        "created_at",
        "updated_at",
        "started_at",
        "finished_at",
    )
    summary = {
        key: operation[key]
        for key in summary_keys
        if key in operation
    }
    detail = operation.get("detail")
    request_detail = detail.get("request") if isinstance(detail, dict) else None
    request_summary = _archive_runtime_request_summary(request_detail)
    if request_summary is not None:
        summary["request"] = request_summary
    return summary


def _archive_runtime_request_summary(request_detail: object) -> dict | None:
    if not isinstance(request_detail, dict):
        return None
    allowed = {
        key: request_detail[key]
        for key in ("source", "archive_filename", "phase")
        if isinstance(request_detail.get(key), str)
    }
    return allowed or None


async def _archive_start_quiesced_bots(
    request: Request,
    *,
    service: ManagerService,
    filename: str | None,
    bot_ids: list[str],
    runtime_quiesce: dict,
    source: str = "archive_restore",
) -> int | None:
    if not bot_ids:
        return None

    runtime_quiesce["restart_attempted"] = True
    first_failure_status: int | None = None
    for bot_id in bot_ids:
        try:
            operation = await service.operate(
                bot_id,
                "start",
                request_detail=_archive_runtime_request_detail(
                    filename,
                    "restart",
                    source=source,
                ),
            )
        except OperationConflict as exc:
            operation_data = exc.operation.to_dict()
            runtime_quiesce["start_operations"].append(
                _archive_runtime_operation_summary(operation_data)
            )
            runtime_quiesce["start_failed"] = True
            if first_failure_status is None:
                first_failure_status = 409
        except OperationFailed as exc:
            operation_data = exc.operation.to_dict()
            runtime_quiesce["start_operations"].append(
                _archive_runtime_operation_summary(operation_data)
            )
            runtime_quiesce["start_failed"] = True
            if first_failure_status is None:
                first_failure_status = exc.status_code
        except Exception as exc:
            operation_data = {
                "bot_id": bot_id,
                "action": "start",
                "status": "failed",
                "message": str(exc) or type(exc).__name__,
                "detail": {
                    "request": _archive_runtime_request_detail(
                        filename,
                        "restart",
                        source=source,
                    ),
                },
            }
            runtime_quiesce["start_operations"].append(
                _archive_runtime_operation_summary(operation_data)
            )
            runtime_quiesce["start_failed"] = True
            if first_failure_status is None:
                first_failure_status = 500
        else:
            runtime_quiesce["start_operations"].append(
                _archive_runtime_operation_summary(operation.to_dict())
            )

    if runtime_quiesce["start_failed"] and runtime_quiesce["failed_stage"] is None:
        runtime_quiesce["failed_stage"] = "start"
    return first_failure_status


async def _archive_stop_runtime_for_restore(
    request: Request,
    *,
    filename: str | None,
    runtime_quiesce: dict,
    source: str = "archive_restore",
) -> tuple[ManagerService, list[str], JSONResponse | None]:
    service = _get_manager_service(request)
    stopped_bot_ids: list[str] = []

    # Manager 未配置时跳过，不阻塞恢复流程
    if isinstance(service.runtime_backend, UnavailableRuntimeBackend):
        runtime_quiesce["enabled"] = False
        return service, stopped_bot_ids, None

    try:
        status = await service.status()
    except Exception as exc:
        runtime_quiesce["failed_stage"] = "discover"
        return service, stopped_bot_ids, JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "message": f"Manager runtime discovery failed: {str(exc) or type(exc).__name__}",
                "runtime_quiesce": runtime_quiesce,
            },
        )

    runtime_quiesce["bots"] = [
        bot["bot_id"]
        for bot in status.get("bots", [])
        if isinstance(bot, dict) and isinstance(bot.get("bot_id"), str)
    ]

    for bot_id in runtime_quiesce["bots"]:
        try:
            operation = await service.operate(
                bot_id,
                "stop",
                request_detail=_archive_runtime_request_detail(
                    filename,
                    "quiesce",
                    source=source,
                ),
            )
        except OperationConflict as exc:
            operation_data = exc.operation.to_dict()
            runtime_quiesce["stop_operations"].append(
                _archive_runtime_operation_summary(operation_data)
            )
            runtime_quiesce["failed_stage"] = "stop"
            await _archive_start_quiesced_bots(
                request,
                service=service,
                filename=filename,
                bot_ids=stopped_bot_ids,
                runtime_quiesce=runtime_quiesce,
                source=source,
            )
            return service, stopped_bot_ids, JSONResponse(
                status_code=409,
                content={
                    "ok": False,
                    "message": operation_data["message"],
                    "runtime_quiesce": runtime_quiesce,
                },
            )
        except OperationFailed as exc:
            operation_data = exc.operation.to_dict()
            runtime_quiesce["stop_operations"].append(
                _archive_runtime_operation_summary(operation_data)
            )
            runtime_quiesce["failed_stage"] = "stop"
            await _archive_start_quiesced_bots(
                request,
                service=service,
                filename=filename,
                bot_ids=stopped_bot_ids,
                runtime_quiesce=runtime_quiesce,
                source=source,
            )
            return service, stopped_bot_ids, JSONResponse(
                status_code=exc.status_code,
                content={
                    "ok": False,
                    "message": f"Manager runtime quiesce failed: {operation_data['message']}",
                    "runtime_quiesce": runtime_quiesce,
                },
            )
        except Exception as exc:
            operation_data = {
                "bot_id": bot_id,
                "action": "stop",
                "status": "failed",
                "message": str(exc) or type(exc).__name__,
                "detail": {
                    "request": _archive_runtime_request_detail(
                        filename,
                        "quiesce",
                        source=source,
                    ),
                },
            }
            runtime_quiesce["stop_operations"].append(
                _archive_runtime_operation_summary(operation_data)
            )
            runtime_quiesce["failed_stage"] = "stop"
            await _archive_start_quiesced_bots(
                request,
                service=service,
                filename=filename,
                bot_ids=stopped_bot_ids,
                runtime_quiesce=runtime_quiesce,
                source=source,
            )
            return service, stopped_bot_ids, JSONResponse(
                status_code=500,
                content={
                    "ok": False,
                    "message": f"Manager runtime quiesce failed: {str(exc) or type(exc).__name__}",
                    "runtime_quiesce": runtime_quiesce,
                },
            )
        else:
            stopped_bot_ids.append(bot_id)
            runtime_quiesce["stop_operations"].append(
                _archive_runtime_operation_summary(operation.to_dict())
            )

    return service, stopped_bot_ids, None


@app.get("/api/archives", dependencies=[Depends(require_auth)])
async def archives_list(request: Request):
    """List local Dashboard save archives."""
    paths = getattr(request.app.state, "dashboard_paths", DashboardPaths)
    return _ok({"archives": list_archives(paths=paths)})


@app.post("/api/archives", dependencies=[Depends(require_auth)])
async def archives_create(request: Request):
    """Create a local Dashboard save archive."""
    try:
        body = await request.json()
    except json.JSONDecodeError:
        body = {}
    if body is None:
        body = {}
    if not isinstance(body, dict):
        _err("Archive request body must be a JSON object", 400)

    description = body.get("description")
    if description is not None and not isinstance(description, str):
        _err("description must be a string", 400)

    paths = getattr(request.app.state, "dashboard_paths", DashboardPaths)
    try:
        archive, manifest = create_archive(description=description, paths=paths)
    except Exception as exc:
        message = str(exc) or type(exc).__name__
        _err(f"Archive creation failed: {message}", 500)
    return _ok({"archive": archive, "manifest": manifest})


@app.post("/api/archives/{filename:path}/verify", dependencies=[Depends(require_auth)])
async def archives_verify(filename: str, request: Request):
    """Verify one local Dashboard save archive before restore."""
    paths = getattr(request.app.state, "dashboard_paths", DashboardPaths)
    try:
        verification = verify_archive(filename, paths=paths)
    except ArchiveNameError as exc:
        _err(str(exc), 400)
    except ArchiveNotFoundError as exc:
        _err(str(exc), 404)
    except ArchiveInvalidError as exc:
        _err(str(exc), 422)
    return _ok({"verification": verification})


@app.post("/api/archives/{filename:path}/restore-plan", dependencies=[Depends(require_auth)])
async def archives_restore_plan(filename: str, request: Request):
    """Return a read-only restore target plan for one verified archive."""
    paths = getattr(request.app.state, "dashboard_paths", DashboardPaths)
    try:
        plan = plan_archive_restore(filename, paths=paths)
    except ArchiveNameError as exc:
        _err(str(exc), 400)
    except ArchiveNotFoundError as exc:
        _err(str(exc), 404)
    except ArchiveInvalidError as exc:
        _err(str(exc), 422)
    except ArchiveRestorePlanVerificationError as exc:
        return JSONResponse(
            status_code=409,
            content={
                "ok": False,
                "message": str(exc),
                "verification": exc.verification,
            },
        )
    return _ok({"plan": plan})


@app.post("/api/archives/{filename:path}/restore", dependencies=[Depends(require_auth)])
async def archives_restore(filename: str, request: Request):
    """Restore one verified archive after creating a pre-restore archive."""
    try:
        body = await request.json()
    except json.JSONDecodeError:
        body = {}
    if body is None:
        body = {}
    if not isinstance(body, dict):
        _err("Archive restore request body must be a JSON object", 400)
    if body.get("confirm_restore") is not True:
        _err("confirm_restore must be true", 400)

    description = body.get("description")
    if description is not None and not isinstance(description, str):
        _err("description must be a string", 400)
    quiesce_runtime = body.get("quiesce_runtime", False)
    if not isinstance(quiesce_runtime, bool):
        _err("quiesce_runtime must be a boolean", 400)

    paths = getattr(request.app.state, "dashboard_paths", DashboardPaths)
    if quiesce_runtime:
        runtime_quiesce = _archive_runtime_quiesce_report()
        try:
            plan = plan_archive_restore(filename, paths=paths)
            if _archive_restore_plan_is_blocked(plan):
                runtime_quiesce["failed_stage"] = "plan"
                return JSONResponse(
                    status_code=409,
                    content={
                        "ok": False,
                        "message": "Archive restore plan is blocked",
                        "plan": plan,
                        "runtime_quiesce": runtime_quiesce,
                    },
                )
        except ArchiveNameError as exc:
            _err(str(exc), 400)
        except ArchiveNotFoundError as exc:
            _err(str(exc), 404)
        except ArchiveInvalidError as exc:
            _err(str(exc), 422)
        except ArchiveRestorePlanVerificationError as exc:
            runtime_quiesce["failed_stage"] = "plan"
            return JSONResponse(
                status_code=409,
                content={
                    "ok": False,
                    "message": str(exc),
                    "verification": exc.verification,
                    "runtime_quiesce": runtime_quiesce,
                },
            )

        service, stopped_bot_ids, stop_response = await _archive_stop_runtime_for_restore(
            request,
            filename=filename,
            runtime_quiesce=runtime_quiesce,
        )
        if stop_response is not None:
            return stop_response

        restore: dict | None = None
        restore_response: JSONResponse | None = None
        runtime_quiesce["restore_started"] = True
        try:
            restore = restore_archive(filename, description=description, paths=paths)
        except ArchiveNameError as exc:
            runtime_quiesce["failed_stage"] = "restore"
            restore_response = JSONResponse(
                status_code=400,
                content={"ok": False, "message": str(exc)},
            )
        except ArchiveNotFoundError as exc:
            runtime_quiesce["failed_stage"] = "restore"
            restore_response = JSONResponse(
                status_code=404,
                content={"ok": False, "message": str(exc)},
            )
        except ArchiveInvalidError as exc:
            runtime_quiesce["failed_stage"] = "restore"
            restore_response = JSONResponse(
                status_code=422,
                content={"ok": False, "message": str(exc)},
            )
        except ArchiveRestorePlanVerificationError as exc:
            runtime_quiesce["failed_stage"] = "restore"
            restore_response = JSONResponse(
                status_code=409,
                content={
                    "ok": False,
                    "message": str(exc),
                    "verification": exc.verification,
                },
            )
        except ArchiveRestorePlanBlockedError as exc:
            runtime_quiesce["failed_stage"] = "restore"
            restore_response = JSONResponse(
                status_code=409,
                content={
                    "ok": False,
                    "message": str(exc),
                    "plan": exc.plan,
                },
            )
        except Exception as exc:
            runtime_quiesce["failed_stage"] = "restore"
            message = str(exc) or type(exc).__name__
            restore_response = JSONResponse(
                status_code=500,
                content={
                    "ok": False,
                    "message": f"Archive restore failed: {message}",
                },
            )
        else:
            if restore["failed_entries"]:
                runtime_quiesce["failed_stage"] = "restore"
                restore_response = JSONResponse(
                    status_code=500,
                    content={
                        "ok": False,
                        "message": "Archive restore failed",
                        "restore": restore,
                    },
                )

        start_failure_status = await _archive_start_quiesced_bots(
            request,
            service=service,
            filename=filename,
            bot_ids=stopped_bot_ids,
            runtime_quiesce=runtime_quiesce,
        )

        if restore_response is not None:
            content = dict(restore_response.body and json.loads(restore_response.body) or {})
            content["runtime_quiesce"] = runtime_quiesce
            return JSONResponse(
                status_code=restore_response.status_code,
                content=content,
            )

        if start_failure_status is not None:
            return JSONResponse(
                status_code=start_failure_status,
                content={
                    "ok": False,
                    "message": "Archive restore completed but runtime restart failed",
                    "restore": restore,
                    "runtime_quiesce": runtime_quiesce,
                },
            )

        return _ok({
            "restore": restore,
            "runtime_quiesce": runtime_quiesce,
        })

    try:
        restore = restore_archive(filename, description=description, paths=paths)
    except ArchiveNameError as exc:
        _err(str(exc), 400)
    except ArchiveNotFoundError as exc:
        _err(str(exc), 404)
    except ArchiveInvalidError as exc:
        _err(str(exc), 422)
    except ArchiveRestorePlanVerificationError as exc:
        return JSONResponse(
            status_code=409,
            content={
                "ok": False,
                "message": str(exc),
                "verification": exc.verification,
            },
        )
    except ArchiveRestorePlanBlockedError as exc:
        return JSONResponse(
            status_code=409,
            content={
                "ok": False,
                "message": str(exc),
                "plan": exc.plan,
            },
        )
    except Exception as exc:
        message = str(exc) or type(exc).__name__
        _err(f"Archive restore failed: {message}", 500)

    if restore["failed_entries"]:
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "message": "Archive restore failed",
                "restore": restore,
            },
        )
    return _ok({"restore": restore})


@app.get("/api/archives/{filename:path}", dependencies=[Depends(require_auth)])
async def archives_detail(filename: str, request: Request):
    """Return one local Dashboard save archive manifest."""
    paths = getattr(request.app.state, "dashboard_paths", DashboardPaths)
    try:
        archive, manifest = read_archive_detail(filename, paths=paths)
    except ArchiveNameError as exc:
        _err(str(exc), 400)
    except ArchiveNotFoundError as exc:
        _err(str(exc), 404)
    except ArchiveInvalidError as exc:
        _err(str(exc), 422)
    return _ok({"archive": archive, "manifest": manifest})


@app.delete("/api/archives/{filename:path}", dependencies=[Depends(require_auth)])
async def archives_delete(filename: str, request: Request):
    """Delete one local Dashboard save archive."""
    paths = getattr(request.app.state, "dashboard_paths", DashboardPaths)
    try:
        archive = delete_archive(filename, paths=paths)
    except ArchiveNameError as exc:
        _err(str(exc), 400)
    except ArchiveNotFoundError as exc:
        _err(str(exc), 404)
    return _ok({"deleted": filename, "archive": archive})


# ── Bot discovery ─────────────────────────────────────────────────────────────


@app.get("/api/bots", dependencies=[Depends(require_auth)])
async def list_bots(request: Request):
    """Scan config/bots/*.json, exclude _template.json, return bot_id list."""
    bots_dir = DashboardPaths.CONFIG_BOTS_DIR
    ids = []
    if bots_dir.exists():
        for f in sorted(bots_dir.iterdir()):
            if f.suffix == ".json" and f.stem != "_template":
                ids.append(f.stem)

    return _ok({"bots": ids})


# ── Data browsing ─────────────────────────────────────────────────────────────

# Table name → Chinese label mapping
TABLE_LABELS: dict = {
    "karma": "用户 Karma",
    "initiative": "先攻列表",
    "characters_dnd": "D&D 角色卡",
    "nickname": "用户昵称",
    "group_config": "群组配置",
    "user_config": "用户配置",
    "group_activate": "群组激活状态",
    "group_welcome": "群组欢迎语",
    "chat_record": "聊天记录",
    "bot_control": "Bot 控制",
    "user_stat": "用户统计",
    "group_stat": "群组统计",
    "meta_stat": "元统计",
    "npc_health": "NPC 生命值",
    "hub_config": "Hub 配置",
    "persona_whitelist": "Persona 白名单",
    "persona_user_mute": "用户禁言",
    "persona_user_llm_config": "用户 LLM 配置",
    "persona_global_settings": "Persona 全局设置",
    "persona_session": "Persona 会话",
    "persona_session_message": "Persona 会话消息",
    "message_stream": "消息流",
    "persona_settings": "Persona 设置",
    "persona_score_history": "评分历史",
    "persona_usage": "每日用量",
    "persona_diary": "日记",
    "persona_daily_events": "每日事件",
    "persona_character_state": "角色状态",
    "persona_user_profiles": "用户画像",
    "persona_user_relationships": "用户关系",
    "persona_scoring_failures": "评分失败记录",
    "persona_group_activity": "群活跃度",
    "persona_familiarity_daily": "每日熟悉度",
    "persona_llm_traces": "LLM 追踪",
    "persona_agent_runs": "Agent 运行",
    "persona_agent_events": "Agent 事件",
    # Content query DB tables
    "data": "词条数据",
    "redirect": "重定向",
}


@app.get("/api/data/{bot_id}/tables", dependencies=[Depends(require_auth)])
async def data_tables(bot_id: str, request: Request):
    """Scan sqlite_master from bot_data.db (mode=ro), return [{name, count, label}]."""
    _validate_identifier(bot_id, "bot_id")
    db_path = DashboardPaths.bot_data_db_path(bot_id)
    if not db_path.exists():
        _err(f"Bot data not found for {bot_id}", 404)

    try:
        with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name != 'schema_version' ORDER BY name"
            )
            tables = [row[0] for row in cursor.fetchall()]

            result = []
            for t in tables:
                count_cursor = conn.execute(f"SELECT COUNT(*) FROM \"{t}\"")
                count = count_cursor.fetchone()[0]
                result.append({"name": t, "count": count, "label": TABLE_LABELS.get(t, t)})

        return _ok({"tables": result})
    except sqlite3.DatabaseError as e:
        _err(f"Database error: {e}", 500)


@app.get("/api/data/{bot_id}/table/{table}", dependencies=[Depends(require_auth)])
async def data_table(
    bot_id: str,
    table: str,
    request: Request,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    q: Optional[str] = Query(None),
):
    """Paginated records with optional search."""
    _validate_identifier(bot_id, "bot_id")
    db_path = DashboardPaths.bot_data_db_path(bot_id)
    if not db_path.exists():
        _err(f"Bot data not found for {bot_id}", 404)

    try:
        with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as conn:
            conn.row_factory = sqlite3.Row

            # Sanitize table name (prevent injection)
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            )
            if not cursor.fetchone():
                _err(f"Table '{table}' not found", 404)

            # Get column info
            col_cursor = conn.execute("SELECT name FROM pragma_table_info(?)", (table,))
            all_columns = [row[0] for row in col_cursor.fetchall()]

            # Auto-detect key columns: columns that aren't 'data' or 'updated_at'
            key_columns = [c for c in all_columns if c not in ("data", "updated_at")]

            # Build query
            select_cols = ", ".join(f'"{c}"' for c in all_columns)
            if q and key_columns:
                # Escape SQL LIKE wildcards (% and _) so user input is matched literally
                escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                like_param = f"%{escaped}%"
                params = [like_param] * len(key_columns)

                escape_sql = "ESCAPE '\\'"
                where_clause = " OR ".join(f'"{c}" LIKE ? {escape_sql}' for c in key_columns)

                count_cursor = conn.execute(f"SELECT COUNT(*) FROM \"{table}\" WHERE {where_clause}", params)
                total = count_cursor.fetchone()[0]

                cursor = conn.execute(
                    f"SELECT {select_cols} FROM \"{table}\" WHERE {where_clause} ORDER BY rowid LIMIT ? OFFSET ?",
                    params + [limit, offset],
                )
            else:
                count_cursor = conn.execute(f"SELECT COUNT(*) FROM \"{table}\"")
                total = count_cursor.fetchone()[0]

                cursor = conn.execute(
                    f"SELECT {select_cols} FROM \"{table}\" ORDER BY rowid LIMIT ? OFFSET ?",
                    (limit, offset),
                )

            records = [dict(row) for row in cursor.fetchall()]

        return _ok({
            "records": records,
            "total": total,
            "columns": all_columns,
            "offset": offset,
            "limit": limit,
        })
    except sqlite3.DatabaseError as e:
        _err(f"Database error: {e}", 500)


# ── Config editing ────────────────────────────────────────────────────────────


# Module-level cache for the loaded pydantic_models module
_pydantic_module_cache = None


def _load_pydantic_models_module():
    """Import pydantic_models.py via importlib (avoids pulling in nonebot2).

    Returns the module on success, or None.
    Module reference is cached so _cached_config_field_metadata() and
    _cached_config_layout() share a single import.
    """
    global _pydantic_module_cache
    if _pydantic_module_cache is not None:
        return _pydantic_module_cache

    import importlib.util

    relative_path = Path("src/plugins/DicePP/core/config/pydantic_models.py")
    candidates = (
        DashboardPaths.PROJECT_ROOT / relative_path,
        DashboardPaths.SOURCE_ROOT / relative_path,
    )
    _pydantic_path = next((path for path in candidates if path.exists()), None)
    if _pydantic_path is None:
        return None

    try:
        spec = importlib.util.spec_from_file_location(
            "dicepp_pydantic_models",
            str(_pydantic_path),
        )
        if spec is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _pydantic_module_cache = mod
        return mod
    except (FileNotFoundError, ModuleNotFoundError):
        return None
    except Exception:
        import logging
        logging.getLogger("dashboard").exception(
            "Unexpected error loading config field metadata from %s", _pydantic_path
        )
        return None


def _flatten_json_schema(s: dict, defs: dict, prefix: str = "",
                         inherited_tab: str = "", inherited_section: str = "") -> dict:
    """Flatten a Pydantic v2 JSON schema to dotted-key → {title, description, tab, section}.

    Model-level json_schema_extra (ConfigDict) keys are merged directly into the
    model schema by Pydantic v2.  Likewise, Field(json_schema_extra={...}) keys
    are merged directly into each property schema — *not* nested under a
    ``json_schema_extra`` sub-key.  This function reads ``dashboard_tab`` and
    ``dashboard_section`` directly from schema nodes.

    Known limitation: does not handle anyOf (Optional[BaseModel]) or
    items.$ref (List[BaseModel]).  These patterns exist for fields like
    persona_ai.providers.<name>.models[*].circuit_breaker, but their dotted
    keys contain dynamic segments the flat config_merged output cannot match.
    """
    # Model-level json_schema_extra keys are merged at the model schema top level
    tab = s.get("dashboard_tab", inherited_tab)
    section = s.get("dashboard_section", inherited_section)

    result = {}
    for key, prop in s.get("properties", {}).items():
        full = f"{prefix}.{key}" if prefix else key
        title = prop.get("title", key)
        desc = prop.get("description", "") or ""

        # Field-level json_schema_extra keys are merged directly into the
        # property schema by Pydantic v2 (not nested under json_schema_extra).
        field_tab = prop.get("dashboard_tab", tab)
        field_section = prop.get("dashboard_section", section)

        result[full] = {
            "title": title,
            "description": desc,
            "tab": field_tab,
            "section": field_section,
        }

        # Resolve $ref for nested models
        if "$ref" in prop:
            ref_name = prop["$ref"].split("/")[-1]
            ref_schema = defs.get(ref_name, {})
            result.update(_flatten_json_schema(ref_schema, defs, full,
                                               field_tab, field_section))
        elif "properties" in prop:
            result.update(_flatten_json_schema(prop, defs, full,
                                               field_tab, field_section))
        # Handle allOf (e.g., for Optional with $ref)
        elif "allOf" in prop:
            for item in prop["allOf"]:
                if "$ref" in item:
                    ref_name = item["$ref"].split("/")[-1]
                    ref_schema = defs.get(ref_name, {})
                    result.update(_flatten_json_schema(ref_schema, defs, full,
                                                       field_tab, field_section))
        # Handle additionalProperties.$ref (Dict[str, BaseModel], e.g. providers)
        elif "additionalProperties" in prop and isinstance(prop["additionalProperties"], dict):
            ap = prop["additionalProperties"]
            if "$ref" in ap:
                ref_name = ap["$ref"].split("/")[-1]
                ref_schema = defs.get(ref_name, {})
                result.update(_flatten_json_schema(ref_schema, defs, full,
                                                   field_tab, field_section))

    return result


def _get_config_field_metadata() -> dict:
    """Extract field titles, descriptions, tab and section from BotConfig Pydantic model.

    Returns a flat dict mapping dotted keys to {title, description, tab, section}.
    Model-level json_schema_extra (ConfigDict) provides default tab/section;
    field-level json_schema_extra (Field) can override section per-field.
    """
    mod = _load_pydantic_models_module()
    if mod is None:
        return {}

    try:
        BotConfig = mod.BotConfig
        schema = BotConfig.model_json_schema()
        defs = schema.get("$defs", {})
        return _flatten_json_schema(schema, defs)
    except Exception:
        import logging
        logging.getLogger("dashboard").exception(
            "Failed to extract field metadata from BotConfig"
        )
        return {}


# Cache metadata at module level (computed once on first use)
_config_field_metadata_cache: Optional[dict] = None
_config_layout_cache: Optional[dict] = None


def _cached_config_field_metadata() -> dict:
    global _config_field_metadata_cache
    if _config_field_metadata_cache is None:
        result = _get_config_field_metadata()
        if result:
            _config_field_metadata_cache = result
        return result
    return _config_field_metadata_cache


def _cached_config_layout() -> dict:
    """Return DASHBOARD_LAYOUT from pydantic_models or empty dict."""
    global _config_layout_cache
    if _config_layout_cache is None:
        mod = _load_pydantic_models_module()
        _config_layout_cache = getattr(mod, "DASHBOARD_LAYOUT", {}) if mod else {}
    return _config_layout_cache


def _find_meta(dotted: str, meta: dict) -> dict:
    """Match a dotted data key against static schema metadata keys.

    Three-level fallback for dynamic keys (e.g. providers.<name>.api_key):
    1. Exact match
    2. Remove one segment at a time (skip the dynamic key segment)
    3. Prefix truncation from right (parent node fallback for tab/section)
    """
    if dotted in meta:
        return meta[dotted]
    parts = dotted.split(".")
    # Level 2: try removing each segment (skip dynamic key)
    for i in range(len(parts)):
        candidate = ".".join(parts[:i] + parts[i+1:])
        if candidate in meta:
            return meta[candidate]
    # Level 3: prefix fallback (get tab/section from parent, label less precise)
    for i in range(len(parts) - 1, 0, -1):
        prefix = ".".join(parts[:i])
        if prefix in meta:
            return meta[prefix]
    return {}


@app.get("/api/config/merged", dependencies=[Depends(require_auth)])
async def config_merged(request: Request, bot_id: Optional[str] = Query(None)):
    """Merge global.json + user.json + bots/{bot_id}.json with source annotation."""
    global_cfg = _read_json_safe(DashboardPaths.CONFIG_GLOBAL)
    user_cfg = _read_json_safe(DashboardPaths.CONFIG_USER)

    # Annotate merged config: global=default, user overlays=user, bot overlays=bot
    result_annotated = {}

    def _annotate_deep(base: dict, overlay: dict, source: str, prefix: str = ""):
        for key, value in base.items():
            if key.startswith("_comment"):
                continue
            dotted = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                # Recurse into nested dicts — if overlay has a dict for this key, pass it
                overlay_child = overlay.get(key, {}) if isinstance(overlay.get(key), dict) else {}
                _annotate_deep(value, overlay_child, source, dotted)
            else:
                if key in overlay:
                    result_annotated[dotted] = {"value": overlay[key], "source": source}
                else:
                    result_annotated[dotted] = {"value": value, "source": "default"}

        # Extra keys from overlay not in base
        for key, value in overlay.items():
            if key.startswith("_comment"):
                continue
            dotted = f"{prefix}.{key}" if prefix else key
            if key not in base:
                if isinstance(value, dict):
                    _annotate_deep(value, {}, source, dotted)
                else:
                    result_annotated[dotted] = {"value": value, "source": source}

    # Merge user over default
    _annotate_deep(global_cfg, user_cfg, "user")

    # Merge bot over user+default
    if bot_id:
        bot_cfg = _read_json_safe(DashboardPaths.bot_config_path(bot_id))
        # Re-annotate: start from the previous result, update with bot overrides
        # For each key in bot_cfg, overwrite source to "bot"
        def _flatten_and_annotate(d: dict, prefix: str = ""):
            """Flatten dict to dotted keys, return {dotted: value}."""
            items = {}
            for key, value in d.items():
                if key.startswith("_comment"):
                    continue
                dotted = f"{prefix}.{key}" if prefix else key
                if isinstance(value, dict):
                    items.update(_flatten_and_annotate(value, dotted))
                else:
                    items[dotted] = value
            return items

        bot_flat = _flatten_and_annotate(bot_cfg)
        for dotted, value in bot_flat.items():
            result_annotated[dotted] = {"value": value, "source": "bot"}

    # Load field metadata from Pydantic models (title=label, description=tooltip, tab, section)
    field_meta = _cached_config_field_metadata()

    # Build output with labels, descriptions, tab and section
    output = {}
    for dotted, entry in result_annotated.items():
        meta = _find_meta(dotted, field_meta)
        output[dotted] = {
            "value": entry["value"],
            "source": entry["source"],
            "label": meta.get("title", dotted),
            "description": meta.get("description", ""),
            "tab": meta.get("tab", "config"),
            "section": meta.get("section", "runtime"),
        }

    layout = _cached_config_layout()
    return _ok({"config": output, "layout": layout})


@app.post("/api/config/set", dependencies=[Depends(require_auth)])
async def config_set(request: Request):
    """Deep merge a value into user.json at a dotted path. Atomic write."""
    body = await request.json()
    path = body.get("path", "")
    value = body.get("value")

    if not path:
        _err("path is required")

    user_path = DashboardPaths.CONFIG_USER
    user_cfg = _read_json_safe(user_path)

    _apply_deep(user_cfg, path, value)
    _write_json_atomic(user_path, user_cfg)

    db_path = request.app.state.dashboard_db
    audit_detail = json.dumps({"value": "***"}, ensure_ascii=False) if re.search(r'\.api_key$', path) else json.dumps({"value": value}, ensure_ascii=False)
    audit_log(db_path, "config.set", path, audit_detail,
              ip=request.client.host if request.client else "")

    # Notify all bots
    reload_results = await _notify_reload(db_path)

    return _ok({"saved": True, "reload": reload_results})


@app.post("/api/config/reset", dependencies=[Depends(require_auth)])
async def config_reset(request: Request):
    """Remove a key from user.json. Atomic write."""
    body = await request.json()
    path = body.get("path", "")

    if not path:
        _err("path is required")

    user_path = DashboardPaths.CONFIG_USER
    user_cfg = _read_json_safe(user_path)

    removed = _remove_deep(user_cfg, path)
    _write_json_atomic(user_path, user_cfg)

    db_path = request.app.state.dashboard_db
    audit_log(db_path, "config.reset", path, "reset to default",
              ip=request.client.host if request.client else "")

    reload_results = await _notify_reload(db_path) if removed else []

    return _ok({"removed": removed, "reload": reload_results})


# NOTE: Not consumed by Dashboard frontend; retained for external API consumers
# (e.g., bot runtime config sync via dashboard_client.py).
@app.get("/api/config/bots/{bot_id}", dependencies=[Depends(require_auth)])
async def config_bot_get(bot_id: str, request: Request):
    """Read bot config file content."""
    _validate_identifier(bot_id, "bot_id")
    cfg_path = DashboardPaths.bot_config_path(bot_id)
    cfg = _read_json_safe(cfg_path)
    return _ok({"config": cfg})


@app.post("/api/config/bots/{bot_id}/save", dependencies=[Depends(require_auth)])
async def config_bot_save(bot_id: str, request: Request):
    """Validate JSON, atomically write bot config, audit, notify reload."""
    _validate_identifier(bot_id, "bot_id")
    body = await request.json()

    cfg_path = DashboardPaths.bot_config_path(bot_id)
    _write_json_atomic(cfg_path, body)

    db_path = request.app.state.dashboard_db
    audit_log(db_path, "config.bot.save", f"bots/{bot_id}", "",
              ip=request.client.host if request.client else "")

    reload_results = await _notify_reload(db_path, bot_id)

    return _ok({"saved": True, "reload": reload_results})


@app.get("/api/config/user", dependencies=[Depends(require_auth)])
async def config_user_get(request: Request):
    """Return raw user.json content for JSON view editing."""
    user_cfg = _read_json_safe(DashboardPaths.CONFIG_USER)
    return _ok({"config": user_cfg})


@app.post("/api/config/user/save", dependencies=[Depends(require_auth)])
async def config_user_save(request: Request):
    """Overwrite user.json with full JSON body. Atomic write + audit + reload."""
    body = await request.json()
    if not isinstance(body, dict):
        _err("Body must be a JSON object")

    _write_json_atomic(DashboardPaths.CONFIG_USER, body)

    db_path = request.app.state.dashboard_db
    audit_log(db_path, "config.user.save", "user.json", "",
              ip=request.client.host if request.client else "")

    reload_results = await _notify_reload(db_path)
    return _ok({"saved": True, "reload": reload_results})


# ── Persona character cards ──────────────────────────────────────────────────

_CHAR_NAME_PATTERN = re.compile(r'^[^\x00/\\]{1,128}$')


def _validate_character_name(name: str) -> None:
    """Validate character name: 1-128 chars, no path separators or null bytes."""
    if not name or not _CHAR_NAME_PATTERN.match(name):
        _err(f"角色名格式无效：1~128 位非空字符，禁止路径分隔符和空字节", 400)


@app.get("/api/persona/characters", dependencies=[Depends(require_auth)])
async def persona_characters(request: Request):
    """List character directories under content/characters/."""
    chars_dir = DashboardPaths.CONTENT_DIR / "characters"
    characters = []
    if chars_dir.exists():
        for d in sorted(chars_dir.iterdir()):
            if d.is_dir() and not d.name.startswith('.'):
                char_yaml = d / "character.yaml"
                characters.append({
                    "name": d.name,
                    "has_config": char_yaml.exists(),
                })
    return _ok({"characters": characters})


@app.get("/api/persona/characters/{name}", dependencies=[Depends(require_auth)])
async def persona_character_get(name: str, request: Request):
    """Read character.yaml for a character."""
    _validate_character_name(name)
    if _is_path_traversal(name, DashboardPaths.CONTENT_DIR / "characters"):
        _err("Path traversal detected", 400)

    yaml_path = DashboardPaths.CONTENT_DIR / "characters" / name / "character.yaml"
    if not yaml_path.exists():
        _err(f"Character not found: {name}", 404)

    try:
        content = yaml_path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        _err("Cannot read character.yaml as text", 400)

    return _ok({"name": name, "content": content})


@app.post("/api/persona/characters/{name}/save", dependencies=[Depends(require_auth)])
async def persona_character_save(name: str, request: Request):
    """Save character.yaml for a character (creates directory if needed)."""
    _validate_character_name(name)
    if _is_path_traversal(name, DashboardPaths.CONTENT_DIR / "characters"):
        _err("Path traversal detected", 400)

    body = await request.json()
    content = body.get("content", "")
    if not isinstance(content, str):
        _err("content must be a string")

    yaml_path = DashboardPaths.CONTENT_DIR / "characters" / name / "character.yaml"
    yaml_path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write: .tmp + fsync + os.replace (consistent with _write_json_atomic)
    tmp_path = yaml_path.with_suffix(".yaml.tmp")
    tmp_path.write_text(content, encoding="utf-8")
    fd = os.open(str(tmp_path), os.O_RDWR)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp_path, yaml_path)

    db_path = request.app.state.dashboard_db
    audit_log(db_path, "persona.character.save", name, "",
              ip=request.client.host if request.client else "")

    return _ok({"saved": True})


# ── Shared bot status computation ──────────────────────────────────────────────


def _compute_bot_statuses(db_path: str) -> list[dict]:
    """Read bots_meta + discovered bots, compute online status.

    Used by both the REST endpoint and the SSE broadcast path.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    meta_bots = {}
    try:
        cursor = conn.execute("SELECT bot_id, version, last_heartbeat FROM bots_meta")
        for row in cursor.fetchall():
            meta_bots[row["bot_id"]] = {
                "bot_id": row["bot_id"],
                "version": row["version"] or "",
                "last_heartbeat_ts": row["last_heartbeat"] or "",
            }
    finally:
        conn.close()

    discovered = set()
    bots_dir = DashboardPaths.CONFIG_BOTS_DIR
    if bots_dir.exists():
        for f in bots_dir.iterdir():
            if f.suffix == ".json" and f.stem != "_template":
                discovered.add(f.stem)

    all_ids = set(meta_bots.keys()) | discovered
    now = time.time()
    result = []
    for bid in sorted(all_ids):
        entry = meta_bots.get(bid, {
            "bot_id": bid,
            "version": "",
            "last_heartbeat_ts": "",
        })
        last_hb = entry.get("last_heartbeat_ts", "")
        online = False
        if last_hb:
            try:
                online = (now - float(last_hb)) <= 15
            except (ValueError, TypeError):
                pass
        entry["online"] = online
        result.append(entry)
    return result


_overview_logger = logging.getLogger("dashboard.overview")


def _find_persona_db(bot_id: str) -> Optional[Path]:
    """Scan bot data dir for personas_data_*.db, return the newest by mtime."""
    matches = [
        match.path
        for match in PERSONA_DB_ASSET.iter_matches(
            DashboardPaths.instance_layout(),
            bot_id=bot_id,
        )
    ]
    if not matches:
        return None
    if len(matches) > 1:
        newest = max(matches, key=lambda p: p.stat().st_mtime)
        _overview_logger.warning(
            "Multiple persona DBs found for %s, using newest: %s",
            bot_id, newest.name,
        )
        return newest
    return matches[0]


def _compute_core_stats(bot_id: str) -> dict:
    """Query bot_data.db for simple row counts (user_stat / group_stat)."""
    db_path = DashboardPaths.bot_data_db_path(bot_id)
    if not db_path.exists():
        return {"users": 0, "groups": 0}

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        users = conn.execute(
            "SELECT COUNT(*) as cnt FROM user_stat"
        ).fetchone()["cnt"]
        groups = conn.execute(
            "SELECT COUNT(*) as cnt FROM group_stat"
        ).fetchone()["cnt"]
    except sqlite3.Error:
        _overview_logger.warning(
            "Core stats query failed for %s", db_path, exc_info=True,
        )
        return {"users": 0, "groups": 0}
    finally:
        conn.close()
    return {"users": users, "groups": groups}


def _compute_persona_stats(persona_db_path: Path, today: str) -> Optional[dict]:
    """Query persona.db for today's chat stats, character state, and events."""

    conn = sqlite3.connect(f"file:{persona_db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        # Chat stats today
        row = conn.execute(
            "SELECT COUNT(*) as total,"
            " COUNT(DISTINCT user_id) as users,"
            " COUNT(DISTINCT CASE WHEN group_id != '' THEN group_id END) as groups"
            " FROM message_stream WHERE type = 'chat' AND date(created_at) = ?",
            (today,),
        ).fetchone()

        chat_messages = row["total"] or 0
        chat_users = row["users"] or 0
        chat_groups = row["groups"] or 0

        # Character state (stored as JSON in text column)
        character_state = {}
        row = conn.execute(
            "SELECT text FROM persona_character_state WHERE id = 1"
        ).fetchone()
        if row and row["text"]:
            try:
                data = json.loads(row["text"])
                if isinstance(data, dict):
                    for key in ("energy", "mood", "health", "current_intention"):
                        if data.get(key) is not None:
                            character_state[key] = data[key]
            except json.JSONDecodeError:
                # Legacy plain-text format
                character_state["text"] = row["text"][:200]

        # Today's events (most recent 3)
        events = []
        cursor = conn.execute(
            "SELECT id, event_type, description FROM persona_daily_events"
            " WHERE date = ? ORDER BY id DESC LIMIT 3",
            (today,),
        )
        for r in cursor.fetchall():
            events.append({
                "id": r["id"],
                "type": r["event_type"],
                "description": r["description"],
            })

    except sqlite3.Error:
        _overview_logger.warning(
            "Persona stats query failed for %s", persona_db_path, exc_info=True,
        )
        return None
    finally:
        conn.close()

    return {
        "chat_messages": chat_messages,
        "chat_users": chat_users,
        "chat_groups": chat_groups,
        "character_state": character_state,
        "events": events,
    }


def _compute_llm_usage(persona_db_path: Path, today: str) -> Optional[dict]:
    """Query persona_llm_traces for today's usage, aggregated and by-model."""

    conn = sqlite3.connect(f"file:{persona_db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(tokens_in + tokens_out), 0) as total_tokens,"
            " COUNT(*) as requests,"
            " COUNT(CASE WHEN status != 'ok' THEN 1 END) as errors"
            " FROM persona_llm_traces WHERE date(created_at) = ?",
            (today,),
        ).fetchone()

        total_tokens = row["total_tokens"] or 0
        requests = row["requests"] or 0
        errors = row["errors"] or 0

        by_model = []
        cursor = conn.execute(
            "SELECT selected_provider, selected_model,"
            " COUNT(*) as requests,"
            " COALESCE(SUM(tokens_in + tokens_out), 0) as tokens"
            " FROM persona_llm_traces WHERE date(created_at) = ?"
            " GROUP BY selected_provider, selected_model"
            " ORDER BY tokens DESC",
            (today,),
        )
        for r in cursor.fetchall():
            by_model.append({
                "provider": r["selected_provider"] or "",
                "model": r["selected_model"] or "",
                "requests": r["requests"],
                "tokens": r["tokens"],
            })

    except sqlite3.Error:
        _overview_logger.warning(
            "LLM usage query failed for %s", persona_db_path, exc_info=True,
        )
        return None
    finally:
        conn.close()

    return {
        "total_tokens": total_tokens,
        "requests": requests,
        "errors": errors,
        "by_model": by_model,
    }


@app.get("/api/overview", dependencies=[Depends(require_auth)])
async def overview(request: Request, bot_id: Optional[str] = Query(None)):
    """Aggregate overview: bot status, core stats, persona stats, LLM usage."""
    result = {
        "bots": _compute_bot_statuses(request.app.state.dashboard_db),
    }

    if bot_id:
        _validate_identifier(bot_id, "bot_id")
        result["core_stats"] = _compute_core_stats(bot_id)

        persona_db = _find_persona_db(bot_id)
        if persona_db:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            result["persona_stats"] = _compute_persona_stats(persona_db, today)
            result["llm_usage"] = _compute_llm_usage(persona_db, today)

    return _ok(result)


@app.get("/api/bots/status", dependencies=[Depends(require_auth)])
async def bot_status(request: Request):
    """Return bot status: union of bots_meta and discovered bots."""
    return _ok({"bots": _compute_bot_statuses(request.app.state.dashboard_db)})


# ── Manager API ───────────────────────────────────────────────────────────────


def _get_manager_service(request: Request) -> ManagerService:
    """Return the Dashboard-local ManagerService for the active test/app DB."""
    db_path = request.app.state.dashboard_db
    service = getattr(request.app.state, "manager_service", None)
    if service is None or getattr(request.app.state, "manager_db_path", None) != db_path:
        service = ManagerService(
            bot_status_provider=lambda: _compute_bot_statuses(request.app.state.dashboard_db),
            db_path=db_path,
        )
        request.app.state.manager_service = service
        request.app.state.manager_db_path = db_path
    return service


def _audit_manager_operation(request: Request, operation: dict, status_code: int) -> None:
    audit_detail = {
        "operation_id": operation["operation_id"],
        "status": operation["status"],
        "message": operation.get("message", ""),
        "status_code": status_code,
    }
    detail = json.dumps(audit_detail, ensure_ascii=False)
    audit_log(
        request.app.state.dashboard_db,
        f"manager.{operation['action']}",
        operation["bot_id"],
        detail,
        ip=request.client.host if request.client else "",
    )

@app.get("/api/manager/status", dependencies=[Depends(require_auth)])
async def manager_status(request: Request):
    """Return discovered bots with Manager and runtime state."""
    service = _get_manager_service(request)
    return _ok(await service.status())


@app.get("/api/manager/operations", dependencies=[Depends(require_auth)])
async def manager_operations(request: Request, limit: int = Query(50, ge=1, le=200)):
    """Return recent Manager operations, newest first."""
    service = _get_manager_service(request)
    return _ok({"operations": service.list_operations(limit)})


@app.get("/api/manager/logs", dependencies=[Depends(require_auth)])
async def manager_runtime_logs(
    request: Request,
    lines: int = Query(200, ge=1, le=1000),
):
    """Return global runtime logs when the configured runtime supports it."""
    service = _get_manager_service(request)
    try:
        logs = await service.runtime_logs(lines)
    except RuntimeOperationUnsupported as exc:
        _err(str(exc) or "Manager runtime logs unsupported", 501)
    except Exception as exc:
        message = str(exc) or type(exc).__name__
        _err(f"Manager logs failed: {message}", 500)
    return _ok({"logs": logs})


@app.get("/api/manager/bots/{bot_id}/logs", dependencies=[Depends(require_auth)])
async def manager_bot_logs(
    bot_id: str,
    request: Request,
    lines: int = Query(200, ge=1, le=1000),
):
    """Return diagnostic logs for a bot when the configured runtime supports it."""
    _validate_identifier(bot_id, "bot_id")
    service = _get_manager_service(request)
    try:
        logs = await service.logs(bot_id, lines)
    except UnknownBot as exc:
        _err(str(exc), 404)
    except RuntimeOperationUnsupported as exc:
        _err(str(exc) or "Manager runtime logs unsupported", 501)
    except Exception as exc:
        message = str(exc) or type(exc).__name__
        _err(f"Manager logs failed: {message}", 500)
    return _ok({"logs": logs})


@app.post("/api/manager/bots/{bot_id}/{action}", dependencies=[Depends(require_auth)])
async def manager_bot_action(bot_id: str, action: str, request: Request):
    """Run a Dashboard-local Manager lifecycle action for a bot."""
    _validate_identifier(bot_id, "bot_id")
    if action not in VALID_ACTIONS:
        _err(
            "Invalid manager action. Allowed: start, stop, restart",
            400,
        )

    service = _get_manager_service(request)
    try:
        operation = await service.operate(
            bot_id,
            cast(ManagerAction, action),
        )
    except UnknownBot as exc:
        audit_log(
            request.app.state.dashboard_db,
            "manager.operation",
            bot_id,
            json.dumps({"status": "rejected", "message": str(exc), "action": action}, ensure_ascii=False),
            ip=request.client.host if request.client else "",
        )
        _err(str(exc), 404)
    except OperationConflict as exc:
        operation_data = exc.operation.to_dict()
        _audit_manager_operation(request, operation_data, 409)
        return JSONResponse(
            status_code=409,
            content={
                "ok": False,
                "message": operation_data["message"],
                "operation": operation_data,
            },
        )
    except OperationFailed as exc:
        operation_data = exc.operation.to_dict()
        _audit_manager_operation(request, operation_data, exc.status_code)
        _err(f"Manager operation failed: {operation_data['message']}", exc.status_code)
    except Exception as exc:
        message = str(exc) or type(exc).__name__
        audit_log(
            request.app.state.dashboard_db,
            f"manager.{action}",
            bot_id,
            json.dumps({"status": "failed", "message": message}, ensure_ascii=False),
            ip=request.client.host if request.client else "",
        )
        _err(f"Manager operation failed: {message}", 500)

    operation_data = operation.to_dict()
    _audit_manager_operation(request, operation_data, 200)
    content = {"operation": operation_data}
    return _ok(content)


# ── SSE endpoint ────────────────────────────────────────────────────────────────


@app.get("/api/events", dependencies=[Depends(require_auth)])
async def events_stream(request: Request):
    """SSE endpoint: pushes bot status updates to connected dashboard clients."""
    queue: asyncio.Queue = asyncio.Queue()
    subscribers: list = request.app.state.status_subscribers
    subscribers.append(queue)

    async def _generate():
        try:
            db_path = request.app.state.dashboard_db
            try:
                bots = _compute_bot_statuses(db_path)
            except Exception:
                bots = []
            yield f"data: {json.dumps({'bots': bots})}\n\n"
            while True:
                payload = await queue.get()
                yield f"data: {payload}\n\n"
        except (asyncio.CancelledError, GeneratorExit):
            pass
        finally:
            try:
                subscribers.remove(queue)
            except ValueError:
                pass

    return StreamingResponse(_generate(), media_type="text/event-stream")


# ── Content management ────────────────────────────────────────────────────────


@app.get("/api/content/{subdir}", dependencies=[Depends(require_auth)])
async def content_list(subdir: str, request: Request):
    """List files in a content subdirectory."""
    if subdir not in _CONTENT_SUBDIRS:
        _err(f"Invalid subdirectory: {subdir}. Allowed: {', '.join(sorted(_CONTENT_SUBDIRS))}", 404)

    content_dir = DashboardPaths.CONTENT_DIR / subdir
    if not content_dir.exists():
        return _ok({"files": []})

    files = []
    for f in sorted(content_dir.iterdir()):
        if f.name.startswith('.'):
            continue
        if f.is_file():
            stat = f.stat()
            files.append({
                "name": f.name,
                "size": stat.st_size,
                "modified": stat.st_mtime,
            })

    return _ok({"files": files})


@app.get("/api/content/queries/{db_name}/tables", dependencies=[Depends(require_auth)])
async def content_queries_tables(db_name: str, request: Request):
    """List tables in a query database."""
    if not db_name or len(db_name) > 128 or db_name.endswith(".db"):
        _err("db_name 无效", 400)
    if _is_path_traversal(db_name, DashboardPaths.CONTENT_DIR / "queries"):
        _err("Path traversal detected", 400)

    db_path = DashboardPaths.CONTENT_DIR / "queries" / f"{db_name}.db"
    if not db_path.exists():
        _err(f"Query database not found: {db_name}", 404)

    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            table_names = [
                row[0] for row in
                conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
            ]
        tables = [{"name": t, "label": TABLE_LABELS.get(t, t)} for t in table_names]
        return _ok({"tables": tables})
    except sqlite3.OperationalError as e:
        _err(f"Database error: {e}", 500)


@app.get("/api/content/queries/{db_name}/entries", dependencies=[Depends(require_auth)])
async def content_queries_entries(
    db_name: str,
    request: Request,
    table: str = Query("data"),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
):
    """Paginated entries from content/queries/{db_name}.db."""
    if not db_name or len(db_name) > 128 or db_name.endswith(".db"):
        _err("db_name 无效", 400)
    if _is_path_traversal(db_name, DashboardPaths.CONTENT_DIR / "queries"):
        _err("Path traversal detected", 400)

    db_path = DashboardPaths.CONTENT_DIR / "queries" / f"{db_name}.db"
    if not db_path.exists():
        _err(f"Query database not found: {db_name}", 404)

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row

        # Validate table exists
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        )
        if not cursor.fetchone():
            conn.close()
            _err(f"Table '{table}' not found", 404)

        safe_table = table.replace('"', '""')
        count_cursor = conn.execute(f'SELECT COUNT(*) FROM "{safe_table}"')
        total = count_cursor.fetchone()[0]

        cursor = conn.execute(
            f'SELECT rowid, * FROM "{safe_table}" ORDER BY rowid LIMIT ? OFFSET ?',
            (limit, offset),
        )
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        records = [dict(row) for row in cursor.fetchall()]
        conn.close()

        return _ok({
            "columns": columns,
            "records": records,
            "total": total,
            "offset": offset,
            "limit": limit,
        })
    except sqlite3.OperationalError as e:
        _err(f"Database error: {e}", 500)


@app.get("/api/content/{subdir}/{name:path}", dependencies=[Depends(require_auth)])
async def content_read(subdir: str, name: str, request: Request):
    """Read file content from a content subdirectory."""
    if subdir not in _CONTENT_SUBDIRS:
        _err(f"Invalid subdirectory: {subdir}", 404)

    if _is_path_traversal(name, DashboardPaths.CONTENT_DIR / subdir):
        _err("Path traversal detected", 400)

    file_path = DashboardPaths.CONTENT_DIR / subdir / name

    if not file_path.exists() or not file_path.is_file():
        _err("File not found", 404)

    if _is_xlsx(file_path):
        stat = file_path.stat()
        return _ok({
            "name": file_path.name,
            "size": stat.st_size,
            "modified": stat.st_mtime,
            "type": "xlsx",
        })

    stat = file_path.stat()
    max_size = 10 * 1024 * 1024  # 10 MB
    if stat.st_size > max_size:
        _err(f"File too large ({stat.st_size} bytes), max {max_size} bytes", 413)

    try:
        content = file_path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        _err("Cannot read file as text", 400)

    return _ok({"name": file_path.name, "content": content})


# ── Audit ─────────────────────────────────────────────────────────────────────


@app.get("/api/audit", dependencies=[Depends(require_auth)])
async def audit_list(request: Request, limit: int = Query(200, ge=1, le=1000)):
    """Return recent audit entries, ordered by id DESC."""
    db_path = request.app.state.dashboard_db
    entries = audit_get_recent(db_path, limit)
    return _ok({"entries": entries})


# ── Static files ──────────────────────────────────────────────────────────────


# Mount static files (no auth)
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/dashboard")
async def dashboard_page():
    """Serve dashboard.html from static directory."""
    static_file = static_dir / "dashboard.html"
    if static_file.exists():
        return FileResponse(str(static_file))
    return JSONResponse(_ok({"message": "Dashboard UI not found"}))


@app.websocket("/ws/control")
async def ws_control(ws: WebSocket):
    """WebSocket endpoint for Bot Control Channel."""
    from .websocket import control_endpoint
    await control_endpoint(ws)


@app.get("/")
async def root_redirect():
    """Redirect / to /dashboard."""
    return RedirectResponse(url="/dashboard")
