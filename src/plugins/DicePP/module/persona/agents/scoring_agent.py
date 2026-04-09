"""
评分 Agent

从对话中提取用户档案和好感度变化
"""
import json
import re
from typing import List, Dict, Any, Optional
import logging

from ..data.models import ScoreDeltas, UserProfile, RelationshipState, ModelTier
from ..llm.router import LLMRouter

logger = logging.getLogger("persona.scoring")


class ScoringAgent:
    """评分 Agent - 批量分析对话提取用户档案和好感度变化"""

    def __init__(self, llm_router: LLMRouter):
        self.llm_router = llm_router

    async def batch_analyze(
        self,
        messages: List[Dict[str, str]],
        current_profile: Optional[UserProfile] = None,
        relationship: Optional[RelationshipState] = None,
    ) -> tuple[ScoreDeltas, Dict[str, Any]]:
        prompt = self._build_analysis_prompt(
            messages,
            current_profile or UserProfile(user_id="", facts={}),
            relationship
        )
        
        # 调用辅助模型
        response = await self.llm_router.generate(
            messages=[{"role": "user", "content": prompt}],
            model_tier=ModelTier.AUXILIARY,
        )
        
        # 解析结果
        deltas, facts = self._parse_response(response)
        
        return deltas, facts

    def _build_analysis_prompt(
        self,
        messages: List[Dict[str, str]],
        profile: UserProfile,
        relationship: Optional[RelationshipState] = None,
    ) -> str:
        dialogue_lines = []
        for msg in messages:
            role = "用户" if msg["role"] == "user" else "AI"
            dialogue_lines.append(f"{role}: {msg['content']}")
        dialogue = "\n".join(dialogue_lines)

        existing_facts = json.dumps(profile.facts, ensure_ascii=False) if profile and profile.facts else "无"

        relationship_info = ""
        if relationship:
            level, label = relationship.get_warmth_level(["陌生", "熟悉", "友好", "亲近", "亲密", "知己"])
            relationship_info = f"当前关系: {label} (综合好感度 {relationship.composite_score:.1f})\n"
        
        prompt = f"""分析以下对话，完成两个任务：

1. 评估好感度变化（四个维度：亲密度、激情、信任、安全感）
2. 提取用户相关信息（名字、爱好、宠物等）

## 当前关系状态
{relationship_info}

## 对话记录
{dialogue}

## 已知的用户信息
{existing_facts}

## 输出格式（严格 JSON）
{{
  "deltas": {{
    "intimacy": 0.0,  // 亲密度变化，范围 -5.0 到 +5.0
    "passion": 0.0,   // 激情变化
    "trust": 0.0,     // 信任变化
    "secureness": 0.0 // 安全感变化
  }},
  "facts": {{
    // 提取或更新的用户事实，key-value 形式
    // 只包含新发现或需要更新的信息
  }}
}}

注意：
- 好感度变化基于用户的态度、话题深度、情感表达
- 用户友好、分享个人信息、表达情感 → 正分
- 用户冷淡、敷衍、负面态度 → 负分
- 提取的事实要简洁具体"""
        
        return prompt

    def _parse_response(self, response: str) -> tuple[ScoreDeltas, Dict[str, Any]]:
        """
        解析 LLM 响应
        
        使用 3 级容错策略：
        1. 直接解析 JSON
        2. 去除 markdown 围栏后重试
        3. 正则提取第一个 {...} 块
        """
        # 尝试 1：直接解析
        try:
            data = json.loads(response)
            return self._extract_result(data)
        except json.JSONDecodeError:
            pass
        
        # 尝试 2：去除 markdown 围栏
        try:
            cleaned = re.sub(r'```json\s*|\s*```', '', response, flags=re.DOTALL)
            cleaned = re.sub(r'^[\s\n]*json\s*', '', cleaned, flags=re.DOTALL)
            cleaned = cleaned.strip()
            data = json.loads(cleaned)
            return self._extract_result(data)
        except json.JSONDecodeError:
            pass
        
        # 尝试 3：括号计数提取第一个完整 JSON 对象
        try:
            start = response.find('{')
            if start >= 0:
                depth = 0
                for i in range(start, len(response)):
                    if response[i] == '{':
                        depth += 1
                    elif response[i] == '}':
                        depth -= 1
                        if depth == 0:
                            data = json.loads(response[start:i+1])
                            return self._extract_result(data)
        except json.JSONDecodeError:
            pass
        
        # 全部失败，返回零值
        logger.warning(f"评分解析失败，返回 zero-delta. raw={response[:200]}")
        return ScoreDeltas(), {}

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        """安全地将值转为 float，处理 None/null/N/A 等"""
        if value is None:
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _extract_result(self, data: dict) -> tuple[ScoreDeltas, Dict[str, Any]]:
        """从解析的数据中提取结果"""
        # 提取 deltas
        deltas_data = data.get("deltas", {})
        if not isinstance(deltas_data, dict):
            deltas_data = {}
        deltas = ScoreDeltas(
            intimacy=self._safe_float(deltas_data.get("intimacy")),
            passion=self._safe_float(deltas_data.get("passion")),
            trust=self._safe_float(deltas_data.get("trust")),
            secureness=self._safe_float(deltas_data.get("secureness")),
        )
        
        # 限制范围
        deltas = deltas.clamp(-5.0, 5.0)
        
        # 提取 facts
        facts = data.get("facts", {})
        if not isinstance(facts, dict):
            facts = {}
        
        return deltas, facts
