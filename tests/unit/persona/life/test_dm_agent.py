"""
单元测试: DMAgent — run() 的记录解析、回退逻辑、story_deck 注入
"""
from unittest.mock import AsyncMock, MagicMock, patch
import json
import pytest
from plugins.DicePP.module.persona.life.dm_agent import (
    DMAgent, _STORY_DECK_INJECTION_PREFIX,
)
from plugins.DicePP.module.persona.life.types import AgentResult, EventGenerationResult
from plugins.DicePP.module.persona.data.models import StoryDeckEntry


def _mock_run_result():
    """构建 mock run_result（带 log_if_failed 方法）"""
    r = MagicMock()
    r.log_if_failed = MagicMock()
    return r


class TestDMAgentRun:
    """测试 DMAgent.run() 的 LLM 输出解析和回退"""

    @pytest.fixture
    def mock_store(self):
        store = MagicMock()
        store.list_story_deck_entries = AsyncMock(return_value=[])
        store.get_linked_entries = AsyncMock(return_value=[])
        return store

    @pytest.fixture
    def mock_router(self):
        return MagicMock()

    @pytest.fixture
    def dm_agent(self, mock_store, mock_router):
        return DMAgent(store=mock_store, router=mock_router)

    @pytest.fixture
    def base_context(self):
        return {
            "character_name": "测试角色",
            "character_description": "一个冒险者",
            "world": "奇幻世界",
            "init_scenario_text": "",
            "state_text": "体力50/心情50/健康50",
            "slot_type": "system",
            "chain_depth": 0,
            "follow_up_text": "",
            "diary_context": "",
            "events_context": "",
            "now_str": "12:00",
            "date_str": "2026年01月01日",
        }

    @pytest.mark.asyncio
    async def test_dm_run_parses_valid_json(self, dm_agent, base_context):
        """mock run_structured_collect 返回合法 event JSON，验证 AgentResult.success=True"""
        valid_args = {
            "content": "测试角色在森林里发现了一株发光的草药。",
            "context_summary": "在森林发现发光草药",
            "duration_minutes": 15,
            "energy_delta": -5,
            "mood_delta": 5,
            "health_delta": 0,
        }
        with patch(
            "plugins.DicePP.module.persona.agent.tool_bridge.run_structured_collect",
            new_callable=AsyncMock,
        ) as mock_collect:
            mock_collect.return_value = ([valid_args], _mock_run_result(), [])
            result = await dm_agent.run(base_context)
        assert result.success is True
        assert isinstance(result.data, EventGenerationResult)
        assert "发光" in result.data.description
        assert result.data.energy_delta == -5
        assert result.data.mood_delta == 5

    @pytest.mark.asyncio
    async def test_dm_run_fallback_on_empty_collected(self, dm_agent, base_context):
        """mock run_structured_collect 返回 [] 空列表，验证 fallback"""
        with patch(
            "plugins.DicePP.module.persona.agent.tool_bridge.run_structured_collect",
            new_callable=AsyncMock,
        ) as mock_collect:
            mock_collect.return_value = ([], _mock_run_result(), [])
            result = await dm_agent.run(base_context)
        assert result.success is False
        assert "LLM 未调用工具" in result.error
        assert "正在房间里休息" in result.data.description

    @pytest.mark.asyncio
    async def test_dm_run_fallback_on_malformed_json(self, dm_agent, base_context):
        """mock run_structured_collect 返回残缺 dict（缺少必要字段），验证回退"""
        with patch(
            "plugins.DicePP.module.persona.agent.tool_bridge.run_structured_collect",
            new_callable=AsyncMock,
        ) as mock_collect:
            mock_collect.return_value = ([{"some_other_field": 123}], _mock_run_result(), [])
            result = await dm_agent.run(base_context)
        assert result.success is True
        assert "我正在房间里休息" in result.data.description

    @pytest.mark.asyncio
    async def test_dm_run_story_deck_injection_path(self, dm_agent, base_context):
        """R1 回归测试：story deck 注入路径不因 API 变更而静默失败

        当前 bug：dm_agent.py:276 调用已删除的 pull_notifications()，
        触发 AttributeError 被 except Exception 吞没，注入文本未写入 Conversation。
        验证方式：run() 后检查 _conversation._messages 中应包含注入文本。
        """
        from plugins.DicePP.module.persona.data.models import StoryDeckEntry

        dm_agent.store.list_story_deck_entries = AsyncMock(return_value=[
            StoryDeckEntry(key="老李", type="entity", content="图书管理员"),
        ])
        dm_agent.store.get_linked_entries = AsyncMock(return_value=[])

        context = {
            **base_context,
            "chain_depth": 0,
            "follow_up_text": "老李",
            "events_context": "去了旧图书馆",
        }

        valid_args = {
            "content": "老李从书架上取下一本古籍。",
            "context_summary": "老李找到古籍",
            "duration_minutes": 10,
            "energy_delta": 0,
            "mood_delta": 5,
            "health_delta": 0,
        }
        with patch(
            "plugins.DicePP.module.persona.agent.tool_bridge.run_structured_collect",
            new_callable=AsyncMock,
        ) as mock_collect:
            mock_collect.return_value = ([valid_args], _mock_run_result(), [])
            result = await dm_agent.run(context)

        assert result.success is True
        assert "老李" in result.data.description
        # 核心断言：注入文本应出现在 Conversation 中
        # 当前 bug：pull_notifications() 不存在 → AttributeError → 注入被跳过
        # 修复后：_conversation._messages 应包含 [故事提示 (story_deck)] 注入文本
        assert dm_agent._conversation is not None, (
            "run() 应创建 Conversation"
        )
        injected = any(
            "[故事提示 (story_deck)]" in msg.get("content", "")
            for msg in dm_agent._conversation._messages
        )
        assert injected, (
            "story_deck 注入文本未出现在 Conversation 中——"
            "pull_notifications() 已被移除，需替换为 fetch/apply"
        )


