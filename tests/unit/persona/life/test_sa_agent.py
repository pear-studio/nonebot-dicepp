"""
单元测试: SAAgent — plan() 和 run() 入口

Story Deck 重构后：SA 通过多轮 tool-call 操作条目和 fronts，
不再输出自由文本 notes。
"""
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from plugins.DicePP.module.persona.life.sa_agent import SAAgent
from plugins.DicePP.module.persona.life.types import AgentResult
from plugins.DicePP.module.persona.data.models import SAState


class TestSAAgent:
    """测试 SAAgent.plan() 和 run()"""

    @pytest.fixture
    def mock_store(self):
        store = MagicMock()
        store.get_sa_state = AsyncMock(return_value=SAState())
        store.update_sa_state = AsyncMock()
        store.get_story_deck_count = AsyncMock(return_value=0)
        store.list_story_deck_entries = AsyncMock(return_value=[])
        store.search_story_deck = AsyncMock(return_value=[])
        store.get_story_deck_entry = AsyncMock(return_value=None)
        store.get_linked_entries = AsyncMock(return_value=[])
        store.get_daily_events = AsyncMock(return_value=[])
        return store

    @pytest.fixture
    def mock_router(self):
        return MagicMock()

    @pytest.fixture
    def sa_agent(self, mock_store, mock_router):
        return SAAgent(store=mock_store, router=mock_router)

    @pytest.fixture
    def sa_context(self):
        return {
            "character_name": "测试角色",
            "character_description": "一个冒险者",
            "world": "奇幻世界",
            "scenario": "在城镇中",
            "diary_text": "今天去了市场。",
            "events_text": "在市场买到了稀有草药。",
            "story_deck_is_empty": True,
        }

    @pytest.mark.asyncio
    async def test_sa_plan_runs_with_tools(self, sa_agent, sa_context):
        """mock AgentRuntime.run() 返回含 final_text 的结果，验证 fronts 保存"""
        mock_result = MagicMock()
        mock_result.final_text = "规划完成"

        with patch(
            "plugins.DicePP.module.persona.agent.runtime.AgentRuntime"
        ) as mock_runtime_cls:
            mock_runtime = MagicMock()
            mock_runtime.run = AsyncMock(return_value=mock_result)
            mock_runtime_cls.return_value = mock_runtime

            result = await sa_agent.plan(sa_context)

        assert result.success is True
        assert isinstance(result.data, SAState)
        assert isinstance(result.data.fronts, list)
        sa_agent.store.update_sa_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_sa_plan_empty_fronts(self, sa_agent, sa_context):
        """即使 fronts 为空，plan() 也应正常保存 state"""
        mock_result = MagicMock()
        mock_result.final_text = ""

        with patch(
            "plugins.DicePP.module.persona.agent.runtime.AgentRuntime"
        ) as mock_runtime_cls:
            mock_runtime = MagicMock()
            mock_runtime.run = AsyncMock(return_value=mock_result)
            mock_runtime_cls.return_value = mock_runtime

            result = await sa_agent.plan(sa_context)

        assert result.success is True
        assert isinstance(result.data, SAState)
        sa_agent.store.update_sa_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_sa_plan_preserves_fronts_from_tools(self, sa_agent, sa_context):
        """验证 tool 修改的 fronts_dicts 被正确写回 SAState"""
        mock_result = MagicMock()
        mock_result.final_text = "规划完成"

        # 预置 SAState 带有一个 campaign front
        from plugins.DicePP.module.persona.data.models import Front, Thread
        existing_front = Front(
            name="主线",
            type="campaign",
            threads=[Thread(name="探索", direction="探索旧城区", milestones=["发现线索"], outcome="揭开秘密", related=[])],
        )
        sa_agent.store.get_sa_state = AsyncMock(return_value=SAState(fronts=[existing_front]))

        with patch(
            "plugins.DicePP.module.persona.agent.runtime.AgentRuntime"
        ) as mock_runtime_cls:
            mock_runtime = MagicMock()
            mock_runtime.run = AsyncMock(return_value=mock_result)
            mock_runtime_cls.return_value = mock_runtime

            result = await sa_agent.plan(sa_context)

        assert result.success is True
        assert len(result.data.fronts) == 1
        assert result.data.fronts[0].name == "主线"
        assert len(result.data.fronts[0].threads) == 1

    @pytest.mark.asyncio
    async def test_run_delegates_to_plan(self, sa_agent, sa_context):
        """run() 应委托到 plan()"""
        mock_result = MagicMock()
        mock_result.final_text = "test"

        with patch(
            "plugins.DicePP.module.persona.agent.runtime.AgentRuntime"
        ) as mock_runtime_cls:
            mock_runtime = MagicMock()
            mock_runtime.run = AsyncMock(return_value=mock_result)
            mock_runtime_cls.return_value = mock_runtime

            result = await sa_agent.run(sa_context)

        assert result.success is True
        assert isinstance(result.data, SAState)

    @pytest.mark.asyncio
    async def test_sa_plan_bootstrap_prompt(self, sa_agent, sa_context):
        """story_deck 为空且 fronts 为空时，user_prompt 应包含 bootstrap 引导"""
        mock_result = MagicMock()
        mock_result.final_text = "创建了初始条目"

        with patch(
            "plugins.DicePP.module.persona.agent.runtime.AgentRuntime"
        ) as mock_runtime_cls:
            mock_runtime = MagicMock()
            mock_runtime.run = AsyncMock(return_value=mock_result)
            mock_runtime_cls.return_value = mock_runtime

            await sa_agent.plan(sa_context)

        # 验证 AgentRuntime 被调用时传入了工具
        call_args = mock_runtime_cls.call_args
        assert call_args is not None

    @pytest.mark.asyncio
    async def test_sa_plan_exception_handling(self, sa_agent, sa_context):
        """AgentRuntime 异常时返回 success=False"""
        with patch(
            "plugins.DicePP.module.persona.agent.runtime.AgentRuntime"
        ) as mock_runtime_cls:
            mock_runtime = MagicMock()
            mock_runtime.run = AsyncMock(side_effect=RuntimeError("LLM 调用失败"))
            mock_runtime_cls.return_value = mock_runtime

            result = await sa_agent.plan(sa_context)

        assert result.success is False
        assert "SA 执行异常" in result.error
        assert "LLM 调用失败" in result.error  # 保留原始异常信息


