"""本地化对话集成测试。"""
import datetime
import pytest

from core.data.models import GroupConfig
from plugins.DicePP.utils.time import datetime_to_str, get_current_date_raw


@pytest.mark.integration
class TestLocalization:
    async def test_chat_greeting_response(self, h):
        await h.send_group("你好", group_id="test_group_a", checker=lambda s: "你好" in s)

    async def test_non_matching_keyword_no_response(self, h):
        """非匹配关键词不应触发随机问候语"""
        await h.send_group("你好123", group_id="test_group_c",
                           checker=lambda s: "你好啊" not in s and "你好呀" not in s)

    async def test_chat_interval_gating(self, h):
        """chat_interval 内发送相同消息不应触发随机问候语"""
        recent_time = get_current_date_raw() - datetime.timedelta(seconds=1)
        await h.bot.db.group_config.upsert(
            GroupConfig(group_id="test_group_a", data={"chat_time": datetime_to_str(recent_time)})
        )
        await h.send_group("你好", group_id="test_group_a",
                           checker=lambda s: "你好啊" not in s and "你好呀" not in s)
