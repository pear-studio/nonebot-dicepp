"""
单元测试: _llm_utils — 共享 LLM 路由基础设施
"""
import pytest
from unittest.mock import MagicMock
from plugins.DicePP.module.persona.life._llm_utils import _run_life_collect_loop, _DEFAULT_BG_TIMEOUT
from plugins.DicePP.module.persona.life.dm_agent import _STATE_SCALE_PROMPT

class TestRunLifeCollectLoop:
    """测试 _run_life_collect_loop 边界行为"""

    def test_empty_tools_returns_empty_list(self):
        """R4: 传入 tools=[] 应返回空列表而非抛出 IndexError"""
        mock_router = MagicMock()
        mock_store = MagicMock()
        import asyncio
        collected, final_msgs = asyncio.run(_run_life_collect_loop(router=mock_router, store=mock_store, messages=[{'role': 'system', 'content': 'test'}, {'role': 'user', 'content': 'test'}], tools=[], temperature=0.9, selection=MagicMock()))
        assert collected == []
        assert final_msgs == []

    def test_state_scale_prompt_contains_all_dimensions(self):
        """验证 _STATE_SCALE_PROMPT 包含三个维度"""
        assert '体力' in _STATE_SCALE_PROMPT
        assert '心情' in _STATE_SCALE_PROMPT
        assert '健康' in _STATE_SCALE_PROMPT

    def test_default_bg_timeout(self):
        assert _DEFAULT_BG_TIMEOUT == 90