"""
Persona 数据存储层

统一的数据访问接口
"""
from typing import List, Optional, Dict, Any, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from ..image_cache import ImageCacheProtocol
from ..image_cache import ImageCache
from datetime import datetime, timedelta
import json
import re
from plugins.DicePP.utils.logger import logger
import os
import base64
import aiosqlite
from pydantic import ValidationError

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from dicepp_data import PERSONA_DB_ASSET
from ..utils.privacy import mask_sensitive_string

from .models import (
    WhitelistEntry, DailyUsage, ScoreEvent, ScoreDeltas, UserProfile,
    RelationshipState, DailyEvent, GroupActivity, UserLLMConfig,
    LLMTraceRecord, CharacterState, DMState, SAState,
    ScoringFailure, UnifiedMessage, MessageType, DEFAULT_RELATION_LABELS,
    DEFAULT_SESSION_TOKEN_BUDGET,
    StoryDeckEntry, VALID_ENTRY_TYPES,
)
from plugins.DicePP.core.data.schema import ensure_schema_async
from plugins.DicePP.core.data.schema.lifecycle import execute_many_async
from .schema import BOT_CORE_SCHEMA_SQL, PERSONA_TARGET


class PersonaDataStore:
    """Persona 数据存储"""

    # 日记搜索默认天数
    DEFAULT_DIARY_DAYS_PRIVATE = 7
    DEFAULT_DIARY_DAYS_GROUP = 3

    # 消息流裁剪限频：每 N 次写入或每 M 秒触发一次实际裁剪
    _PRUNE_INTERVAL_WRITES = 50
    _PRUNE_INTERVAL_SECONDS = 300

    # 图片 hash 查找的最大消息扫描数。远大于 MAX_CONTEXT_MESSAGES(30)，
    # 覆盖群聊中偶发的纯文本刷屏导致图片消息被推远的情况。
    _IMAGE_HASH_SCAN_LIMIT = 200

    # SYSTEM_LOG 过滤条件，用于裁剪和内部逻辑
    _EXCLUDE_SYSTEM_LOG = "type != 'system_log'"

    # AMBIENT 冷数据保留期。过期行仅在未被任何 Conversation ref 引用时清理，
    # 避免数量上限把长时间活跃 scope 中的引用裁成悬空引用。
    _AMBIENT_RETENTION_DAYS = 30

    # Persona 面层查询过滤：排除系统日志，包含 ambient 以提供完整上下文
    _PERSONA_SCOPE = "type != 'system_log'"

    def __init__(
        self,
        persona_db_path: str,
        core_db: aiosqlite.Connection,
        *,
        group_activity_decay_per_day: float = 10.0,
        group_activity_floor_whitelist: float = 50.0,
        timezone: str = "Asia/Shanghai",
        message_stream_max_per_group: int = 1000,
    ):
        self._persona_db_path = persona_db_path
        self._core_db = core_db
        self._persona_db: Optional[aiosqlite.Connection] = None
        self._group_activity_decay_per_day = group_activity_decay_per_day
        self._group_activity_floor_whitelist = group_activity_floor_whitelist
        self._timezone = timezone
        self._message_stream_max_per_group = message_stream_max_per_group
        self._msg_stream_write_count = 0
        self._last_prune_at: Optional[datetime] = None
        self.image_cache: Optional["ImageCacheProtocol"] = None  # 由 command.py 注入 ImageCache 实例

    @property
    def db(self) -> aiosqlite.Connection:
        """返回 persona_db 连接（保持外部 self.db.xxx 引用不变）"""
        if self._persona_db is None:
            raise RuntimeError("PersonaDataStore 未打开，先调用 await store.open()")
        return self._persona_db

    @property
    def timezone(self) -> str:
        """公共时区属性（原 _timezone 为内部命名约定）"""
        return self._timezone

    async def _init_connection(self, conn: aiosqlite.Connection) -> None:
        """设置连接 PRAGMA"""
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA foreign_keys=ON")

    async def _connect_initialized_persona_db(self, db_path: str) -> aiosqlite.Connection:
        """创建并初始化 persona 连接；初始化失败时不遗留后台线程。"""
        conn = await aiosqlite.connect(db_path)
        try:
            await self._init_connection(conn)
        except BaseException:
            try:
                await conn.close()
            except Exception:
                logger.exception("Persona 数据库初始化失败后的连接关闭失败")
            raise
        return conn

    async def open(self) -> None:
        """连接 persona_db，设置 PRAGMA，创建表"""
        if self._persona_db is not None:
            raise RuntimeError("PersonaDataStore 已打开，关闭后才能再次打开")

        conn = await self._connect_initialized_persona_db(self._persona_db_path)
        self._persona_db = conn
        try:
            await self.ensure_tables()
            await self._migrate_schema_t6()
        except BaseException:
            self._persona_db = None
            try:
                await conn.close()
            except Exception:
                logger.exception("Persona 数据库打开失败后的连接关闭失败")
            raise

    async def close(self) -> None:
        """关闭 persona_db 连接。core_db 由 Bot 生命周期管理，不在此关闭。"""
        if self._persona_db is not None:
            await self._persona_db.close()
            self._persona_db = None

    async def __aenter__(self) -> "PersonaDataStore":
        """打开 persona 数据库并返回 store。"""
        await self.open()
        return self

    async def __aexit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        """退出作用域时关闭由 store 拥有的 persona 数据库。"""
        await self.close()

    def _wall_now(self) -> datetime:
        """与 `PersonaConfig.timezone` 一致的墙钟（naive 本地时间）。"""
        from plugins.DicePP.utils.time import get_clock
        return get_clock().now()

    @staticmethod
    def _is_private_chat(group_id: Optional[str]) -> bool:
        """判断是否为私聊场景

        私聊: group_id 为 None 或空字符串
        群聊: group_id 为非空字符串
        """
        return not (group_id and group_id.strip())

    async def ensure_tables(self) -> None:
        """确保 persona 角色库 schema 已由统一 lifecycle 管理。"""
        persona_db = self.db
        await ensure_schema_async(persona_db, PERSONA_TARGET)
        persona_db.row_factory = lambda cur, row: {col[0]: row[i] for i, col in enumerate(cur.description)}
        await execute_many_async(self._core_db, BOT_CORE_SCHEMA_SQL)
        await self._core_db.commit()

    async def _migrate_schema_t6(self) -> None:
        """T6 schema 迁移：新增列以匹配新命名。

        SQLite 的 CREATE TABLE IF NOT EXISTS 不修改已存在的表，
        因此需要 ALTER TABLE 迁移已有数据库。

        新 schema 直接使用 interaction_id。保留 session_id → interaction_id
        重命名（persona_llm_traces 表）。

        迁移内容:
        - persona_llm_traces: session_id → interaction_id, 新增 usage 列
        - persona_agent_runs: 新增 agent_name/run_tag/completion 列
        """
        db = self._persona_db

        # 辅助函数：检查列是否存在
        async def _has_column(table: str, column: str) -> bool:
            rows = await db.execute_fetchall(
                f"PRAGMA table_info({table})"
            )
            return any(row["name"] == column for row in rows)

        # 辅助函数：安全添加列（列不存在时才添加）
        async def _add_column_if_missing(table: str, column: str, col_def: str) -> None:
            if not await _has_column(table, column):
                try:
                    await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
                except Exception:
                    pass  # 列可能已存在（SQLite 报错即忽略）

        # ── persona_llm_traces: session_id → interaction_id + 新增 usage 列 ──
        if await _has_column("persona_llm_traces", "session_id"):
            try:
                await db.execute(
                    "ALTER TABLE persona_llm_traces RENAME COLUMN session_id TO interaction_id"
                )
            except Exception:
                pass
        await _add_column_if_missing("persona_llm_traces", "usage_status", "TEXT NOT NULL DEFAULT ''")
        await _add_column_if_missing("persona_llm_traces", "usage_raw_json", "TEXT NOT NULL DEFAULT ''")
        await _add_column_if_missing("persona_llm_traces", "usage_note", "TEXT NOT NULL DEFAULT ''")

        # ── persona_agent_runs: 新增 agent_name/run_tag/completion 列 ──
        if await _has_column("persona_agent_runs", "mode"):
            await _add_column_if_missing("persona_agent_runs", "agent_name", "TEXT NOT NULL DEFAULT ''")
            await _add_column_if_missing("persona_agent_runs", "run_tag", "TEXT NOT NULL DEFAULT ''")
        if await _has_column("persona_agent_runs", "final_reason"):
            await _add_column_if_missing("persona_agent_runs", "completion_kind", "TEXT NOT NULL DEFAULT ''")
            await _add_column_if_missing("persona_agent_runs", "completion_code", "TEXT NOT NULL DEFAULT ''")
            await _add_column_if_missing("persona_agent_runs", "completion_message", "TEXT NOT NULL DEFAULT ''")

        await db.commit()

    async def switch_persona_db(self, new_character_name: str) -> None:
        """关闭当前 persona_db，打开新角色的 persona_db（先开后关策略）"""
        if self._persona_db_path == ":memory:":
            raise ValueError("switch_persona_db 不适用于 :memory: 数据库")
        new_path = str(
            PERSONA_DB_ASSET.resolve_sibling(
                self._persona_db_path,
                character=new_character_name,
            )
        )
        new_conn = await self._connect_initialized_persona_db(new_path)
        old_db = self._persona_db
        old_path = self._persona_db_path
        self._persona_db = new_conn
        self._persona_db_path = new_path
        try:
            await self.ensure_tables()
        except BaseException:
            self._persona_db = old_db
            self._persona_db_path = old_path
            try:
                await new_conn.close()
            except Exception:
                logger.exception("Persona 数据库切换回滚时的新连接关闭失败")
            raise
        if old_db is not None:
            await old_db.close()

    # ========== 消息流表 (message_stream) ==========

    @staticmethod
    def _row_to_message(row: dict) -> UnifiedMessage:
        """将数据库行反序列化为 UnifiedMessage（dict row_factory 模式）。"""
        image_meta_raw = row.get("image_meta")
        image_meta = json.loads(image_meta_raw) if image_meta_raw else None
        return UnifiedMessage(
            id=row["id"],
            user_id=row["user_id"],
            group_id=row["group_id"],
            role=row["role"],
            type=MessageType(row["type"]),
            content=row["content"],
            display_name=row.get("display_name") or "",
            created_at=datetime.fromisoformat(row["created_at"]) if row.get("created_at") else None,
            agent_run_id=row.get("agent_run_id", ""),
            interaction_id=row.get("interaction_id", ""),
            segment_index=row.get("segment_index", -1),
            segment_phase=row.get("segment_phase", ""),
            image_meta=image_meta,
        )

    async def add_message_stream(
        self,
        user_id: str,
        group_id: str,
        role: str,
        type: MessageType,
        content: str,
        display_name: str = "",
        *,
        agent_run_id: str = "",
        interaction_id: str = "",
        segment_index: int = -1,
        segment_phase: str = "",
        image_meta: Optional[List[dict]] = None,
    ) -> int:
        """写入一条消息流记录，返回 last_insert_rowid。写入后按限频触发保留裁剪。

        新增参数 (Phase M1):
            agent_run_id: 所属 Agent run ID
            interaction_id: 所属 interaction ID
            segment_index: 分段序号 (>=0)
            segment_phase: 分段阶段 ("interim" / "final")

        新增参数 (Phase 3):
            image_meta: 图片元信息列表（JSON 序列化存储）
        """
        now_iso = self._wall_now().isoformat()
        image_meta_json = json.dumps(image_meta, ensure_ascii=False) if image_meta else None
        cursor = await self.db.execute(
            """
            INSERT INTO message_stream
            (user_id, group_id, role, type, content, display_name, created_at,
             agent_run_id, interaction_id, segment_index, segment_phase, image_meta)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, group_id, role, type.value, content, display_name, now_iso,
             agent_run_id, interaction_id, segment_index, segment_phase, image_meta_json),
        )
        await self.db.commit()
        rowid = cursor.lastrowid
        await self._retain_message_stream(group_id, user_id)
        return rowid

    async def get_recent_messages(
        self,
        user_id: str,
        group_id: str = "",
        limit: int = 20,
    ) -> List[UnifiedMessage]:
        """获取最近消息（按 user_id + group_id），时间升序返回"""
        async with self.db.execute(
            f"""
            SELECT id, user_id, group_id, role, type, content, display_name, created_at,
                   agent_run_id, interaction_id, segment_index, segment_phase, image_meta
            FROM message_stream
            WHERE user_id = ? AND group_id = ?
              AND {PersonaDataStore._PERSONA_SCOPE}
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (user_id, group_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
            return [self._row_to_message(r) for r in reversed(list(rows))]

    async def read_message_stream_batch(
        self, ids: List[int]
    ) -> Dict[int, UnifiedMessage]:
        """按 id 批量读取 message_stream 记录，返回 {id: UnifiedMessage}。

        供 Conversation 引用展开（render_resolved）使用：Conversation 只存
        message_stream_id 引用，render 时批量取回权威正文。
        不存在的 id 不出现在返回 dict 中（悬空引用由调用方 fallback 处理）。
        """
        if not ids:
            return {}
        # 去重并保持稳定；用参数占位符防注入
        unique_ids = list(dict.fromkeys(int(i) for i in ids))
        placeholders = ",".join("?" for _ in unique_ids)
        result: Dict[int, UnifiedMessage] = {}
        async with self.db.execute(
            f"""
            SELECT id, user_id, group_id, role, type, content, display_name, created_at,
                   agent_run_id, interaction_id, segment_index, segment_phase, image_meta
            FROM message_stream
            WHERE id IN ({placeholders})
            """,
            unique_ids,
        ) as cursor:
            rows = await cursor.fetchall()
        for r in rows:
            msg = self._row_to_message(r)
            result[msg.id] = msg
        return result

    async def get_image_by_hash(
        self,
        user_id: str,
        group_id: str,
        image_hash: str,
    ) -> Optional[dict]:
        """通过 image_hash 查找图片元信息。

        扫描最近消息的 image_meta，上限 _IMAGE_HASH_SCAN_LIMIT。
        返回匹配的 entry（带 _message_id 和 _image_meta_list），找不到返回 None。
        存量数据无 image_hash 时，用 url/file 现场计算比对。
        """
        async with self.db.execute(
            f"""
            SELECT id, image_meta FROM message_stream
            WHERE user_id = ? AND group_id = ?
              AND image_meta IS NOT NULL AND image_meta != ''
              AND {PersonaDataStore._PERSONA_SCOPE}
            ORDER BY created_at DESC, id DESC
            LIMIT {PersonaDataStore._IMAGE_HASH_SCAN_LIMIT}
            """,
            (user_id, group_id),
        ) as cursor:
            rows = await cursor.fetchall()

        for row in rows:
            msg_id, raw_meta = row["id"], row["image_meta"]
            if not raw_meta:
                continue
            try:
                meta_list = json.loads(raw_meta)
                if isinstance(meta_list, list):
                    for entry in meta_list:
                        entry_hash = entry.get("image_hash")
                        if not entry_hash:
                            # 存量数据现场计算
                            entry_hash = ImageCache.compute_image_hash(entry)
                        if entry_hash == image_hash:
                            entry["_message_id"] = msg_id
                            entry["_image_meta_list"] = meta_list
                            return entry
            except (json.JSONDecodeError, TypeError):
                continue
        return None

    async def update_image_meta(
        self,
        user_id: str,
        group_id: str,
        message_id: int,
        image_meta: List[dict],
    ) -> None:
        """更新指定消息的 image_meta（用于回填 cache_hash）。"""
        image_meta_json = json.dumps(image_meta, ensure_ascii=False)
        await self.db.execute(
            "UPDATE message_stream SET image_meta = ? WHERE id = ? AND user_id = ? AND group_id = ?",
            (image_meta_json, message_id, user_id, group_id),
        )
        await self.db.commit()

    async def get_earliest_message_time(self, user_id: str, group_id: str = "") -> Optional[datetime]:
        """获取用户最早消息时间（ORDER BY created_at ASC LIMIT 1）

        group_id 非空时查群聊，为空时查私聊。
        """
        async with self.db.execute(
            f"""
            SELECT created_at FROM message_stream
            WHERE user_id = ? AND group_id = ? AND {PersonaDataStore._PERSONA_SCOPE}
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (user_id, group_id),
        ) as cursor:
            row = await cursor.fetchone()
            if row and row.get("created_at"):
                return datetime.fromisoformat(row["created_at"])
            return None

    async def count_messages(self, user_id: str, group_id: str = "") -> int:
        """统计用户消息数量（使用 SELECT COUNT(*) 避免全量加载）"""
        async with self.db.execute(
            f"SELECT COUNT(*) as cnt FROM message_stream WHERE user_id = ? AND group_id = ? AND {PersonaDataStore._PERSONA_SCOPE}",
            (user_id, group_id),
        ) as cursor:
            row = await cursor.fetchone()
            return row["cnt"] if row else 0

    async def get_group_messages(
        self,
        group_id: str,
        limit: Optional[int] = 50,
    ) -> List[UnifiedMessage]:
        """获取群聊最近消息，时间升序返回"""
        if limit is None:
            sql = f"""
            SELECT id, user_id, group_id, role, type, content, display_name, created_at,
                   agent_run_id, interaction_id, segment_index, segment_phase, image_meta
            FROM message_stream
            WHERE group_id = ? AND group_id != '' AND {PersonaDataStore._PERSONA_SCOPE}
            ORDER BY created_at DESC, id DESC
            """
            params = (group_id,)
        else:
            sql = f"""
            SELECT id, user_id, group_id, role, type, content, display_name, created_at,
                   agent_run_id, interaction_id, segment_index, segment_phase, image_meta
            FROM message_stream
            WHERE group_id = ? AND group_id != '' AND {PersonaDataStore._PERSONA_SCOPE}
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """
            params = (group_id, limit)
        async with self.db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
            return [self._row_to_message(r) for r in reversed(list(rows))]

    async def read_messages(
        self, user_id: str, group_id: str = "",
        *, limit: int = 20, offset: int = 0,
        filter_user_id: Optional[str] = None,
    ) -> List[UnifiedMessage]:
        """分页读取聊天记录（不需要关键词），时间降序返回（最新的在前）。

        scope 隔离（防越权）：私聊路径恒绑定 user_id（当前用户），filter_user_id
        不能替换它、只能在群聊内按参与者收窄；跨用户私聊查询被结构性禁止。
        """
        conditions = [PersonaDataStore._PERSONA_SCOPE]
        params: List[Any] = []

        if group_id:
            conditions.append("group_id = ?")
            params.append(group_id)
            # 群聊：允许按群内参与者过滤（仍限定在本群）
            if filter_user_id:
                conditions.append("user_id = ?")
                params.append(filter_user_id)
        else:
            conditions.append("group_id = ''")
            # 私聊：查询目标恒为绑定用户，filter_user_id 无法改变（防越权读他人私聊）
            conditions.append("user_id = ?")
            params.append(user_id)

        where_clause = " AND ".join(conditions)
        sql = f"""
            SELECT id, user_id, group_id, role, type, content, display_name, created_at,
                   agent_run_id, interaction_id, segment_index, segment_phase, image_meta
            FROM message_stream
            WHERE {where_clause}
            ORDER BY created_at DESC, id DESC
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])

        async with self.db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
            return [self._row_to_message(r) for r in rows]

    async def search_messages(
        self,
        group_id: str,
        *,
        keyword: Optional[str] = None,
        type: Optional[MessageType] = None,
        user_id: Optional[str] = None,
        filter_user_id: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        hours_back: Optional[int] = None,
        limit: int = 5,
    ) -> List[UnifiedMessage]:
        """搜索消息流表，时间升序返回

        参数优先级：hours_back 与 start_time/end_time 互斥。
        """
        if hours_back is not None and (start_time is not None or end_time is not None):
            raise ValueError("hours_back 与 start_time/end_time 不能同时使用")
        if (start_time is None) != (end_time is None):
            raise ValueError("start_time 和 end_time 必须同时提供或同时省略")

        conditions = [PersonaDataStore._PERSONA_SCOPE]
        params: List[Any] = []

        if group_id:
            conditions.append("group_id = ?")
            params.append(group_id)
            # 群聊：允许按群内参与者过滤（仍限定在本群）
            if filter_user_id:
                conditions.append("user_id = ?")
                params.append(filter_user_id)
        else:
            conditions.append("group_id = ''")
            # 私聊：查询目标恒为绑定用户，filter_user_id 无法改变（防越权读他人私聊）
            conditions.append("user_id = ?")
            params.append(user_id)

        if keyword:
            safe_query = self._sanitize_search_query(keyword)
            conditions.append("content LIKE ? ESCAPE '\\'")
            params.append(f"%{safe_query}%")

        if type is not None:
            conditions.append("type = ?")
            params.append(type.value)

        if hours_back is not None:
            cutoff = self._wall_now() - timedelta(hours=hours_back)
            conditions.append("created_at >= ?")
            params.append(cutoff.isoformat())
        elif start_time is not None and end_time is not None:
            conditions.append("created_at >= ? AND created_at <= ?")
            params.append(start_time.isoformat())
            params.append(end_time.isoformat())

        where_clause = " AND ".join(conditions)
        sql = f"""
            SELECT id, user_id, group_id, role, type, content, display_name, created_at,
                   agent_run_id, interaction_id, segment_index, segment_phase, image_meta
            FROM message_stream
            WHERE {where_clause}
            ORDER BY created_at DESC, id DESC
            LIMIT ?
        """
        params.append(limit)

        async with self.db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
            return [self._row_to_message(r) for r in reversed(list(rows))]

    async def get_daily_chat_stats(self, date: str) -> Dict[str, Any]:
        """获取某日 persona 聊天统计（仅 type='chat'，排除 SYSTEM_LOG）"""
        chat_filter = f"type = 'chat' AND {PersonaDataStore._EXCLUDE_SYSTEM_LOG}"

        async with self.db.execute(
            f"""
            SELECT
                SUM(CASE WHEN role = 'assistant' THEN 1 ELSE 0 END) as bot,
                SUM(CASE WHEN role = 'user' THEN 1 ELSE 0 END) as user,
                COUNT(DISTINCT user_id) as users,
                COUNT(DISTINCT CASE WHEN group_id != '' THEN group_id END) as groups
            FROM message_stream
            WHERE {chat_filter} AND date(created_at) = ?
            """,
            (date,),
        ) as cursor:
            row = await cursor.fetchone()

        bot = row["bot"] or 0
        user = row["user"] or 0
        users = row["users"] or 0
        groups = row["groups"] or 0

        async with self.db.execute(
            f"""
            SELECT COUNT(DISTINCT user_id) as new_users
            FROM message_stream
            WHERE {chat_filter} AND date(created_at) = ?
              AND user_id NOT IN (
                SELECT DISTINCT user_id FROM message_stream
                WHERE {chat_filter} AND date(created_at) < ?
              )
            """,
            (date, date),
        ) as cursor:
            row = await cursor.fetchone()
        new_users = row["new_users"] if row else 0

        async with self.db.execute(
            f"""
            SELECT user_id, MAX(display_name) as name, COUNT(*) as cnt
            FROM message_stream
            WHERE {chat_filter} AND date(created_at) = ? AND role = 'user'
            GROUP BY user_id ORDER BY cnt DESC LIMIT 3
            """,
            (date,),
        ) as cursor:
            top_user_rows = await cursor.fetchall()

        async with self.db.execute(
            f"""
            SELECT group_id, COUNT(*) as cnt
            FROM message_stream
            WHERE {chat_filter} AND date(created_at) = ?
              AND group_id != ''
            GROUP BY group_id ORDER BY cnt DESC LIMIT 3
            """,
            (date,),
        ) as cursor:
            top_group_rows = await cursor.fetchall()

        return {
            "bot": bot,
            "user": user,
            "users": users,
            "new_users": new_users,
            "groups": groups,
            "top_users": [
                {"user_id": r["user_id"], "display_name": r["name"] or "", "cnt": r["cnt"]}
                for r in top_user_rows
            ],
            "top_groups": [
                {"group_id": r["group_id"], "cnt": r["cnt"]} for r in top_group_rows
            ],
        }

    def _tick_and_check_prune(self, now: datetime) -> bool:
        """递增计数器并判断是否应触发裁剪：每 N 次写入或每 M 秒"""
        self._msg_stream_write_count += 1
        if self._msg_stream_write_count >= self._PRUNE_INTERVAL_WRITES:
            return True
        if self._last_prune_at is None:
            return True
        if (now - self._last_prune_at).total_seconds() >= self._PRUNE_INTERVAL_SECONDS:
            return True
        return False

    async def _retain_message_stream(self, group_id: str, user_id: str) -> None:
        """写入后触发保留策略（限频）。

        取消对用户可见 chat 消息的破坏性数量裁剪 —— 这些消息是
        message_stream 权威记录、被 Conversation 以 message_stream_id 引用，
        按固定条数删除会把引用裁成悬空引用，违背隔离/引用完整不变量。
        仅保留 ambient（旁观）未引用冷数据与 system_log 过期淘汰；
        图片二进制缓存、冷数据归档另行治理（见交接文档）。
        """
        if not user_id and not group_id:
            return
        now = self._wall_now()
        if not self._tick_and_check_prune(now):
            return
        self._msg_stream_write_count = 0
        self._last_prune_at = now
        await self._prune_ambient_messages(user_id, group_id)
        await self._prune_system_log()

    async def _prune_system_log(self) -> None:
        """清理 30 天前的 SYSTEM_LOG 消息（不占用户配额，独立过期淘汰）"""
        await self.db.execute(
            "DELETE FROM message_stream WHERE type = 'system_log' AND created_at < date('now', '-30 days')"
        )
        await self.db.commit()

    async def _prune_ambient_messages(self, user_id: str, group_id: str) -> None:
        """清理过期且未被 Conversation 引用的 AMBIENT 消息。

        Conversation 的可见条目以 ``message_stream_id`` 引用权威正文。
        因此保留策略不再按固定条数删除，且对 active/closed 等所有
        session 的 ref 统一保护；只有超过保留期的孤儿行才可回收。
        """
        if group_id:
            where = "group_id = ? AND group_id != ''"
            params = (group_id,)
        else:
            where = "user_id = ? AND group_id = ''"
            params = (user_id,)
        await self.db.execute(
            f"""
            DELETE FROM message_stream
            WHERE {where}
              AND type = 'ambient'
              AND datetime(created_at) < datetime('now', ?)
              AND NOT EXISTS (
                SELECT 1
                FROM persona_session_message AS conversation_message
                WHERE conversation_message.entry_type = 'ref'
                  AND conversation_message.message_stream_id = message_stream.id
              )
            """,
            (*params, f"-{self._AMBIENT_RETENTION_DAYS} days"),
        )
        await self.db.commit()

    # ========== LLM Trace 相关 (Phase 7a) ==========

    async def add_llm_trace(self, trace: LLMTraceRecord) -> None:
        created_at_str = (
            trace.created_at.strftime("%Y-%m-%d %H:%M:%S")
            if trace.created_at
            else self._wall_now().strftime("%Y-%m-%d %H:%M:%S")
        )
        await self.db.execute(
            """
            INSERT INTO persona_llm_traces (
                interaction_id, user_id, group_id, run_id, model, tier,
                messages, response, tool_calls, round_messages,
                selected_provider, selected_model, selection_policy, candidate_count,
                latency_ms,
                tokens_in, tokens_out, temperature, status, error,
                reasoning_content, cache_read, cache_creation, reasoning_tokens,
                usage_status, usage_raw_json, usage_note,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trace.interaction_id,
                trace.user_id,
                trace.group_id,
                trace.run_id,
                trace.model,
                trace.tier,
                trace.messages,
                trace.response,
                trace.tool_calls,
                trace.round_messages,
                trace.selected_provider,
                trace.selected_model,
                trace.selection_policy,
                trace.candidate_count,
                trace.latency_ms,
                trace.tokens_in,
                trace.tokens_out,
                trace.temperature,
                trace.status,
                trace.error,
                trace.reasoning_content or "",
                trace.cache_read,
                trace.cache_creation,
                trace.reasoning_tokens,
                trace.usage_status,
                trace.usage_raw_json,
                trace.usage_note,
                created_at_str,
            ),
        )
        await self.db.commit()

    async def get_llm_traces(
        self,
        user_id: str,
        limit: int = 5,
    ) -> List[LLMTraceRecord]:
        async with self.db.execute(
            """
            SELECT id, interaction_id, user_id, group_id, run_id, model, tier,
                   messages, response, tool_calls, round_messages,
                   selected_provider, selected_model, selection_policy, candidate_count,
                   latency_ms,
                   tokens_in, tokens_out, temperature, status, error,
                   reasoning_content, cache_read, cache_creation, reasoning_tokens,
                   usage_status, usage_raw_json, usage_note,
                   created_at
            FROM persona_llm_traces
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
            traces: List[LLMTraceRecord] = []
            for row in rows:
                traces.append(LLMTraceRecord(
                    id=row["id"],
                    interaction_id=row["interaction_id"] or "",
                    user_id=row["user_id"],
                    group_id=row["group_id"],
                    run_id=row["run_id"] or "",
                    model=row["model"],
                    tier=row["tier"],
                    messages=row["messages"],
                    response=row["response"],
                    tool_calls=row["tool_calls"] or "",
                    round_messages=row["round_messages"] or "",
                    selected_provider=row["selected_provider"] or "",
                    selected_model=row["selected_model"] or "",
                    selection_policy=row["selection_policy"] or "",
                    candidate_count=row["candidate_count"] or 0,
                    latency_ms=row["latency_ms"],
                    tokens_in=row["tokens_in"] or 0,
                    tokens_out=row["tokens_out"] or 0,
                    temperature=row["temperature"],
                    status=row["status"],
                    error=row["error"] or "",
                    reasoning_content=row["reasoning_content"] or "",
                    cache_read=row["cache_read"] or 0,
                    cache_creation=row["cache_creation"] or 0,
                    reasoning_tokens=row["reasoning_tokens"] or 0,
                    usage_status=row.get("usage_status", ""),
                    usage_raw_json=row.get("usage_raw_json", ""),
                    usage_note=row.get("usage_note", ""),
                    created_at=datetime.fromisoformat(row["created_at"]) if row.get("created_at") else None,
                ))
            return traces

    async def prune_llm_traces(self, max_age_days: int) -> int:
        cutoff = (self._wall_now() - timedelta(days=max_age_days)).strftime("%Y-%m-%d %H:%M:%S")
        cursor = await self.db.execute(
            "DELETE FROM persona_llm_traces WHERE created_at < ?",
            (cutoff,),
        )
        await self.db.commit()
        return cursor.rowcount

    async def get_today_token_usage(self) -> tuple[Optional[int], Optional[int]]:
        """返回今日 LLM trace 的 token 总消耗 (tokens_in, tokens_out)"""
        today = self._wall_now().strftime("%Y-%m-%d")
        async with self.db.execute(
            "SELECT SUM(tokens_in) as tokens_in, SUM(tokens_out) as tokens_out FROM persona_llm_traces WHERE date(created_at) = ?",
            (today,),
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return row["tokens_in"], row["tokens_out"]
            return None, None

    async def get_daily_token_usage(self, date: str) -> list[dict]:
        """返回指定日期各模型的 LLM 调用统计（次数 + token 消耗）

        返回 [{"provider": str, "model": str, "requests": int,
               "tokens_in": int, ...}, ...]
        """
        async with self.db.execute(
            "SELECT selected_provider, selected_model, COUNT(*) as requests,"
            " COALESCE(SUM(tokens_in),0) as tokens_in, COALESCE(SUM(tokens_out),0) as tokens_out,"
            " COALESCE(SUM(cache_read),0) as cache_read, COALESCE(SUM(cache_creation),0) as cache_creation,"
            " COALESCE(SUM(reasoning_tokens),0) as reasoning_tokens"
            " FROM persona_llm_traces WHERE date(created_at) = ?"
            " GROUP BY selected_provider, selected_model"
            " ORDER BY SUM(tokens_in) + SUM(tokens_out) DESC",
            (date,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [
            {
                "provider": row["selected_provider"] or "",
                "model": row["selected_model"] or "",
                "requests": row["requests"],
                "tokens_in": row["tokens_in"],
                "tokens_out": row["tokens_out"],
                "cache_read": row["cache_read"],
                "cache_creation": row["cache_creation"],
                "reasoning_tokens": row["reasoning_tokens"],
            }
            for row in rows
        ]

    async def get_error_summary_since(self, since_iso: str) -> list[tuple[str, int]]:
        """返回自 since_iso 以来的错误统计 [(status, count), ...]"""
        async with self.db.execute(
            "SELECT status, COUNT(*) as count FROM persona_llm_traces WHERE datetime(created_at) >= datetime(?) AND status = 'failed' GROUP BY status",
            (since_iso,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [(row["status"], row["count"]) for row in rows]

    async def get_recent_score_events(self, user_id: str, limit: int = 2) -> List[ScoreEvent]:
        """获取最近评分事件，用于趋势计算"""
        async with self.db.execute(
            """
            SELECT user_id, group_id, intimacy_delta, reputation_delta, familiarity_delta,
                   composite_before, composite_after, reason, conversation_digest, created_at
            FROM persona_score_history
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (user_id, limit)
        ) as cursor:
            rows = await cursor.fetchall()
            events = []
            for row in reversed(list(rows)):  # Reverse to get chronological order
                events.append(ScoreEvent(
                    user_id=row["user_id"],
                    group_id=row["group_id"],
                    deltas=ScoreDeltas(
                        intimacy=row["intimacy_delta"] if row.get("intimacy_delta") is not None else 0.0,
                        reputation_delta=row["reputation_delta"] if row.get("reputation_delta") is not None else 0.0,
                    ),
                    familiarity_delta=row["familiarity_delta"] if row.get("familiarity_delta") is not None else 0.0,
                    composite_before=row["composite_before"],
                    composite_after=row["composite_after"],
                    reason=row["reason"],
                    conversation_digest=row["conversation_digest"] or "",
                    created_at=datetime.fromisoformat(row["created_at"]) if row.get("created_at") else None
                ))
            return events

    # ========== 白名单相关（core_db 侧） ==========

    async def is_user_whitelisted(self, user_id: str) -> bool:
        """检查用户是否在白名单"""
        async with self._core_db.execute(
            "SELECT 1 FROM persona_whitelist WHERE id = ? AND type = 'user'",
            (user_id,)
        ) as cursor:
            return await cursor.fetchone() is not None

    async def is_group_whitelisted(self, group_id: str) -> bool:
        """检查群是否在白名单"""
        async with self._core_db.execute(
            "SELECT 1 FROM persona_whitelist WHERE id = ? AND type = 'group'",
            (group_id,)
        ) as cursor:
            return await cursor.fetchone() is not None

    # --- 用户主动消息静音 (core_db 侧) ---

    async def is_user_muted(self, user_id: str) -> bool:
        """检查用户是否关闭了主动消息"""
        async with self._core_db.execute(
            "SELECT 1 FROM persona_user_mute WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            return await cursor.fetchone() is not None

    async def mute_user(self, user_id: str, reason: str = "") -> None:
        """关闭用户的主动消息"""
        await self._core_db.execute(
            """
            INSERT OR REPLACE INTO persona_user_mute (user_id, muted_at, reason)
            VALUES (?, ?, ?)
            """,
            (user_id, self._wall_now().isoformat(), reason)
        )
        await self._core_db.commit()

    async def unmute_user(self, user_id: str) -> None:
        """开启用户的主动消息"""
        await self._core_db.execute(
            "DELETE FROM persona_user_mute WHERE user_id = ?",
            (user_id,)
        )
        await self._core_db.commit()

    async def add_user_to_whitelist(self, user_id: str) -> None:
        """添加用户到白名单"""
        await self._core_db.execute(
            """
            INSERT OR IGNORE INTO persona_whitelist (id, type, joined_at)
            VALUES (?, 'user', ?)
            """,
            (user_id, self._wall_now().isoformat())
        )
        await self._core_db.commit()

    async def add_group_to_whitelist(self, group_id: str) -> None:
        """添加群到白名单"""
        await self._core_db.execute(
            """
            INSERT OR IGNORE INTO persona_whitelist (id, type, joined_at)
            VALUES (?, 'group', ?)
            """,
            (group_id, self._wall_now().isoformat())
        )
        await self._core_db.commit()

    async def remove_from_whitelist(self, entry_id: str, entry_type: str) -> None:
        """从白名单移除"""
        await self._core_db.execute(
            "DELETE FROM persona_whitelist WHERE id = ? AND type = ?",
            (entry_id, entry_type)
        )
        await self._core_db.commit()

    async def list_whitelist(self) -> List[WhitelistEntry]:
        """列出所有白名单条目"""
        async with self._core_db.execute(
            "SELECT id, type, joined_at FROM persona_whitelist ORDER BY type, joined_at"
        ) as cursor:
            raw_rows = await cursor.fetchall()
            cols = [desc[0] for desc in cursor.description]
            rows = [dict(zip(cols, r)) for r in raw_rows]
            return [
                WhitelistEntry(
                    id=row["id"],
                    type=row["type"],
                    joined_at=datetime.fromisoformat(row["joined_at"]) if row.get("joined_at") else None
                )
                for row in rows
            ]

    async def clear_whitelist(self) -> None:
        """清空白名单"""
        await self._core_db.execute("DELETE FROM persona_whitelist")
        await self._core_db.commit()

    # ========== 设置相关（角色级，persona_db 侧） ==========

    async def get_setting(self, key: str) -> Optional[str]:
        """获取角色级设置值"""
        async with self.db.execute(
            "SELECT value FROM persona_settings WHERE key = ?",
            (key,)
        ) as cursor:
            row = await cursor.fetchone()
            return row["value"] if row else None

    async def set_setting(self, key: str, value: str) -> None:
        """设置角色级设置值"""
        await self.db.execute(
            """
            INSERT INTO persona_settings (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value)
        )
        await self.db.commit()

    async def delete_setting(self, key: str) -> None:
        """删除角色级设置"""
        await self.db.execute(
            "DELETE FROM persona_settings WHERE key = ?",
            (key,)
        )
        await self.db.commit()

    # ========== 全局设置相关（bot 级，core_db 侧） ==========

    async def get_global_setting(self, key: str) -> Optional[str]:
        """获取 bot 级全局设置值（如口令 'code'）"""
        async with self._core_db.execute(
            "SELECT value FROM persona_global_settings WHERE key = ?",
            (key,)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

    async def set_global_setting(self, key: str, value: str) -> None:
        """设置 bot 级全局设置值"""
        await self._core_db.execute(
            """
            INSERT INTO persona_global_settings (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value)
        )
        await self._core_db.commit()

    async def delete_global_setting(self, key: str) -> None:
        """删除 bot 级全局设置"""
        await self._core_db.execute(
            "DELETE FROM persona_global_settings WHERE key = ?",
            (key,)
        )
        await self._core_db.commit()

    # ========== 用量相关 ==========

    async def get_daily_usage(self, user_id: str, date: str) -> int:
        """获取某日用量"""
        async with self.db.execute(
            "SELECT count FROM persona_usage WHERE user_id = ? AND date = ?",
            (user_id, date)
        ) as cursor:
            row = await cursor.fetchone()
            return row["count"] if row else 0

    async def increment_daily_usage(self, user_id: str, date: str) -> None:
        """增加用量"""
        await self.db.execute(
            """
            INSERT INTO persona_usage (user_id, date, count)
            VALUES (?, ?, 1)
            ON CONFLICT(user_id, date) DO UPDATE SET count = count + 1
            """,
            (user_id, date)
        )
        await self.db.commit()

    # ========== 评分历史 ==========

    async def add_score_event(self, event: ScoreEvent) -> None:
        """添加评分事件"""
        await self.db.execute(
            """
            INSERT INTO persona_score_history
            (user_id, group_id, intimacy_delta, reputation_delta, familiarity_delta,
             composite_before, composite_after, reason, conversation_digest, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.user_id,
                event.group_id,
                event.deltas.intimacy,
                event.deltas.reputation_delta,
                event.familiarity_delta,
                event.composite_before,
                event.composite_after,
                event.reason,
                event.conversation_digest,
                event.created_at.isoformat() if event.created_at else self._wall_now().isoformat(),
            )
        )
        await self.db.commit()

    async def record_scoring_failure(self, failure: ScoringFailure) -> None:
        """记录评分失败"""
        await self.db.execute(
            """
            INSERT INTO persona_scoring_failures
            (user_id, group_id, messages_count, error, raw_response, conversation_digest, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                failure.user_id,
                failure.group_id,
                failure.messages_count,
                failure.error,
                failure.raw_response,
                failure.conversation_digest,
                failure.created_at.isoformat() if failure.created_at else self._wall_now().isoformat(),
            )
        )
        await self.db.commit()

    async def get_recent_scoring_failures(
        self,
        user_id: str,
        group_id: Optional[str] = None,
        limit: int = 10,
    ) -> List[ScoringFailure]:
        """获取最近评分失败记录"""
        limit = max(1, limit)
        async with self.db.execute(
            """
            SELECT id, user_id, group_id, messages_count, error, raw_response, conversation_digest, created_at
            FROM persona_scoring_failures
            WHERE user_id = ? AND (? IS NULL OR group_id = ?)
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (user_id, group_id, group_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                ScoringFailure(
                    id=row["id"],
                    user_id=row["user_id"],
                    group_id=row["group_id"],
                    messages_count=row["messages_count"] or 0,
                    error=row["error"] or "",
                    raw_response=row["raw_response"] or "",
                    conversation_digest=row["conversation_digest"] or "",
                    created_at=datetime.fromisoformat(row["created_at"]) if row.get("created_at") else None,
                )
                for row in rows
            ]

    async def prune_scoring_failures(self, max_age_days: int) -> int:
        """清理超过 max_age_days 的评分失败记录"""
        cutoff = (self._wall_now() - timedelta(days=max_age_days)).isoformat()
        cursor = await self.db.execute(
            "DELETE FROM persona_scoring_failures WHERE datetime(created_at) < datetime(?)",
            (cutoff,),
        )
        await self.db.commit()
        return cursor.rowcount

    # ========== 日记相关 ==========

    async def get_diary(self, date: str) -> Optional[str]:
        """获取某天的日记"""
        async with self.db.execute(
            "SELECT content FROM persona_diary WHERE date = ?",
            (date,)
        ) as cursor:
            row = await cursor.fetchone()
            return row["content"] if row else None

    async def save_diary(self, date: str, content: str) -> None:
        """保存日记"""
        await self.db.execute(
            """
            INSERT INTO persona_diary (date, content, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET content = excluded.content
            """,
            (date, content, self._wall_now().isoformat())
        )
        await self.db.commit()

    async def get_recent_diaries(self, days: int = 7, limit: int = 5) -> List[Tuple[str, str]]:
        """获取最近 N 天的日记全文（按日期降序）"""
        cutoff_date = (self._wall_now() - timedelta(days=days)).strftime("%Y-%m-%d")
        async with self.db.execute(
            """
            SELECT date, content
            FROM persona_diary
            WHERE date >= ?
            ORDER BY date DESC
            LIMIT ?
            """,
            (cutoff_date, limit),
        ) as cursor:
            rows = await cursor.fetchall()
            return [(row["date"], row["content"] or "") for row in rows]

    # ========== 每日事件 ==========

    async def add_daily_event(
        self,
        date: str,
        event_type: str,
        description: str,
        reaction: str = "",
        duration_minutes: int = 0,
        system_prompt_digest: str = "",
        raw_response: str = "",
        energy_delta: Optional[int] = None,
        mood_delta: Optional[int] = None,
        health_delta: Optional[int] = None,
        context_summary: str = "",
    ) -> int:
        """添加每日事件，返回新事件 ID"""
        cursor = await self.db.execute(
            """
            INSERT INTO persona_daily_events (
                date, event_type, description, reaction,
                duration_minutes,
                system_prompt_digest, raw_response,
                energy_delta, mood_delta, health_delta, created_at,
                context_summary
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                date,
                event_type,
                description,
                reaction,
                duration_minutes,
                system_prompt_digest,
                raw_response,
                energy_delta,
                mood_delta,
                health_delta,
                self._wall_now().isoformat(),
                context_summary,
            )
        )
        await self.db.commit()
        return cursor.lastrowid

    async def get_daily_events(self, date: str) -> List[DailyEvent]:
        """获取某天的所有事件"""
        async with self.db.execute(
            """
            SELECT id, event_type, description, reaction,
                   duration_minutes, created_at,
                   system_prompt_digest, raw_response,
                   energy_delta, mood_delta, health_delta,
                   context_summary
            FROM persona_daily_events
            WHERE date = ?
            ORDER BY created_at
            """,
            (date,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                DailyEvent(
                    id=row["id"],
                    date=date,
                    event_type=row["event_type"],
                    description=row["description"],
                    reaction=row["reaction"] or "",
                    duration_minutes=row["duration_minutes"] if row.get("duration_minutes") is not None else 0,
                    created_at=datetime.fromisoformat(row["created_at"]) if row.get("created_at") else None,
                    system_prompt_digest=row.get("system_prompt_digest") or "",
                    raw_response=row.get("raw_response") or "",
                    energy_delta=row.get("energy_delta"),
                    mood_delta=row.get("mood_delta"),
                    health_delta=row.get("health_delta"),
                    context_summary=row.get("context_summary") or "",
                )
                for row in rows
            ]

    async def get_events_range(self, start_date: str, end_date: str) -> List[DailyEvent]:
        """获取日期范围内的所有事件（单次 SQL 查询）。

        替代逐日调用 get_daily_events 的 N+1 模式。
        """
        async with self.db.execute(
            """
            SELECT id, date, event_type, description, reaction,
                   duration_minutes, created_at,
                   system_prompt_digest, raw_response,
                   energy_delta, mood_delta, health_delta,
                   context_summary
            FROM persona_daily_events
            WHERE date BETWEEN ? AND ?
            ORDER BY date DESC, created_at DESC
            """,
            (start_date, end_date),
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                DailyEvent(
                    id=row["id"],
                    date=row.get("date", ""),
                    event_type=row["event_type"],
                    description=row["description"],
                    reaction=row["reaction"] or "",
                    duration_minutes=row["duration_minutes"] if row.get("duration_minutes") is not None else 0,
                    created_at=datetime.fromisoformat(row["created_at"]) if row.get("created_at") else None,
                    system_prompt_digest=row.get("system_prompt_digest") or "",
                    raw_response=row.get("raw_response") or "",
                    energy_delta=row.get("energy_delta"),
                    mood_delta=row.get("mood_delta"),
                    health_delta=row.get("health_delta"),
                    context_summary=row.get("context_summary") or "",
                )
                for row in rows
            ]

    async def clear_daily_events(self, date: str) -> None:
        """清空某天的事件"""
        await self.db.execute(
            "DELETE FROM persona_daily_events WHERE date = ?",
            (date,)
        )
        await self.db.commit()

    async def search_events(
        self,
        query: str,
        days: int,
        limit: int,
    ) -> List[DailyEvent]:
        """搜索每日事件，按关键词匹配 description 和 reaction"""
        cutoff_date = (self._wall_now() - timedelta(days=days)).strftime("%Y-%m-%d")
        safe_query = self._sanitize_search_query(query)

        async with self.db.execute(
            """
            SELECT id, date, event_type, description, reaction,
                   duration_minutes, created_at,
                   system_prompt_digest, raw_response,
                   energy_delta, mood_delta, health_delta,
                   context_summary
            FROM persona_daily_events
            WHERE date >= ?
              AND (description LIKE ? ESCAPE '\\' OR reaction LIKE ? ESCAPE '\\')
            ORDER BY date DESC, created_at DESC
            LIMIT ?
            """,
            (cutoff_date, f"%{safe_query}%", f"%{safe_query}%", limit),
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                DailyEvent(
                    id=row["id"],
                    date=row["date"],
                    event_type=row["event_type"],
                    description=row["description"],
                    reaction=row["reaction"] or "",
                    duration_minutes=row["duration_minutes"] if row.get("duration_minutes") is not None else 0,
                    created_at=datetime.fromisoformat(row["created_at"]) if row.get("created_at") else None,
                    system_prompt_digest=row.get("system_prompt_digest") or "",
                    raw_response=row.get("raw_response") or "",
                    energy_delta=row.get("energy_delta"),
                    mood_delta=row.get("mood_delta"),
                    health_delta=row.get("health_delta"),
                    context_summary=row.get("context_summary") or "",
                )
                for row in rows
            ]

    async def get_event_by_id(self, event_id: int) -> Optional[DailyEvent]:
        """按 ID 查询单条事件"""
        async with self.db.execute(
            """
            SELECT id, date, event_type, description, reaction,
                   duration_minutes, created_at,
                   system_prompt_digest, raw_response,
                   energy_delta, mood_delta, health_delta,
                   context_summary
            FROM persona_daily_events
            WHERE id = ?
            """,
            (event_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return None
            return DailyEvent(
                id=row["id"],
                date=row["date"],
                event_type=row["event_type"],
                description=row["description"],
                reaction=row["reaction"] or "",
                duration_minutes=row["duration_minutes"] if row.get("duration_minutes") is not None else 0,
                created_at=datetime.fromisoformat(row["created_at"]) if row.get("created_at") else None,
                system_prompt_digest=row.get("system_prompt_digest") or "",
                raw_response=row.get("raw_response") or "",
                energy_delta=row.get("energy_delta"),
                mood_delta=row.get("mood_delta"),
                health_delta=row.get("health_delta"),
                context_summary=row.get("context_summary") or "",
            )

    async def prune_daily_events(self, keep_days: int) -> int:
        """清理 keep_days 天之前的每日事件"""
        cutoff_date = (self._wall_now() - timedelta(days=keep_days)).strftime("%Y-%m-%d")
        cursor = await self.db.execute(
            "DELETE FROM persona_daily_events WHERE date < ?",
            (cutoff_date,)
        )
        await self.db.commit()
        return cursor.rowcount

    # ========== 角色状态 ==========

    async def get_character_state(self) -> CharacterState:
        """获取角色永久状态（结构化 JSON）"""
        async with self.db.execute(
            "SELECT text FROM persona_character_state WHERE id = 1"
        ) as cursor:
            row = await cursor.fetchone()
            raw = row["text"] if row else ""

        if not raw:
            return CharacterState()

        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                try:
                    return CharacterState.model_validate(data)
                except ValidationError as ve:
                    logger.warning(
                        "CharacterState JSON 字段验证失败: %s 原始数据: %s",
                        str(ve), raw[:500],
                    )
        except json.JSONDecodeError:
            pass

        # Phase 1: 旧版纯文本数据 — 记录日志后返回默认值（设计决策：不迁移中间数据）
        logger.warning(
            "旧版 CharacterState 纯文本数据已丢弃，长度=%d — "
            "返回空默认值（Phase 1 设计中明确不迁移中间数据）",
            len(raw),
        )
        return CharacterState()

    async def update_character_state(self, state: CharacterState) -> None:
        """更新角色永久状态（结构化 JSON 存储）"""
        await self.db.execute(
            """
            INSERT INTO persona_character_state (id, text)
            VALUES (1, ?)
            ON CONFLICT(id) DO UPDATE SET text = excluded.text,
                                          updated_at = CURRENT_TIMESTAMP
            """,
            (state.model_dump_json(),)
        )
        await self.db.commit()

    # ========== DM 状态 (Phase 1 Agent 框架 — 已废弃，表已删除) ==========

    async def get_dm_state(self) -> DMState:
        """[已废弃] 获取 DM 工作状态。DMState 表已删除，始终返回空默认值。"""
        return DMState()

    async def update_dm_state(self, state: DMState) -> None:
        """[已废弃] 更新 DM 工作状态。DMState 表已删除，无操作。"""
        pass

    # ========== Story Deck (叙事条目图) ==========

    @staticmethod
    def _row_to_story_deck_entry(row: dict) -> StoryDeckEntry:
        return StoryDeckEntry(
            key=row["key"],
            type=row["type"],
            content=row["content"],
        )

    async def get_story_deck_entry(self, key: str) -> Optional[StoryDeckEntry]:
        """单条精确查询"""
        async with self.db.execute(
            "SELECT key, type, content FROM persona_story_deck WHERE key = ?",
            (key,),
        ) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return None
            return self._row_to_story_deck_entry(row)

    async def list_story_deck_entries(
        self, type: Optional[str] = None, limit: int = 50, offset: int = 0
    ) -> list[StoryDeckEntry]:
        """分页列表查询，返回 key + type + content，可按 type 过滤"""
        if type is not None and type not in VALID_ENTRY_TYPES:
            logger.warning(f"list_story_deck_entries: 无效 type={type}，返回空列表（合法值: entity/detail/plot）")
            return []
        if type is not None:
            async with self.db.execute(
                "SELECT key, type, content FROM persona_story_deck "
                "WHERE type = ? ORDER BY key LIMIT ? OFFSET ?",
                (type, limit, offset),
            ) as cursor:
                rows = await cursor.fetchall()
        else:
            async with self.db.execute(
                "SELECT key, type, content FROM persona_story_deck "
                "ORDER BY key LIMIT ? OFFSET ?",
                (limit, offset),
            ) as cursor:
                rows = await cursor.fetchall()
        return [self._row_to_story_deck_entry(r) for r in rows]

    async def search_story_deck(self, query: str) -> list[StoryDeckEntry]:
        """key + content 子串匹配，返回匹配列表"""
        if not query or not query.strip():
            return []
        safe_query = self._sanitize_search_query(query)
        pattern = f"%{safe_query}%"
        # 精确匹配 key 优先，再子串搜索
        async with self.db.execute(
            "SELECT key, type, content FROM persona_story_deck WHERE key = ?",
            (query,),
        ) as cursor:
            exact_row = await cursor.fetchone()
        exact = [self._row_to_story_deck_entry(exact_row)] if exact_row else []

        async with self.db.execute(
            "SELECT key, type, content FROM persona_story_deck "
            "WHERE key LIKE ? ESCAPE '\\' OR content LIKE ? ESCAPE '\\' "
            "ORDER BY key LIMIT 50",
            (pattern, pattern),
        ) as cursor:
            rows = await cursor.fetchall()
        fuzzy = [self._row_to_story_deck_entry(r) for r in rows]

        # 合并：精确命中排最前，去重
        seen = {e.key for e in exact}
        result = list(exact)
        for e in fuzzy:
            if e.key not in seen:
                result.append(e)
                seen.add(e.key)
        return result

    async def upsert_story_deck_entry(
        self, key: str, type: str, content: str, max_entries: int = 100
    ) -> tuple[bool, Optional[str]]:
        """创建或更新条目。含引用校验和总量上限校验。

        Returns:
            (success, error_reason): success=True 表示操作成功；
            error_reason 在失败时包含原因描述。
        """
        # 校验 type
        if type not in VALID_ENTRY_TYPES:
            return False, f"无效的 type: {type}，必须是 entity/detail/plot"

        # 校验 key 长度：至少 2 个汉字或 3 个 ASCII 字符
        key_len = len(key)
        ascii_count = sum(1 for c in key if ord(c) < 128)
        non_ascii_count = key_len - ascii_count
        if non_ascii_count < 2 and key_len < 3:
            return False, "key 长度不足：至少需要 2 个汉字，或 3 个及以上字符"

        # 校验 content 长度：≤300 字
        if len(content) > 300:
            return False, f"content 超长: {len(content)} > 300 字"

        # 校验 [[key]] 引用完整性
        refs = re.findall(r"\[\[([^\]]+)\]\]", content)
        for ref in refs:
            # 排除自引用
            if ref == key:
                continue
            async with self.db.execute(
                "SELECT 1 FROM persona_story_deck WHERE key = ?", (ref,)
            ) as cursor:
                if await cursor.fetchone() is None:
                    return False, f"引用目标不存在: [[{ref}]]"

        # 事务内检查 exists + 总量上限 + INSERT，防止并发竞态
        try:
            await self.db.execute("BEGIN IMMEDIATE")
            async with self.db.execute(
                "SELECT key FROM persona_story_deck WHERE key = ?", (key,)
            ) as cursor:
                exists = await cursor.fetchone() is not None

            if not exists:
                async with self.db.execute(
                    "SELECT COUNT(*) as cnt FROM persona_story_deck"
                ) as cursor:
                    row = await cursor.fetchone()
                    if row and row["cnt"] >= max_entries:
                        await self.db.execute("ROLLBACK")
                        return False, f"条目总数已达上限 {max_entries}，请先清理旧条目"
                await self.db.execute(
                    "INSERT INTO persona_story_deck (key, type, content) VALUES (?, ?, ?)",
                    (key, type, content),
                )
            else:
                await self.db.execute(
                    """
                    INSERT INTO persona_story_deck (key, type, content)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET content = excluded.content
                    """,
                    (key, type, content),
                )
            await self.db.commit()
            return True, None
        except Exception:
            await self.db.execute("ROLLBACK")
            raise

    async def delete_story_deck_entry(self, key: str) -> tuple[bool, Optional[str], list[str]]:
        """删除条目。含反向引用检查。

        Returns:
            (success, error_reason, backlink_keys): success=True 表示已删除；
            backlink_keys 是引用此 key 的其他条目 key 列表（仅在 success=True 时有意义）。
        """
        # 检查条目是否存在
        entry = await self.get_story_deck_entry(key)
        if entry is None:
            return False, f"条目不存在: {key}", []

        # 反向引用检查
        safe_pattern = self._sanitize_search_query(f"[[{key}]]")
        async with self.db.execute(
            "SELECT key FROM persona_story_deck "
            "WHERE content LIKE ? ESCAPE '\\' AND key != ?",
            (f"%{safe_pattern}%", key),
        ) as cursor:
            rows = await cursor.fetchall()
        backlinks = [r["key"] for r in rows]

        await self.db.execute(
            "DELETE FROM persona_story_deck WHERE key = ?", (key,)
        )
        await self.db.commit()
        return True, None, backlinks

    async def get_linked_entries(self, key: str) -> list[StoryDeckEntry]:
        """一度关联：content 中 [[linked]] + 其他条目 content 中引用此 key"""
        entry = await self.get_story_deck_entry(key)
        if entry is None:
            return []

        # 此条目引用了谁
        refs = re.findall(r"\[\[([^\]]+)\]\]", entry.content)
        # 谁引用了此条目
        safe_pattern = self._sanitize_search_query(f"[[{key}]]")
        async with self.db.execute(
            "SELECT key, type, content FROM persona_story_deck "
            "WHERE content LIKE ? ESCAPE '\\' AND key != ?",
            (f"%{safe_pattern}%", key),
        ) as cursor:
            backlink_rows = await cursor.fetchall()

        seen = {key}
        result: list[StoryDeckEntry] = []
        for ref in refs:
            if ref not in seen:
                ref_entry = await self.get_story_deck_entry(ref)
                if ref_entry:
                    result.append(ref_entry)
                    seen.add(ref)
        for row in backlink_rows:
            if row["key"] not in seen:
                result.append(self._row_to_story_deck_entry(row))
                seen.add(row["key"])
        return result

    async def get_story_deck_count(self) -> int:
        """获取条目总数"""
        async with self.db.execute(
            "SELECT COUNT(*) as cnt FROM persona_story_deck"
        ) as cursor:
            row = await cursor.fetchone()
            return row["cnt"] if row else 0

    # ========== SA 状态 (Phase 1 Agent 框架) ==========

    async def get_sa_state(self) -> SAState:
        """获取 SA 世界设定（单行 JSON blob）"""
        async with self.db.execute(
            "SELECT text FROM persona_sa_state WHERE id = 1"
        ) as cursor:
            row = await cursor.fetchone()
            raw = row["text"] if row else ""

        if not raw:
            return SAState()

        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                try:
                    return SAState.model_validate(data)
                except ValidationError:
                    pass
        except json.JSONDecodeError:
            pass

        return SAState()

    async def update_sa_state(self, state: SAState) -> None:
        """更新 SA 世界设定"""
        await self.db.execute(
            """
            INSERT INTO persona_sa_state (id, text)
            VALUES (1, ?)
            ON CONFLICT(id) DO UPDATE SET text = excluded.text,
                                          updated_at = CURRENT_TIMESTAMP
            """,
            (state.model_dump_json(),)
        )
        await self.db.commit()

    async def get_user_profile(self, user_id: str) -> Optional[UserProfile]:
        async with self.db.execute(
            "SELECT facts, updated_at FROM persona_user_profiles WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return UserProfile(
                user_id=user_id,
                facts=json.loads(row["facts"]) if row.get("facts") else {},
                updated_at=datetime.fromisoformat(row["updated_at"]) if row.get("updated_at") else None
            )

    async def save_user_profile(self, profile: UserProfile) -> None:
        await self.db.execute(
            """
            INSERT INTO persona_user_profiles (user_id, facts, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                facts = excluded.facts,
                updated_at = excluded.updated_at
            """,
            (profile.user_id, json.dumps(profile.facts), self._wall_now().isoformat())
        )
        await self.db.commit()

    async def get_relationship(self, user_id: str) -> Optional[RelationshipState]:
        async with self.db.execute(
            """
            SELECT COALESCE(familiarity, 0.0) AS familiarity,
                   COALESCE(peak_familiarity, 0.0) AS peak_familiarity,
                   COALESCE(intimacy, 0.0) AS intimacy,
                   COALESCE(peak_intimacy, 0.0) AS peak_intimacy,
                   COALESCE(reputation, 100.0) AS reputation,
                   last_interaction_at, last_reputation_recovery_date,
                   last_relationship_decay_applied_at,
                   last_miss_sent_at, updated_at
            FROM persona_user_relationships
            WHERE user_id = ?
            """,
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return RelationshipState(
                user_id=user_id,
                familiarity=row["familiarity"],
                peak_familiarity=row["peak_familiarity"],
                intimacy=row["intimacy"],
                peak_intimacy=row["peak_intimacy"],
                reputation=row["reputation"],
                last_interaction_at=datetime.fromisoformat(row["last_interaction_at"]) if row.get("last_interaction_at") else None,
                last_reputation_recovery_date=(
                    datetime.fromisoformat(row["last_reputation_recovery_date"])
                    if row.get("last_reputation_recovery_date")
                    else None
                ),
                last_relationship_decay_applied_at=(
                    datetime.fromisoformat(row["last_relationship_decay_applied_at"]) if row.get("last_relationship_decay_applied_at") else None
                ),
                last_miss_sent_at=datetime.fromisoformat(row["last_miss_sent_at"]) if row.get("last_miss_sent_at") else None,
                updated_at=datetime.fromisoformat(row["updated_at"]) if row.get("updated_at") else None
            )

    async def init_relationship(self, user_id: str) -> RelationshipState:
        await self.db.execute(
            """
            INSERT OR IGNORE INTO persona_user_relationships
            (user_id, familiarity, peak_familiarity, intimacy, peak_intimacy, reputation,
             last_interaction_at, last_reputation_recovery_date,
             last_relationship_decay_applied_at,
             last_miss_sent_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                0.0,   # familiarity
                0.0,   # peak_familiarity
                0.0,   # intimacy
                0.0,   # peak_intimacy
                100.0, # reputation
                self._wall_now().isoformat(),
                None,
                None,
                None,
                self._wall_now().isoformat(),
            )
        )
        await self.db.commit()
        rel = await self.get_relationship(user_id)
        if rel is None:
            return RelationshipState(user_id=user_id)
        return rel

    async def update_relationship(self, rel: RelationshipState) -> None:
        decay_at = (
            rel.last_relationship_decay_applied_at.isoformat()
            if rel.last_relationship_decay_applied_at
            else None
        )
        miss_at = (
            rel.last_miss_sent_at.isoformat()
            if rel.last_miss_sent_at
            else None
        )
        recovery_at = (
            rel.last_reputation_recovery_date.isoformat()
            if rel.last_reputation_recovery_date
            else None
        )
        await self.db.execute(
            """
            INSERT INTO persona_user_relationships
            (user_id, familiarity, peak_familiarity, intimacy, peak_intimacy, reputation,
             last_interaction_at, last_reputation_recovery_date,
             last_relationship_decay_applied_at,
             last_miss_sent_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                familiarity = excluded.familiarity,
                peak_familiarity = excluded.peak_familiarity,
                intimacy = excluded.intimacy,
                peak_intimacy = excluded.peak_intimacy,
                reputation = excluded.reputation,
                last_interaction_at = excluded.last_interaction_at,
                last_reputation_recovery_date = excluded.last_reputation_recovery_date,
                last_relationship_decay_applied_at = excluded.last_relationship_decay_applied_at,
                last_miss_sent_at = excluded.last_miss_sent_at,
                updated_at = excluded.updated_at
            """,
            (
                rel.user_id,
                rel.familiarity,
                rel.peak_familiarity,
                rel.intimacy,
                rel.peak_intimacy,
                rel.reputation,
                rel.last_interaction_at.isoformat()
                if rel.last_interaction_at
                else self._wall_now().isoformat(),
                recovery_at,
                decay_at,
                miss_at,
                self._wall_now().isoformat(),
            )
        )
        await self.db.commit()

    async def try_daily_reputation_recovery(
        self, rel: RelationshipState, now: datetime,
        *, persist: bool = True,
    ) -> bool:
        """执行 reputation 每日恢复。返回是否发生了恢复。

        persist=True（默认）时立即持久化，适用于 session.py 和 proactive_scheduler.py
        等恢复后方法即返回的场景。
        persist=False 时仅修改内存，由调用方在后续统一 update_relationship 时持久化，
        适用于 on_interaction 等已有后续持久化的路径。
        """
        if rel.reputation >= 100.0:
            return False
        today = now.strftime("%Y-%m-%d")
        last_recovery = rel.last_reputation_recovery_date
        last_date = (
            last_recovery.strftime("%Y-%m-%d") if last_recovery else None
        )
        if last_date == today:
            return False
        rel.reputation = min(100.0, rel.reputation + 2.0)
        rel.last_reputation_recovery_date = now
        if persist:
            await self.update_relationship(rel)
        return True

    async def get_familiarity_daily(self, user_id: str, date: str) -> float:
        """获取用户指定日期的 familiarity 累计值。"""
        async with self.db.execute(
            "SELECT total FROM persona_familiarity_daily WHERE user_id = ? AND date = ?",
            (user_id, date),
        ) as cursor:
            row = await cursor.fetchone()
            return row["total"] if row and row.get("total") is not None else 0.0

    async def add_familiarity_daily(
        self, user_id: str, date: str, delta: float, cap: float = 15.0
    ) -> float:
        """原子递增 familiarity 日累计，返回递增后的 total（不超过 cap）。
        若增量会超过 cap，则截断到 cap。
        """
        await self.db.execute(
            """
            INSERT INTO persona_familiarity_daily (user_id, date, total)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, date) DO UPDATE SET
                total = MIN(?, total + excluded.total)
            """,
            (user_id, date, delta, cap),
        )
        await self.db.commit()
        return await self.get_familiarity_daily(user_id, date)

    async def get_top_relationships(self, limit: int = 10) -> List[RelationshipState]:
        async with self.db.execute(
            """
            SELECT user_id,
                   COALESCE(familiarity, 0.0) AS familiarity,
                   COALESCE(peak_familiarity, 0.0) AS peak_familiarity,
                   COALESCE(intimacy, 0.0) AS intimacy,
                   COALESCE(peak_intimacy, 0.0) AS peak_intimacy,
                   COALESCE(reputation, 100.0) AS reputation,
                   last_interaction_at, last_reputation_recovery_date,
                   last_relationship_decay_applied_at,
                   last_miss_sent_at, updated_at
            FROM persona_user_relationships
            ORDER BY (familiarity * 0.6 + intimacy * 0.4) DESC
            LIMIT ?
            """,
            (limit,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                RelationshipState(
                    user_id=row["user_id"],
                    familiarity=row["familiarity"],
                    peak_familiarity=row["peak_familiarity"],
                    intimacy=row["intimacy"],
                    peak_intimacy=row["peak_intimacy"],
                    reputation=row["reputation"],
                    last_interaction_at=datetime.fromisoformat(row["last_interaction_at"]) if row.get("last_interaction_at") else None,
                    last_relationship_decay_applied_at=(
                        datetime.fromisoformat(row["last_relationship_decay_applied_at"]) if row.get("last_relationship_decay_applied_at") else None
                    ),
                    last_miss_sent_at=datetime.fromisoformat(row["last_miss_sent_at"]) if row.get("last_miss_sent_at") else None,
                    updated_at=datetime.fromisoformat(row["updated_at"]) if row.get("updated_at") else None
                )
                for row in rows
            ]

    # ========== 群活跃度相关 ==========

    async def get_group_activity(self, group_id: str) -> GroupActivity:
        """
        获取群活跃度（惰性计算，带衰减）

        衰减策略：
        - 24小时内有互动（@bot/AI回复）→ 不衰减
        - 无互动 → 按天衰减

        Returns:
            GroupActivity 对象
        """
        async with self.db.execute(
            """
            SELECT score, last_interaction_at
            FROM persona_group_activity WHERE group_id = ?
            """,
            (group_id,)
        ) as cursor:
            row = await cursor.fetchone()

            if not row:
                return GroupActivity(group_id=group_id)

            score = row["score"]
            last_interaction = datetime.fromisoformat(row["last_interaction_at"]) if row.get("last_interaction_at") else None

            now = self._wall_now()
            decay = self._calculate_decay(now, last_interaction)
            if decay > 0:
                score = max(0.0, score - decay)

            return GroupActivity(
                group_id=group_id,
                score=score,
                last_interaction_at=last_interaction,
            )

    def _calculate_decay(
        self,
        now: datetime,
        last_interaction: Optional[datetime],
    ) -> float:
        """
        计算衰减量

        Returns:
            应衰减的分数
        """
        if last_interaction:
            hours_since_interaction = (now - last_interaction).total_seconds() / 3600
            if hours_since_interaction < 24.0:
                return 0.0

            days_since = (now - last_interaction).days
            if days_since <= 0:
                days_since = 1
            return float(days_since) * self._group_activity_decay_per_day

        # 新群，不衰减
        return 0.0

    async def update_group_activity(
        self,
        group_id: str,
        score_delta: float = 2.0,
        max_daily_add: float = 20.0,
        is_whitelisted: bool = False,
    ) -> GroupActivity:
        """
        更新群活跃度（互动类型：@bot/AI回复）

        衰减策略：
        - 24小时内有互动 → 不衰减
        - 无互动 → 按天衰减

        Args:
            group_id: 群ID
            score_delta: 每次互动增加的分数
            max_daily_add: 每天最多增加的分数（按自然日累计）
            is_whitelisted: 是否在白名单（有下限保护）

        Returns:
            更新后的 GroupActivity
        """
        async with self.db.execute(
            """
            SELECT score, last_interaction_at,
                   daily_add_date, daily_add_total
            FROM persona_group_activity
            WHERE group_id = ?
            """,
            (group_id,),
        ) as cursor:
            row = await cursor.fetchone()

        today_s = self._wall_now().strftime("%Y-%m-%d")
        if not row:
            raw_score = 50.0
            last_interaction: Optional[datetime] = None
            daily_add_date: Optional[str] = None
            daily_add_total = 0.0
        else:
            raw_score = float(row["score"])
            last_interaction = datetime.fromisoformat(row["last_interaction_at"]) if row.get("last_interaction_at") else None
            daily_add_date = row["daily_add_date"]
            daily_add_total = float(row["daily_add_total"]) if row.get("daily_add_total") is not None else 0.0

        now = self._wall_now()
        decay = self._calculate_decay(now, last_interaction)
        score = max(0.0, raw_score - decay)

        # 检查每日加分限额
        if daily_add_date == today_s:
            today_added = daily_add_total
        else:
            today_added = 0.0

        actual_add = min(score_delta, max(0.0, max_daily_add - today_added))
        score_after_add = min(100.0, score + actual_add)

        # 白名单下限保护
        floor = self._group_activity_floor_whitelist
        if is_whitelisted and score_after_add < floor:
            new_score = floor
        else:
            new_score = score_after_add

        new_daily_total = today_added + actual_add

        await self.db.execute(
            """
            INSERT INTO persona_group_activity (
                group_id, score, last_interaction_at,
                daily_add_date, daily_add_total
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(group_id) DO UPDATE SET
                score = excluded.score,
                last_interaction_at = excluded.last_interaction_at,
                daily_add_date = excluded.daily_add_date,
                daily_add_total = excluded.daily_add_total
            """,
            (
                group_id,
                new_score,
                now.isoformat(),
                today_s,
                new_daily_total,
            ),
        )
        await self.db.commit()

        return GroupActivity(
            group_id=group_id,
            score=new_score,
            last_interaction_at=now,
        )

    async def get_all_group_activities(self, min_score: float = 0) -> List[GroupActivity]:
        """获取所有群活跃度（应用衰减）"""
        async with self.db.execute(
            """
            SELECT group_id, score, last_interaction_at
            FROM persona_group_activity
            WHERE score >= ?
            ORDER BY score DESC
            """,
            (min_score,)
        ) as cursor:
            rows = await cursor.fetchall()
            activities = []
            now = self._wall_now()
            for row in rows:
                last_interaction = datetime.fromisoformat(row["last_interaction_at"]) if row.get("last_interaction_at") else None

                decay = self._calculate_decay(now, last_interaction)
                score = max(0.0, row["score"] - decay)

                activity = GroupActivity(
                    group_id=row["group_id"],
                    score=score,
                    last_interaction_at=last_interaction,
                )
                activities.append(activity)
            return activities

    async def list_all_relationships_raw(self) -> List[RelationshipState]:
        """列出所有关系行，无过滤（用于每日衰减批处理等）。"""
        async with self.db.execute(
            """
            SELECT user_id,
                   COALESCE(familiarity, 0.0) AS familiarity,
                   COALESCE(peak_familiarity, 0.0) AS peak_familiarity,
                   COALESCE(intimacy, 0.0) AS intimacy,
                   COALESCE(peak_intimacy, 0.0) AS peak_intimacy,
                   COALESCE(reputation, 100.0) AS reputation,
                   last_interaction_at, last_relationship_decay_applied_at,
                   last_miss_sent_at, updated_at
            FROM persona_user_relationships
            ORDER BY user_id
            """
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                RelationshipState(
                    user_id=row["user_id"],
                    familiarity=row["familiarity"],
                    peak_familiarity=row["peak_familiarity"],
                    intimacy=row["intimacy"],
                    peak_intimacy=row["peak_intimacy"],
                    reputation=row["reputation"],
                    last_interaction_at=datetime.fromisoformat(row["last_interaction_at"]) if row.get("last_interaction_at") else None,
                    last_relationship_decay_applied_at=(
                        datetime.fromisoformat(row["last_relationship_decay_applied_at"]) if row.get("last_relationship_decay_applied_at") else None
                    ),
                    last_miss_sent_at=datetime.fromisoformat(row["last_miss_sent_at"]) if row.get("last_miss_sent_at") else None,
                    updated_at=datetime.fromisoformat(row["updated_at"]) if row.get("updated_at") else None,
                )
                for row in rows
            ]

    async def list_active_relationships(self, min_score: float = 0, active_within_days: int = 30) -> List[RelationshipState]:
        """列出活跃关系记录（用于想念触发等场景）

        Args:
            min_score: 最小综合分数
            active_within_days: 只返回最近 N 天内有互动的关系

        Returns:
            关系状态列表
        """
        cutoff_date = (self._wall_now() - timedelta(days=active_within_days)).isoformat()

        async with self.db.execute(
            """
            SELECT user_id,
                   COALESCE(familiarity, 0.0) AS familiarity,
                   COALESCE(peak_familiarity, 0.0) AS peak_familiarity,
                   COALESCE(intimacy, 0.0) AS intimacy,
                   COALESCE(peak_intimacy, 0.0) AS peak_intimacy,
                   COALESCE(reputation, 100.0) AS reputation,
                   last_interaction_at, last_relationship_decay_applied_at,
                   last_miss_sent_at, updated_at
            FROM persona_user_relationships
            WHERE (familiarity * 0.6 + intimacy * 0.4) >= ?
              AND last_interaction_at >= ?
            ORDER BY last_interaction_at DESC
            """,
            (min_score, cutoff_date)
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                RelationshipState(
                    user_id=row["user_id"],
                    familiarity=row["familiarity"],
                    peak_familiarity=row["peak_familiarity"],
                    intimacy=row["intimacy"],
                    peak_intimacy=row["peak_intimacy"],
                    reputation=row["reputation"],
                    last_interaction_at=datetime.fromisoformat(row["last_interaction_at"]) if row.get("last_interaction_at") else None,
                    last_relationship_decay_applied_at=(
                        datetime.fromisoformat(row["last_relationship_decay_applied_at"]) if row.get("last_relationship_decay_applied_at") else None
                    ),
                    last_miss_sent_at=datetime.fromisoformat(row["last_miss_sent_at"]) if row.get("last_miss_sent_at") else None,
                    updated_at=datetime.fromisoformat(row["updated_at"]) if row.get("updated_at") else None
                )
                for row in rows
            ]

    async def prune_diaries(self, keep_days: int) -> int:
        """清理旧日记，只保留最近 N 天的日记

        Args:
            keep_days: 保留最近 N 天的日记

        Returns:
            删除的记录数
        """
        cutoff_date = (self._wall_now() - timedelta(days=keep_days)).strftime("%Y-%m-%d")

        cursor = await self.db.execute(
            "DELETE FROM persona_diary WHERE date < ?",
            (cutoff_date,)
        )
        await self.db.commit()
        return cursor.rowcount

    async def prune_score_history(self, max_age_days: int) -> int:
        cutoff = (self._wall_now() - timedelta(days=max_age_days)).isoformat()
        cursor = await self.db.execute(
            "DELETE FROM persona_score_history WHERE datetime(created_at) < datetime(?)",
            (cutoff,),
        )
        await self.db.commit()
        return cursor.rowcount

    async def run_cleanup(
        self,
        llm_traces_max_age_days: int,
        score_history_max_age_days: int,
        daily_events_keep_days: int,
        diary_keep_days: int,
        scoring_failures_max_age_days: int,
    ) -> dict:
        """统一清理入口，返回各表删除行数。"""
        results = {}
        results["llm_traces"] = await self.prune_llm_traces(llm_traces_max_age_days)
        results["score_history"] = await self.prune_score_history(score_history_max_age_days)
        results["daily_events"] = await self.prune_daily_events(daily_events_keep_days)
        results["diary"] = await self.prune_diaries(diary_keep_days)
        results["scoring_failures"] = await self.prune_scoring_failures(scoring_failures_max_age_days)
        total = sum(v for v in results.values() if isinstance(v, int))
        if total:
            logger.info(f"Persona 数据清理完成: 共清理 {total} 条记录, 明细={results}")
        return results

    # ========== Session 相关 (persona_session + persona_session_message) ==========

    @staticmethod
    def _row_to_persona_session(row: dict) -> "PersonaSession":
        from .models import PersonaSession
        return PersonaSession(
            session_id=row["session_id"],
            user_id=row["user_id"],
            character_id=row["character_id"],
            static_prompt=row.get("static_prompt") or "",
            static_hash=row.get("static_hash") or "",
            token_budget=row.get("token_budget", DEFAULT_SESSION_TOKEN_BUDGET),
            token_estimate=row.get("token_estimate", 0),
            status=row["status"],
            summary_text=row.get("summary_text") or "",
            cursors_json=row.get("cursors_json") or "{}",
            last_active_at=datetime.fromisoformat(row["last_active_at"]) if row.get("last_active_at") else None,
            created_at=datetime.fromisoformat(row["created_at"]) if row.get("created_at") else None,
        )

    @staticmethod
    def _row_to_persona_session_message(row: dict) -> "PersonaSessionMessage":
        from .models import PersonaSessionMessage
        tool_calls_raw = row.get("tool_calls") or ""
        return PersonaSessionMessage(
            message_id=row["message_id"],
            session_id=row["session_id"],
            role=row["role"],
            content=row["content"],
            tool_calls=tool_calls_raw,
            tool_call_id=row.get("tool_call_id") or "",
            name=row.get("name"),
            sequence=row.get("sequence", 0),
            created_at=datetime.fromisoformat(row["created_at"]) if row.get("created_at") else None,
        )

    async def create_session(
        self,
        user_id: str,
        character_id: str,
        static_prompt: str,
        static_hash: str,
        token_budget: int,
        status: str,
        last_active_at: datetime,
    ) -> "PersonaSession":
        # 不变量：last_active_at 的时区基准必须与 scope 写入侧一致——静默判定
        # _is_silence_expired 对 life scope 用生产 Clock（上海 naive）、chat scope 用
        # UTC 比较本列。registry 热路径走 _create_session，不经此方法。
        from .models import PersonaSession
        now_iso = last_active_at.isoformat()
        cursor = await self.db.execute(
            """
            INSERT INTO persona_session
            (user_id, character_id, static_prompt, static_hash,
             token_budget, token_estimate, status, last_active_at, created_at)
            VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)
            """,
            (user_id, character_id, static_prompt, static_hash,
             token_budget, status, now_iso, now_iso),
        )
        await self.db.commit()
        return PersonaSession(
            session_id=cursor.lastrowid,
            user_id=user_id,
            character_id=character_id,
            static_prompt=static_prompt,
            static_hash=static_hash,
            token_budget=token_budget,
            token_estimate=0,
            status=status,
            last_active_at=last_active_at,
            created_at=last_active_at,
        )

    async def get_active_session(self, user_id: str) -> Optional["PersonaSession"]:
        async with self.db.execute(
            """
            SELECT session_id, user_id, character_id, static_prompt, static_hash,
                   token_budget, token_estimate, status, summary_text, cursors_json, last_active_at, created_at
            FROM persona_session
            WHERE user_id = ? AND status = 'active'
            ORDER BY last_active_at DESC
            LIMIT 1
            """,
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return None
            return self._row_to_persona_session(row)

    async def get_session_by_id(self, session_id: int) -> Optional["PersonaSession"]:
        async with self.db.execute(
            """
            SELECT session_id, user_id, character_id, static_prompt, static_hash,
                   token_budget, token_estimate, status, summary_text, cursors_json, last_active_at, created_at
            FROM persona_session
            WHERE session_id = ?
            """,
            (session_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return None
            return self._row_to_persona_session(row)

    async def update_session(self, session_id: int, **updates: object) -> None:
        # summary_text 不在 allowed 中：摘要写一次不可变，唯一合法写入走
        # ConversationRegistry._ensure_summary_for_scope 的直 UPDATE；此处排除以在
        # 通用更新 API 层强制 write-once，防止误经 update_session 改写摘要。
        allowed = {
            "static_prompt", "static_hash", "token_budget",
            "token_estimate", "status", "last_active_at",
        }
        cols = []
        vals: list = []
        for key, val in updates.items():
            if key not in allowed:
                continue
            cols.append(f"{key} = ?")
            if isinstance(val, datetime):
                vals.append(val.isoformat())
            else:
                vals.append(val)
        if not cols:
            return
        vals.append(session_id)
        await self.db.execute(
            f"UPDATE persona_session SET {', '.join(cols)} WHERE session_id = ?",
            vals,
        )
        await self.db.commit()

    async def delete_session(self, session_id: int) -> None:
        await self.db.execute(
            "DELETE FROM persona_session WHERE session_id = ?",
            (session_id,),
        )
        await self.db.commit()

    async def add_session_messages(
        self, session_id: int, messages: List["PersonaSessionMessage"],
    ) -> None:
        now_iso = self._wall_now().isoformat()
        for msg in messages:
            await self.db.execute(
                """
                INSERT INTO persona_session_message
                (session_id, role, content, tool_calls, tool_call_id, name,
                 sequence, created_at)
                VALUES (?, ?, ?, ?, ?, ?, COALESCE(
                  (SELECT MAX(sequence) + 1 FROM persona_session_message WHERE session_id = ?),
                  1
                ), ?)
                """,
                (session_id, msg.role, msg.content, msg.tool_calls,
                 msg.tool_call_id, msg.name, session_id, msg.created_at.isoformat() if msg.created_at else now_iso),
            )
        await self.db.commit()

    async def get_session_messages(
        self, session_id: int,
    ) -> List["PersonaSessionMessage"]:
        async with self.db.execute(
            """
            SELECT message_id, session_id, role, content, tool_calls,
                   tool_call_id, name, sequence, created_at
            FROM persona_session_message
            WHERE session_id = ?
            ORDER BY sequence ASC
            """,
            (session_id,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [self._row_to_persona_session_message(r) for r in rows]

    # ========== Phase 3: 记忆搜索工具 ==========

    def _sanitize_search_query(self, query: str) -> str:
        r"""转义 LIKE 特殊字符，防止通配符被误解释

        转义规则:
        - \ → \\ (先转义反斜杠本身)
        - % → \%
        - _ → \_
        """
        return query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    async def search_diaries(
        self,
        query: str,
        days: int,
        limit: int,
    ) -> List[Tuple[str, str]]:
        """搜索日记，返回 [(date, snippet), ...]"""
        cutoff_date = (self._wall_now() - timedelta(days=days)).strftime("%Y-%m-%d")
        safe_query = self._sanitize_search_query(query)

        async with self.db.execute(
            """
            SELECT date, content
            FROM persona_diary
            WHERE date >= ? AND content LIKE ? ESCAPE '\\'
            ORDER BY date DESC
            LIMIT ?
            """,
            (cutoff_date, f"%{safe_query}%", limit)
        ) as cursor:
            rows = await cursor.fetchall()
            results = []
            for row in rows:
                date = row["date"]
                raw_content = row.get("content") or ""
                content = raw_content[:200]
                if len(raw_content) > 200:
                    content += "..."
                results.append((date, content))
            return results

    # ========== Phase 4: 用户 LLM 配置（core_db 侧） ==========

    @staticmethod
    def _get_encryption_key() -> Optional[bytes]:
        """从环境变量获取加密密钥，返回 32 字节密钥或 None"""
        secret = os.environ.get("DICE_PERSONA_SECRET")
        if not secret:
            return None
        # 使用 PBKDF2 从密码派生密钥
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"dicepp_persona_static_salt_v1",  # 固定 salt，保证可逆
            iterations=100000,
        )
        key = kdf.derive(secret.encode("utf-8"))
        return base64.urlsafe_b64encode(key)

    @classmethod
    def encrypt_api_key(cls, api_key: str) -> Optional[str]:
        """加密 API Key，返回 base64 编码的密文或 None（空输入/密钥未设置时）"""
        if not api_key:
            return None
        key = cls._get_encryption_key()
        if not key:
            return None
        f = Fernet(key)
        encrypted = f.encrypt(api_key.encode("utf-8"))
        return base64.urlsafe_b64encode(encrypted).decode("ascii")

    @classmethod
    def decrypt_api_key(cls, encrypted_key: Optional[str]) -> Optional[str]:
        """解密 API Key，返回明文或 None（空输入/解密失败时）"""
        if not encrypted_key:
            return None
        key = cls._get_encryption_key()
        if not key:
            return None
        try:
            f = Fernet(key)
            encrypted_bytes = base64.urlsafe_b64decode(encrypted_key.encode("ascii"))
            decrypted = f.decrypt(encrypted_bytes)
            return decrypted.decode("utf-8")
        except Exception:
            logger.warning("API Key 解密失败", exc_info=True)
            return None

    async def get_user_llm_config(self, user_id: str) -> Optional[UserLLMConfig]:
        """获取用户 LLM 配置（自动解密 API Key）"""
        async with self._core_db.execute(
            """
            SELECT user_id, primary_api_key_encrypted, primary_base_url, primary_model,
                   auxiliary_api_key_encrypted, auxiliary_base_url, auxiliary_model, updated_at
            FROM persona_user_llm_config
            WHERE user_id = ?
            """,
            (user_id,)
        ) as cursor:
            raw_row = await cursor.fetchone()
            if not raw_row:
                return None
            cols = [desc[0] for desc in cursor.description]
            row = dict(zip(cols, raw_row))

            # 解密 API Keys
            primary_enc = row.get("primary_api_key_encrypted")
            auxiliary_enc = row.get("auxiliary_api_key_encrypted")
            primary_key = self.decrypt_api_key(primary_enc) if primary_enc else None
            auxiliary_key = self.decrypt_api_key(auxiliary_enc) if auxiliary_enc else None

            decrypt_failed = bool(
                (primary_enc and primary_key is None) or (auxiliary_enc and auxiliary_key is None)
            )

            return UserLLMConfig(
                user_id=row["user_id"],
                primary_api_key=primary_key or "",
                primary_base_url=row.get("primary_base_url") or "",
                primary_model=row.get("primary_model") or "",
                auxiliary_api_key=auxiliary_key or "",
                auxiliary_base_url=row.get("auxiliary_base_url") or "",
                auxiliary_model=row.get("auxiliary_model") or "",
                updated_at=datetime.fromisoformat(row["updated_at"]) if row.get("updated_at") else None,
                decrypt_failed=decrypt_failed,
            )

    async def save_user_llm_config(self, config: UserLLMConfig) -> bool:
        """保存用户 LLM 配置（自动加密 API Key）

        Returns:
            是否成功（加密密钥未设置时返回 False）
        """
        # 加密 API Keys（内存中为明文，存储前加密）
        primary_encrypted = self.encrypt_api_key(config.primary_api_key)
        if primary_encrypted is None and config.primary_api_key:
            return False

        auxiliary_encrypted = self.encrypt_api_key(config.auxiliary_api_key)
        if auxiliary_encrypted is None and config.auxiliary_api_key:
            return False

        await self._core_db.execute(
            """
            INSERT INTO persona_user_llm_config
            (user_id, primary_api_key_encrypted, primary_base_url, primary_model,
             auxiliary_api_key_encrypted, auxiliary_base_url, auxiliary_model, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                primary_api_key_encrypted = excluded.primary_api_key_encrypted,
                primary_base_url = excluded.primary_base_url,
                primary_model = excluded.primary_model,
                auxiliary_api_key_encrypted = excluded.auxiliary_api_key_encrypted,
                auxiliary_base_url = excluded.auxiliary_base_url,
                auxiliary_model = excluded.auxiliary_model,
                updated_at = excluded.updated_at
            """,
            (
                config.user_id,
                primary_encrypted,
                config.primary_base_url,
                config.primary_model,
                auxiliary_encrypted,
                config.auxiliary_base_url,
                config.auxiliary_model,
                self._wall_now().isoformat(),
            )
        )
        await self._core_db.commit()
        return True

    async def clear_user_llm_config(self, user_id: str) -> bool:
        """清除用户 LLM 配置

        Returns:
            是否成功清除（配置不存在也返回 True）
        """
        await self._core_db.execute(
            "DELETE FROM persona_user_llm_config WHERE user_id = ?",
            (user_id,)
        )
        await self._core_db.commit()
        return True

    # ========== Agent Runtime (Phase M1) ==========

    async def insert_agent_run(
        self,
        run_id: str,
        interaction_id: str,
        user_id: str,
        group_id: str,
        agent_name: str = "",
        run_tag: str = "",
        *,
        started_at: Optional[str] = None,
    ) -> None:
        """创建 agent run 记录。"""
        now = started_at or self._wall_now().isoformat()
        await self.db.execute(
            """
            INSERT INTO persona_agent_runs
                (run_id, interaction_id, user_id, group_id, agent_name, run_tag, started_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, interaction_id, user_id, group_id, agent_name, run_tag, now),
        )
        await self.db.commit()

    async def update_agent_run(
        self,
        run_id: str,
        **updates: Any,
    ) -> None:
        """更新 agent run 记录。支持字段：status, finished_at,
        completion_kind, completion_code, completion_message,
        provider, model, tokens_in, tokens_out, tool_rounds,
        warning_count, sink_failure_count, error。"""
        allowed = {
            "status", "finished_at",
            "completion_kind", "completion_code", "completion_message",
            "provider", "model",
            "tokens_in", "tokens_out", "tool_rounds",
            "warning_count", "sink_failure_count", "error",
        }
        cols = []
        vals: list = []
        for key, val in updates.items():
            if key not in allowed:
                logger.warning(f"update_agent_run: 忽略未知字段 {key}")
                continue
            cols.append(f"{key} = ?")
            vals.append(val)
        if not cols:
            return
        vals.append(run_id)
        await self.db.execute(
            f"UPDATE persona_agent_runs SET {', '.join(cols)} WHERE run_id = ?",
            vals,
        )
        await self.db.commit()

    async def get_agent_run(self, run_id: str) -> Optional[dict]:
        """获取 agent run 记录，返回 dict 或 None。"""
        async with self.db.execute(
            """
            SELECT run_id, interaction_id, user_id, group_id, agent_name, run_tag,
                   status, started_at, finished_at,
                   completion_kind, completion_code, completion_message,
                   provider, model, tokens_in, tokens_out, tool_rounds,
                   warning_count, sink_failure_count, error
            FROM persona_agent_runs WHERE run_id = ?
            """,
            (run_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            return None
        return row  # dict row_factory 已返回与所需 key 一致的 dict

    async def insert_agent_event(
        self,
        run_id: str,
        seq: int,
        event_type: str,
        payload_json: str,
        *,
        created_at: Optional[str] = None,
    ) -> None:
        """写入一条 agent 事件记录。"""
        now = created_at or self._wall_now().isoformat()
        await self.db.execute(
            """
            INSERT INTO persona_agent_events
                (run_id, seq, event_type, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (run_id, seq, event_type, payload_json, now),
        )
        await self.db.commit()

    async def get_agent_events(
        self,
        run_id: str,
    ) -> List[dict]:
        """获取指定 run 的所有事件，按 seq 升序。"""
        async with self.db.execute(
            """
            SELECT id, run_id, seq, event_type, payload_json, schema_version, created_at
            FROM persona_agent_events
            WHERE run_id = ?
            ORDER BY seq ASC
            """,
            (run_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return rows  # dict row_factory 已返回与所需 key 一致的 dict 列表


