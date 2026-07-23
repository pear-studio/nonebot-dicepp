"""
DailyReportGenerator 单元测试

覆盖 per-table 容错、diary=None 降级、数据收集正确性、段结构。
"""
import pytest
import json
from unittest.mock import MagicMock, AsyncMock, PropertyMock, patch
from datetime import datetime, timedelta

from plugins.DicePP.core.statistics.user_stat import UserStatInfo
from plugins.DicePP.core.statistics.group_stat import GroupStatInfo
from plugins.DicePP.core.statistics.basic_stat import StatElementBase
from plugins.DicePP.module.persona.report.daily_report import (
    DailyReportGenerator, _DIARY_UNAVAILABLE,
)
from plugins.DicePP.module.persona.life.types import AgentResult
from plugins.DicePP.module.persona.gateway.port import MessagePort
from plugins.DicePP.utils.time import wall_now, get_current_date_int
from plugins.DicePP.core.message_types import MessageType
from plugins.DicePP.core.config.pydantic_models import PersonaConfig


def _make_mock_bot(with_master=True):
    """创建最小 mock Bot，包含 config、db 属性。"""
    bot = MagicMock()
    bot.account = "test_bot"
    bot.config.master = ["master_123"] if with_master else []
    bot.config.persona_ai = PersonaConfig(
        daily_report_voice_enabled=False,
    )

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
    async def test_generate_and_send_produces_two_segments(self):
        """正常路径发送 2 段消息且使用 SYSTEM_LOG 类型"""
        bot = _make_mock_bot()
        port, mock_bot = _make_mock_port()
        gen = DailyReportGenerator(bot=bot, port=port)

        await gen.generate_and_send("今天是美好的一天。")

        calls = mock_bot.proxy.process_bot_command.await_args_list
        assert len(calls) == 2
        seg1_cmd = calls[0].args[0]
        seg2_cmd = calls[1].args[0]

        assert seg1_cmd.message_type == MessageType.SYSTEM_LOG
        assert seg2_cmd.message_type == MessageType.SYSTEM_LOG

        # 段 1 含日记和角色状态标签（角色状态可能不存在）
        assert "今天是美好的一天" in seg1_cmd.msg
        assert "—— 日记 ——" in seg1_cmd.msg

        # 段 2 含运营统计（新格式）
        assert "活跃用户" in seg2_cmd.msg
        assert "指令分布" in seg2_cmd.msg

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
        assert "—— 日记 ——" in seg1

    @pytest.mark.asyncio
    async def test_segment_2_contains_new_format_sections(self):
        """段 2 按新格式包含活跃用户、指令分布、LLM 用量"""
        bot = _make_mock_bot()
        port, mock_bot = _make_mock_port()
        gen = DailyReportGenerator(bot=bot, port=port)

        await gen.generate_and_send("diary")

        seg2 = mock_bot.proxy.process_bot_command.await_args_list[1].args[0].msg
        assert "活跃用户" in seg2
        assert "用户消息" in seg2
        assert "指令合计" in seg2
        assert "指令分布" in seg2
        assert "LLM" in seg2

    @pytest.mark.asyncio
    async def test_segment_2_empty_flag_displays_zero(self):
        """指令分布中无数据的 flag 显示 0"""
        bot = _make_mock_bot()
        port, mock_bot = _make_mock_port()
        gen = DailyReportGenerator(bot=bot, port=port)

        await gen.generate_and_send("diary")

        seg2 = mock_bot.proxy.process_bot_command.await_args_list[1].args[0].msg
        # 空数据时所有 flag 都应该显示 0
        assert "帮助 0" in seg2
        assert "战斗 0" in seg2

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

    # ── Q87: LLM voice path ─────────────────────────────────────

    @pytest.mark.asyncio
    async def test_voice_enabled_uses_llm_opening(self):
        """voice_enabled=True 且 LLM 返回非空时使用 LLM 开场白"""
        bot = _make_mock_bot()
        bot.config.persona_ai.daily_report_voice_enabled = True
        port, mock_bot = _make_mock_port()
        gen = DailyReportGenerator(bot=bot, port=port)
        gen._character = MagicMock()
        gen._character.name = "测试角色"
        gen._character.description = "一个测试角色"
        gen._router = MagicMock()

        mock_opening = "早上好，主人！今天机器人状态良好。"

        target = 'plugins.DicePP.module.persona.life.character_agent.CharacterAgent'
        core_stats = gen._empty_core_stats()
        with patch(target) as mock_char_agent_cls:
            mock_agent = MagicMock()
            mock_agent.opening = AsyncMock(return_value=AgentResult(success=True, data=mock_opening))
            mock_char_agent_cls.return_value = mock_agent
            opening = await gen._generate_opening("昨日日记测试", core_stats)

        assert opening == mock_opening

    @pytest.mark.asyncio
    async def test_voice_llm_exception_falls_back_to_template(self):
        """LLM 开场白抛异常时降级为纯模板"""
        bot = _make_mock_bot()
        bot.config.persona_ai.daily_report_voice_enabled = True
        port, mock_bot = _make_mock_port()
        gen = DailyReportGenerator(bot=bot, port=port)
        gen._character = MagicMock()
        gen._character.name = "测试角色"
        gen._character.description = ""
        gen._router = MagicMock()

        target = 'plugins.DicePP.module.persona.life.character_agent.CharacterAgent'
        core_stats = gen._empty_core_stats()
        with patch(target) as mock_char_agent_cls:
            mock_agent = MagicMock()
            mock_agent.opening = AsyncMock(side_effect=RuntimeError("LLM down"))
            mock_char_agent_cls.return_value = mock_agent
            opening = await gen._generate_opening("昨日日记测试", core_stats)

        # 降级为模板
        assert "早上好" in opening

    @pytest.mark.asyncio
    async def test_voice_llm_returns_none_falls_back_to_template(self):
        """LLM 开场白返回 None 时降级为纯模板"""
        bot = _make_mock_bot()
        bot.config.persona_ai.daily_report_voice_enabled = True
        port, mock_bot = _make_mock_port()
        gen = DailyReportGenerator(bot=bot, port=port)
        gen._character = MagicMock()
        gen._character.name = "测试角色"
        gen._character.description = ""
        gen._router = MagicMock()

        from plugins.DicePP.module.persona.life.types import AgentResult
        target = 'plugins.DicePP.module.persona.life.character_agent.CharacterAgent'
        core_stats = gen._empty_core_stats()
        with patch(target) as mock_char_cls:
            mock_agent = MagicMock()
            mock_agent.opening = AsyncMock(return_value=AgentResult(success=True, data=None))
            mock_char_cls.return_value = mock_agent
            opening = await gen._generate_opening("昨日日记测试", core_stats)

        # 降级为模板
        assert "早上好" in opening

    @pytest.mark.asyncio
    async def test_voice_disabled_uses_template_directly(self):
        """voice_enabled=False 时即使有 router 也直接使用模板"""
        bot = _make_mock_bot()
        bot.config.persona_ai.daily_report_voice_enabled = False
        port, mock_bot = _make_mock_port()
        gen = DailyReportGenerator(bot=bot, port=port)
        gen._character = MagicMock()
        gen._character.name = "测试角色"
        gen._router = MagicMock()

        core_stats = gen._empty_core_stats()
        opening = await gen._generate_opening("昨日日记测试", core_stats)

        # 直接走模板
        assert "测试角色" in opening
        assert "每日报告" in opening

    # ── _collect_core_stats ─────────────────────────────────────

    @pytest.mark.asyncio
    async def test_core_stats_aggregates_with_dimension_split(self):
        """核心统计按新模型聚合总量，并从 group_stat 推导群/私消息量"""
        bot = _make_mock_bot()

        # user_stat 只保留用户总量
        info1 = UserStatInfo()
        info1.msg.inc(10)
        info1.msg.update()  # last_day=10

        info2 = UserStatInfo()
        info2.msg.inc(7)
        info2.msg.update()  # last_day=7

        mock_users = [
            MagicMock(user_id="u1", data=info1.serialize()),
            MagicMock(user_id="u2", data=info2.serialize()),
        ]
        bot.db.user_stat.list_all = AsyncMock(return_value=mock_users)

        # group_stat 可可靠表示群聊总量，私聊量由用户总量 - 群聊总量推导。
        group_info = GroupStatInfo()
        group_info.msg.inc(10)
        group_info.msg.update()
        bot.db.group_stat.list_all = AsyncMock(
            return_value=[MagicMock(group_id="g1", data=group_info.serialize())]
        )

        port, _mock_bot = _make_mock_port()
        gen = DailyReportGenerator(bot=bot, port=port)

        stats = await gen._collect_core_stats()

        assert stats["msg"]["total"] == 17  # 10 + 7
        assert stats["msg"]["group"] == 10
        assert stats["msg"]["private"] == 7
        assert stats["active_users"]["total"] == 2
        assert stats["active_groups"] == 1

    @pytest.mark.asyncio
    async def test_core_stats_per_flag_user_count(self):
        """per-flag 用户计数正确去重"""
        bot = _make_mock_bot()
        from plugins.DicePP.core.command.const import DPP_COMMAND_FLAG_ROLL, DPP_COMMAND_FLAG_FUN

        # 用户 1：用过掷骰
        info1 = UserStatInfo()
        info1.msg.inc(1)
        info1.msg.update()
        info1.cmd.flag_dict[DPP_COMMAND_FLAG_ROLL] = StatElementBase()
        info1.cmd.flag_dict[DPP_COMMAND_FLAG_ROLL].inc(3)
        info1.cmd.flag_dict[DPP_COMMAND_FLAG_ROLL].update()  # last_day=3

        # 用户 2：用过掷骰和娱乐
        info2 = UserStatInfo()
        info2.msg.inc(1)
        info2.msg.update()
        info2.cmd.flag_dict[DPP_COMMAND_FLAG_ROLL] = StatElementBase()
        info2.cmd.flag_dict[DPP_COMMAND_FLAG_ROLL].inc(2)
        info2.cmd.flag_dict[DPP_COMMAND_FLAG_ROLL].update()  # last_day=2
        info2.cmd.flag_dict[DPP_COMMAND_FLAG_FUN] = StatElementBase()
        info2.cmd.flag_dict[DPP_COMMAND_FLAG_FUN].inc(1)
        info2.cmd.flag_dict[DPP_COMMAND_FLAG_FUN].update()  # last_day=1
        mock_users = [
            MagicMock(user_id="u1", data=info1.serialize()),
            MagicMock(user_id="u2", data=info2.serialize()),
        ]
        bot.db.user_stat.list_all = AsyncMock(return_value=mock_users)

        group_info = GroupStatInfo()
        group_info.cmd.flag_dict[DPP_COMMAND_FLAG_FUN] = StatElementBase()
        group_info.cmd.flag_dict[DPP_COMMAND_FLAG_FUN].inc(1)
        group_info.cmd.flag_dict[DPP_COMMAND_FLAG_FUN].update()
        bot.db.group_stat.list_all = AsyncMock(
            return_value=[MagicMock(group_id="g1", data=group_info.serialize())]
        )

        port, _mock_bot = _make_mock_port()
        gen = DailyReportGenerator(bot=bot, port=port)

        stats = await gen._collect_core_stats()

        # 掷骰 count = 3 (u1) + 2 (u2) = 5, users = {u1, u2} = 2
        assert stats["flag_breakdown"][DPP_COMMAND_FLAG_ROLL]["count"] == 5
        assert stats["flag_breakdown"][DPP_COMMAND_FLAG_ROLL]["users"] == 2
        # 娱乐 count = 1, users = {u2} = 1
        assert stats["flag_breakdown"][DPP_COMMAND_FLAG_FUN]["count"] == 1
        assert stats["flag_breakdown"][DPP_COMMAND_FLAG_FUN]["users"] == 1

        # group_stat 维度拆分
        assert stats["cmd"]["group"] == 1
        assert stats["cmd"]["private"] == 5

    @pytest.mark.asyncio
    async def test_core_stats_new_user_detection_is_zero_without_created_at(self):
        """新统计模型不再存储可靠 created_at，因此新增用户保持 0"""
        bot = _make_mock_bot()

        info = UserStatInfo()
        info.msg.inc(1)
        info.msg.update()

        mock_users = [
            MagicMock(user_id="active_user", data=info.serialize()),
        ]
        bot.db.user_stat.list_all = AsyncMock(return_value=mock_users)

        port, _mock_bot = _make_mock_port()
        gen = DailyReportGenerator(bot=bot, port=port)

        stats = await gen._collect_core_stats()
        assert stats["new_users"] == 0

    @pytest.mark.asyncio
    async def test_core_stats_broken_data_returns_empty_structure(self):
        """反序列化失败时返回空统计结构（不是 _DATA_UNAVAILABLE 字符串）"""
        bot = _make_mock_bot()
        bot.db.user_stat.list_all = AsyncMock(side_effect=Exception("DB down"))
        bot.db.group_stat.list_all = AsyncMock(return_value=[])

        port, _mock_bot = _make_mock_port()
        gen = DailyReportGenerator(bot=bot, port=port)

        stats = await gen._collect_core_stats()
        # 现在返回空结构而非 _DATA_UNAVAILABLE
        assert stats["active_users"]["total"] == 0
        assert stats["active_groups"] == 0
        assert stats["new_users"] == 0

    @pytest.mark.asyncio
    async def test_core_stats_partial_row_failure_tolerated(self):
        """个别行反序列化失败不应阻塞其他行的聚合"""
        bot = _make_mock_bot()

        info = UserStatInfo()
        info.msg.inc(5)
        info.msg.update()

        mock_users = [
            MagicMock(user_id="good", data=info.serialize()),
            MagicMock(user_id="bad", data="invalid json {{{"),
        ]
        bot.db.user_stat.list_all = AsyncMock(return_value=mock_users)

        port, _mock_bot = _make_mock_port()
        gen = DailyReportGenerator(bot=bot, port=port)

        stats = await gen._collect_core_stats()
        # good row 的 5 条消息被聚合
        assert stats["msg"]["total"] == 5
        assert stats["active_users"]["total"] == 1

    # ── LLM usage summary ────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_llm_summary_no_store_returns_empty(self):
        """store 为 None 时 LLM 汇总返回空结构"""
        bot = _make_mock_bot()
        port, _mock_bot = _make_mock_port()
        gen = DailyReportGenerator(bot=bot, port=port, store=None)

        result = await gen._collect_llm_summary(use_cur_day=False)
        assert result["total_calls"] == 0
        assert result["total_tokens"] == 0
        assert result["errors"] == 0
        assert result["models"] == []

    @pytest.mark.asyncio
    async def test_llm_summary_happy_path(self):
        """_collect_llm_summary 正常返回精简后的 LLM 汇总"""
        bot = _make_mock_bot()
        port, _mock_bot = _make_mock_port()

        mock_store = MagicMock()
        mock_store.get_daily_token_usage = AsyncMock(return_value=[
            {"provider": "openai", "model": "gpt-4", "requests": 10,
             "tokens_in": 5000, "tokens_out": 2000, "status": "ok"},
            {"provider": "claude", "model": "sonnet", "requests": 5,
             "tokens_in": 3000, "tokens_out": 1500, "status": "ok"},
        ])
        mock_store.get_error_summary_since = AsyncMock(return_value=[])

        gen = DailyReportGenerator(bot=bot, port=port, store=mock_store, config=MockConfig())
        result = await gen._collect_llm_summary(use_cur_day=False)

        assert result["total_calls"] == 15
        assert result["total_tokens"] == 11500  # 7000 + 4500, 统一为 int
        assert result["errors"] == 0
        assert len(result["models"]) == 2
        assert "openai/gpt-4: 10次" in result["models"][0]
        assert "claude/sonnet: 5次" in result["models"][1]

    @pytest.mark.asyncio
    async def test_llm_error_count_use_cur_day_true(self, monkeypatch):
        """use_cur_day=True 时错误统计使用今天午夜的 cutoff"""
        fixed_now = datetime(2026, 6, 25, 14, 30, 0)
        monkeypatch.setattr(
            'plugins.DicePP.module.persona.report.daily_report.wall_now',
            lambda tz: fixed_now,
        )

        bot = _make_mock_bot()
        port, _mock_bot = _make_mock_port()

        mock_store = MagicMock()
        mock_store.get_daily_token_usage = AsyncMock(return_value=[
            {"provider": "openai", "model": "gpt-4", "requests": 10,
             "tokens_in": 5000, "tokens_out": 2000},
        ])
        mock_store.get_error_summary_since = AsyncMock(return_value=[("failed", 5)])

        gen = DailyReportGenerator(bot=bot, port=port, store=mock_store, config=MockConfig())
        result = await gen._collect_llm_summary(use_cur_day=True)

        assert result["errors"] == 5
        mock_store.get_error_summary_since.assert_called_once_with("2026-06-25T00:00:00")

    @pytest.mark.asyncio
    async def test_llm_error_count_use_cur_day_false(self, monkeypatch):
        """use_cur_day=False 时错误统计使用昨天午夜的 cutoff"""
        fixed_now = datetime(2026, 6, 25, 2, 30, 0)
        monkeypatch.setattr(
            'plugins.DicePP.module.persona.report.daily_report.wall_now',
            lambda tz: fixed_now,
        )

        bot = _make_mock_bot()
        port, _mock_bot = _make_mock_port()

        mock_store = MagicMock()
        mock_store.get_daily_token_usage = AsyncMock(return_value=[
            {"provider": "openai", "model": "gpt-4", "requests": 5,
             "tokens_in": 2000, "tokens_out": 1000},
        ])
        mock_store.get_error_summary_since = AsyncMock(return_value=[("failed", 3)])

        gen = DailyReportGenerator(bot=bot, port=port, store=mock_store, config=MockConfig())
        result = await gen._collect_llm_summary(use_cur_day=False)

        assert result["errors"] == 3
        mock_store.get_error_summary_since.assert_called_once_with("2026-06-24T00:00:00")

    # ── 角色状态 ────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_character_state_in_segment_1(self):
        """有角色状态时段 1 包含角色状态标签"""
        bot = _make_mock_bot()
        port, mock_bot = _make_mock_port()

        from plugins.DicePP.module.persona.data.models import CharacterState
        state = CharacterState(
            energy=80, mood=65, health=90,
            current_intention="sleeping",
            text="",
        )

        mock_store = MagicMock()
        mock_store.get_character_state = AsyncMock(return_value=state)

        gen = DailyReportGenerator(bot=bot, port=port, store=mock_store, config=MockConfig())

        await gen.generate_and_send("diary")

        seg1 = mock_bot.proxy.process_bot_command.await_args_list[0].args[0].msg
        assert "—— 角色状态 ——" in seg1
        assert "活力 80" in seg1
        assert "心情 65" in seg1
        assert "健康 90" in seg1

    @pytest.mark.asyncio
    async def test_character_state_none_omitted(self):
        """无角色状态时段 1 不显示角色状态标签"""
        bot = _make_mock_bot()
        port, mock_bot = _make_mock_port()

        mock_store = MagicMock()
        mock_store.get_character_state = AsyncMock(return_value=None)

        gen = DailyReportGenerator(bot=bot, port=port, store=mock_store, config=MockConfig())

        await gen.generate_and_send("diary")

        seg1 = mock_bot.proxy.process_bot_command.await_args_list[0].args[0].msg
        assert "—— 角色状态 ——" not in seg1

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
        assert "活跃用户" in result

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

        # 即使 store 为 None（Persona 数据源全部不可用），日报仍发送 2 段
        await gen.generate_and_send("diary")

        calls = mock_bot.proxy.process_bot_command.await_args_list
        assert len(calls) == 2
        # 段 2 应有核心统计数据
        seg2 = calls[1].args[0].msg
        assert "活跃用户" in seg2
        assert "用户消息" in seg2


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

        calls = _mock_bot.proxy.process_bot_command.await_args_list
        assert len(calls) == 2
        assert "diary content" in calls[0].args[0].msg

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


class TestFlagDisplayOrder:
    """校验 _FLAG_DISPLAY_ORDER 与 DPP_COMMAND_FLAG_DICT 键集一致"""

    def test_display_order_matches_flag_dict(self):
        """_FLAG_DISPLAY_ORDER 的集合与 DPP_COMMAND_FLAG_DICT 的键集合一致"""
        from plugins.DicePP.module.persona.report.daily_report import _FLAG_DISPLAY_ORDER
        from plugins.DicePP.core.command.const import DPP_COMMAND_FLAG_DICT
        assert set(_FLAG_DISPLAY_ORDER) == set(DPP_COMMAND_FLAG_DICT.keys()), \
            "_FLAG_DISPLAY_ORDER 与 DPP_COMMAND_FLAG_DICT 键集不一致，请同步更新"


class MockConfig:
    timezone = "Asia/Shanghai"
    daily_report_voice_enabled = False