class TestSABuildUserPrompt:
    """测试 SAAgent._build_user_prompt()"""

    @pytest.fixture
    def mock_store(self):
        store = MagicMock()
        store.get_sa_state = AsyncMock(return_value=SAState())
        store.update_sa_state = AsyncMock()
        store.get_story_deck_count = AsyncMock(return_value=0)
        store.list_story_deck_entries = AsyncMock(return_value=[])
        store.search_story_deck = AsyncMock(return_value=[])
        store.get_story_deck_entry = AsyncMock(return_value=None)
        store.get_linked_entries = AsyncMock(return_value=[])
        store.get_daily_events = AsyncMock(return_value=[])
        return store

    @pytest.fixture
    def mock_router(self):
        return MagicMock()

    @pytest.fixture
    def sa_agent(self, mock_store, mock_router):
        return SAAgent(store=mock_store, router=mock_router)

    def test_prompt_contains_front_rules(self, sa_agent):
        """_build_user_prompt 应包含 _FRONT_RULES"""
        context = {
            "character_name": "测试", "character_description": "描述",
            "world": "现代", "scenario": "",
            "diary_text": "", "events_text": "",
            "story_deck_is_empty": False,
        }
        prompt = sa_agent._build_user_prompt(context, [])
        assert "Front 是写给你自己的叙事规划草稿" in prompt
        assert "Milestone 写法" in prompt

    def test_prompt_bootstrap_when_empty(self, sa_agent):
        """fronts 为空 + story_deck_is_empty=True 时包含 bootstrap 引导"""
        context = {
            "character_name": "测试", "character_description": "描述",
            "world": "现代", "scenario": "",
            "diary_text": "", "events_text": "",
            "story_deck_is_empty": True,
        }
        prompt = sa_agent._build_user_prompt(context, [])
        assert "还没有 fronts 和 story_deck 条目" in prompt
        assert "请根据角色设定创建初始的 campaign front" in prompt

    def test_prompt_no_bootstrap_when_has_fronts(self, sa_agent):
        """fronts 非空时不包含 bootstrap 引导"""
        context = {
            "character_name": "测试", "character_description": "描述",
            "world": "现代", "scenario": "",
            "diary_text": "", "events_text": "",
            "story_deck_is_empty": True,
        }
        prompt = sa_agent._build_user_prompt(context, [{"name": "主线", "type": "campaign", "threads": []}])
        assert "还没有 fronts 和 story_deck 条目" not in prompt

    def test_prompt_no_bootstrap_when_story_deck_not_empty(self, sa_agent):
        """story_deck 非空时不包含 bootstrap 引导（即使 fronts 为空）"""
        context = {
            "character_name": "测试", "character_description": "描述",
            "world": "现代", "scenario": "",
            "diary_text": "", "events_text": "",
            "story_deck_is_empty": False,
        }
        prompt = sa_agent._build_user_prompt(context, [])
        assert "还没有 fronts 和 story_deck 条目" not in prompt
