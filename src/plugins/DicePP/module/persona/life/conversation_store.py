"""ConversationStore — Store 协议的 SQLite 生产实现

使用现有 persona_session + persona_session_message 表。
conv_id 映射到 session_id。
"""

from __future__ import annotations

import json
from typing import Optional

from utils.logger import logger
from ..data.store import PersonaDataStore
from .conversation import Snapshot, Store


class ConversationStore(Store):
    """Store 协议 SQLite 实现，通过 PersonaDataStore 操作 persona_session 表。"""

    def __init__(self, store: PersonaDataStore, user_id: str = "",
                 character_id: str = "") -> None:
        self._store = store
        self._user_id = user_id
        self._character_id = character_id

    # ── Store 实现 ───────────────────────────────────────

    async def register(self, conv_id: str, cursor_tag: str) -> None:
        """注册 cursor tag。

        当前为 no-op：cursor 在首次 fetch_notifications() 时懒初始化，
        不需要预注册。
        T5: 若需要持久化 cursor 注册，在此处实现 persona_session cursor 写入。
        """

    async def put(self, conv_id: str, snapshot: Snapshot) -> str:
        """写入（全量覆盖）。conv_id 为空时创建新 session。

        Returns:
            写入后的 conv_id（首次创建时返回新分配的 id，更新时返回原 id）。

        T3: system_prompt 不再由 Snapshot 携带，static_prompt 列留存空字符串
        （DB schema 保留该列以兼容旧数据，T5 清理）。
        """
        db = self._store._persona_db
        sid = _parse_conv_id(conv_id)
        cursors_raw = json.dumps(snapshot.cursors, ensure_ascii=False)
        system = ""  # T3: system_prompt 不持久化

        if sid is not None:
            # 更新已有 session
            await db.execute(
                "UPDATE persona_session SET static_prompt=?, cursors_json=?, last_active_at=CURRENT_TIMESTAMP WHERE session_id=?",
                (system, cursors_raw, sid),
            )
            # 删除旧消息后重新写入
            await db.execute(
                "DELETE FROM persona_session_message WHERE session_id=?",
                (sid,),
            )
        else:
            # 创建新 session
            cursor = await db.execute(
                "INSERT INTO persona_session (user_id, character_id, static_prompt, cursors_json) VALUES (?, ?, ?, ?)",
                (self._user_id, self._character_id, system, cursors_raw),
            )
            sid = cursor.lastrowid

        # 写入消息
        for seq, msg in enumerate(snapshot.messages):
            await db.execute(
                "INSERT INTO persona_session_message (session_id, role, content, sequence) VALUES (?, ?, ?, ?)",
                (sid, msg.get("role", ""), msg.get("content", ""), seq),
            )
        await db.commit()
        return str(sid)

    async def get(self, conv_id: str) -> Snapshot | None:
        """读取指定 conversation 的快照。

        T3: static_prompt 列不再恢复到 Snapshot（system_prompt 不持久化）。
        """
        db = self._store._persona_db
        sid = _parse_conv_id(conv_id)
        if sid is None:
            return None

        cursor = await db.execute(
            "SELECT static_prompt, cursors_json FROM persona_session WHERE session_id=? AND status='active'",
            (sid,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None

        cursors_raw = row[1] or "{}"
        try:
            cursors = json.loads(cursors_raw)
        except json.JSONDecodeError:
            cursors = {}

        msg_cursor = await db.execute(
            "SELECT role, content FROM persona_session_message WHERE session_id=? ORDER BY sequence",
            (sid,),
        )
        msg_rows = await msg_cursor.fetchall()
        messages = [{"role": r[0], "content": r[1]} for r in msg_rows]

        return Snapshot(messages=messages, cursors=cursors)

    async def delete(self, conv_id: str) -> None:
        """删除指定 conversation 及其消息。"""
        db = self._store._persona_db
        sid = _parse_conv_id(conv_id)
        if sid is None:
            return
        # 更新状态为非活跃（保留下消息以供审计，非真删除）
        await db.execute(
            "UPDATE persona_session SET status='deleted' WHERE session_id=?",
            (sid,),
        )
        await db.commit()


def _parse_conv_id(conv_id: str) -> Optional[int]:
    """将字符串 conv_id 解析为整数 session_id。"""
    if not conv_id or not conv_id.strip():
        return None
    try:
        return int(conv_id)
    except (ValueError, TypeError):
        return None
