"""
评分 Agent

从对话中提取用户档案和好感度变化
"""
import json
from typing import List, Dict, Any, Optional
from utils.logger import logger
from pydantic import BaseModel
from ..data.models import ScoreDeltas, UserProfile, RelationshipState, DEFAULT_RELATION_LABELS
from ..data.store import PersonaDataStore
from ..llm.router import LLMRouter, ServiceUnavailableError
from ..llm.selection import SelectionPolicy, SCORING
from ..utils.json_helpers import safe_json_loads
from utils.time import wall_now, format_timestamp, format_relative_time


class ScoringAnalysisResult(BaseModel):
    """评分分析结果"""
    deltas: ScoreDeltas
    facts: Dict[str, Any]
    raw_response: str = ""
    parse_error: str = ""  # 非空表示解析失败


class ScoringAgent:
    """评分 Agent - 批量分析对话提取用户档案和好感度变化"""

    def __init__(self, llm_router: LLMRouter, timezone: str = "Asia/Shanghai",
                 max_rounds: int = 3,
                 store: Optional[PersonaDataStore] = None):
        self.llm_router = llm_router
        self.timezone = timezone
        self.max_rounds = max_rounds
        self._store = store

    async def batch_analyze(
        self,
        messages: List[Dict[str, str]],
        current_profile: Optional[UserProfile] = None,
        relationship: Optional[RelationshipState] = None,
        user_id: str = "",
        group_id: str = "",
        *,
        warn_pending: bool = False,
    ) -> ScoringAnalysisResult:
        from ..agent.tool_bridge import run_structured_collect

        tool_name = "record_score"

        prompt = self._build_analysis_prompt(
            messages,
            current_profile or UserProfile(user_id="", facts={}),
            relationship,
            tool_name=tool_name,
            warn_pending=warn_pending,
        )

        try:
            collected_args, runtime_result = await run_structured_collect(
                router=self.llm_router,
                store=self._store,
                messages=[{"role": "user", "content": prompt}],
                user_id=user_id,
                group_id=group_id,
                required_tools=["record_score"],
                temperature=0.7,
                timeout=60,
                selection=SCORING,
                max_rounds=self.max_rounds,
            )
            content = runtime_result.final_text or ""

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
        tool_name: str = "record_score",
        *,
        warn_pending: bool = False,
    ) -> str:
        dialogue_lines = []
        now = wall_now(self.timezone)
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
            level, label = relationship.get_relation_level(DEFAULT_RELATION_LABELS)
            relationship_info = (
                f"当前关系: {label} (综合 {relationship.composite_score:.1f}, "
                f"熟悉度 {relationship.familiarity:.1f}, 亲密度 {relationship.intimacy:.1f})\n"
            )

        warn_info = ""
        if warn_pending:
            warn_info = (
                "\n**注意**：上轮对话中用户有不当言论，AI 已设置警告标记（warn_pending）。"
                "本轮如继续恶意行为，可以扣减 reputation。\n"
            )

        prompt = f"""分析以下对话，完成三个任务：

1. 评估亲密度（intimacy）变化，范围 -5.0 到 +5.0
2. 评估是否需要扣减信誉（reputation_delta）——仅限恶意行为（骚扰、谩骂、恶意刷屏等），范围 -30 到 0
3. 提取用户相关信息（名字、爱好、宠物等）

**注意**：熟悉度（familiarity）由系统自动根据互动频率计算，你不需要评估。

## 当前关系状态
{relationship_info}
{warn_info}
## 对话记录
{dialogue}

## 已知的用户信息
{existing_facts}

你必须通过调用 {tool_name} 工具来输出结果，不要直接回复文本。

评分指南：
- intimacy 基于用户的态度、话题深度、情感表达
- 用户友好、分享个人信息、表达情感 → 正分
- 用户冷淡、敷衍、负面态度 → 负分
- reputation_delta 仅在明确恶意行为时扣分（-30~0），正常互动为 0
- **扣分前警告规则**：检查 AI 回复是否已包含不认可/警告表述；若已警告或上轮有 warn_pending 标记，则可以扣减 reputation；否则 reputation_delta 保持 0，系统会设置 warn_pending 在下一轮警告用户
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
        """从解析的数据中提取结果

        兼容两种输入格式：
        - 新格式 (record_score): 扁平字段 {intimacy, reputation_delta, facts}
        - 旧格式 (score_relationship): 嵌套 {deltas: {...}, facts: {...}}
        """
        # 判断格式：有 "deltas" 键 → 旧格式；否则 → 新格式
        if "deltas" in data and isinstance(data["deltas"], dict):
            deltas_data = data["deltas"]
        else:
            deltas_data = data  # 扁平格式，直接取根级字段

        if not isinstance(deltas_data, dict):
            deltas_data = {}
        deltas = ScoreDeltas(
            intimacy=self._safe_float(deltas_data.get("intimacy")),
            reputation_delta=self._safe_float(deltas_data.get("reputation_delta"), default=0.0),
            warning_issued=bool(deltas_data.get("warning_issued", False)),
        )

        # 限制范围
        deltas = deltas.clamp()

        # 提取 facts
        facts = data.get("facts", {})
        if not isinstance(facts, dict):
            facts = {}

        return deltas, facts
