"""
SA Agent — Story Architect 长期叙事规划

每天日记生成后触发。读 diary + events + DM state → LLM 产出叙事规划 → 写入 SAState.notes。
Phase 1: 纯文本 notes，不涉及条目化。
"""
from typing import Any, Optional
from utils.logger import logger
from ..data.models import SAState
from ..data.store import PersonaDataStore
from ..llm.router import LLMRouter
from ..llm.selection import SUMMARIZE
from .agent import Agent
from .types import AgentResult


class SAAgent(Agent):
    """SA Agent — Story Architect 长期叙事规划"""

    name = "SA"
    role = "Story Architect — 长期叙事规划"
    state_model = SAState
    tools = []  # Phase 1: 无工具，context 直接注入日记+事件

    def __init__(
        self,
        store: PersonaDataStore,
        router: LLMRouter,
        config=None,
        tool_registry=None,
    ):
        super().__init__(store, router, config, tool_registry=tool_registry)

    async def load_state(self) -> SAState:
        """从 store 加载 SA 世界设定"""
        return await self.store.get_sa_state()

    async def save_state(self, state: SAState) -> None:
        """持久化 SA 世界设定"""
        await self.store.update_sa_state(state)

    def build_system_prompt(self, state: SAState, context: dict) -> str:
        """构建 SA 系统提示词"""
        notes = state.notes or "（尚无规划设定）"
        diary_text = context.get("diary_text", "")
        events_text = context.get("events_text", "")
        dm_scratchpad = context.get("dm_scratchpad", "")

        system_prompt = f"""你是 Story Architect（SA），负责角色的长期叙事规划。

你的职责：
1. 审视角色近期的日记、事件和 DM 记录
2. 思考角色成长弧线、NPC 关系和潜在的剧情发展
3. 将叙事规划以自由文本形式记录在 notes 中

当前已有叙事设定:
{notes}

素材审视:
- 日记:\n{diary_text or "（无）"}
- 事件:\n{events_text or "（无）"}
- DM 记录:\n{dm_scratchpad or "（无）"}

要求:
1. 保持第一人称"我"作为叙事规划者视角
2. 关注角色成长、关系变化、未完成的目标
3. 可以规划未来 1-3 天的可能发展
4. 不直接修改角色数值
5. 笔记内容会被下次 DM 执行时参考

请更新叙事规划，只输出更新后的 notes 文本。"""
        return system_prompt

    def _get_selection_policy(self):
        return SUMMARIZE

    def _get_openai_tools(self) -> list:
        """Phase 1: SA 无工具调用，直接输出文本"""
        return []

    async def run(self, context: dict) -> AgentResult:
        """统一入口 — 委托到 plan()。"""
        return await self.plan(context)

    async def plan(self, context: dict) -> AgentResult:
        """SA 规划入口

        读 diary + events + DM state → LLM 产出叙事规划 → 写入 SAState.notes。
        使用 AgentRuntime.run() 直接调用（无工具收集管道），读取 result.final_text。

        Returns:
            AgentResult(data=SAState)
        """
        state = await self.load_state()
        system_prompt = self.build_system_prompt(state, context)
        user_prompt = "请更新叙事规划："

        try:
            from ..agent.runtime import AgentRuntime
            from ..agent.request import AgentRunLimits
            from ..agent.tool_executor import ToolRegistry

            runtime = AgentRuntime(
                router=self.router,
                store=self.store,
                limits=AgentRunLimits(max_rounds=1),
            )

            result = await runtime.run(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                user_id="",
                group_id="",
                tool_registry=ToolRegistry(),
                temperature=0.85,
                timeout=None,
                selection=SUMMARIZE,
            )
            text = (result.final_text or "").strip().strip('"').strip("'")

            if text:
                state.notes = text
                await self.save_state(state)
                logger.info(f"SA 叙事规划已更新: {len(text)} 字")
                return AgentResult(success=True, data=state, raw_response=text)
            else:
                logger.warning("SA 规划输出为空")
                return AgentResult(
                    success=False,
                    data=state,
                    error="SA 输出为空",
                )
        except Exception:
            logger.exception("SA 规划执行失败")
            return AgentResult(
                success=False,
                data=state,
                error="SA 执行异常",
            )
