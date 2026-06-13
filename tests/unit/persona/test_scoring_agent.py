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


class TestScoringAgentAnalysisPrompt:
    """测试评分分析 prompt 构建"""

    def test_build_analysis_prompt_contains_required_fields(self):
        """验证评分分析 prompt 包含必要的上下文字段"""
        from plugins.DicePP.module.persona.data.models import UserProfile, RelationshipState
        agent = ScoringAgent(None, timezone="Asia/Shanghai")

        messages = [
            {"role": "user", "content": "你好呀", "created_at": None},
            {"role": "assistant", "content": "你好！今天怎么样？", "created_at": None},
        ]
        profile = UserProfile(user_id="u1", facts={"name": "小明", "hobbies": ["读书", "游戏"]})
        relationship = RelationshipState(user_id="u1", intimacy=50, familiarity=40)

        prompt = agent._build_analysis_prompt(messages, profile, relationship)

        # 验证包含关系信息
        # composite = familiarity*0.6 + intimacy*0.4 = 40*0.6 + 50*0.4 = 44.0
        assert "综合 44.0" in prompt
        assert "熟悉度 40.0" in prompt
        assert "亲密度 50.0" in prompt

        # 验证包含对话记录
        assert "你好呀" in prompt
        assert "你好！今天怎么样？" in prompt

        # 验证包含已知用户信息（facts）
        assert "小明" in prompt
        assert "读书" in prompt
        assert "游戏" in prompt

        # 验证包含工具调用说明
        assert "record_score" in prompt

    def test_build_analysis_prompt_with_warn_pending(self):
        """验证 warn_pending 标记出现在 prompt 中"""
        agent = ScoringAgent(None, timezone="Asia/Shanghai")
        from plugins.DicePP.module.persona.data.models import UserProfile

        messages = [{"role": "user", "content": "hello"}]
        profile = UserProfile(user_id="u1", facts={})

        prompt = agent._build_analysis_prompt(messages, profile, warn_pending=True)

        assert "warn_pending" in prompt
        assert "警告标记" in prompt
