"""
单元测试: CharacterAgent — react() / diary() / share() 入口
"""
from unittest.mock import AsyncMock, MagicMock, patch
import json
import pytest
from plugins.DicePP.module.persona.life.character_agent import CharacterAgent
from plugins.DicePP.module.persona.life.types import AgentResult, EventReactionResult
from plugins.DicePP.module.persona.life.conversation import ConversationRunResult
from plugins.DicePP.module.persona.data.models import CharacterState


_CONV_RUN_PATH = (
    'plugins.DicePP.module.persona.life.conversation.Conversation.run'
)


class TestCharacterAgentReact:
    """测试 CharacterAgent.react()"""

    @pytest.fixture
    def mock_store(self):
        store = MagicMock()
        store.get_character_state = AsyncMock(return_value=CharacterState(energy=50, mood=50, health=50))
        store.update_character_state = AsyncMock()
        return store

    @pytest.fixture
    def mock_router(self):
        return MagicMock()

    @pytest.fixture
    def agent(self, mock_store, mock_router):
        return CharacterAgent(store=mock_store, router=mock_router)

    @pytest.mark.asyncio
    async def test_react_parses_valid_reaction(self, agent):
        """mock conv.run 返回合法 reaction（T4: output_arguments 路径）"""
        with patch(_CONV_RUN_PATH, new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ConversationRunResult(
                final_text="",
                final_reason="output_collected",
                completion_kind="completed",
                output_arguments={'content': '我刚才好像听到什么声音...', 'has_follow_up': True},
            )
            result = await agent.react({
                'event': '远处传来一阵奇怪的声音。',
                'character_name': '测试角色',
                'character_description': '谨慎的冒险者',
                'energy': 50, 'mood': 50, 'health': 50,
            }, interaction_id="test-id")
        assert result.success is True
        assert isinstance(result.data, EventReactionResult)
        assert '声音' in result.data.reaction

    @pytest.mark.asyncio
    async def test_react_fallback_on_empty_collected(self, agent):
        """mock 空 output_arguments，验证回退"""
        with patch(_CONV_RUN_PATH, new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ConversationRunResult(
                final_text="",
                final_reason="output_collected",
                completion_kind="completed",
                output_arguments=None,
            )
            result = await agent.react({
                'event': 'test', 'character_name': '测试角色',
                'character_description': '', 'energy': 50, 'mood': 50, 'health': 50,
            }, interaction_id="test-id")
        assert result.success is False
        assert 'LLM 未调用' in result.error


class TestCharacterAgentDiary:
    """测试 CharacterAgent.diary()"""

    @pytest.fixture
    def mock_store(self):
        store = MagicMock()
        store.get_character_state = AsyncMock(return_value=CharacterState())
        store.update_character_state = AsyncMock()
        return store

    @pytest.fixture
    def mock_router(self):
        return MagicMock()

    @pytest.fixture
    def agent(self, mock_store, mock_router):
        return CharacterAgent(store=mock_store, router=mock_router)

    @pytest.mark.asyncio
    async def test_diary_truncates_long_text(self, agent):
        """验证日记 >300 字时截断（T4: output_arguments 路径）"""
        long_diary = '日' * 350
        with patch(_CONV_RUN_PATH, new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ConversationRunResult(
                final_text="",
                final_reason="output_collected",
                completion_kind="completed",
                output_arguments={'diary': long_diary},
            )
            result = await agent.diary({
                'events': [], 'character_name': '测试角色',
                'character_description': '',
            }, interaction_id="test-id")
        assert result.success is True
        assert len(result.data) <= 300
        assert result.data.endswith('...')


class TestCharacterAgentShare:
    """测试 CharacterAgent.share() — 已禁用，验证返回空结果"""

    @pytest.fixture
    def mock_store(self):
        store = MagicMock()
        store.get_character_state = AsyncMock(return_value=CharacterState())
        store.update_character_state = AsyncMock()
        return store

    @pytest.fixture
    def mock_router(self):
        return MagicMock()

    @pytest.fixture
    def agent(self, mock_store, mock_router):
        return CharacterAgent(store=mock_store, router=mock_router)

    @pytest.mark.asyncio
    async def test_share_disabled_returns_none(self, agent):
        """share 已禁用，应返回 success=True data=None"""
        result = await agent.share({'event_description': 'test', 'character_name': '测试'})
        assert result.success is True
        assert result.data is None


class TestCharacterAgentContract:
    """测试 CharacterAgent.run() 统一入口"""

    @pytest.fixture
    def mock_store(self):
        store = MagicMock()
        store.get_character_state = AsyncMock(return_value=CharacterState())
        store.update_character_state = AsyncMock()
        return store

    @pytest.fixture
    def mock_router(self):
        return MagicMock()

    @pytest.fixture
    def agent(self, mock_store, mock_router):
        return CharacterAgent(store=mock_store, router=mock_router)

    @pytest.mark.asyncio
    async def test_run_dispatches_to_react(self, agent):
        """run(context) 应分派到 react()（T4: output_arguments 路径）"""
        with patch(_CONV_RUN_PATH, new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ConversationRunResult(
                final_text="",
                final_reason="output_collected",
                completion_kind="completed",
                output_arguments={'content': '测试反应', 'has_follow_up': False},
            )
            result = await agent.run({
                'mode': 'reaction', 'event': 'test',
                'character_name': '测试角色', 'character_description': '',
                'energy': 50, 'mood': 50, 'health': 50,
            }, interaction_id="test-id")
        assert result.success is True
        assert isinstance(result.data, EventReactionResult)

    @pytest.mark.asyncio
    async def test_run_dispatches_to_diary(self, agent):
        """run(context) 应分派到 diary()（T4: output_arguments 路径）"""
        with patch(_CONV_RUN_PATH, new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ConversationRunResult(
                final_text="",
                final_reason="output_collected",
                completion_kind="completed",
                output_arguments={'diary': '今天天气真好。'},
            )
            result = await agent.run({
                'mode': 'diary', 'events': [],
                'character_name': '测试角色', 'character_description': '',
            }, interaction_id="test-id")
        assert result.success is True
        assert '天气真好' in result.data

    @pytest.mark.asyncio
    async def test_run_share_mode_disabled(self, agent):
        """share mode 已禁用，应返回空结果"""
        result = await agent.run({
            'mode': 'share', 'character_name': '测试角色', 'character_description': '',
        }, interaction_id="test-id")
        assert result.success is True
        assert result.data is None

    @pytest.mark.asyncio
    async def test_run_dispatches_to_opening(self, agent):
        """run(context) 应分派到 opening() — T6: opening 走 Conversation.run"""
        with patch(_CONV_RUN_PATH, new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ConversationRunResult(
                final_text="昨天过得还不错。",
                final_reason="direct_content",
                completion_kind="completed",
            )
            result = await agent.run({
                'mode': 'opening', 'character_name': '测试角色',
                'character_description': '', 'summary': '冒险了一天',
            }, interaction_id="test-id")
        assert result.success is True
        assert '过得还不错' in result.data

    @pytest.mark.asyncio
    async def test_opening_uses_conv_run_with_output_none(self, agent):
        """opening() 走 Conversation.run，output=None, tools=ToolKit(), run_tag='opening'"""
        with patch(_CONV_RUN_PATH, new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ConversationRunResult(
                final_text="开场白",
                final_reason="direct_content",
                completion_kind="completed",
            )
            result = await agent.opening({
                'character_name': '测试角色',
                'character_description': '',
                'summary': '冒险了一天',
            }, interaction_id="test-id")
        assert result.success is True
        assert result.data == "开场白"
        # 验证 conv.run 调用参数
        call_kwargs = mock_run.call_args.kwargs
        assert call_kwargs['output'] is None
        assert len(call_kwargs['tools'].tools) == 0
        assert call_kwargs['run_tag'] == 'opening'

    @pytest.mark.asyncio
    async def test_run_accepts_interaction_id(self, agent):
        """R1: agent.run(context, interaction_id="ext_123") 不抛 TypeError"""
        with patch(_CONV_RUN_PATH, new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ConversationRunResult(
                final_text="",
                final_reason="output_collected",
                completion_kind="completed",
                output_arguments={'content': '测试', 'has_follow_up': False},
            )
            # 不应抛 TypeError: run() got an unexpected keyword argument 'interaction_id'
            result = await agent.run({
                'mode': 'reaction', 'event': 'test',
                'character_name': '测试角色', 'character_description': '',
                'energy': 50, 'mood': 50, 'health': 50,
            }, interaction_id="ext_123")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_react_passes_interaction_id_to_conv_run(self, agent):
        """R1: react() 将外层传入的 interaction_id 透传到 conv.run"""
        with patch(_CONV_RUN_PATH, new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ConversationRunResult(
                final_text="",
                final_reason="output_collected",
                completion_kind="completed",
                output_arguments={'content': '反应内容', 'has_follow_up': False},
            )
            result = await agent.run({
                'mode': 'reaction', 'event': 'test',
                'character_name': '测试角色', 'character_description': '',
                'energy': 50, 'mood': 50, 'health': 50,
            }, interaction_id="ext_456")
        # 验证 conv.run 被调用时 interaction_id 等于外层传入值
        call_kwargs = mock_run.call_args.kwargs
        assert call_kwargs['interaction_id'] == "ext_456"


class TestCharacterAgentNoEndConversation:
    """Fix 1: Character reaction 新路径不含 end_conversation"""

    @pytest.fixture
    def mock_store(self):
        store = MagicMock()
        store.get_character_state = AsyncMock(return_value=CharacterState())
        store.update_character_state = AsyncMock()
        return store

    @pytest.fixture
    def mock_router(self):
        return MagicMock()

    @pytest.fixture
    def agent(self, mock_store, mock_router):
        return CharacterAgent(store=mock_store, router=mock_router)

    @pytest.mark.asyncio
    async def test_build_reaction_spec_no_end_conversation_tool(self, agent):
        """_build_reaction_spec 的 ToolKit 为空（不含 end_conversation）。"""
        spec = await agent._build_reaction_spec({
            "event": "测试", "character_name": "T", "character_description": "",
            "energy": 50, "mood": 50, "health": 50,
        })
        assert "end_conversation" not in spec.tools.tools

    def test_reaction_user_prompt_no_end_conversation(self, agent):
        """reaction user_prompt 不含 end_conversation 字样。"""
        prompt = agent._build_reaction_user_prompt({
            "event": "测试", "character_name": "T",
        })
        assert "end_conversation" not in prompt
        assert "want_to_end=true" in prompt

    def test_dm_want_to_end_prompt_no_end_conversation(self, agent):
        """dm_want_to_end=True 时不出现 end_conversation。"""
        prompt = agent._build_reaction_user_prompt({
            "event": "测试", "character_name": "T",
            "dm_want_to_end": True,
        })
        assert "end_conversation" not in prompt
        assert "want_to_end=true" in prompt


class TestDiarySubmitDiaryOutputSpec:
    """Fix 3: diary OutputSpec 改名为 submit_diary"""

    @pytest.fixture
    def mock_store(self):
        store = MagicMock()
        store.get_character_state = AsyncMock(return_value=CharacterState())
        store.update_character_state = AsyncMock()
        return store

    @pytest.fixture
    def mock_router(self):
        return MagicMock()

    @pytest.fixture
    def agent(self, mock_store, mock_router):
        return CharacterAgent(store=mock_store, router=mock_router)

    @pytest.mark.asyncio
    async def test_diary_output_name_is_submit_diary(self, agent):
        """_build_diary_spec 的 OutputSpec.name == 'submit_diary'。"""
        spec = await agent._build_diary_spec({
            "events": [], "character_name": "T", "character_description": "",
        })
        assert spec.output is not None
        assert spec.output.name == "submit_diary"

    def test_diary_user_prompt_no_record_diary_entry(self, agent):
        """diary user_prompt 不含 record_diary_entry 字样。"""
        prompt = agent._build_diary_user_prompt({
            "events": [], "character_name": "T",
        })
        assert "record_diary_entry" not in prompt
        assert "submit_diary" in prompt


class TestFormatStatePrompt:
    """测试 _format_state_prompt 静态方法"""

    def test_no_intention_param(self):
        """验证无 intention 参数时输出不含 '当前意向'"""
        from plugins.DicePP.module.persona.life.character_agent import CharacterAgent
        result = CharacterAgent._format_state_prompt(energy=80, mood=60, health=90)
        assert '体力' in result
        assert '心情' in result
        assert '健康' in result
        assert '当前意向' not in result

    def test_handles_all_none(self):
        """验证全部 None 时返回默认提示"""
        from plugins.DicePP.module.persona.life.character_agent import CharacterAgent
        result = CharacterAgent._format_state_prompt(energy=None, mood=None, health=None)
        assert result == '无记录'


class TestCharacterAgentChangeSources:
    """测试 CharacterAgent._get_change_sources()"""

    def test_returns_single_source(self):
        from unittest.mock import MagicMock
        store = MagicMock()
        router = MagicMock()
        agent = CharacterAgent(store=store, router=router)
        sources = agent._get_change_sources()
        assert len(sources) == 1
        assert sources[0].source_id == "state.character"
        assert sources[0].name == "状态变化"
        assert sources[0].priority == 10


class TestReactionUserPromptNoStateText:
    """验证 _build_reaction_user_prompt 不再拼接 _format_state_prompt"""

    def test_no_state_text_in_user_prompt(self):
        from unittest.mock import MagicMock
        store = MagicMock()
        router = MagicMock()
        agent = CharacterAgent(store=store, router=router)
        prompt = agent._build_reaction_user_prompt({
            "event": "远处传来声音",
            "energy": 80, "mood": 60, "health": 90,
        })
        assert "你当前的状态" not in prompt
        assert "体力:" not in prompt
        assert "心情:" not in prompt
        assert "健康:" not in prompt
        assert "当前事件: 远处传来声音" in prompt
