"""DicePP WebUI 后台主应用：汇总所有 API 路由 + 服务静态 HTML。"""
import asyncio
import atexit
from typing import Any, Dict, Optional

from fastapi import Body, Cookie, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from dicepp_admin import audit, auth, data_api, instance_manager, llonebot_manager, log_api, query_db_api, ui
from dicepp_admin.config import AdminPaths, DEFAULT_USERNAME

app = FastAPI(title="DicePP Admin", version="0.1.0", docs_url="/admin/docs", openapi_url="/admin/openapi.json")


@app.on_event("startup")
async def _startup() -> None:
    AdminPaths.ensure_dirs()


@app.on_event("shutdown")
async def _shutdown() -> None:
    instance_manager.stop_all()


atexit.register(instance_manager.stop_all)


# ─── 根路由：跳转到 /admin ───────────────────────────────────────────────

@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/admin")


@app.get("/admin", include_in_schema=False, response_class=HTMLResponse)
def admin_ui() -> HTMLResponse:
    return HTMLResponse(content=ui.render())


# ─── Auth 路由 ───────────────────────────────────────────────────────────

class _SetupBody(BaseModel):
    password: str
    username: Optional[str] = None


class _LoginBody(BaseModel):
    username: Optional[str] = None
    password: str


class _ChangePwBody(BaseModel):
    old_password: str
    new_password: str


@app.get("/api/auth/status")
def auth_status(request: Request) -> Dict:
    token = request.cookies.get(auth.get_cookie_name(), "")
    session = auth.get_session(token) if token else None
    return {
        "initialized": auth.is_initialized(),
        "logged_in": session is not None,
        "username": session["username"] if session else None,
    }


@app.post("/api/auth/setup")
def auth_setup(body: _SetupBody, response: Response) -> Dict:
    if auth.is_initialized():
        raise HTTPException(status_code=400, detail={"message": "已初始化，请用「修改密码」"})
    username = (body.username or DEFAULT_USERNAME).strip() or DEFAULT_USERNAME
    auth.set_password(body.password, username)
    token = auth.create_session(username)
    response.set_cookie(
        key=auth.get_cookie_name(), value=token,
        httponly=True, samesite="lax", max_age=7 * 24 * 3600,
    )
    audit.log(username, "auth.setup")
    return {"ok": True, "username": username}


@app.post("/api/auth/login")
def auth_login(body: _LoginBody, request: Request, response: Response) -> Dict:
    if not auth.is_initialized():
        raise HTTPException(status_code=400, detail={"message": "尚未初始化"})
    username = (body.username or DEFAULT_USERNAME).strip() or DEFAULT_USERNAME
    if not auth.verify_password(username, body.password):
        audit.log(username, "auth.login_failed", ip=_client_ip(request))
        raise HTTPException(status_code=401, detail={"message": "用户名或密码错误"})
    token = auth.create_session(username)
    response.set_cookie(
        key=auth.get_cookie_name(), value=token,
        httponly=True, samesite="lax", max_age=7 * 24 * 3600,
    )
    audit.log(username, "auth.login", ip=_client_ip(request))
    return {"ok": True, "username": username}


@app.post("/api/auth/logout")
def auth_logout(request: Request, response: Response) -> Dict:
    token = request.cookies.get(auth.get_cookie_name(), "")
    if token:
        auth.revoke_session(token)
    response.delete_cookie(auth.get_cookie_name())
    return {"ok": True}


@app.post("/api/auth/change_password")
def auth_change_password(body: _ChangePwBody, session: Dict = Depends(auth.require_auth)) -> Dict:
    username = session["username"]
    if not auth.verify_password(username, body.old_password):
        raise HTTPException(status_code=401, detail={"message": "旧密码错误"})
    auth.set_password(body.new_password, username)
    audit.log(username, "auth.change_password")
    return {"ok": True}


# ─── 实例管理 ────────────────────────────────────────────────────────────

class _InstanceCreateBody(BaseModel):
    name: str
    qq_id: Optional[str] = None
    master_qq: Optional[str] = None


class _InstancePatchBody(BaseModel):
    name: Optional[str] = None
    qq_id: Optional[str] = None
    master_qq: Optional[str] = None
    auto_start: Optional[bool] = None
    access_token: Optional[str] = None


@app.get("/api/instances")
def instances_list(session: Dict = Depends(auth.require_auth)) -> Dict:
    return {"instances": instance_manager.list_instances()}


