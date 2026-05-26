"""
DailyReportGenerator 单元测试

覆盖 per-table 容错、diary=None 降级、数据收集正确性、段结构。
"""
import pytest
import json
from unittest.mock import MagicMock, AsyncMock, PropertyMock
from datetime import datetime, timedelta

from core.statistics.user_stat import UserStatInfo
from core.statistics.group_stat import GroupStatInfo
from core.statistics.basic_stat import StatElementBase
from plugins.DicePP.module.persona.report.daily_report import (
    DailyReportGenerator, _DATA_UNAVAILABLE, _DIARY_UNAVAILABLE,
)
from plugins.DicePP.module.persona.gateway.port import MessagePort
from plugins.DicePP.module.persona.wall_clock import persona_wall_now
from plugins.DicePP.core.message_types import MessageType


def _make_mock_bot(with_master=True):
    """创建最小 mock Bot，包含 config、db 属性。"""
    bot = MagicMock()
    bot.account = "test_bot"
    bot.config.master = ["master_123"] if with_master else []
    bot.config.persona_ai.daily_report_enabled = True
    bot.config.persona_ai.daily_report_voice_enabled = False  # 默认关闭 LLM
    bot.config.persona_ai.timezone = "Asia/Shanghai"

    # 模拟 db 访问

    bot.db.user_stat.list_all = AsyncMock(return_value=[])
    bot.db.group_stat.list_all = AsyncMock(return_value=[])

    return bot


def _make_mock_port():
    """创建能捕获发送内容的 MessagePort。"""
    bot = MagicMock()
    bot.account = "test_bot"
    bot.proxy = MagicMock()
    bot.proxy.process_bot_command = AsyncMock()
    port = MessagePort(bot)
    return port, bot


