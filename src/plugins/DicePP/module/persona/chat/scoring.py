"""
评分 Agent

从对话中提取用户档案和好感度变化
"""
import json
from typing import List, Dict, Any, Optional
from nonebot.log import logger
from pydantic import BaseModel
from ..data.models import ScoreDeltas, UserProfile, RelationshipState
from ..llm.router import LLMRouter, ServiceUnavailableError
from ..llm.selection import SelectionPolicy
from ..tools.collecting import make_collecting_executor
from ..tools.registry import ToolRegistry, ToolDef
from ..utils.json_helpers import safe_json_loads
from ..wall_clock import persona_wall_now, format_timestamp, format_relative_time


class ScoringAnalysisResult(BaseModel):
    """评分分析结果"""
    deltas: ScoreDeltas
    facts: Dict[str, Any]
    raw_response: str = ""
    parse_error: str = ""  # 非空表示解析失败


class ScoringAgent:
    """评分 Agent - 批量分析对话提取用户档案和好感度变化"""

    def __init__(self, llm_router: LLMRouter, timezone: str = "Asia/Shanghai",
                 max_tool_rounds: int = 3):
        self.llm_router = llm_router
        self.timezone = timezone
        self.max_tool_rounds = max_tool_rounds

    async def batch_analyze(
        self,
        messages: List[Dict[str, str]],
        current_profile: Optional[UserProfile] = None,
        relationship: Optional[RelationshipState] = None,
    ) -> ScoringAnalysisResult:
        prompt = self._build_analysis_prompt(
            messages,
            current_profile or UserProfile(user_id="", facts={}),
            relationship
        )

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "score_relationship",
                    "description": "输出好感度变化分析和用户事实提取结果",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "deltas": {
                                "type": "object",
                                "properties": {
                                    "intimacy": {"type": "number", "description": "亲密度变化，范围 -5.0 到 +5.0"},
                                    "passion": {"type": "number", "description": "激情变化"},
                                    "trust": {"type": "number", "description": "信任变化"},
                                    "secureness": {"type": "number", "description": "安全感变化"},
                                },
                                "required": ["intimacy", "passion", "trust", "secureness"],
                            },
                            "facts": {
                                "type": "object",
                                "description": "提取或更新的用户事实，key-value 形式",
                            },
                        },
                        "required": ["deltas", "facts"],
                    },
                },
            }
        ]

        collected_args: list = []
        tool_name = tools[0]["function"]["name"]

        tool_registry = ToolRegistry()
        tool_registry.register(
            "scoring",
            ToolDef(name=tool_name, description="评分工具",
                    parameters=tools[0]["function"]["parameters"]),
            make_collecting_executor(collected_args),
        )

        hooks = self.llm_router.make_default_hooks()

        try:
            result = await self.llm_router.run_via_loop(
                messages=[{"role": "user", "content": prompt}],
                tools=tools,
                temperature=0.7,
                timeout=60,
                selection=SelectionPolicy.SCORING,
                tool_registry=tool_registry,
                tool_domains=["scoring"],
                hooks=hooks,
                max_tool_rounds=self.max_tool_rounds,
            )
        except ServiceUnavailableError as e:
            logger.error(f"评分: 无可用 LLM provider: {e}")
            return ScoringAnalysisResult(
                deltas=ScoreDeltas(), facts={},
                parse_error=f"无可用 LLM provider: {e}",
            )
        except Exception as e:
            logger.error(f"评分 LLM 调用失败: {e}")
            return ScoringAnalysisResult(
                deltas=ScoreDeltas(), facts={},
                parse_error=f"LLM 调用失败: {type(e).__name__}: {e}",
            )

        content = result.final_output or ""

        if not collected_args:
            raw_response = content if content else ""
            deltas, facts, parse_error = self._parse_response(raw_response)
            return ScoringAnalysisResult(
                deltas=deltas, facts=facts,
                raw_response=raw_response, parse_error=parse_error,
            )

        data = collected_args[0]
        raw_args = json.dumps(data, ensure_ascii=False)
        if not isinstance(data, dict):
            return ScoringAnalysisResult(
                deltas=ScoreDeltas(),
                facts={},
                raw_response=raw_args,
                parse_error=f"JSON 解析失败或返回非 dict: type={type(data).__name__}",
            )
        try:
            deltas, facts = self._extract_result(data)
            return ScoringAnalysisResult(
                deltas=deltas,
                facts=facts,
                raw_response=json.dumps(data, ensure_ascii=False),
                parse_error="",
            )
        except Exception as exc:
            return ScoringAnalysisResult(
                deltas=ScoreDeltas(),
                facts={},
                raw_response=raw_args,
                parse_error=f"提取评分结果异常: {type(exc).__name__}: {exc}",
            )

    def _build_analysis_prompt(
        self,
        messages: List[Dict[str, str]],
        profile: UserProfile,
        relationship: Optional[RelationshipState] = None,
    ) -> str:
        dialogue_lines = []
        now = persona_wall_now(self.timezone)
        for msg in messages:
            role = "用户" if msg["role"] == "user" else "AI"
            rel = format_relative_time(msg.get("created_at"), now)
            extra = f" {rel}" if rel else ""
            prefix = f"{format_timestamp(msg.get('created_at'), now)}{extra}"
            if prefix.strip():
                dialogue_lines.append(f"[{prefix}] {role}: {msg['content']}")
            else:
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

你必须通过调用 score_relationship 工具来输出结果，不要直接回复文本。

注意：
- 好感度变化基于用户的态度、话题深度、情感表达
- 用户友好、分享个人信息、表达情感 → 正分
- 用户冷淡、敷衍、负面态度 → 负分
- 提取的事实要简洁具体"""
        
        return prompt

    def _parse_response(self, response: str) -> tuple[ScoreDeltas, Dict[str, Any], str]:
        """解析 LLM 响应（统一走 safe_json_loads 容错）

        Returns:
            (deltas, facts, parse_error)
        """
        data = safe_json_loads(response, fallback=None, log_prefix="评分解析")
        if not isinstance(data, dict):
            return ScoreDeltas(), {}, f"JSON 解析失败或返回非 dict: type={type(data).__name__}"
        try:
            return (*self._extract_result(data), "")
        except Exception as exc:
            return ScoreDeltas(), {}, f"提取评分结果异常: {type(exc).__name__}: {exc}"

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
