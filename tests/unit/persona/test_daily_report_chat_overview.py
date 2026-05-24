"""_collect_chat_overview 格式化测试 — 验证 persona 聊天概览输出格式"""

import pytest
from unittest.mock import MagicMock
from datetime import timedelta

from plugins.DicePP.module.persona.report.daily_report import DailyReportGenerator
from plugins.DicePP.module.persona.wall_clock import persona_wall_now


class MockConfig:
    timezone = "Asia/Shanghai"


def _yesterday() -> str:
    return (persona_wall_now("Asia/Shanghai") - timedelta(days=1)).strftime("%Y-%m-%d")


class TestCollectChatOverview:

    @pytest.mark.asyncio
    async def test_full_output_format(self, temp_db):
        """正常数据 → 完整格式化输出"""
        from plugins.DicePP.core.message_types import MessageType

        y = _yesterday()
        timestamp = f"{y}T10:00:00"

        await temp_db.add_message_stream("u1", "g1", "assistant", MessageType.CHAT, "hi", "Bot")
        await temp_db.add_message_stream("u2", "g1", "user", MessageType.CHAT, "hello", "Alice")
        await temp_db.add_message_stream("u2", "g1", "user", MessageType.CHAT, "world", "Alice")
        await temp_db.add_message_stream("u3", "g2", "user", MessageType.CHAT, "hey", "Bob")
        # 修正所有消息的 created_at 到 yesterday
        await temp_db.db.execute("UPDATE message_stream SET created_at = ?", (timestamp,))
        await temp_db.db.commit()

        gen = DailyReportGenerator(
            bot=MagicMock(),
            port=MagicMock(),
            store=temp_db,
            config=MockConfig(),
        )
        lines = await gen._collect_chat_overview()

        assert len(lines) == 8
        assert lines[0] == "聊天消息: 4 条（Bot 回复 1 / 用户发言 3）"
        assert "参与: 3 人" in lines[1]
        assert "新增 3" in lines[1]
        assert "覆盖 2 个群" in lines[1]
        assert lines[2] == "活跃用户 Top 3:"
        assert "Alice" in lines[3]
        assert "Bob" in lines[4]
        assert lines[5] == "活跃群 Top 3:"

    @pytest.mark.asyncio
    async def test_no_new_users_omitted_from_participation_line(self, temp_db):
        """new_users=0 时 '新增 N' 不出现"""
        from plugins.DicePP.core.message_types import MessageType

        y = _yesterday()
        # 两天前的日期，确保在 yesterday 之前
        two_days_ago = (persona_wall_now("Asia/Shanghai") - timedelta(days=2)).strftime("%Y-%m-%d")

        # u1 在两天前聊过 → 不算新增
        await temp_db.add_message_stream("u1", "g1", "user", MessageType.CHAT, "old")
        await temp_db.db.execute(
            "UPDATE message_stream SET created_at = ?", (f"{two_days_ago}T10:00:00",),
        )
        await temp_db.db.commit()
        # u1 昨天又聊了
        await temp_db.add_message_stream("u1", "g1", "user", MessageType.CHAT, "hello")
        await temp_db.db.execute(
            "UPDATE message_stream SET created_at = ? WHERE content = ?",
            (f"{y}T10:00:00", "hello"),
        )
        await temp_db.db.commit()

        gen = DailyReportGenerator(
            bot=MagicMock(),
            port=MagicMock(),
            store=temp_db,
            config=MockConfig(),
        )
        lines = await gen._collect_chat_overview()

        assert "新增" not in lines[1]

    @pytest.mark.asyncio
    async def test_no_top_lists_when_empty(self, temp_db):
        """无聊天消息时 Top 3 标题不出现"""
        gen = DailyReportGenerator(
            bot=MagicMock(),
            port=MagicMock(),
            store=temp_db,
            config=MockConfig(),
        )
        lines = await gen._collect_chat_overview()

        assert len(lines) == 2
        assert lines[0] == "聊天消息: 0 条（Bot 回复 0 / 用户发言 0）"
        assert "参与: 0 人" in lines[1]
        assert not any("Top 3" in l for l in lines)

    @pytest.mark.asyncio
    async def test_store_none_returns_unavailable(self):
        gen = DailyReportGenerator(
            bot=MagicMock(),
            port=MagicMock(),
            store=None,
            config=MockConfig(),
        )
        lines = await gen._collect_chat_overview()
        assert lines == ["数据暂不可用"]

    @pytest.mark.asyncio
    async def test_user_label_format_with_and_without_display_name(self, temp_db):
        """display_name 有值时格式为 'name(id)'，无时仅为 'id'"""
        from plugins.DicePP.core.message_types import MessageType

        y = _yesterday()
        timestamp = f"{y}T10:00:00"

        await temp_db.add_message_stream("u1", "g1", "user", MessageType.CHAT, "a", "Alice")
        await temp_db.add_message_stream("u2", "g1", "user", MessageType.CHAT, "b", "")
        await temp_db.db.execute("UPDATE message_stream SET created_at = ?", (timestamp,))
        await temp_db.db.commit()

        gen = DailyReportGenerator(
            bot=MagicMock(),
            port=MagicMock(),
            store=temp_db,
            config=MockConfig(),
        )
        lines = await gen._collect_chat_overview()

        assert "Alice(u1)" in lines[3]
        assert "u2:" in lines[4] or "u2 " in lines[4]
