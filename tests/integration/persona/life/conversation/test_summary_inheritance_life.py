"""DM/Character 日界 close -> 次日摘要继承 端到端测试（A3）。

验证：Day1 通过 life.dm/life.character scope 写入消息 ->
tick_daily compact（registry.close，status=closed） ->
Day2 首次 _ensure_conversation（registry.get_or_create）继承 Day1 摘要。

使用 temp_db + FakeSummarizer，绝不调真实 LLM。
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from module.persona.life.conversation import NOTIFICATION_PREFIX
from module.persona.life.conversation_scope import (
    ConversationScope,
    NS_LIFE_DM,
    NS_LIFE_CHARACTER,
)
from module.persona.life.conversation_registry import ConversationRegistry
from module.persona.life.conversation_summary import FakeSummarizer


@pytest.fixture
def mock_runtime_factory():
    """返回 get_or_create 只持有 runtime 不调用 run 的 mock factory。"""
    return MagicMock


class TestLifeScopeSummaryInheritance:
    """life.* scope 的摘要继承端到端验证。"""

    @pytest.mark.asyncio
    async def test_life_dm_inherits_summary_after_compact(self, temp_db):
        """DM (life.dm): Day1 写消息 -> compact(close) -> Day2 get_or_create 继承摘要。"""
        summarizer = FakeSummarizer(return_text="DM 上轮对话摘要")
        registry = ConversationRegistry(
            temp_db,
            runtime_factory=MagicMock,
            summarizer=summarizer,
            character_id_provider=lambda: "char-001",
        )

        dm_scope = ConversationScope.for_life_dm("char-001")

        # Day 1: 获取 conv，写消息（>= SUMMARY_MIN_MESSAGES=4），关闭
        conv_dm = await registry.get_or_create(dm_scope)
        sid1 = int(conv_dm.id)
        for i in range(5):
            conv_dm.add_message("user", f"dm_day1_msg{i}")
        await conv_dm.save()

        # Day 边界: compact = registry.close（模拟 tick_daily compact_conversation）
        await registry.close(dm_scope)

        # 验证旧 session 已 closed
        async with temp_db.db.execute(
            "SELECT status FROM persona_session WHERE session_id=?", (sid1,)
        ) as cur:
            row = await cur.fetchone()
        assert row["status"] == "closed"

        # Day 2: 新 get_or_create -> 应继承 Day1 摘要
        conv_dm2 = await registry.get_or_create(dm_scope)

        # 新 conv 有摘要前缀
        msgs = conv_dm2.get_messages()
        assert len(msgs) >= 1, "新 conv 至少应有摘要前缀"
        summary_msg = msgs[0]
        assert f"{NOTIFICATION_PREFIX} 之前的对话摘要：DM 上轮对话摘要" in str(summary_msg.get("content", ""))

        # 摘要已写入旧 session 的 summary_text
        async with temp_db.db.execute(
            "SELECT summary_text FROM persona_session WHERE session_id=?", (sid1,)
        ) as cur:
            row = await cur.fetchone()
        assert row["summary_text"] == "DM 上轮对话摘要"

        # summarizer 被调用了
        assert len(summarizer.called_with) >= 1

    @pytest.mark.asyncio
    async def test_life_character_inherits_summary_after_compact(self, temp_db):
        """Character (life.character): Day1 写消息 -> compact(close) -> Day2 get_or_create 继承摘要。"""
        summarizer = FakeSummarizer(return_text="Character 上轮摘要")
        registry = ConversationRegistry(
            temp_db,
            runtime_factory=MagicMock,
            summarizer=summarizer,
            character_id_provider=lambda: "char-001",
        )

        char_scope = ConversationScope.for_life_character("char-001")

        # Day 1
        conv_char = await registry.get_or_create(char_scope)
        sid1 = int(conv_char.id)
        for i in range(5):
            conv_char.add_message("assistant", f"char_day1_reaction{i}")
        await conv_char.save()

        # Day 边界: compact
        await registry.close(char_scope)

        async with temp_db.db.execute(
            "SELECT status FROM persona_session WHERE session_id=?", (sid1,)
        ) as cur:
            assert (await cur.fetchone())["status"] == "closed"

        # Day 2
        conv_char2 = await registry.get_or_create(char_scope)

        msgs = conv_char2.get_messages()
        assert len(msgs) >= 1
        summary_msg = msgs[0]
        assert f"{NOTIFICATION_PREFIX} 之前的对话摘要：Character 上轮摘要" in str(summary_msg.get("content", ""))

        # 摘要落库
        async with temp_db.db.execute(
            "SELECT summary_text FROM persona_session WHERE session_id=?", (sid1,)
        ) as cur:
            assert (await cur.fetchone())["summary_text"] == "Character 上轮摘要"

    @pytest.mark.asyncio
    async def test_life_scopes_inherit_independently(self, temp_db):
        """life.dm 和 life.character scope 各自独立继承摘要（隔离验证）。"""
        summarizer = FakeSummarizer(return_text="独立摘要")
        registry = ConversationRegistry(
            temp_db,
            runtime_factory=MagicMock,
            summarizer=summarizer,
            character_id_provider=lambda: "char-001",
        )

        dm_scope = ConversationScope.for_life_dm("char-001")
        char_scope = ConversationScope.for_life_character("char-001")

        # Day 1: 两个 scope 都写消息并关闭
        conv_dm = await registry.get_or_create(dm_scope)
        sid_dm = int(conv_dm.id)
        for i in range(5):
            conv_dm.add_message("user", f"dm_{i}")
        await conv_dm.save()

        conv_char = await registry.get_or_create(char_scope)
        sid_char = int(conv_char.id)
        for i in range(5):
            conv_char.add_message("user", f"char_{i}")
        await conv_char.save()

        # 同时关闭两个 scope
        await registry.close(dm_scope)
        await registry.close(char_scope)

        # Day 2: 各自继承
        conv_dm2 = await registry.get_or_create(dm_scope)
        conv_char2 = await registry.get_or_create(char_scope)

        dm_msgs = conv_dm2.get_messages()
        char_msgs = conv_char2.get_messages()

        # 各自有摘要前缀
        assert any("独立摘要" in m.get("content", "") for m in dm_msgs)
        assert any("独立摘要" in m.get("content", "") for m in char_msgs)

        # 各自落库到自己的旧 session
        for sid in (sid_dm, sid_char):
            async with temp_db.db.execute(
                "SELECT summary_text, scope_namespace FROM persona_session WHERE session_id=?", (sid,)
            ) as cur:
                row = await cur.fetchone()
                assert row["summary_text"] == "独立摘要"

    @pytest.mark.asyncio
    async def test_life_compact_short_session_skips_summary(self, temp_db):
        """短会话（<4 条消息）跳过摘要生成，新 conv 无摘要前缀。"""
        summarizer = FakeSummarizer(return_text="不应出现")
        registry = ConversationRegistry(
            temp_db,
            runtime_factory=MagicMock,
            summarizer=summarizer,
            character_id_provider=lambda: "char-001",
        )

        dm_scope = ConversationScope.for_life_dm("char-001")

        conv = await registry.get_or_create(dm_scope)
        # 只写 2 条消息（< SUMMARY_MIN_MESSAGES=4）
        conv.add_message("user", "msg1")
        conv.add_message("user", "msg2")
        await conv.save()
        await registry.close(dm_scope)

        conv2 = await registry.get_or_create(dm_scope)
        msgs = conv2.get_messages()
        # 不应有摘要前缀
        assert not any("不应出现" in m.get("content", "") for m in msgs)
        # generate_summary 未被调用
        assert len(summarizer.called_with) == 0

    @pytest.mark.asyncio
    async def test_life_compact_no_summarizer_returns_empty(self, temp_db):
        """未配置 summarizer 时 compact close 后新 conv 无摘要。"""
        registry = ConversationRegistry(
            temp_db,
            runtime_factory=MagicMock,
            summarizer=None,
            character_id_provider=lambda: "char-001",
        )

        dm_scope = ConversationScope.for_life_dm("char-001")

        conv = await registry.get_or_create(dm_scope)
        for i in range(5):
            conv.add_message("user", f"msg{i}")
        await conv.save()
        await registry.close(dm_scope)

        conv2 = await registry.get_or_create(dm_scope)
        msgs = conv2.get_messages()
        # 无 summarizer 时，摘要不会被生成或注入
        summary_msgs = [m for m in msgs if NOTIFICATION_PREFIX in m.get("content", "")]
        if summary_msgs:
            # 如果摘要系统回退了更旧摘要，检查不含虚假摘要文本
            for m in summary_msgs:
                assert "摘要" not in m.get("content", "")
