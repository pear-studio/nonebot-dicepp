"""AgentLoop — 统一的 Agent 循环抽象（一等公民，供 chat/life/scoring 复用）"""
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
import asyncio
import json
import re
import time
import uuid

from nonebot.log import logger

from ..llm.hook_protocol import LoopContext, ToolResult
from ..llm.providers.protocol import LLMProvider
from ..llm.providers.protocol import NonRetryableError
from ..llm.errors import classify

_THINK_RE = r'<think>.*?</think>'
_L1_MSG = {"role": "user", "content": "[系统指令] 你必须调用工具来完成任务。不要直接输出文本——只能通过调用工具来输出结果。"}

_FINISH_TASK_TOOL = {
    "type": "function",
    "function": {
        "name": "finish_task",
        "description": (
            "当你完成所有必要工作后调用此工具来结束任务。"
            "调用后对话立即终止，不需要再生成任何后续回复或调用其它工具。"
            "只在所有实际工具已调用完毕后才使用此工具。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "简短总结你完成了什么",
                },
            },
            "required": ["summary"],
        },
    },
}


@dataclass
class LoopResult:
    final_output: Optional[str]
    metadata: dict = field(default_factory=dict)
    aborted: bool = False
    abort_reason: str = ""


class AgentLoop:
    """统一的 Agent 循环 — LLM 调用 → 工具分派 → 继续/终止"""

    MAX_TOOLS_PER_ROUND = 10

    def __init__(
        self, provider: LLMProvider, tool_registry: Any = None,
        hooks: Optional[List] = None,
        max_tool_rounds: int = 5, max_round_callbacks: int = 3,
    ):
        self.provider = provider
        self.tool_registry = tool_registry
        self.hooks: List = hooks or []
        self.max_tool_rounds = max_tool_rounds
        self.max_round_callbacks = max_round_callbacks

    async def run(
        self, messages: List[dict], tools: Optional[List[dict]] = None,
        temperature: Optional[float] = None,
        timeout: int = 60, user_id: str = "", group_id: str = "",
        tool_domains: Optional[List[str]] = None, tool_ctx: Any = None,
    ) -> LoopResult:

        session_id = f"{user_id or ''}:{group_id or ''}:{uuid.uuid4().hex[:16]}"
        ctx = LoopContext(
            user_id=user_id, group_id=group_id, session_id=session_id,
            max_tool_rounds=self.max_tool_rounds,
            max_round_callbacks=self.max_round_callbacks,
            provider=self.provider, mode="chat",
        )

        current = list(messages)
        all_tools = list(tools) + [_FINISH_TASK_TOOL] if tools is not None else None
        records: List[dict] = []
        trn = 0  # tool_round_num
        cb = 0   # callback_count
        tin = 0  # total tokens in
        tout = 0 # total tokens out
        tnames: List[str] = []
        cached = 0
        model = ""
        tr = 0   # total_rounds
        max_tr = self.max_tool_rounds + self.max_round_callbacks
        t0 = time.monotonic()

        while tr < max_tr:
            ctx.tool_round_num = trn
            ctx.callback_count = cb
            ctx.messages = current

            # ── pre_llm ──
            for h in self.hooks:
                if not hasattr(h, 'pre_llm'):
                    continue
                r = await h.pre_llm(current, ctx)
                if r and r.abort:
                    return LoopResult(final_output=None, aborted=True, abort_reason=r.abort_reason,
                        metadata=self._md(tin, tout, trn, tnames, cached, records, model, cb,
                                          int((time.monotonic() - t0) * 1000),
                                          "quota_exceeded", r.abort_reason,
                                          uid=user_id, gid=group_id, msgs=messages, temp=temperature))
                if r and r.messages is not None:
                    current = r.messages

            # ── generate ──
            try:
                resp = await self.provider.generate(
                    messages=current, tools=all_tools,
                    temperature=temperature, timeout=timeout)
            except NonRetryableError:
                raise
            except Exception as e:
                logger.error(f"AgentLoop LLM error: {self._ce(e)} {e}", exc_info=True)
                raise

            tr += 1
            tin += resp.usage.input
            tout += resp.usage.output
            cached = resp.usage.cached
            if resp.model:
                model = resp.model

            # ── think ──
            raw = resp.content or ""
            think = self._extract_think(raw)
            resp.content = self._filter_think_tags(raw)
            resp._think_raw = think  # type: ignore[attr-defined]

            # 截断
            tcs = resp.tool_calls
            if len(tcs) > self.MAX_TOOLS_PER_ROUND:
                logger.warning(f"工具超限 {len(tcs)} > {self.MAX_TOOLS_PER_ROUND}")
                tcs = tcs[:self.MAX_TOOLS_PER_ROUND]

            # ── L1 ──
            if (trn < self.max_tool_rounds and not tcs
                    and all_tools
                    and cb < self.max_round_callbacks):
                current.append(dict(_L1_MSG))
                cb += 1
                records.append({"round": trn, "think": think, "tool_calls": [],
                                "tool_results": [], "callback": dict(_L1_MSG)})
                continue

            # ── post_llm ──
            injected = None
            for h in self.hooks:
                if not hasattr(h, 'post_llm'):
                    continue
                try:
                    hr = await h.post_llm(current, resp, ctx)
                except Exception as e:
                    logger.warning(f"post_llm hook error ({type(h).__name__}): {e}", exc_info=True)
                    continue
                if hr is not None and getattr(h, 'injects_message', False):
                    injected = hr

            if (injected and trn < self.max_tool_rounds
                    and cb < self.max_round_callbacks):
                current.append(injected)
                cb += 1
                records.append({"round": trn, "think": think,
                    "tool_calls": [{"id": tc.id, "name": tc.name, "arguments": tc.arguments} for tc in tcs],
                    "tool_results": [], "callback": injected})
                continue

            # ── 无工具 → 返回 ──
            if not tcs:
                records.append({"round": trn, "think": think, "tool_calls": [],
                                "tool_results": [], "callback": None})
                return LoopResult(
                    final_output=resp.content or "",
                    metadata=self._md(tin, tout, trn, tnames, cached, records, model, cb,
                                      int((time.monotonic() - t0) * 1000), "ok",
                                      content=resp.content or "",
                                      uid=user_id, gid=group_id, msgs=messages, temp=temperature))

            # ── finish_task 过滤 ──
            other_tcs = [tc for tc in tcs if tc.name != "finish_task"]
            finish_tcs = [tc for tc in tcs if tc.name == "finish_task"]
            if finish_tcs and other_tcs:
                logger.warning(
                    f"finish_task 与其他工具共存（总计 {len(tcs)} 个），"
                    f"忽略 finish_task 并执行其余 {len(other_tcs)} 个工具")
                tcs = other_tcs
            elif finish_tcs and not other_tcs:
                # ── finish_task 终止 ──
                finish_tc = finish_tcs[0]
                try:
                    finish_args = json.loads(finish_tc.arguments or "{}")
                except json.JSONDecodeError:
                    finish_args = {}
                finish_summary = finish_args.get("summary", "")
                records.append({"round": trn, "think": think,
                                "tool_calls": [{"id": finish_tc.id, "name": "finish_task",
                                                "arguments": finish_tc.arguments}],
                                "tool_results": [], "callback": None,
                                "finish_summary": finish_summary})
                current.append({
                    "role": "assistant", "content": resp.content or "",
                    "tool_calls": [{"id": finish_tc.id, "type": "function",
                                     "function": {"name": "finish_task",
                                                  "arguments": finish_tc.arguments}}],
                })
                tnames.append("finish_task")
                return LoopResult(
                    final_output=resp.content or finish_summary,
                    metadata=self._md(tin, tout, trn, tnames, cached, records, model, cb,
                                      int((time.monotonic() - t0) * 1000), "finished",
                                      content=resp.content or finish_summary,
                                      uid=user_id, gid=group_id, msgs=messages, temp=temperature))

            # ── 工具分派 ──
            tc_dicts = [{"id": tc.id, "name": tc.name, "arguments": tc.arguments} for tc in tcs]
            for tc in tcs:
                if tc.name not in tnames:
                    tnames.append(tc.name)

            current.append({
                "role": "assistant", "content": resp.content or "",
                "tool_calls": [{"id": tc.id, "type": "function",
                    "function": {"name": tc.name, "arguments": tc.arguments}} for tc in tcs],
            })
            executor = self._executor(tool_domains, tool_ctx)
            try:
                tresults = await executor([tc.to_dict() for tc in tcs])
            except Exception as e:
                logger.warning(f"工具执行异常: tool={tnames}, error={e}", exc_info=True)
                tresults = [{"tool_call_id": tc.id, "content": f"工具执行失败: {e}"} for tc in tcs]
            for tr_item in tresults:
                current.append({"role": "tool", "tool_call_id": tr_item["tool_call_id"],
                                "content": tr_item["content"]})
            tr_dicts = [{"tool_call_id": r["tool_call_id"], "content": r["content"]} for r in tresults]

            if self.tool_registry:
                for h in self.hooks:
                    if not hasattr(h, 'post_tool'):
                        continue
                    try:
                        await h.post_tool(
                            [ToolResult(tool_call_id=r["tool_call_id"], content=r["content"]) for r in tresults], ctx)
                    except Exception as e:
                        logger.warning(f"post_tool hook error ({type(h).__name__}): {e}", exc_info=True)

            trn += 1
            records.append({"round": trn - 1, "think": think, "tool_calls": tc_dicts,
                            "tool_results": tr_dicts, "callback": None})
            continue

        return LoopResult(final_output="",
            metadata=self._md(tin, tout, trn, tnames, cached, records, model, cb,
                              int((time.monotonic() - t0) * 1000), "max_rounds",
                              uid=user_id, gid=group_id, msgs=messages, temp=temperature))

    def _executor(self, domains: Optional[List[str]], tool_ctx: Any):
        if not self.tool_registry:
            async def _noop(tcs):
                return [{"tool_call_id": tc["id"], "content": "工具执行不可用"} for tc in tcs]
            return _noop
        if domains:
            return self.tool_registry.make_executor_for(*domains, ctx=tool_ctx)
        return self.tool_registry.make_executor_for(
            *getattr(self.tool_registry, '_domains', {}).keys(), ctx=tool_ctx)

    @staticmethod
    def _extract_think(content: Optional[str]) -> Optional[str]:
        if not content:
            return None
        blocks = re.findall(_THINK_RE, content, flags=re.DOTALL)
        return "".join(blocks) if blocks else None

    @staticmethod
    def _filter_think_tags(content: str) -> str:
        return re.sub(_THINK_RE, '', content, flags=re.DOTALL).strip()

    @staticmethod
    def _ce(e: Exception) -> str:
        return classify(e).value

    @staticmethod
    def _md(tin=0, tout=0, trn=0, tnames=None, cached=0, records=None, model="",
            cb=0, lat=0, status="ok", error="", content="", uid="", gid="", msgs=None, temp=None):
        return {
            "model": model, "tokens_input": tin, "tokens_output": tout,
            "tool_rounds": trn, "tool_names": tnames or [], "cached_tokens": cached,
            "round_records": records or [], "callback_count": cb,
            "latency_ms": lat, "status": status, "error": error,
            "content": content, "user_id": uid, "group_id": gid,
            "messages": msgs or [], "temperature": temp,
        }