class TestStoryDeckInjection:
    """测试 DMAgent._build_story_deck_injection() 纯辅助方法"""

    @pytest.fixture
    def mock_store(self):
        store = MagicMock()
        store.list_story_deck_entries = AsyncMock(return_value=[])
        store.get_linked_entries = AsyncMock(return_value=[])
        return store

    @pytest.fixture
    def mock_router(self):
        return MagicMock()

    @pytest.fixture
    def dm_agent(self, mock_store, mock_router):
        agent = DMAgent(store=mock_store, router=mock_router)
        agent.config = MagicMock(story_deck_max_injection=3)
        return agent

    @pytest.mark.asyncio
    async def test_injection_skips_when_chain_depth_nonzero(self, dm_agent):
        """chain_depth >= 1 时不注入"""
        result = await dm_agent._build_story_deck_injection(
            {"chain_depth": 1, "follow_up_text": "老李", "events_context": ""}
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_injection_empty_match_text(self, dm_agent):
        """匹配文本为空时不注入"""
        result = await dm_agent._build_story_deck_injection(
            {"chain_depth": 0, "follow_up_text": "", "events_context": ""}
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_injection_no_matching_entries(self, dm_agent, mock_store):
        """无匹配条目时返回 None"""
        mock_store.list_story_deck_entries = AsyncMock(return_value=[
            StoryDeckEntry(key="老李", type="entity", content="图书管理员"),
        ])
        result = await dm_agent._build_story_deck_injection(
            {"chain_depth": 0, "follow_up_text": "小明", "events_context": "去公园"}
        )
        assert result is None  # "老李" 不在匹配文本中

    @pytest.mark.asyncio
    async def test_injection_normal_match(self, dm_agent, mock_store):
        """匹配文本中包含条目 key 时正常注入"""
        mock_store.list_story_deck_entries = AsyncMock(return_value=[
            StoryDeckEntry(key="老李", type="entity", content="市立图书馆管理员，50多岁"),
        ])
        result = await dm_agent._build_story_deck_injection(
            {"chain_depth": 0, "follow_up_text": "老李", "events_context": "去了图书馆"}
        )
        assert result is not None
        assert _STORY_DECK_INJECTION_PREFIX in result
        assert "老李" in result
        assert "市立图书馆管理员" in result

    @pytest.mark.asyncio
    async def test_injection_type_priority_sort(self, dm_agent, mock_store):
        """plot 类型优先于 entity 和 detail"""
        mock_store.list_story_deck_entries = AsyncMock(return_value=[
            StoryDeckEntry(key="图书馆", type="detail", content="旧城区的小图书馆"),
            StoryDeckEntry(key="老李", type="entity", content="图书馆管理员"),
            StoryDeckEntry(key="旧信件", type="plot", content="神秘来信"),
        ])
        result = await dm_agent._build_story_deck_injection(
            {"chain_depth": 0, "follow_up_text": "老李 旧信件 图书馆",
             "events_context": "去了旧城区"}
        )
        assert result is not None
        # plot 应该排在 entity 和 detail 之前
        lines = result.split("\n")
        injected_lines = [l for l in lines if l.startswith("- ")]
        assert "plot" in injected_lines[0]  # 第一条应是 plot

    @pytest.mark.asyncio
    async def test_injection_trim_to_max(self, dm_agent, mock_store):
        """裁剪到 max_injection 限制"""
        mock_store.list_story_deck_entries = AsyncMock(return_value=[
            StoryDeckEntry(key=f"条目{i}", type="entity", content=f"内容{i}")
            for i in range(5)
        ])
        # 覆盖 fixture 默认的 max_injection=3 为 2
        dm_agent.config.story_deck_max_injection = 2

        match_text = " ".join([f"条目{i}" for i in range(5)])
        result = await dm_agent._build_story_deck_injection(
            {"chain_depth": 0, "follow_up_text": match_text, "events_context": ""}
        )
        assert result is not None
        # 应该只有 2 条注入
        injected_lines = [l for l in result.split("\n") if l.strip().startswith("- ")]
        assert len(injected_lines) == 2

    @pytest.mark.asyncio
    async def test_injection_dedup(self, dm_agent, mock_store):
        """已注入的 key 不应重复出现"""
        mock_store.list_story_deck_entries = AsyncMock(return_value=[
            StoryDeckEntry(key="老李", type="entity", content="图书管理员"),
            StoryDeckEntry(key="图书馆", type="detail", content="旧图书馆"),
        ])
        # 模拟 Conversation 中已有老李的注入
        from plugins.DicePP.module.persona.life.conversation import Conversation
        conv = Conversation()
        conv.add_user(
            f"{_STORY_DECK_INJECTION_PREFIX}\n- 老李 (entity)：图书管理员\n"
        )
        dm_agent._conversation = conv

        result = await dm_agent._build_story_deck_injection(
            {"chain_depth": 0, "follow_up_text": "老李 图书馆",
             "events_context": "去了旧图书馆"}
        )
        assert result is not None
        # "老李" 已注入，不应再出现
        injected_text = "\n".join(
            [l for l in result.split("\n") if l.strip().startswith("- ")]
        )
        assert "老李" not in injected_text
        assert "图书馆" in injected_text  # 图书馆未注入过


class TestDMBuildUserPrompt:
    """R3/R6: _build_user_prompt 的 init_scenario_text 渲染行为"""

    @pytest.fixture
    def mock_store(self):
        return MagicMock()

    @pytest.fixture
    def mock_router(self):
        return MagicMock()

    @pytest.fixture
    def dm_agent(self, mock_store, mock_router):
        return DMAgent(store=mock_store, router=mock_router)

    def test_init_scenario_text_empty(self, dm_agent):
        """init_scenario_text 为空时不注入场景段落"""
        context = {
            "init_scenario_text": "",
            "diary_context": "",
            "events_context": "",
            "now_str": "12:00",
            "date_str": "2026-01-01",
            "chain_depth": 0,
            "follow_up_text": "",
        }
        prompt = dm_agent._build_user_prompt(context)
        assert "【场景】" not in prompt
        assert "初始场景" not in prompt

    def test_init_scenario_text_nonempty_depth_zero(self, dm_agent):
        """init_scenario_text 非空且 depth=0 时注入【场景】段落"""
        context = {
            "init_scenario_text": "在古老遗迹中",
            "diary_context": "",
            "events_context": "",
            "now_str": "12:00",
            "date_str": "2026-01-01",
            "chain_depth": 0,
            "follow_up_text": "",
        }
        prompt = dm_agent._build_user_prompt(context)
        assert "【场景】" in prompt
        assert "在古老遗迹中" in prompt

    def test_init_scenario_text_nonempty_depth_one(self, dm_agent):
        """init_scenario_text 非空且 depth=1 时清空后不应出现（由 character_life 的 R3 修复保证）

        此测试验证 dm_agent 层面对 init_scenario_text 字段的处理不区分 depth——
        depth 过滤是 character_life 的职责（首次注入后清空 init_scenario_text）。
        dm_agent 本身对任意 depth 都会渲染非空的 init_scenario_text。
        """
        context = {
            "init_scenario_text": "在古老遗迹中",
            "diary_context": "",
            "events_context": "",
            "now_str": "12:00",
            "date_str": "2026-01-01",
            "chain_depth": 1,
            "follow_up_text": "继续探索",
        }
        prompt = dm_agent._build_user_prompt(context)
        # dm_agent 不自行过滤 depth，由 character_life 侧在首次注入后清空保证语义
        assert "【场景】" in prompt
        assert "在古老遗迹中" in prompt