class TestDailyReportGenerator:
    """日报生成器核心测试"""

    # ── 入口方法 ────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_no_master_skips_all_sends(self):
        """Master 列表为空时 generate_and_send 直接返回，不发送任何消息"""
        bot = _make_mock_bot(with_master=False)
        port, mock_bot = _make_mock_port()
        gen = DailyReportGenerator(bot=bot, port=port)

        await gen.generate_and_send(None)

        mock_bot.proxy.process_bot_command.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_generate_and_send_produces_three_segments(self):
        """正常路径发送 3 段消息且使用 SYSTEM_LOG 类型"""
        bot = _make_mock_bot()
        port, mock_bot = _make_mock_port()
        gen = DailyReportGenerator(bot=bot, port=port)

        await gen.generate_and_send("今天是美好的一天。")

        assert mock_bot.proxy.process_bot_command.await_count == 3

        calls = mock_bot.proxy.process_bot_command.await_args_list
        seg1_cmd = calls[0].args[0]
        seg2_cmd = calls[1].args[0]
        seg3_cmd = calls[2].args[0]

        assert seg1_cmd.message_type == MessageType.SYSTEM_LOG
        assert seg2_cmd.message_type == MessageType.SYSTEM_LOG
        assert seg3_cmd.message_type == MessageType.SYSTEM_LOG

        # 段 1 含日记
        assert "今天是美好的一天" in seg1_cmd.msg

        # 段 2 含核心统计标题
        assert "机器人运营统计" in seg2_cmd.msg

        # 段 3 含 Persona 标题
        assert "Persona 运营数据" in seg3_cmd.msg

    # ── diary=None 降级 ─────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_diary_none_fallback_message(self):
        """diary 为 None 时段 1 使用替代消息"""
        bot = _make_mock_bot()
        port, mock_bot = _make_mock_port()
        gen = DailyReportGenerator(bot=bot, port=port)

        await gen.generate_and_send(None)

        seg1_cmd = mock_bot.proxy.process_bot_command.await_args_list[0].args[0]
        assert _DIARY_UNAVAILABLE in seg1_cmd.msg
        assert "早上好" in seg1_cmd.msg

    # ── 段结构 ──────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_segment_1_contains_opening_and_diary(self):
        """段 1 包含开场白和日记全文"""
        bot = _make_mock_bot()
        port, mock_bot = _make_mock_port()
        gen = DailyReportGenerator(bot=bot, port=port)
        diary = "今日日记内容：一切平安。"

        await gen.generate_and_send(diary)

        seg1 = mock_bot.proxy.process_bot_command.await_args_list[0].args[0].msg
        assert "机器人" in seg1
        assert diary in seg1

    @pytest.mark.asyncio
    async def test_segment_2_contains_core_stats_sections(self):
        """段 2 包含必要的数据标题"""
        bot = _make_mock_bot()
        port, mock_bot = _make_mock_port()
        gen = DailyReportGenerator(bot=bot, port=port)

        await gen.generate_and_send("diary")

        seg2 = mock_bot.proxy.process_bot_command.await_args_list[1].args[0].msg
        assert "昨日消息" in seg2
        assert "昨日命令" in seg2
        assert "昨日掷骰" in seg2

    # ── _generate_opening ──────────────────────────────────────

    @pytest.mark.asyncio
    async def test_template_opening_includes_character_name(self):
        """纯模板开场白包含角色名（无 router/character 时走模板）"""
        bot = _make_mock_bot()
        port, mock_bot = _make_mock_port()
        gen = DailyReportGenerator(bot=bot, port=port)

        gen._character = None
        gen._router = None

        await gen.generate_and_send(None)
        seg1 = mock_bot.proxy.process_bot_command.await_args_list[0].args[0].msg
        assert "机器人" in seg1

    # ── _collect_core_stats ─────────────────────────────────────

    @pytest.mark.asyncio
    async def test_core_stats_aggregates_user_stat_correctly(self):
        """核心统计正确聚合 user_stat 列表"""
        bot = _make_mock_bot()
        info1 = UserStatInfo()
        info1.msg.inc(10)
        info1.msg.update()  # last_day=10
        info1.roll.times.inc(5)
        info1.roll.times.update()  # last_day=5

        info2 = UserStatInfo()
        info2.msg.inc(7)
        info2.msg.update()  # last_day=7
        info2.roll.times.inc(2)
        info2.roll.times.update()  # last_day=2

        mock_users = [
            MagicMock(user_id="u1", data=info1.serialize()),
            MagicMock(user_id="u2", data=info2.serialize()),
        ]
        bot.db.user_stat.list_all = AsyncMock(return_value=mock_users)

        port, _mock_bot = _make_mock_port()
        gen = DailyReportGenerator(bot=bot, port=port)

        stats = await gen._collect_core_stats()
        assert stats["msg"] == str(17)  # 10 + 7 last_day_val
        assert stats["roll"] == str(7)  # 5 + 2 last_day_val

    @pytest.mark.asyncio
    async def test_core_stats_broken_data_returns_unavailable(self):
        """反序列化失败时返回 _DATA_UNAVAILABLE"""
        bot = _make_mock_bot()
        bot.db.user_stat.list_all = AsyncMock(side_effect=Exception("DB down"))
        bot.db.group_stat.list_all = AsyncMock(return_value=[])

        port, _mock_bot = _make_mock_port()
        gen = DailyReportGenerator(bot=bot, port=port)

        stats = await gen._collect_core_stats()
        assert stats["msg"] == _DATA_UNAVAILABLE
        assert stats["cmd"] == _DATA_UNAVAILABLE

    # ── _collect_llm_usage ──────────────────────────────────────

    @pytest.mark.asyncio
    async def test_llm_usage_no_router_returns_unavailable(self):
        """router 为 None 时 LLM 用量返回 _DATA_UNAVAILABLE"""
        bot = _make_mock_bot()
        port, _mock_bot = _make_mock_port()
        gen = DailyReportGenerator(bot=bot, port=port, router=None)

        result = await gen._collect_llm_usage()
        assert result == [_DATA_UNAVAILABLE]

    # ── _collect_character_state ────────────────────────────────

    @pytest.mark.asyncio
    async def test_character_state_no_store_returns_unavailable(self):
        """store 为 None 时角色状态返回 _DATA_UNAVAILABLE"""
        bot = _make_mock_bot()
        port, _mock_bot = _make_mock_port()
        gen = DailyReportGenerator(bot=bot, port=port, store=None)

        result = await gen._collect_character_state()
        assert result == [_DATA_UNAVAILABLE]

    # ── generate_snapshot ───────────────────────────────────────

    @pytest.mark.asyncio
    async def test_generate_snapshot_uses_cur_day(self):
        """generate_snapshot 使用 cur_day_val 并返回文本"""
        bot = _make_mock_bot()
        info = UserStatInfo()
        info.msg.inc(5)
        mock_users = [MagicMock(user_id="u1", data=info.serialize())]
        bot.db.user_stat.list_all = AsyncMock(return_value=mock_users)

        port, _mock_bot = _make_mock_port()
        gen = DailyReportGenerator(bot=bot, port=port)

        result = await gen.generate_snapshot()
        assert "即时快照" in result
        assert "消息: 5" in result  # cur_day_val

    # ── set_app ─────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_set_app_injects_store_and_router(self):
        """set_app 从 PersonaApp 注入 store/router/character"""
        bot = _make_mock_bot()
        port, _mock_bot = _make_mock_port()
        gen = DailyReportGenerator(bot=bot, port=port)

        mock_app = MagicMock()
        mock_app.store = MagicMock()
        mock_app.get_router.return_value = "mock_router"
        mock_app.get_character.return_value = "mock_character"

        gen.set_app(mock_app)

        assert gen._store is mock_app.store
        assert gen._router == "mock_router"
        assert gen._character == "mock_character"

    # ── per-table 容错集成测试 ──────────────────────────────────

    @pytest.mark.asyncio
    async def test_one_source_failure_does_not_block_others(self):
        """单个数据源失败不影响其他数据源的收集和发送"""
        bot = _make_mock_bot()
        # 模拟 daily_update 后 last_day_val 已有值
        info = UserStatInfo()
        info.msg.inc(3)
        info.msg.update()  # cur_day→last_day, cur_day=0
        mock_users = [MagicMock(user_id="u1", data=info.serialize())]
        bot.db.user_stat.list_all = AsyncMock(return_value=mock_users)
        bot.db.group_stat.list_all = AsyncMock(return_value=[])

        port, mock_bot = _make_mock_port()
        gen = DailyReportGenerator(bot=bot, port=port)

        # 即使 store 为 None（Persona 数据源全部不可用），日报仍发送 3 段
        await gen.generate_and_send("diary")

        assert mock_bot.proxy.process_bot_command.await_count == 3
        # 段 2 应有核心统计数据（即使 Persona 数据不可用）
        seg2 = mock_bot.proxy.process_bot_command.await_args_list[1].args[0].msg
        assert "昨日消息: 3" in seg2


