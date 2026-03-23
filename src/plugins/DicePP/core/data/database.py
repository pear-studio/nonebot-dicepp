import os
from typing import Optional

import aiosqlite

from core.config import BOT_DATA_PATH
from core.data.migrations import MigrationExecutionError, MigrationRunner, default_registry
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
    UserVariable,
    UserFavor,
)


class BotDatabase:
    def __init__(self, bot_id: str):
        self._bot_id = bot_id
        self._bot_dir = os.path.join(BOT_DATA_PATH, bot_id)
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
        self._variable: Optional[Repository[UserVariable]] = None
        self._favor: Optional[Repository[UserFavor]] = None
        self.query: QueryStore = QueryStore()
        self._migration_runner: Optional[MigrationRunner] = None

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

    @property
    def variable(self) -> Repository[UserVariable]:
        if self._variable is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._variable

    @property
    def favor(self) -> Repository[UserFavor]:
        if self._favor is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._favor

    async def connect(self) -> None:
        # allow idempotent connect() (some packaged runs may receive events early)
        if self._db is not None and self._log_db is not None:
            return
        os.makedirs(self._bot_dir, exist_ok=True)

        self._db = await aiosqlite.connect(self._db_path)
        await self._db.execute("PRAGMA journal_mode=WAL;")
        await self._db.execute("PRAGMA synchronous=NORMAL;")
        await self._db.execute("PRAGMA foreign_keys=ON;")

        self._log_db = await aiosqlite.connect(self._log_db_path)
        await self._log_db.execute("PRAGMA journal_mode=WAL;")
        await self._log_db.execute("PRAGMA synchronous=NORMAL;")
        await self._log_db.execute("PRAGMA foreign_keys=ON;")

        self._migration_runner = MigrationRunner(
            db=self._db,
            log_db=self._log_db,
            registry=default_registry(),
        )
        await self._migration_runner.migrate_up()
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
        self._group_activate = None
        self._group_welcome = None
        self._chat_record = None
        self._bot_control = None
        self._user_stat = None
        self._group_stat = None
        self._meta_stat = None
        self._npc_health = None
        self._variable = None
        self._favor = None
        self._migration_runner = None

        # 关闭 query 数据库连接
        await self.query.close_all()

    async def schema_version(self) -> int:
        if self._migration_runner is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return await self._migration_runner.current_version()

    async def target_schema_version(self) -> int:
        if self._migration_runner is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return await self._migration_runner.latest_version()

    async def pending_schema_versions(self) -> list[int]:
        if self._migration_runner is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        pending = await self._migration_runner.pending_migrations()
        return [migration.version for migration in pending]

    async def run_migrations(self) -> None:
        if self._migration_runner is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        try:
            await self._migration_runner.migrate_up()
        except MigrationExecutionError:
            raise

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

        self._variable = Repository[UserVariable](
            self._db, UserVariable, "variable", ["user_id", "group_id", "name"]
        )

        self._favor = Repository[UserFavor](
            self._db, UserFavor, "favor", ["user_id", "group_id"]
        )