@app.post("/api/instances")
def instances_create(body: _InstanceCreateBody, request: Request,
                     session: Dict = Depends(auth.require_auth)) -> Dict:
    try:
        inst = instance_manager.create_instance(body.name, body.qq_id, body.master_qq)
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"message": str(e)})
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail={"message": str(e)})
    audit.log(session["username"], "instance.create", target=inst["id"],
              detail=inst["name"], ip=_client_ip(request))
    return inst


@app.get("/api/instances/{instance_id}")
def instances_get(instance_id: str, session: Dict = Depends(auth.require_auth)) -> Dict:
    inst = instance_manager.get_instance(instance_id)
    if not inst:
        raise HTTPException(status_code=404, detail={"message": "实例不存在"})
    return inst


@app.patch("/api/instances/{instance_id}")
def instances_patch(instance_id: str, body: _InstancePatchBody, request: Request,
                    session: Dict = Depends(auth.require_auth)) -> Dict:
    patch = {k: v for k, v in body.dict().items() if v is not None}
    try:
        inst = instance_manager.update_instance(instance_id, patch)
    except KeyError:
        raise HTTPException(status_code=404, detail={"message": "实例不存在"})
    audit.log(session["username"], "instance.update", target=instance_id,
              detail=str(patch), ip=_client_ip(request))
    return inst


@app.delete("/api/instances/{instance_id}")
def instances_delete(instance_id: str, remove_data: bool = Query(False),
                     request: Request = None,  # type: ignore[assignment]
                     session: Dict = Depends(auth.require_auth)) -> Dict:
    instance_manager.delete_instance(instance_id, remove_data=remove_data)
    audit.log(session["username"], "instance.delete", target=instance_id,
              detail=f"remove_data={remove_data}",
              ip=_client_ip(request) if request else None)
    return {"ok": True}


@app.post("/api/instances/{instance_id}/start")
def instances_start(instance_id: str, request: Request,
                    session: Dict = Depends(auth.require_auth)) -> Dict:
    try:
        inst = instance_manager.start_instance(instance_id)
    except KeyError:
        raise HTTPException(status_code=404, detail={"message": "实例不存在"})
    except Exception as e:
        raise HTTPException(status_code=500, detail={"message": str(e)})
    audit.log(session["username"], "instance.start", target=instance_id, ip=_client_ip(request))
    return inst


@app.post("/api/instances/{instance_id}/stop")
def instances_stop(instance_id: str, request: Request,
                   session: Dict = Depends(auth.require_auth)) -> Dict:
    instance_manager.stop_instance(instance_id)
    audit.log(session["username"], "instance.stop", target=instance_id, ip=_client_ip(request))
    return {"ok": True}


@app.get("/api/instances/{instance_id}/log")
def instances_log(instance_id: str, tail: int = Query(500, ge=1, le=10000),
                  session: Dict = Depends(auth.require_auth)) -> Dict:
    return {"log": instance_manager.read_runtime_log(instance_id, tail=tail)}


@app.get("/api/instances/{instance_id}/bots")
def instances_bots(instance_id: str, session: Dict = Depends(auth.require_auth)) -> Dict:
    return {"bots": log_api.list_bots(instance_id)}


# ─── LLOneBot ────────────────────────────────────────────────────────────

class _LlbotConfigBody(BaseModel):
    llbot_path: str


class _LlbotSyncBody(BaseModel):
    instance_id: str


@app.get("/api/llonebot/status")
def llonebot_status(session: Dict = Depends(auth.require_auth)) -> Dict:
    return llonebot_manager.status_snapshot()


@app.post("/api/llonebot/config")
def llonebot_config(body: _LlbotConfigBody, request: Request,
                    session: Dict = Depends(auth.require_auth)) -> Dict:
    cfg = llonebot_manager.set_llbot_path(body.llbot_path)
    audit.log(session["username"], "llonebot.config", detail=body.llbot_path, ip=_client_ip(request))
    return cfg


@app.post("/api/llonebot/auto_acquire")
def llonebot_auto_acquire(request: Request,
                          session: Dict = Depends(auth.require_auth)) -> Dict:
    result = llonebot_manager.auto_acquire()
    audit.log(session["username"], "llonebot.auto_acquire",
              detail=result.get("status"), ip=_client_ip(request))
    # 自动获取成功后，把所有已绑定 QQ 的实例都同步一遍配置
    if result.get("status") in ("acquired", "already_acquired"):
        for inst in instance_manager.list_instances():
            if inst.get("qq_id"):
                try:
                    llonebot_manager.generate_config(
                        inst["qq_id"], inst["port"], inst.get("access_token", "")
                    )
                except Exception:
                    pass
    return result