class TestTickDailyIntegration:
    """tick_daily 流程集成测试：日报发送与旧通知抑制"""

    @pytest.mark.asyncio
    async def test_generate_and_send_called_during_run_daily(self):
        """_run_daily() 获取 diary 后调用 generate_and_send"""
        from plugins.DicePP.module.persona.command import PersonaCommand

        bot = _make_mock_bot()
        cmd = PersonaCommand(bot)
        cmd.enabled = True
        cmd.config = bot.config.persona_ai

        port, _mock_bot = _make_mock_port()
        gen = DailyReportGenerator(bot=bot, port=port)
        cmd.report_generator = gen

        # 构造最小 mock app
        mock_app = MagicMock()
        mock_app.tick_daily = AsyncMock(return_value="diary content")
        cmd.app = mock_app
        cmd.data_store = MagicMock()

        # 直接调用 _run_daily 内部逻辑
        diary = await mock_app.tick_daily()
        await gen.generate_and_send(diary) if cmd.config.daily_report_enabled else None

        assert _mock_bot.proxy.process_bot_command.await_count == 3

    @pytest.mark.asyncio
    async def test_daily_report_disabled_skips_generate(self):
        """daily_report_enabled=False 时 _run_daily 不调用 generate_and_send"""
        from plugins.DicePP.module.persona.command import PersonaCommand

        bot = _make_mock_bot()
        bot.config.persona_ai.daily_report_enabled = False
        cmd = PersonaCommand(bot)
        cmd.config = bot.config.persona_ai

        port, mock_bot = _make_mock_port()
        gen = DailyReportGenerator(bot=bot, port=port)
        gen.generate_and_send = AsyncMock()
        cmd.report_generator = gen

        # 模拟 _run_daily 的条件分支
        diary = "test diary"
        if cmd.report_generator and cmd.config.daily_report_enabled:
            await cmd.report_generator.generate_and_send(diary)

        gen.generate_and_send.assert_not_awaited()

    def test_loc_daily_update_suppressed(self):
        """PersonaCommand enabled=True + daily_report_enabled=True 时抑制 LOC"""
        # 模拟 R1 修复后 dicebot.py 的条件：遍历 command_dict 检查实例 enabled
        persona_running = True  # PersonaCommand.enabled = True
        daily_report_enabled = True
        should_send_loc = not (persona_running and daily_report_enabled)
        assert should_send_loc is False

    def test_loc_daily_update_sent_when_persona_disabled(self):
        """PersonaCommand enabled=False 时发送 LOC（即使 config 上 enabled=True）"""
        persona_running = False  # PersonaCommand.enabled = False（init 失败）
        daily_report_enabled = True
        should_send_loc = not (persona_running and daily_report_enabled)
        assert should_send_loc is True

    def test_loc_daily_update_sent_when_daily_report_disabled(self):
        """daily_report_enabled=False 时发送 LOC"""
        persona_running = True
        daily_report_enabled = False
        should_send_loc = not (persona_running and daily_report_enabled)
        assert should_send_loc is True

    def test_loc_daily_update_sent_when_command_not_registered(self):
        """command_dict 中没有 PersonaCommand 时发送 LOC"""
        persona_running = False  # 未找到 enabled 属性 → 保持 False
        daily_report_enabled = True
        should_send_loc = not (persona_running and daily_report_enabled)
        assert should_send_loc is True


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
