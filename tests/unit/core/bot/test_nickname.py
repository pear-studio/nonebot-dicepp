from unittest.mock import AsyncMock, MagicMock

import pytest

from plugins.DicePP.core.bot.dicebot import Bot


@pytest.mark.asyncio
async def test_get_nickname_falls_back_to_user_id_when_no_record():
    bot = Bot.__new__(Bot)
    bot.db = MagicMock()
    bot.db.nickname.get = AsyncMock(return_value=None)

    assert await bot.get_nickname("user-123", "group-1") == "user-123"
    assert bot.db.nickname.get.await_args_list[-1].args == ("user-123", "origin")
