import os
from typing import Optional

import aiosqlite

from core.config import DATA_PATH
from .repository import Repository
from .log_repository import LogRepository
from .query_store import QueryStore
from .models import (
    UserKarma,
    InitList,
    DNDCharacter,
    COCCharacter,
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
        self._bot_dir = os.path.join(DATA_PATH, "Bot", bot_id)
        self._db_path = os.path.join(self._bot_dir, "bot_data.db")
        self._log_db_path = os.path.join(self._bot_dir, "log.db")

        self._db: Optional[aiosqlite.Connection] = None
        self._log_db: Optional[aiosqlite.Connection] = None

        self._karma: Optional[Repository[UserKarma]] = None
        self._initiative: Optional[Repository[InitList]] = None
        self._characters_dnd: Optional[Repository[DNDCharacter]] = None
        self._characters_coc: Optional[Repository[COCCharacter]] = None
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
    def characters_coc(self) -> Repository[COCCharacter]:
        if self._characters_coc is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._characters_coc

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
        os.makedirs(self._bot_dir, exist_ok=True)

        self._db = await aiosqlite.connect(self._db_path)
        await self._db.execute("PRAGMA journal_mode=WAL;")
        await self._db.execute("PRAGMA synchronous=NORMAL;")
        await self._db.execute("PRAGMA foreign_keys=ON;")

        self._log_db = await aiosqlite.connect(self._log_db_path)
        await self._log_db.execute("PRAGMA journal_mode=WAL;")
        await self._log_db.execute("PRAGMA synchronous=NORMAL;")
        await self._log_db.execute("PRAGMA foreign_keys=ON;")

        await self._ensure_all_tables()

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
        self._characters_coc = None
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

        # 关闭 query 数据库连接
        await self.query.close_all()

    async def _ensure_all_tables(self) -> None:
        self._karma = Repository[UserKarma](
            self._db, UserKarma, "karma", ["user_id", "group_id"]
        )
        await self._karma._ensure_table()

        self._initiative = Repository[InitList](
            self._db, InitList, "initiative", ["group_id"]
        )
        await self._initiative._ensure_table()

        self._characters_dnd = Repository[DNDCharacter](
            self._db, DNDCharacter, "characters_dnd", ["group_id", "user_id"]
        )
        await self._characters_dnd._ensure_table()

        self._characters_coc = Repository[COCCharacter](
            self._db, COCCharacter, "characters_coc", ["group_id", "user_id"]
        )
        await self._characters_coc._ensure_table()

        self._log = LogRepository(self._log_db)
        await self._log._ensure_table()

        self._nickname = Repository[UserNickname](
            self._db, UserNickname, "nickname", ["user_id", "group_id"]
        )
        await self._nickname._ensure_table()

        self._group_config = Repository[GroupConfig](
            self._db, GroupConfig, "group_config", ["group_id"]
        )
        await self._group_config._ensure_table()

        self._group_activate = Repository[GroupActivate](
            self._db, GroupActivate, "group_activate", ["group_id"]
        )
        await self._group_activate._ensure_table()

        self._group_welcome = Repository[GroupWelcome](
            self._db, GroupWelcome, "group_welcome", ["group_id"]
        )
        await self._group_welcome._ensure_table()

        self._chat_record = Repository[ChatRecord](
            self._db, ChatRecord, "chat_record", ["group_id", "user_id", "time"]
        )
        await self._chat_record._ensure_table()

        self._bot_control = Repository[BotControl](
            self._db, BotControl, "bot_control", ["key"]
        )
        await self._bot_control._ensure_table()

        self._user_stat = Repository[UserStat](
            self._db, UserStat, "user_stat", ["user_id"]
        )
        await self._user_stat._ensure_table()

        self._group_stat = Repository[GroupStat](
            self._db, GroupStat, "group_stat", ["group_id"]
        )
        await self._group_stat._ensure_table()

        self._meta_stat = Repository[MetaStat](
            self._db, MetaStat, "meta_stat", ["key"]
        )
        await self._meta_stat._ensure_table()

        self._npc_health = Repository[NPCHealth](
            self._db, NPCHealth, "npc_health", ["group_id", "name"]
        )
        await self._npc_health._ensure_table()

        self._variable = Repository[UserVariable](
            self._db, UserVariable, "variable", ["user_id", "group_id", "name"]
        )
        await self._variable._ensure_table()

        self._favor = Repository[UserFavor](
            self._db, UserFavor, "favor", ["user_id", "group_id"]
        )
        await self._favor._ensure_table()