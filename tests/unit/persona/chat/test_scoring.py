"""ScoringAgent 单元测试 — T6 ToolKit+OutputSpec 新路径"""
import pytest
from unittest.mock import Mock, AsyncMock

from plugins.DicePP.module.persona.chat.scoring import ScoringAgent, ScoringAnalysisResult
from plugins.DicePP.module.persona.data.models import ScoreDeltas


class TestScoringAgent:
    """ScoringAgent 基础单元测试"""

    def test_init_defaults(self):
        """默认初始化参数"""
        client = Mock()
        agent = ScoringAgent(client)
        assert agent.client is client
        assert agent.timezone == "Asia/Shanghai"
        assert agent.max_rounds == 3
        assert agent._store is None

    def test_init_with_store(self):
        """带 store 初始化"""
        client = Mock()
        store = Mock()
        agent = ScoringAgent(client, timezone="UTC", max_rounds=5, store=store)
        assert agent.timezone == "UTC"
        assert agent.max_rounds == 5
        assert agent._store is store


class TestScoringAnalysisResult:
    """ScoringAnalysisResult 模型测试"""

    def test_empty_result(self):
        result = ScoringAnalysisResult(
            deltas=ScoreDeltas(),
            facts={},
        )
        assert result.deltas == ScoreDeltas()
        assert result.facts == {}
        assert result.raw_response == ""
        assert result.parse_error == ""

    def test_parse_error(self):
        result = ScoringAnalysisResult(
            deltas=ScoreDeltas(),
            facts={},
            parse_error="JSON解析失败",
        )
        assert result.parse_error == "JSON解析失败"
