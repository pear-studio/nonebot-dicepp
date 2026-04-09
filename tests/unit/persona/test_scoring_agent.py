"""
单元测试: Persona 评分 Agent
"""

import pytest
import sys
sys.path.insert(0, "src")

from plugins.DicePP.module.persona.agents.scoring_agent import ScoringAgent
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
            "passion": 1.0,
            "trust": 2.0,
            "secureness": 0.5
          },
          "facts": {
            "name": "张三",
            "hobbies": ["读书", "游戏"]
          }
        }
        '''
        
        deltas, facts = agent._parse_response(response)
        
        assert deltas.intimacy == 3.5
        assert deltas.passion == 1.0
        assert deltas.trust == 2.0
        assert deltas.secureness == 0.5
        assert facts["name"] == "张三"

    def test_parse_with_markdown_fence(self):
        """测试解析带 markdown 围栏的 JSON"""
        agent = ScoringAgent(None)
        
        response = '''
```json
{
  "deltas": {
    "intimacy": 2.0,
    "passion": 0.0,
    "trust": 1.5,
    "secureness": -0.5
  },
  "facts": {}
}
```
        '''
        
        deltas, facts = agent._parse_response(response)
        
        assert deltas.intimacy == 2.0
        assert deltas.secureness == -0.5

    def test_parse_invalid_fallback(self):
        """测试解析失败返回零值"""
        agent = ScoringAgent(None)
        
        response = "这不是有效的 JSON"
        
        deltas, facts = agent._parse_response(response)
        
        assert deltas.intimacy == 0.0
        assert deltas.passion == 0.0
        assert deltas.trust == 0.0
        assert deltas.secureness == 0.0
        assert facts == {}

    def test_parse_bracket_counting_fallback(self):
        """测试 Level 3：括号计数从噪声文本中提取 JSON"""
        agent = ScoringAgent(None)

        response = '好的，根据分析结果如下：{"deltas": {"intimacy": 1.0, "passion": 0.5, "trust": 2.0, "secureness": 0.0}, "facts": {"name": "小明"}} 以上为评分结果。'

        deltas, facts = agent._parse_response(response)

        assert deltas.intimacy == 1.0
        assert deltas.trust == 2.0
        assert facts["name"] == "小明"

    def test_clamp_values(self):
        """测试值被限制在范围内"""
        agent = ScoringAgent(None)
        
        response = '''
        {
          "deltas": {
            "intimacy": 10.0,
            "passion": -10.0,
            "trust": 5.0,
            "secureness": -5.0
          }
        }
        '''
        
        deltas, _ = agent._parse_response(response)
        
        # 应该被限制在 [-5, 5] 范围内
        assert deltas.intimacy == 5.0
        assert deltas.passion == -5.0
        assert deltas.trust == 5.0
        assert deltas.secureness == -5.0


class TestScoringAgentPrompt:
    """测试评分 Agent 的 Prompt 构建"""

    def test_build_prompt_structure(self):
        """测试 Prompt 结构"""
        agent = ScoringAgent(None)
        
        messages = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好呀~"},
        ]
        profile = UserProfile(user_id="test", facts={"name": "张三"})
        
        prompt = agent._build_analysis_prompt(messages, profile)
        
        assert "你好" in prompt
        assert "你好呀~" in prompt
        assert "张三" in prompt
        assert "deltas" in prompt
        assert "facts" in prompt

    def test_build_prompt_empty_profile(self):
        """测试空档案的 Prompt"""
        agent = ScoringAgent(None)
        
        messages = [{"role": "user", "content": "测试"}]
        profile = UserProfile(user_id="test", facts={})
        
        prompt = agent._build_analysis_prompt(messages, profile)
        
        assert "测试" in prompt


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
