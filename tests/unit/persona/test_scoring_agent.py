"""
单元测试: Persona 评分 Agent
"""

import pytest


from plugins.DicePP.module.persona.chat.scoring import ScoringAgent
from plugins.DicePP.module.persona.data.models import ScoreDeltas, UserProfile


class TestScoringAgentParsing:
    """测试评分 Agent 的响应解析"""

    def test_parse_valid_json(self):
        """测试解析有效 JSON"""
        agent = ScoringAgent(None)  # 不需要 LLM 来测试解析

        response = '''
        {
          "deltas": {
            "intimacy": 3.5,
            "reputation_delta": -15.0,
            "warning_issued": true
          },
          "facts": {
            "name": "张三",
            "hobbies": ["读书", "游戏"]
          }
        }
        '''

        deltas, facts, parse_error = agent._parse_response(response)

        assert deltas.intimacy == 3.5
        assert deltas.reputation_delta == -15.0
        assert deltas.warning_issued is True
        assert facts["name"] == "张三"
        assert parse_error == ""

    def test_parse_with_markdown_fence(self):
        """测试解析带 markdown 围栏的 JSON"""
        agent = ScoringAgent(None)

        response = '''
```json
{
  "deltas": {
    "intimacy": 2.0,
    "reputation_delta": 0.0
  },
  "facts": {}
}
```
        '''

        deltas, facts, parse_error = agent._parse_response(response)

        assert deltas.intimacy == 2.0
        assert deltas.reputation_delta == 0.0
        assert parse_error == ""

    def test_parse_invalid_fallback(self):
        """测试解析失败返回零值并附带 parse_error"""
        agent = ScoringAgent(None)

        response = "这不是有效的 JSON"

        deltas, facts, parse_error = agent._parse_response(response)

        assert deltas.intimacy == 0.0
        assert deltas.reputation_delta == 0.0
        assert facts == {}
        assert "JSON 解析失败" in parse_error

    def test_parse_bracket_counting_fallback(self):
        """测试 Level 3：括号计数从噪声文本中提取 JSON"""
        agent = ScoringAgent(None)

        response = '好的，根据分析结果如下：{"deltas": {"intimacy": 1.0, "reputation_delta": -5.0}, "facts": {"name": "小明"}} 以上为评分结果。'

        deltas, facts, parse_error = agent._parse_response(response)

        assert deltas.intimacy == 1.0
        assert deltas.reputation_delta == -5.0
        assert facts["name"] == "小明"
        assert parse_error == ""

    def test_clamp_values(self):
        """测试值被限制在范围内"""
        agent = ScoringAgent(None)

        response = '''
        {
          "deltas": {
            "intimacy": 10.0,
            "reputation_delta": -50.0
          }
        }
        '''

        deltas, _, parse_error = agent._parse_response(response)

        # intimacy 应限制在 [-5, 5]，reputation_delta 限制在 [-30, 0]
        assert deltas.intimacy == 5.0
        assert deltas.reputation_delta == -30.0


class TestScoringAgentPrompt:
    """测试评分 Agent 的 Prompt 构建"""
