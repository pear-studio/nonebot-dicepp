import os
from typing import Optional

import aiosqlite

from core.config import DATA_PATH
from .repository import Repository
from .log_repository import LogRepository
from .models import (
    UserKarma,
    InitList,
    Macro,
    Variable,
    DNDCharacter,
    COCCharacter,
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
        self._macro: Optional[Repository[Macro]] = None
        self._variable: Optional[Repository[Variable]] = None
        self._characters_dnd: Optional[Repository[DNDCharacter]] = None
        self._characters_coc: Optional[Repository[COCCharacter]] = None
        self._log: Optional[LogRepository] = None

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
    def macro(self) -> Repository[Macro]:
        if self._macro is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._macro

    @property
    def variable(self) -> Repository[Variable]:
        if self._variable is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._variable

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
        self._macro = None
        self._variable = None
        self._characters_dnd = None
        self._characters_coc = None
        self._log = None

    async def _ensure_all_tables(self) -> None:
        self._karma = Repository[UserKarma](
            self._db, UserKarma, "karma", ["user_id", "group_id"]
        )
        await self._karma._ensure_table()

        self._initiative = Repository[InitList](
            self._db, InitList, "initiative", ["group_id"]
        )
        await self._initiative._ensure_table()

        self._macro = Repository[Macro](
            self._db, Macro, "macro", ["user_id", "name"]
        )
        await self._macro._ensure_table()

        self._variable = Repository[Variable](
            self._db, Variable, "variable", ["user_id", "name"]
        )
        await self._variable._ensure_table()

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
