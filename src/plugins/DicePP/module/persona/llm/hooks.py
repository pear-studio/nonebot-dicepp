"""
内置 Hook 集合

QuotaHook(pre_llm) — 配额超限中止循环
TraceHook(post_llm + flush) — 累计轮次记录，循环结束后写入 persona_llm_traces
BillingHook(post_llm) — 每次 run 仅首次扣费
SegmentCorrectionHook(post_llm) — 分段回复纠正注入
"""
from typing import List, Dict, Optional, Any
import asyncio
import json
import time

from nonebot.log import logger

from .hook_protocol import LoopHook, PreLLMResult, LoopContext, ToolResult, RoundRecord
from .router import QuotaExceeded


class QuotaHook:
    """配额检查 Hook (pre_llm) — 从 Router._check_quota / _is_exempt_from_quota 迁移"""

    def __init__(
        self,
        data_store: Any,
        quota_check_enabled: bool = True,
        daily_limit: int = 20,
        config: Any = None,
    ):
        self.data_store = data_store
        self.quota_check_enabled = quota_check_enabled
        self.daily_limit = daily_limit
        self.config = config

    async def pre_llm(self, messages: List[dict], ctx: LoopContext) -> PreLLMResult:
        if not self.quota_check_enabled:
            return PreLLMResult()
        if not self.data_store:
            return PreLLMResult()

        try:
            if await self._is_exempt(ctx.user_id, ctx.group_id):
                return PreLLMResult()

            from ..wall_clock import persona_wall_now
            today = persona_wall_now(
                self.config.timezone if self.config else "Asia/Shanghai"
            ).strftime("%Y-%m-%d")
            usage = await self.data_store.get_daily_usage(ctx.user_id, today)

            if usage >= self.daily_limit:
                logger.info(f"配额超限: user={ctx.user_id}, usage={usage}/{self.daily_limit}")
                return PreLLMResult(abort=True, abort_reason="今日配额已用完")
            return PreLLMResult()
        except Exception as e:
            logger.error(f"配额检查失败: user={ctx.user_id}, error={e}", exc_info=True)
            return PreLLMResult(abort=True, abort_reason=f"配额检查失败: {e}")

    async def _is_exempt(self, user_id: str, group_id: str) -> bool:
        if not self.data_store:
            return False
        try:
            if self.config and self.config.whitelist_enabled:
                if await self.data_store.is_user_whitelisted(user_id):
                    return True
                if group_id and await self.data_store.is_group_whitelisted(group_id):
                    return True
            return False
        except Exception as e:
            logger.error(f"豁免检查失败: user={user_id}, error={e}", exc_info=True)
            return False


