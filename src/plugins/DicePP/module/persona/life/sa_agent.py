"""
SA Agent — Story Architect 长期叙事规划

每天日记生成后触发。多轮 tool-call 操作 story_deck 条目和 fronts 规划。
通过 Agent 基类 AgentRunSpec 新路径执行。
"""
from typing import Any, Optional
from utils.logger import logger
from ..data.models import SAState
from ..data.store import PersonaDataStore
from ..llm.router import LLMRouter
from ..llm.selection import SUMMARIZE
from ..agent.runtime_types import AgentRunSpec, FinishPlanArgs, LoopLimits, OutputSpec
from .agent import Agent
from .types import AgentResult


class SAAgent(Agent):
    """SA Agent — Story Architect 长期叙事规划"""

    name = "SA"
    role = "Story Architect — 长期叙事规划"
    state_model = SAState
    tools = []

    def __init__(
        self,
        store: PersonaDataStore,
        router: LLMRouter,
        config=None,
    ):
        super().__init__(store, router, config)
        self._sa_fronts_dicts: list = []  # build_run_spec → interpret_result 间传递
        self._sa_state: Optional[SAState] = None

    async def load_state(self) -> SAState:
        """从 store 加载 SA 叙事规划"""
        return await self.store.get_sa_state()

    async def save_state(self, state: SAState) -> None:
        """持久化 SA 叙事规划"""
        await self.store.update_sa_state(state)

    def build_system_prompt(self, state: SAState, context: dict) -> str:
        """构建 SA 系统提示词

        SA 通过工具调用来管理 story_deck 和 fronts，
        system prompt 只描述角色和职责，具体规则在 user prompt 的 _FRONT_RULES 中。
        """
        return f"""你是 Story Architect（SA），负责角色的长期叙事规划。

你的职责：
1. 审视角色的日记和每日事件，从 DM 的叙述中识别新创造的实体和线索
2. 管理叙事条目库（story_deck）：创建 entity/plot/detail 条目，用 [[key]] 语法建立关联
3. 维护叙事前线（fronts）：规划 campaign 和 adventure 级别的叙事线（threads）
4. 每天审视已有的 thread：推进、调整、合并或完结

工作方式：
- 使用 search_story_deck 查看条目详情
- 使用 list_story_deck 浏览条目全貌
- 使用 read_past_events 追溯历史事件
- 使用 edit_story_deck 批量增删改条目
- 使用 edit_fronts 增量调整规划
- 看到 errors 后修正重试

条目类型：
- entity：扮演锚点——人、地点、物品
- plot：推进方向——叙事钩子，最高优先级
- detail：从 entity 拆出的扩展信息，挂在 entity 下

用 [[条目key]] 语法在 content 中建立条目之间的关联。"""

    def _get_selection_policy(self):
        return SUMMARIZE

    def _get_openai_tools(self) -> list:
        """SA 通过 ToolKit + OutputSpec 执行，不在此返回工具"""
        return []

    # ── T5: AgentRunSpec 路径 ─────────────────────────────────

    async def build_run_spec(self, context: dict) -> AgentRunSpec:
        """构建 SA run 规格：加载 state → 构建 ToolKit + finish_plan OutputSpec。

        将 fronts_dicts 存入 self._sa_fronts_dicts，供 interpret_result 使用。
        """
        from ..tools.story_deck import build_sa_toolkit

        # 加载状态
        state = await self.load_state()
        self._sa_state = state

        # 将 Pydantic Front 模型转为 dict 列表（便于 tool 修改）
        fronts_dicts = []
        for f in state.fronts:
            fd = {
                "name": f.name,
                "type": f.type,
                "threads": [
                    {
                        "name": t.name,
                        "direction": t.direction,
                        "milestones": list(t.milestones),
                        "outcome": t.outcome,
                        "related": list(t.related),
                    }
                    for t in f.threads
                ],
            }
            fronts_dicts.append(fd)
        self._sa_fronts_dicts = fronts_dicts

        # 构建 system / user prompt
        system_prompt = self.build_system_prompt(state, context)
        user_prompt = self._build_user_prompt(context, fronts_dicts)

        # 获取配置参数
        max_entries = self.config.story_deck_max_entries if self.config else 100
        front_max_campaign = self.config.front_max_campaign if self.config else 1
        front_max_adventure = self.config.front_max_adventure if self.config else 2
        threads_per_front = self.config.threads_per_front if self.config else 3
        sa_max_rounds = self.config.sa_max_rounds if self.config else 100

        # 构建 ToolKit（新路径）
        toolkit = build_sa_toolkit(
            store=self.store,
            fronts=fronts_dicts,
            max_entries=max_entries,
            front_max_campaign=front_max_campaign,
            front_max_adventure=front_max_adventure,
            threads_per_front=threads_per_front,
        )

        # finish_plan OutputSpec — SA 必须调用此输出标记规划完成
        finish_plan_spec = OutputSpec(
            name="finish_plan",
            description=(
                "提交规划结果，标记本次规划完成。"
                "即使无需修改，也必须调用此工具。"
                "summary 简短说明做了什么或为什么无需调整。"
                "changed 表示是否修改了 story_deck 或 fronts。"
            ),
            args_schema=FinishPlanArgs,
        )

        return AgentRunSpec(
            system_prompt=system_prompt,
            user_input=user_prompt,
            tools=toolkit,
            output=finish_plan_spec,
            selection=SUMMARIZE,
            limits=LoopLimits(max_rounds=sa_max_rounds),
            run_tag="sa_plan",
        )

    async def interpret_result(
        self, result: "ConversationRunResult", context: dict
    ) -> AgentResult:
        """解释 Conversation.run() 结果：fronts_dicts 转回 Pydantic → 保存 state。

        无论 run 是否成功（finish_plan 是否被调用），都保存 fronts
        （edit_fronts 可能在 run 中途已通过 handler 修改 fronts_dicts）。
        """
        fronts_dicts = self._sa_fronts_dicts
        state = self._sa_state
        if state is None:
            return AgentResult(success=False, data=None, error="SA state 未加载")

        # 检查 completion
        if result.completion_kind == "completed" and result.output_arguments:
            summary = result.output_arguments.get("summary", "")
            changed = result.output_arguments.get("changed", False)
            logger.info(f"SA finish_plan: changed={changed} summary={summary[:100]}")
        elif result.completion_kind != "completed":
            logger.warning(
                f"SA 规划未完成: completion={result.completion_kind} "
                f"final_reason={result.final_reason}"
            )

        # fronts_dicts 转回 Pydantic Front 模型
        from ..data.models import Front, Thread
        new_fronts = []
        for fd in fronts_dicts:
            fd_name = fd.get("name", "").strip()
            fd_type = fd.get("type", "").strip()
            if not fd_name or not fd_type:
                logger.warning(f"front dict 缺少必填字段 (name/type)，跳过: {fd}")
                continue
            if fd_type not in ("campaign", "adventure"):
                logger.warning(f"front type 无效 '{fd_type}'，跳过: {fd}")
                continue
            threads = []
            for t in fd.get("threads", []):
                t_name = t.get("name", "").strip()
                if not t_name:
                    logger.warning(f"thread dict 缺少必填字段 name，跳过: {t}")
                    continue
                threads.append(Thread(
                    name=t_name,
                    direction=t.get("direction", ""),
                    milestones=t.get("milestones", []),
                    outcome=t.get("outcome", ""),
                    related=t.get("related", []),
                ))
            new_fronts.append(Front(
                name=fd_name,
                type=fd_type,
                threads=threads,
            ))
        state.fronts = new_fronts

        await self.save_state(state)
        logger.info(
            f"SA 叙事规划完成: {len(state.fronts)} fronts, "
            f"{sum(len(f.threads) for f in state.fronts)} threads"
        )

        success = result.completion_kind == "completed" and result.output_arguments is not None
        return AgentResult(
            success=success,
            data=state,
            raw_response=(
                result.output_arguments.get("summary", "")
                if result.output_arguments
                else ""
            ),
        )

    def _build_user_prompt(self, context: dict, fronts_dicts: list) -> str:
        """构建 SA user prompt：角色信息 + fronts 现状 + 日记 + 事件 + _FRONT_RULES"""
        from ..tools.story_deck import _FRONT_RULES, _format_fronts

        character_name = context.get("character_name", "")
        character_description = context.get("character_description", "")
        world = context.get("world", "")
        diary_text = context.get("diary_text", "")
        events_text = context.get("events_text", "")

        prompt = f"""角色信息：
{character_name} — {character_description}
世界观：{world}

你的叙事线（fronts）当前状态：
{_format_fronts(fronts_dicts)}

{_FRONT_RULES}

日记：{diary_text or "（无）"}
今日事件：{events_text or "（无）"}

请审视 fronts 和故事甲板，按需调整。"""

        # bootstrap：fronts 为空且 story_deck 为空时追加引导
        story_deck_is_empty = context.get("story_deck_is_empty", False)
        if not fronts_dicts and story_deck_is_empty:
            prompt += "\n\n目前还没有 fronts 和 story_deck 条目，请根据角色设定创建初始的 campaign front 和第一批核心 entity。"

        return prompt