@app.post("/api/llonebot/start")
def llonebot_start(request: Request,
                   session: Dict = Depends(auth.require_auth)) -> Dict:
    result = llonebot_manager.start()
    audit.log(session["username"], "llonebot.start", ip=_client_ip(request))
    return result


@app.post("/api/llonebot/stop")
def llonebot_stop(request: Request,
                  session: Dict = Depends(auth.require_auth)) -> Dict:
    result = llonebot_manager.stop()
    audit.log(session["username"], "llonebot.stop", ip=_client_ip(request))
    return result


@app.post("/api/llonebot/sync_config")
def llonebot_sync_config(body: _LlbotSyncBody, request: Request,
                         session: Dict = Depends(auth.require_auth)) -> Dict:
    """根据实例当前 qq_id + port 重写 LLOneBot 反向 WS 配置。"""
    inst = instance_manager.get_instance(body.instance_id)
    if not inst:
        raise HTTPException(status_code=404, detail={"message": "实例不存在"})
    if not inst.get("qq_id"):
        return {"status": "no_qq", "message": "请先在实例设置里填 QQ 号"}
    if not llonebot_manager.is_acquired():
        return {"status": "no_bundle", "message": "请先获取 LLOneBot 整合包"}
    result = llonebot_manager.generate_config(
        inst["qq_id"], inst["port"], inst.get("access_token", "")
    )
    audit.log(session["username"], "llonebot.sync_config",
              target=body.instance_id, ip=_client_ip(request))
    return result


# ─── Data ────────────────────────────────────────────────────────────────

@app.get("/api/data/{instance_id}/{bot_id}/tables")
def data_tables(instance_id: str, bot_id: str,
                session: Dict = Depends(auth.require_auth)) -> Dict:
    return {"tables": data_api.list_tables(instance_id, bot_id)}


@app.get("/api/data/{instance_id}/{bot_id}/table/{table}")
def data_table(instance_id: str, bot_id: str, table: str,
               offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=1000),
               q: Optional[str] = Query(None),
               session: Dict = Depends(auth.require_auth)) -> Dict:
    return data_api.list_records(instance_id, bot_id, table, offset, limit, q)


class _RecordKeysBody(BaseModel):
    keys: Dict[str, str]
    data: Optional[Any] = None


@app.put("/api/data/{instance_id}/{bot_id}/table/{table}/record")
def data_record_update(instance_id: str, bot_id: str, table: str, body: _RecordKeysBody,
                       request: Request, session: Dict = Depends(auth.require_auth)) -> Dict:
    ok = data_api.update_record_data(instance_id, bot_id, table, body.keys, body.data)
    audit.log(session["username"], "data.update",
              target=f"{instance_id}/{bot_id}/{table}", detail=str(body.keys),
              ip=_client_ip(request))
    return {"ok": ok}


@app.delete("/api/data/{instance_id}/{bot_id}/table/{table}/record")
def data_record_delete(instance_id: str, bot_id: str, table: str, body: _RecordKeysBody,
                       request: Request, session: Dict = Depends(auth.require_auth)) -> Dict:
    n = data_api.delete_record(instance_id, bot_id, table, body.keys)
    audit.log(session["username"], "data.delete",
              target=f"{instance_id}/{bot_id}/{table}", detail=str(body.keys),
              ip=_client_ip(request))
    return {"deleted": n}


@app.get("/api/data/{instance_id}/{bot_id}/group_configs")
def data_group_configs(instance_id: str, bot_id: str,
                       session: Dict = Depends(auth.require_auth)) -> Dict:
    return data_api.list_group_configs(instance_id, bot_id)


@app.get("/api/data/{instance_id}/{bot_id}/group_activate")
def data_group_activate(instance_id: str, bot_id: str,
                        session: Dict = Depends(auth.require_auth)) -> Dict:
    return data_api.list_group_activate(instance_id, bot_id)


@app.get("/api/data/{instance_id}/{bot_id}/group_welcome")
def data_group_welcome(instance_id: str, bot_id: str,
                       session: Dict = Depends(auth.require_auth)) -> Dict:
    return data_api.list_group_welcome(instance_id, bot_id)


@app.get("/api/data/{instance_id}/{bot_id}/nicknames")
def data_nicknames(instance_id: str, bot_id: str,
                   session: Dict = Depends(auth.require_auth)) -> Dict:
    return data_api.list_user_nickname(instance_id, bot_id)


@app.get("/api/data/{instance_id}/{bot_id}/characters")
def data_characters(instance_id: str, bot_id: str,
                    session: Dict = Depends(auth.require_auth)) -> Dict:
    return data_api.list_dnd_characters(instance_id, bot_id)


# ─── 牌堆 / 随机 ─────────────────────────────────────────────────────────

