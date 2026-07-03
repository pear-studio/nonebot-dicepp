import os
from typing import Optional

import aiosqlite

from core.config.basic import Paths
from core.data.schema import (
    BOT_CORE_TARGET,
    BOT_LOG_TARGET,
    SchemaLifecycleError,
    apply_schema_target,
    current_version,
    pending_versions,
)
from .repository import Repository
from .log_repository import LogRepository
from .query_store import QueryStore
from .models import (
    UserKarma,
    InitList,
    DNDCharacter,
    UserNickname,
    GroupConfig,
    GroupActivate,
    GroupWelcome,
    ChatRecord,
    BotControl,
    UserStat,
    GroupStat,
    MetaStat,
    NPCHealth,
    UserConfig,
)


class BotDatabase:
    def __init__(self, bot_id: str):
        self._bot_id = bot_id
        self._bot_dir = str(Paths.bot_data_dir(bot_id))
        self._db_path = os.path.join(self._bot_dir, "bot_data.db")
        self._log_db_path = os.path.join(self._bot_dir, "log.db")

        self._db: Optional[aiosqlite.Connection] = None
        self._log_db: Optional[aiosqlite.Connection] = None

        self._karma: Optional[Repository[UserKarma]] = None
        self._initiative: Optional[Repository[InitList]] = None
        self._characters_dnd: Optional[Repository[DNDCharacter]] = None
        self._log: Optional[LogRepository] = None
        self._nickname: Optional[Repository[UserNickname]] = None
        self._group_config: Optional[Repository[GroupConfig]] = None
        self._group_activate: Optional[Repository[GroupActivate]] = None
        self._group_welcome: Optional[Repository[GroupWelcome]] = None
        self._chat_record: Optional[Repository[ChatRecord]] = None
        self._bot_control: Optional[Repository[BotControl]] = None
        self._user_stat: Optional[Repository[UserStat]] = None
        self._group_stat: Optional[Repository[GroupStat]] = None
        self._meta_stat: Optional[Repository[MetaStat]] = None
        self._npc_health: Optional[Repository[NPCHealth]] = None
        self._user_config: Optional[Repository[UserConfig]] = None
        self.query: QueryStore = QueryStore()

    @property
    def karma(self) -> Repository[UserKarma]:
        if self._karma is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._karma

    @property
    def initiative(self) -> Repository[InitList]:
        if self._initiative is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._initiative

    @property
    def characters_dnd(self) -> Repository[DNDCharacter]:
        if self._characters_dnd is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._characters_dnd

    @property
    def log(self) -> LogRepository:
        if self._log is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._log

    @property
    def nickname(self) -> Repository[UserNickname]:
        if self._nickname is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._nickname

    @property
    def group_config(self) -> Repository[GroupConfig]:
        if self._group_config is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._group_config

    @property
    def user_config(self) -> Repository[UserConfig]:
        if self._user_config is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._user_config

    @property
    def group_activate(self) -> Repository[GroupActivate]:
        if self._group_activate is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._group_activate

    @property
    def group_welcome(self) -> Repository[GroupWelcome]:
        if self._group_welcome is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._group_welcome

    @property
    def chat_record(self) -> Repository[ChatRecord]:
        if self._chat_record is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._chat_record

    @property
    def bot_control(self) -> Repository[BotControl]:
        if self._bot_control is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._bot_control

    @property
    def user_stat(self) -> Repository[UserStat]:
        if self._user_stat is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._user_stat

    @property
    def group_stat(self) -> Repository[GroupStat]:
        if self._group_stat is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._group_stat

    @property
    def meta_stat(self) -> Repository[MetaStat]:
        if self._meta_stat is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._meta_stat

    @property
    def npc_health(self) -> Repository[NPCHealth]:
        if self._npc_health is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._npc_health

    async def connect(self) -> None:
        # allow idempotent connect() (some packaged runs may receive events early)
        if self._db is not None and self._log_db is not None:
            return
        os.makedirs(self._bot_dir, exist_ok=True)

        apply_schema_target(self._db_path, BOT_CORE_TARGET)
        apply_schema_target(self._log_db_path, BOT_LOG_TARGET)

        self._db = await aiosqlite.connect(self._db_path)
        await self._db.execute("PRAGMA journal_mode=WAL;")
        await self._db.execute("PRAGMA synchronous=NORMAL;")
        await self._db.execute("PRAGMA foreign_keys=ON;")

        self._log_db = await aiosqlite.connect(self._log_db_path)
        await self._log_db.execute("PRAGMA journal_mode=WAL;")
        await self._log_db.execute("PRAGMA synchronous=NORMAL;")
        await self._log_db.execute("PRAGMA foreign_keys=ON;")

        await self._init_repositories()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None
        if self._log_db is not None:
            await self._log_db.close()
            self._log_db = None

        self._karma = None
        self._initiative = None
        self._characters_dnd = None
        self._log = None
        self._nickname = None
        self._group_config = None
        self._user_config = None
        self._group_activate = None
        self._group_welcome = None
        self._chat_record = None
        self._bot_control = None
        self._user_stat = None
        self._group_stat = None
        self._meta_stat = None
        self._npc_health = None

        # 关闭 query 数据库连接
        await self.query.close_all()

    async def schema_version(self) -> int:
        return current_version(self._db_path)

    async def target_schema_version(self) -> int:
        return BOT_CORE_TARGET.latest_version

    async def pending_schema_versions(self) -> list[int]:
        return pending_versions(self._db_path, BOT_CORE_TARGET)

    async def run_migrations(self) -> None:
        try:
            apply_schema_target(self._db_path, BOT_CORE_TARGET)
            apply_schema_target(self._log_db_path, BOT_LOG_TARGET)
        except SchemaLifecycleError:
            raise

    async def hub_get(self, key: str) -> Optional[str]:
        if self._db is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        cursor = await self._db.execute(
            "SELECT data FROM hub_config WHERE key = ?",
            (key,),
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    async def hub_set(self, key: str, value: str) -> None:
        if self._db is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        await self._db.execute(
            """
            INSERT INTO hub_config (key, data, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(key) DO UPDATE SET
                data = excluded.data,
                updated_at = datetime('now')
            """,
            (key, value),
        )
        await self._db.commit()

    async def _init_repositories(self) -> None:
        self._karma = Repository[UserKarma](
            self._db, UserKarma, "karma", ["user_id", "group_id"]
        )

        self._initiative = Repository[InitList](
            self._db, InitList, "initiative", ["group_id"]
        )

        self._characters_dnd = Repository[DNDCharacter](
            self._db, DNDCharacter, "characters_dnd", ["group_id", "user_id"]
        )

        self._log = LogRepository(self._log_db)

        self._nickname = Repository[UserNickname](
            self._db, UserNickname, "nickname", ["user_id", "group_id"]
        )

        self._group_config = Repository[GroupConfig](
            self._db, GroupConfig, "group_config", ["group_id"]
        )

        self._user_config = Repository[UserConfig](
            self._db, UserConfig, "user_config", ["user_id"]
        )

        self._group_activate = Repository[GroupActivate](
            self._db, GroupActivate, "group_activate", ["group_id"]
        )

        self._group_welcome = Repository[GroupWelcome](
            self._db, GroupWelcome, "group_welcome", ["group_id"]
        )

        self._chat_record = Repository[ChatRecord](
            self._db, ChatRecord, "chat_record", ["group_id", "user_id", "time"]
        )

        self._bot_control = Repository[BotControl](
            self._db, BotControl, "bot_control", ["key"]
        )

        self._user_stat = Repository[UserStat](
            self._db, UserStat, "user_stat", ["user_id"]
        )

        self._group_stat = Repository[GroupStat](
            self._db, GroupStat, "group_stat", ["group_id"]
        )

        self._meta_stat = Repository[MetaStat](
            self._db, MetaStat, "meta_stat", ["key"]
        )

        self._npc_health = Repository[NPCHealth](
            self._db, NPCHealth, "npc_health", ["group_id", "name"]
        )
