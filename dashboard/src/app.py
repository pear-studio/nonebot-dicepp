import asyncio
import ipaddress
import json
import os
import re
import sqlite3
import sys
import time
import uuid
from pathlib import Path
from typing import Optional

import logging

from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

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
from .config import DashboardPaths

logger = logging.getLogger("dashboard")

# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(title="DicePP Dashboard", version="1.0.0")

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
_READONLY_CONFIG_NAMES: set[str] = {"global.json", "schema.json"}


def _write_json_atomic(path: Path, data: dict) -> None:
    """Atomically write a dict to a JSON file using .tmp + os.replace.

    Refuses to write to protected files (global.json, schema.json) which
    are git-managed and should only be changed via code review.
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


# ── Startup event ─────────────────────────────────────────────────────────────


@app.on_event("startup")
async def _startup():
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
    })


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


@app.get("/api/data/{bot_id}/tables", dependencies=[Depends(require_auth)])
async def data_tables(bot_id: str, request: Request):
    """Scan sqlite_master from bot_data.db (mode=ro), return [{name, count}]."""
    _validate_identifier(bot_id, "bot_id")
    db_path = DashboardPaths.bot_data_db_path(bot_id)
    if not db_path.exists():
        _err(f"Bot data not found for {bot_id}", 404)

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name != 'schema_version' ORDER BY name"
        )
        tables = [row[0] for row in cursor.fetchall()]

        result = []
        for t in tables:
            count_cursor = conn.execute(f"SELECT COUNT(*) FROM \"{t}\"")
            count = count_cursor.fetchone()[0]
            result.append({"name": t, "count": count})

        conn.close()
        return _ok({"tables": result})
    except sqlite3.OperationalError as e:
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
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row

        # Sanitize table name (prevent injection)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        )
        if not cursor.fetchone():
            conn.close()
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
        conn.close()

        return _ok({
            "records": records,
            "total": total,
            "columns": all_columns,
            "offset": offset,
            "limit": limit,
        })
    except sqlite3.OperationalError as e:
        _err(f"Database error: {e}", 500)


# ── Config editing ────────────────────────────────────────────────────────────


@app.get("/api/config/merged", dependencies=[Depends(require_auth)])
async def config_merged(request: Request, bot_id: Optional[str] = Query(None)):
    """Merge global.json + user.json + bots/{bot_id}.json with source annotation."""
    global_cfg = _read_json_safe(DashboardPaths.CONFIG_GLOBAL)
    user_cfg = _read_json_safe(DashboardPaths.CONFIG_USER)

    # Annotate merged config: global=default, user overlays=user, bot overlays=bot
    result_annotated = {}

    def _annotate_deep(base: dict, overlay: dict, source: str, prefix: str = ""):
        for key, value in base.items():
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
                dotted = f"{prefix}.{key}" if prefix else key
                if isinstance(value, dict):
                    items.update(_flatten_and_annotate(value, dotted))
                else:
                    items[dotted] = value
            return items

        bot_flat = _flatten_and_annotate(bot_cfg)
        for dotted, value in bot_flat.items():
            result_annotated[dotted] = {"value": value, "source": "bot"}

    # Load schema for descriptions
    schema = _read_json_safe(DashboardPaths.CONFIG_SCHEMA) if DashboardPaths.CONFIG_SCHEMA.exists() else {}

    # Build output with descriptions
    output = {}
    for dotted, entry in result_annotated.items():
        output[dotted] = {
            "value": entry["value"],
            "source": entry["source"],
        }
        desc = schema.get(dotted) if isinstance(schema, dict) else None
        if desc:
            output[dotted]["description"] = desc

    return _ok({"config": output})


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


@app.get("/api/bots/status", dependencies=[Depends(require_auth)])
async def bot_status(request: Request):
    """Return bot status: union of bots_meta and discovered bots."""
    return _ok({"bots": _compute_bot_statuses(request.app.state.dashboard_db)})


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
        if f.is_file():
            stat = f.stat()
            files.append({
                "name": f.name,
                "size": stat.st_size,
                "modified": stat.st_mtime,
            })

    return _ok({"files": files})


@app.get("/api/content/queries/{db_name}/entries", dependencies=[Depends(require_auth)])
async def content_queries_entries(
    db_name: str,
    request: Request,
    table: str = Query("data"),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
):
    """Paginated entries from content/queries/{db_name}.db."""
    _validate_identifier(db_name, "db_name")
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

        count_cursor = conn.execute(f"SELECT COUNT(*) FROM \"{table}\"")
        total = count_cursor.fetchone()[0]

        cursor = conn.execute(
            f"SELECT * FROM \"{table}\" ORDER BY rowid LIMIT ? OFFSET ?",
            (limit, offset),
        )
        records = [dict(row) for row in cursor.fetchall()]
        conn.close()

        return _ok({
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