@app.get("/api/data/{instance_id}/decks")
def data_decks(instance_id: str, session: Dict = Depends(auth.require_auth)) -> Dict:
    return {"files": data_api.list_deck_files(instance_id)}


@app.get("/api/data/{instance_id}/decks/{name}")
def data_deck_read(instance_id: str, name: str, session: Dict = Depends(auth.require_auth)) -> Dict:
    content = data_api.read_deck_file(instance_id, name)
    if content is None:
        raise HTTPException(status_code=404, detail={"message": "牌堆文件不存在"})
    return {"name": name, "content": content}


class _FileWriteBody(BaseModel):
    content: str


@app.put("/api/data/{instance_id}/decks/{name}")
def data_deck_write(instance_id: str, name: str, body: _FileWriteBody, request: Request,
                    session: Dict = Depends(auth.require_auth)) -> Dict:
    ok = data_api.write_deck_file(instance_id, name, body.content)
    audit.log(session["username"], "deck.update", target=f"{instance_id}/{name}",
              ip=_client_ip(request))
    return {"ok": ok}


@app.delete("/api/data/{instance_id}/decks/{name}")
def data_deck_delete(instance_id: str, name: str, request: Request,
                     session: Dict = Depends(auth.require_auth)) -> Dict:
    ok = data_api.delete_deck_file(instance_id, name)
    audit.log(session["username"], "deck.delete", target=f"{instance_id}/{name}",
              ip=_client_ip(request))
    return {"ok": ok}


@app.get("/api/data/{instance_id}/random")
def data_random(instance_id: str, session: Dict = Depends(auth.require_auth)) -> Dict:
    return {"files": data_api.list_random_files(instance_id)}


@app.get("/api/data/{instance_id}/random/{name}")
def data_random_read(instance_id: str, name: str, session: Dict = Depends(auth.require_auth)) -> Dict:
    content = data_api.read_random_file(instance_id, name)
    if content is None:
        raise HTTPException(status_code=404, detail={"message": "随机文件不存在"})
    return {"name": name, "content": content}


@app.put("/api/data/{instance_id}/random/{name}")
def data_random_write(instance_id: str, name: str, body: _FileWriteBody, request: Request,
                      session: Dict = Depends(auth.require_auth)) -> Dict:
    ok = data_api.write_random_file(instance_id, name, body.content)
    audit.log(session["username"], "random.update", target=f"{instance_id}/{name}",
              ip=_client_ip(request))
    return {"ok": ok}


# ─── Logs ────────────────────────────────────────────────────────────────

@app.get("/api/logs/{instance_id}/{bot_id}/sessions")
def logs_sessions(instance_id: str, bot_id: str,
                  group_id: Optional[str] = Query(None),
                  limit: int = Query(200, ge=1, le=1000),
                  session: Dict = Depends(auth.require_auth)) -> Dict:
    return {"sessions": log_api.list_log_sessions(instance_id, bot_id, group_id, limit)}


@app.get("/api/logs/{instance_id}/{bot_id}/sessions/{log_id}/records")
def logs_records(instance_id: str, bot_id: str, log_id: str,
                 offset: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=1000),
                 user_id: Optional[str] = Query(None),
                 keyword: Optional[str] = Query(None),
                 session: Dict = Depends(auth.require_auth)) -> Dict:
    return log_api.list_log_records(instance_id, bot_id, log_id, offset, limit, user_id, keyword)


@app.get("/api/logs/{instance_id}/{bot_id}/sessions/{log_id}/export")
def logs_export(instance_id: str, bot_id: str, log_id: str,
                fmt: str = Query("txt", regex="^(txt|md)$"),
                session: Dict = Depends(auth.require_auth)) -> Response:
    content = log_api.export_log_session(instance_id, bot_id, log_id, fmt)
    if content is None:
        raise HTTPException(status_code=404, detail={"message": "日志会话不存在"})
    media = "text/markdown" if fmt == "md" else "text/plain"
    headers = {"Content-Disposition": f'attachment; filename="log_{log_id}.{fmt}"'}
    return Response(content=content, media_type=media, headers=headers)


@app.delete("/api/logs/{instance_id}/{bot_id}/sessions/{log_id}")
def logs_session_delete(instance_id: str, bot_id: str, log_id: str, request: Request,
                        session: Dict = Depends(auth.require_auth)) -> Dict:
    n = log_api.delete_log_session(instance_id, bot_id, log_id)
    audit.log(session["username"], "log.delete_session",
              target=f"{instance_id}/{bot_id}/{log_id}", ip=_client_ip(request))
    return {"deleted": n}


