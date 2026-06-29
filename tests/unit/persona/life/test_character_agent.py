"""
单元测试: CharacterAgent — react() / diary() / share() 入口
"""
from unittest.mock import AsyncMock, MagicMock, patch
import json
import pytest
from plugins.DicePP.module.persona.life.character_agent import CharacterAgent
from plugins.DicePP.module.persona.life.types import AgentResult, EventReactionResult
from plugins.DicePP.module.persona.data.models import CharacterState

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
        """mock _run_life_collect_loop 返回合法 reaction JSON"""
        valid_args = {'content': '我刚才好像听到什么声音...', 'has_follow_up': True}
        with patch('plugins.DicePP.module.persona.life._llm_utils._run_life_collect_loop', new_callable=AsyncMock) as mock_loop:
            mock_loop.return_value = ([valid_args], [])
            result = await agent.react({'event': '远处传来一阵奇怪的声音。', 'character_name': '测试角色', 'character_description': '谨慎的冒险者', 'energy': 50, 'mood': 50, 'health': 50})
        assert result.success is True
        assert isinstance(result.data, EventReactionResult)
        assert '声音' in result.data.reaction

    @pytest.mark.asyncio
    async def test_react_fallback_on_empty_collected(self, agent):
        """mock 空收集，验证回退"""
        with patch('plugins.DicePP.module.persona.life._llm_utils._run_life_collect_loop', new_callable=AsyncMock) as mock_loop:
            mock_loop.return_value = ([], [])
            result = await agent.react({'event': 'test', 'character_name': '测试角色', 'character_description': '', 'energy': 50, 'mood': 50, 'health': 50})
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
        """验证日记 >300 字时截断"""
        long_diary = '日' * 350
        with patch('plugins.DicePP.module.persona.life._llm_utils._run_life_collect_loop', new_callable=AsyncMock) as mock_loop:
            mock_loop.return_value = ([{'diary': long_diary}], [])
            result = await agent.diary({'events': [], 'character_name': '测试角色', 'character_description': ''})
        assert result.success is True
        assert len(result.data) <= 300
        assert result.data.endswith('...')

class TestCharacterAgentShare:
    """测试 CharacterAgent.share()"""

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
    async def test_share_returns_message(self, agent):
        """mock 返回合法消息"""
        with patch('plugins.DicePP.module.persona.life._llm_utils._run_life_collect_loop', new_callable=AsyncMock) as mock_loop:
            mock_loop.return_value = ([{'message': '今天在森林里发现了好东西！'}], [])
            result = await agent.share({'event_description': '发现稀有草药', 'reaction': '太幸运了', 'character_name': '测试角色', 'character_description': '', 'relationship_score': 50, 'relation_label': '友好', 'user_profile_facts': '', 'recent_history': ''})
        assert result.success is True
        assert '好东西' in result.data

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
        """run(context) 应分派到 react()"""
        valid_args = {'reaction': '测试反应', 'content': '测试反应', 'has_follow_up': False}
        with patch('plugins.DicePP.module.persona.life._llm_utils._run_life_collect_loop', new_callable=AsyncMock) as mock_loop:
            mock_loop.return_value = ([valid_args], [])
            result = await agent.run({'mode': 'reaction', 'event': 'test', 'character_name': '测试角色', 'character_description': '', 'energy': 50, 'mood': 50, 'health': 50})
        assert result.success is True
        assert isinstance(result.data, EventReactionResult)

    @pytest.mark.asyncio
    async def test_run_dispatches_to_diary(self, agent):
        """run(context) 应分派到 diary()"""
        with patch('plugins.DicePP.module.persona.life._llm_utils._run_life_collect_loop', new_callable=AsyncMock) as mock_loop:
            mock_loop.return_value = ([{'diary': '今天天气真好。'}], [])
            result = await agent.run({'mode': 'diary', 'events': [], 'character_name': '测试角色', 'character_description': ''})
        assert result.success is True
        assert '天气真好' in result.data

    @pytest.mark.asyncio
    async def test_run_dispatches_to_opening(self, agent):
        """run(context) 应分派到 opening()"""
        mock_result = MagicMock()
        mock_result.final_text = '昨天过得还不错。'
        with patch('plugins.DicePP.module.persona.agent.runtime.AgentRuntime') as mock_runtime_cls:
            mock_runtime = MagicMock()
            mock_runtime.run = AsyncMock(return_value=mock_result)
            mock_runtime_cls.return_value = mock_runtime
            result = await agent.run({'mode': 'opening', 'character_name': '测试角色', 'character_description': '', 'summary': '冒险了一天'})
        assert result.success is True
        assert '过得还不错' in result.data


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