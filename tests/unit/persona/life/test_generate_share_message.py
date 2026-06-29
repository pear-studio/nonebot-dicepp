"""
单元测试: CharacterAgent share (替代旧 EventGenerationAgent.generate_share_message)
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from plugins.DicePP.module.persona.life.character_agent import CharacterAgent
from plugins.DicePP.module.persona.life.types import AgentResult
from conftest import _make_tool_registry, make_mock_runtime

class MockConfig:
    proactive_share_max_chars = 200
    background_llm_timeout_seconds = 10
    background_llm_max_rounds = 3

@pytest.fixture
def mock_router():
    router = MagicMock()
    router.data_store = MagicMock()
    router.quota_check_enabled = False
    router.daily_limit = 20
    router.trace_enabled = False
    router.trace_max_age_days = 7
    router.config = MagicMock()
    router.config.timezone = 'Asia/Shanghai'
    router._pending_tool_args = None
    router._pending_final_output = 'ok'
    return router

@pytest.fixture
def agent(mock_router, monkeypatch):
    make_mock_runtime(monkeypatch)
    return CharacterAgent(mock_router.data_store, mock_router, config=MockConfig(), tool_registry=_make_tool_registry())

@pytest.fixture
def share_context():
    return {'mode': 'share', 'event_description': '在公园里散步', 'reaction': '感觉很好', 'character_name': '测试角色', 'character_description': '一个友好的角色', 'target_user_id': 'user123', 'relationship_score': 50.0, 'relation_label': '友好', 'user_profile_facts': '（无）', 'recent_history': '（无）', 'message_type': 'random_event', 'environment': 'private', 'share_message_examples': [], 'energy': 60, 'mood': 70, 'health': 80, 'today_events': None, 'current_intention': None}