@app.delete("/api/logs/{instance_id}/{bot_id}/records/{record_id}")
def logs_record_delete(instance_id: str, bot_id: str, record_id: int, request: Request,
                       session: Dict = Depends(auth.require_auth)) -> Dict:
    n = log_api.delete_log_record(instance_id, bot_id, record_id)
    audit.log(session["username"], "log.delete_record",
              target=f"{instance_id}/{bot_id}/{record_id}", ip=_client_ip(request))
    return {"deleted": n}


@app.get("/api/logs/{instance_id}/{bot_id}/chat")
def logs_chat(instance_id: str, bot_id: str,
              group_id: Optional[str] = Query(None),
              user_id: Optional[str] = Query(None),
              keyword: Optional[str] = Query(None),
              limit: int = Query(100, ge=1, le=1000),
              session: Dict = Depends(auth.require_auth)) -> Dict:
    return {
        "records": log_api.search_chat_records(
            instance_id, bot_id, group_id, user_id, keyword, limit
        )
    }


# ─── Query Database ──────────────────────────────────────────────────────

class _QueryEntryBody(BaseModel):
    rowid: Optional[int] = None
    values: Dict[str, str]


class _QueryDbCreateBody(BaseModel):
    name: str


@app.get("/api/query_db")
def query_db_list(session: Dict = Depends(auth.require_auth)) -> Dict:
    return {"databases": query_db_api.list_databases()}


@app.post("/api/query_db")
def query_db_create(body: _QueryDbCreateBody, request: Request,
                    session: Dict = Depends(auth.require_auth)) -> Dict:
    result = query_db_api.create_database(body.name)
    audit.log(session["username"], "query_db.create", target=body.name,
              ip=_client_ip(request))
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail={"message": result.get("message")})
    return result


@app.delete("/api/query_db/{name}")
def query_db_delete(name: str, request: Request,
                    session: Dict = Depends(auth.require_auth)) -> Dict:
    result = query_db_api.delete_database(name)
    audit.log(session["username"], "query_db.delete", target=name, ip=_client_ip(request))
    return result


@app.get("/api/query_db/{name}/entries")
def query_db_entries(name: str,
                     table: str = Query("data", regex="^(data|redirect)$"),
                     offset: int = Query(0, ge=0),
                     limit: int = Query(100, ge=1, le=1000),
                     keyword: Optional[str] = Query(None),
                     catalogue: Optional[str] = Query(None),
                     source: Optional[str] = Query(None),
                     session: Dict = Depends(auth.require_auth)) -> Dict:
    return query_db_api.list_entries(name, table, offset, limit, keyword, catalogue, source)


@app.put("/api/query_db/{name}/entries")
def query_db_entry_upsert(name: str, body: _QueryEntryBody, request: Request,
                          table: str = Query("data", regex="^(data|redirect)$"),
                          session: Dict = Depends(auth.require_auth)) -> Dict:
    result = query_db_api.upsert_entry(name, table, body.rowid, body.values)
    audit.log(session["username"],
              "query_db.upsert" if body.rowid else "query_db.insert",
              target=f"{name}/{table}/{body.rowid or ''}", ip=_client_ip(request))
    return result


@app.delete("/api/query_db/{name}/entries/{rowid}")
def query_db_entry_delete(name: str, rowid: int, request: Request,
                          table: str = Query("data", regex="^(data|redirect)$"),
                          session: Dict = Depends(auth.require_auth)) -> Dict:
    result = query_db_api.delete_entry(name, table, rowid)
    audit.log(session["username"], "query_db.delete_entry",
              target=f"{name}/{table}/{rowid}", ip=_client_ip(request))
    return result


@app.get("/api/query_db/{name}/distinct/{field}")
def query_db_distinct(name: str, field: str,
                      table: str = Query("data", regex="^(data|redirect)$"),
                      session: Dict = Depends(auth.require_auth)) -> Dict:
    return {"values": query_db_api.get_distinct_values(name, field, table)}


# ─── Audit ───────────────────────────────────────────────────────────────

@app.get("/api/audit")
def audit_list(limit: int = Query(200, ge=1, le=1000),
               session: Dict = Depends(auth.require_auth)) -> Dict:
    return {"logs": audit.list_recent(limit)}


# ─── 全局错误处理 ────────────────────────────────────────────────────────

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content={"ok": False, **detail})


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={"ok": False, "message": f"内部错误：{exc}"},
    )


# ─── 工具 ────────────────────────────────────────────────────────────────

def _client_ip(request: Optional[Request]) -> Optional[str]:
    if request is None or not request.client:
        return None
    return request.client.host