class TraceHook:
    """Trace 记录 Hook (post_llm + flush) — 从 Router._execute_and_trace 迁移"""

    injects_message: bool = False

    def __init__(
        self,
        data_store: Any,
        trace_enabled: bool = False,
        trace_max_age_days: int = 7,
    ):
        self.data_store = data_store
        self.trace_enabled = trace_enabled
        self.trace_max_age_days = trace_max_age_days
        self.round_records: List[dict] = []
        self._trace_tasks: set[asyncio.Task] = set()

    async def post_llm(
        self, messages: List[dict], response: Any, ctx: LoopContext
    ) -> Optional[dict]:
        if not self.trace_enabled:
            return None

        tool_calls_dicts = [
            {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
            for tc in (response.tool_calls or [])
        ]

        record = {
            "round": ctx.tool_round_num,
            "think": getattr(response, "_think_raw", None),
            "tool_calls": tool_calls_dicts,
            "tool_results": [],
            "callback": None,
        }
        self.round_records.append(record)
        return None

    def add_tool_results(self, results: List[dict]) -> None:
        if self.round_records:
            self.round_records[-1]["tool_results"] = results

    def add_callback(self, callback: dict) -> None:
        if self.round_records:
            self.round_records[-1]["callback"] = callback

    async def flush(self, session_id: str, final_metadata: dict) -> None:
        if not self.trace_enabled or not self.data_store or not self.round_records:
            return
        try:
            await self._write_trace(session_id, final_metadata)
        except Exception as e:
            logger.warning(f"Trace flush 失败: {e}", exc_info=True)

    async def _write_trace(self, session_id: str, metadata: dict) -> None:
        from ..data.models import LLMTraceRecord

        _MAX_ROUND_MESSAGES_BYTES = 100 * 1024
        round_json = json.dumps(self.round_records, ensure_ascii=False, default=str)
        if len(round_json.encode("utf-8")) > _MAX_ROUND_MESSAGES_BYTES:
            round_json = json.dumps(
                {"_truncated": True, "reason": "round_messages exceeded 100KB",
                 "rounds": len(self.round_records)},
                ensure_ascii=False,
            )

        tool_calls_json = json.dumps(
            [{"name": n} for n in metadata.get("tool_names", [])],
            ensure_ascii=False,
        )

        trace = LLMTraceRecord(
            session_id=session_id,
            user_id=metadata.get("user_id", ""),
            group_id=metadata.get("group_id", ""),
            model=metadata.get("model", ""),
            tier=metadata.get("tier", ""),
            messages=json.dumps(metadata.get("messages", []), ensure_ascii=False, default=str),
            response=metadata.get("content", ""),
            tool_calls=tool_calls_json,
            round_messages=round_json,
            selected_provider=metadata.get("provider_name", ""),
            selected_model=metadata.get("model_name", ""),
            selection_policy=metadata.get("selection_policy", ""),
            candidate_count=metadata.get("candidate_count", 0),
            latency_ms=metadata.get("latency_ms", 0),
            tokens_in=metadata.get("tokens_input", 0),
            tokens_out=metadata.get("tokens_output", 0),
            temperature=metadata.get("temperature"),
            status=metadata.get("status", "ok"),
            error=metadata.get("error", ""),
        )
        task = asyncio.create_task(self.data_store.add_llm_trace(trace))
        self._trace_tasks.add(task)
        task.add_done_callback(self._trace_tasks.discard)


class BillingHook:
    """计费 Hook (post_llm) — 从 BillingPolicy.charge 迁移。

    每次 AgentLoop.run() 仅首次 post_llm 扣费，维护 _charged_this_run 状态。
    """
    injects_message: bool = False

    def __init__(self, router: Any):
        self._router = router
        self._charged_this_run = False

    async def post_llm(
        self, messages: List[dict], response: Any, ctx: LoopContext
    ) -> Optional[dict]:
        if self._charged_this_run:
            return None
        self._charged_this_run = True

        if self._router and ctx.user_id:
            await self._router.increment_usage(ctx.user_id)
        return None


class SegmentCorrectionHook:
    """分段纠正 Hook (post_llm) — 从 ChatSession._on_segment_round_complete 迁移。

    注入型 Hook (injects_message=True)，检测 LLM 未调用 send_reply_segment
    时注入纠正指令。
    """
    injects_message: bool = True

    async def post_llm(
        self, messages: List[dict], response: Any, ctx: LoopContext
    ) -> Optional[dict]:
        content = response.content or ""

        has_send_reply_segment = any(
            tc.name == "send_reply_segment" for tc in (response.tool_calls or [])
        )

        if has_send_reply_segment:
            if content.strip():
                logger.warning(
                    "LLM returned content alongside send_reply_segment; content ignored"
                )
            return None

        if content.strip():
            logger.warning(
                f"send_reply_segment 未被调用，注入纠正指令: "
                f"round={ctx.tool_round_num}, content_preview={content.strip()[:30]!r}"
            )
            return {
                "role": "user",
                "content": (
                    "[系统指令] 你必须调用 send_reply_segment 工具发送回复。"
                    "这不是用户消息，不要回应它，直接调用工具。"
                ),
            }

        return None
