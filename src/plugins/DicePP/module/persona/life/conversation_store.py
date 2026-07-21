"""ConversationStore — Store 协议的 SQLite 生产实现

使用现有 persona_session + persona_session_message 表。
conv_id 映射到 session_id。

阶段 1：
- persona_session 写入 scope_namespace/scope_key（Conversation 范围，分列存）。
- persona_session_message 读写 message_stream_id/entry_type：可见消息以 ref 引用
  message_stream 权威记录，内部条目（通知/工具/摘要）为 own。
- 新增 append()：增量追加，避免取消裁剪后全量 put 的 O(n²)。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Optional
from weakref import WeakKeyDictionary

from utils.logger import logger
from utils.time import get_clock
from ..data.store import PersonaDataStore
from ..agent.output_protocol import (
    INTERNAL_MESSAGE_TYPE_FIELD,
    get_internal_message_type,
)
from .conversation import ENTRY_TYPE_OWN, ENTRY_TYPE_REF, Snapshot, Store


# ConversationStore 会因缓存重建而产生多个实例；append 的串行边界必须绑定到底层
# SQLite connection，而不是绑定到短生命周期的 Store 实例。每个 session 单独一把锁，
# 避免不相关 Conversation 互相阻塞。连接释放后 WeakKeyDictionary 会自动清理。
@dataclass
class _AppendLockEntry:
    lock: asyncio.Lock
    users: int = 0


_APPEND_LOCKS: WeakKeyDictionary[object, dict[int, _AppendLockEntry]] = WeakKeyDictionary()


class _AppendLockLease:
    """一次 append 的锁租约；最后一个 holder/waiter 离开时回收锁。"""

    def __init__(self, db: object, session_id: int) -> None:
        self._db = db
        self._session_id = session_id
        self._entry: Optional[_AppendLockEntry] = None
        self._acquired = False

    async def __aenter__(self) -> None:
        locks = _APPEND_LOCKS.setdefault(self._db, {})
        entry = locks.get(self._session_id)
        if entry is None:
            entry = _AppendLockEntry(asyncio.Lock())
            locks[self._session_id] = entry
        # 无 await 的计数使即将等待的 caller 也成为租约用户；holder 释放时不会
        # 提前删除仍有 waiter 引用的锁并创建第二把并行锁。
        entry.users += 1
        self._entry = entry
        try:
            await entry.lock.acquire()
            self._acquired = True
        except BaseException:
            self._drop_user(entry)
            raise

    async def __aexit__(self, exc_type, exc, tb) -> None:
        entry = self._entry
        if entry is None:
            return
        if self._acquired:
            entry.lock.release()
            self._acquired = False
        self._drop_user(entry)

    def _drop_user(self, entry: _AppendLockEntry) -> None:
        entry.users -= 1
        locks = _APPEND_LOCKS.get(self._db)
        if entry.users == 0 and locks is not None and locks.get(self._session_id) is entry:
            locks.pop(self._session_id, None)
            if not locks:
                _APPEND_LOCKS.pop(self._db, None)
        self._entry = None


def _append_lock_for(db: object, session_id: int) -> _AppendLockLease:
    return _AppendLockLease(db, session_id)


class ConversationStore(Store):
    """Store 协议 SQLite 实现，通过 PersonaDataStore 操作 persona_session 表。"""

    def __init__(self, store: PersonaDataStore, user_id: str = "",
                 character_id: str = "",
                 scope_namespace: str = "", scope_key: str = "") -> None:
        self._store = store
        self._user_id = user_id
        self._character_id = character_id
        self._scope_namespace = scope_namespace
        self._scope_key = scope_key

    # ── Store 实现 ───────────────────────────────────────

    async def register(self, conv_id: str, cursor_tag: str) -> None:
        """注册 cursor tag。

        当前为 no-op：cursor 在首次 fetch_notifications() 时懒初始化，
        不需要预注册。
        """

    async def put(self, conv_id: str, snapshot: Snapshot) -> str:
        """写入（全量覆盖）。conv_id 为空时创建新 session。

        保留给"新建 session"和未来"摘要重写"；日常增量走 append()。

        Returns:
            写入后的 conv_id（首次创建时返回新分配的 id，更新时返回原 id）。
        """
        db = self._store._persona_db
        sid = _parse_conv_id(conv_id)
        cursors_raw = json.dumps(snapshot.cursors, ensure_ascii=False)
        system = ""  # T3: system_prompt 不持久化

        if sid is not None:
            # 更新已有 session
            if self._scope_namespace.startswith("life."):
                await db.execute(
                    "UPDATE persona_session SET static_prompt=?, cursors_json=?, "
                    "last_active_at=? WHERE session_id=?",
                    (system, cursors_raw, get_clock().now().isoformat(sep=" "), sid),
                )
            else:
                await db.execute(
                    "UPDATE persona_session SET static_prompt=?, cursors_json=?, "
                    "last_active_at=CURRENT_TIMESTAMP WHERE session_id=?",
                    (system, cursors_raw, sid),
                )
            # 删除旧消息后重新写入
            await db.execute(
                "DELETE FROM persona_session_message WHERE session_id=?",
                (sid,),
            )
        else:
            # 创建新 session（写入 scope 列）
            if self._scope_namespace.startswith("life."):
                now = get_clock().now().isoformat(sep=" ")
                cursor = await db.execute(
                    "INSERT INTO persona_session "
                    "(user_id, character_id, static_prompt, cursors_json, "
                    " scope_namespace, scope_key, last_active_at, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (self._user_id, self._character_id, system, cursors_raw,
                     self._scope_namespace, self._scope_key, now, now),
                )
            else:
                cursor = await db.execute(
                    "INSERT INTO persona_session "
                    "(user_id, character_id, static_prompt, cursors_json, "
                    " scope_namespace, scope_key) VALUES (?, ?, ?, ?, ?, ?)",
                    (self._user_id, self._character_id, system, cursors_raw,
                     self._scope_namespace, self._scope_key),
                )
            sid = cursor.lastrowid

        # 写入消息
        for seq, msg in enumerate(snapshot.messages):
            f = _decompose_message(msg)
            await db.execute(
                "INSERT INTO persona_session_message "
                "(session_id, role, content, tool_calls, tool_call_id, name, "
                " provider_context, message_stream_id, entry_type, sequence) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (sid, f["role"], f["content"], f["tool_calls"], f["tool_call_id"],
                 f["name"], f["provider_context"], f["message_stream_id"],
                 f["entry_type"], seq),
            )
        await db.commit()
        return str(sid)

    async def append(self, conv_id: str, messages: list[dict]) -> None:
        """增量追加消息，不触碰已有行。

        sequence 由 INSERT 内的子查询原子计算（MAX(sequence)+1），避免"读 MAX 再
        写入"之间的 await 缝隙被并发 append 插入而撞号 —— hook 的 append_visible
        （持 per-scope 锁）与 orchestrator run() 的 _persist_new（不持锁）可能并发。

        R6: 底层 SQLite connection + session 共享的 asyncio.Lock 确保同一 batch
        的所有消息不被其他 Store 实例的 append 插队 —— assistant(tool_call) 与
        tool(result) 始终保持相邻。
        """
        if not messages:
            return
        db = self._store._persona_db
        sid = _parse_conv_id(conv_id)
        if sid is None:
            logger.warning("ConversationStore.append: 无效 conv_id=%r，跳过", conv_id)
            return
        async with _append_lock_for(db, sid):

            for msg in messages:
                f = _decompose_message(msg)
                await db.execute(
                    "INSERT INTO persona_session_message "
                    "(session_id, role, content, tool_calls, tool_call_id, name, "
                    " provider_context, message_stream_id, entry_type, sequence) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, "
                    " (SELECT COALESCE(MAX(sequence), -1) + 1 "
                    "  FROM persona_session_message WHERE session_id=?))",
                    (sid, f["role"], f["content"], f["tool_calls"], f["tool_call_id"],
                     f["name"], f["provider_context"], f["message_stream_id"],
                     f["entry_type"], sid),
                )
            # 刷新会话活跃时间。加 status='active' 过滤：run() 的 _persist_new 不持 per-scope
            # 锁，可能在并发 close/rotate 把 session 标记 closed 之后才落到这里；此时不应把已
            # 关闭活跃期的 last_active_at 刷新回来（否则静默判定 _is_silence_expired 会误判）。
            if self._scope_namespace.startswith("life."):
                await db.execute(
                    "UPDATE persona_session SET last_active_at=? "
                    "WHERE session_id=? AND status='active'",
                    (get_clock().now().isoformat(sep=" "), sid),
                )
            else:
                await db.execute(
                    "UPDATE persona_session SET last_active_at=CURRENT_TIMESTAMP "
                    "WHERE session_id=? AND status='active'",
                    (sid,),
                )
            await db.commit()

    async def get(self, conv_id: str) -> Snapshot | None:
        """读取指定 conversation 的快照。

        ref 条目还原为 {role, entry_type:'ref', message_stream_id}（不含正文）；
        own 条目还原为 {role, content}。
        """
        db = self._store._persona_db
        sid = _parse_conv_id(conv_id)
        if sid is None:
            return None

        cursor = await db.execute(
            "SELECT static_prompt, cursors_json FROM persona_session "
            "WHERE session_id=? AND status='active'",
            (sid,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None

        cursors_raw = _cell(row, 1, "cursors_json") or "{}"
        try:
            cursors = json.loads(cursors_raw)
        except json.JSONDecodeError:
            cursors = {}

        msg_cursor = await db.execute(
            "SELECT role, content, tool_calls, tool_call_id, name, "
            "provider_context, message_stream_id, entry_type "
            "FROM persona_session_message WHERE session_id=? ORDER BY sequence",
            (sid,),
        )
        msg_rows = await msg_cursor.fetchall()
        messages = [_recompose_message(r) for r in msg_rows]

        return Snapshot(messages=messages, cursors=cursors)

    async def delete(self, conv_id: str) -> None:
        """删除指定 conversation 及其消息（标记 deleted，保留审计）。"""
        db = self._store._persona_db
        sid = _parse_conv_id(conv_id)
        if sid is None:
            return
        await db.execute(
            "UPDATE persona_session SET status='deleted' WHERE session_id=?",
            (sid,),
        )
        await db.commit()


# R5: 结构序列化标记 — 用于区分"被 JSON 序列化的非字符串类型"与"碰巧长得像 JSON 的纯文本"
_SERIALIZED_PREFIX = "\x01JSON:"


def _serialize_plain_string_if_ambiguous(value: str) -> str:
    """标记会被旧格式兼容探测误判的新版纯字符串。"""
    ambiguous = value == "null" or value.startswith(_SERIALIZED_PREFIX)
    if value.startswith("["):
        try:
            ambiguous = ambiguous or isinstance(json.loads(value), list)
        except json.JSONDecodeError:
            pass
    if ambiguous:
        return _SERIALIZED_PREFIX + json.dumps(value, ensure_ascii=False)
    return value


def _decompose_message(msg: dict) -> dict:
    """将内存消息 dict 拆为落盘字段。

    ref 条目正文不落盘（正文在 message_stream）；own 条目落 content 与工具字段
    （tool_calls/tool_call_id/name），保证 assistant 工具调用/工具结果在重载后不丢，
    多轮工具上下文完整。

    R5: 非字符串类型的 content/tool_calls 以 _SERIALIZED_PREFIX 标记序列化，
    使 _recompose_message 可精确还原类型（避免把纯 JSON 文本误判为结构字段）。
    """
    entry_type = msg.get("entry_type", ENTRY_TYPE_OWN)
    role = msg.get("role", "")
    if entry_type == ENTRY_TYPE_REF:
        return {
            "role": role, "content": "", "tool_calls": "", "tool_call_id": "",
            "name": None, "provider_context": "",
            "message_stream_id": msg.get("message_stream_id"),
            "entry_type": ENTRY_TYPE_REF,
        }
    content = msg.get("content", "")
    if isinstance(content, str):
        content = _serialize_plain_string_if_ambiguous(content)
    elif content is None:
        content = _SERIALIZED_PREFIX + "null"
    else:
        # 多模态 list[dict] content
        content = _SERIALIZED_PREFIX + json.dumps(content, ensure_ascii=False)
    tool_calls = msg.get("tool_calls", "")
    if isinstance(tool_calls, str):
        tool_calls = _serialize_plain_string_if_ambiguous(tool_calls)
    elif tool_calls:
        tool_calls = _SERIALIZED_PREFIX + json.dumps(tool_calls, ensure_ascii=False)
    raw_provider_context = msg.get("_provider_context")
    provider_context = (
        dict(raw_provider_context)
        if isinstance(raw_provider_context, dict)
        else {}
    )
    internal_message_type = get_internal_message_type(msg)
    if internal_message_type:
        # 复用既有 JSON 列持久化 Runtime 私有标记；公开 name 仅作为
        # provider 可见语义，不能作为内部消息身份凭据。
        provider_context[INTERNAL_MESSAGE_TYPE_FIELD] = internal_message_type
    if provider_context:
        provider_context_raw = json.dumps(provider_context, ensure_ascii=False)
    else:
        provider_context_raw = ""
    return {
        "role": role, "content": content,
        "tool_calls": tool_calls or "", "tool_call_id": msg.get("tool_call_id", "") or "",
        "name": msg.get("name"), "provider_context": provider_context_raw,
        "message_stream_id": None,
        "entry_type": ENTRY_TYPE_OWN,
    }


def _recompose_message(row) -> dict:
    """将落盘行还原为内存消息 dict。兼容 tuple 与 dict/Row 行工厂。

    ref 条目还原为 {role, entry_type:'ref', message_stream_id}（不含正文）；
    own 条目还原为 {role, content, [tool_calls, tool_call_id, name]}（仅非空字段）。

    R5: 检测 _SERIALIZED_PREFIX 标记，将序列化字段还原为原始 Python 类型
    （tool_calls → list, content → None/list）。
    """
    role = _cell(row, 0, "role")
    content = _cell(row, 1, "content")
    tool_calls = _cell(row, 2, "tool_calls")
    tool_call_id = _cell(row, 3, "tool_call_id")
    name = _cell(row, 4, "name")
    provider_context_raw = _cell(row, 5, "provider_context")
    msid = _cell(row, 6, "message_stream_id")
    entry_type = _cell(row, 7, "entry_type") or ENTRY_TYPE_OWN
    if entry_type == ENTRY_TYPE_REF:
        return {
            "role": role,
            "entry_type": ENTRY_TYPE_REF,
            "message_stream_id": msid,
        }
    # R5: 类型还原
    content = _restore_serialized(content)
    tool_calls = _restore_serialized(tool_calls)
    out = {"role": role, "content": content}
    if tool_calls:
        out["tool_calls"] = tool_calls
    if tool_call_id:
        out["tool_call_id"] = tool_call_id
    if name:
        out["name"] = name
    if provider_context_raw:
        try:
            provider_context = json.loads(provider_context_raw)
        except (TypeError, json.JSONDecodeError):
            provider_context = None
        if isinstance(provider_context, dict):
            internal_message_type = provider_context.pop(
                INTERNAL_MESSAGE_TYPE_FIELD, "",
            )
            validated_type = get_internal_message_type({
                INTERNAL_MESSAGE_TYPE_FIELD: internal_message_type,
            })
            if validated_type:
                out[INTERNAL_MESSAGE_TYPE_FIELD] = validated_type
            if provider_context:
                out["_provider_context"] = provider_context
    return out


def _restore_serialized(value):
    """检测 _SERIALIZED_PREFIX 标记，还原被 JSON 序列化的非字符串类型。

    R5 新数据：标记 + "null" → None；标记 + JSON → json.loads 还原。
    无标记旧数据（修复前已落盘）：启发式兼容读取 —
      - "null" → None（content 旧格式）
      - 以 [ 开头 → 尝试 json.loads，若结果为 list 则还原（tool_calls 旧格式 /
        多模态 content 旧格式）
    纯文本 content 不受影响。
    """
    if not isinstance(value, str):
        return value
    if value.startswith(_SERIALIZED_PREFIX):
        payload = value[len(_SERIALIZED_PREFIX):]
        if payload == "null":
            return None
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            logger.warning("ConversationStore: 无法解析序列化标记后的 payload: %s", payload[:100])
            return payload
    # 向后兼容：修复前已落盘的无前缀旧数据
    if value == "null":
        return None
    if value.startswith("["):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass
    return value


def _cell(row, index: int, key: str):
    try:
        return row[key]
    except (TypeError, KeyError, IndexError):
        return row[index]


def _parse_conv_id(conv_id: str) -> Optional[int]:
    """将字符串 conv_id 解析为整数 session_id。"""
    if not conv_id or not conv_id.strip():
        return None
    try:
        return int(conv_id)
    except (ValueError, TypeError):
        return None
