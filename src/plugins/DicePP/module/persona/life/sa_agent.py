"""
SA Agent — Story Architect 长期叙事规划

每天日记生成后触发。多轮 tool-call 操作 story_deck 条目和 fronts 规划。
使用 AgentRuntime.run() 获取多轮 tool-call 结果。
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
    tools = []

    def __init__(
        self,
        store: PersonaDataStore,
        router: LLMRouter,
        config=None,
        tool_registry=None,
    ):
        super().__init__(store, router, config, tool_registry=tool_registry)

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
        """SA 使用 AgentRuntime 多轮 tool-call，不在此返回工具"""
        return []

    async def run(self, context: dict) -> AgentResult:
        """统一入口 — 委托到 plan()。"""
        return await self.plan(context)

    async def plan(self, context: dict) -> AgentResult:
        """SA 规划入口

        加载 fronts → 构建 user prompt → AgentRuntime 多轮 tool-call →
        保存 fronts → 返回结果。

        max_rounds=100，允许 SA 进行多轮探索和修正。

        Returns:
            AgentResult(data=SAState)
        """
        state = await self.load_state()

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

        # 构建 system prompt（固定部分）
        system_prompt = self.build_system_prompt(state, context)

        # 构建 user prompt（动态部分：角色信息 + fronts + 日记 + 事件 + 规则）
        user_prompt = self._build_user_prompt(context, fronts_dicts)

        # 延迟导入避免循环依赖（sa_agent → story_deck → store）
        from ..agent.runtime import AgentRuntime
        from ..agent.request import AgentRunLimits, ToolUseMode
        from ..tools.story_deck import build_sa_tool_registry

        try:
            # 获取配置参数
            max_entries = self.config.story_deck_max_entries if self.config else 100
            front_max_campaign = self.config.front_max_campaign if self.config else 1
            front_max_adventure = self.config.front_max_adventure if self.config else 2
            threads_per_front = self.config.threads_per_front if self.config else 3
            # 默认 100 轮：SA 需在单次规划中创建 campaign front + ≤2 adventure
            # fronts + 多条 entity/plot 条目，且工具返回 errors 时需修正重试。
            sa_max_rounds = self.config.sa_max_rounds if self.config else 100

            # 构建工具注册表（传入 fronts 可变引用）
            tool_registry = build_sa_tool_registry(
                store=self.store,
                fronts=fronts_dicts,
                max_entries=max_entries,
                front_max_campaign=front_max_campaign,
                front_max_adventure=front_max_adventure,
                threads_per_front=threads_per_front,
            )

            runtime = AgentRuntime(
                router=self.router,
                store=self.store,
                limits=AgentRunLimits(max_rounds=sa_max_rounds),
            )

            result = await runtime.run(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                user_id="",
                group_id="",
                tool_registry=tool_registry,
                tools=tool_registry.get_openai_schemas(),
                tool_use_mode=ToolUseMode.AUTO,
                temperature=0.85,
                timeout=None,
                selection=SUMMARIZE,
            )

            # 将 fronts_dicts 转回 Pydantic Front 模型
            # 使用 .get() 防御 LLM 产生的非法字典（缺少必填字段）
            from ..data.models import Front, Thread
            new_fronts = []
            for fd in fronts_dicts:
                fd_name = fd.get("name", "").strip()
                fd_type = fd.get("type", "").strip()
                if not fd_name or not fd_type:
                    logger.warning(f"front dict 缺少必填字段 (name/type)，跳过: {fd}")
                    continue
                if fd_type not in ("campaign", "adventure"):
                    logger.warning(f"front type 无效 '{fd_type}'（合法值: campaign/adventure），跳过: {fd}")
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
            final_text = getattr(result, "final_text", "") or ""
            logger.info(f"SA 叙事规划完成: {len(state.fronts)} fronts, {sum(len(f.threads) for f in state.fronts)} threads")
            return AgentResult(success=True, data=state, raw_response=final_text)

        except Exception as e:
            logger.exception("SA 规划执行失败")
            return AgentResult(
                success=False,
                data=state,
                error=f"SA 执行异常: {e}",
            )

    def _build_user_prompt(self, context: dict, fronts_dicts: list) -> str:
        """构建 SA user prompt：角色信息 + fronts 现状 + 日记 + 事件 + _FRONT_RULES"""
        from ..tools.story_deck import _FRONT_RULES, _format_fronts

        character_name = context.get("character_name", "")
        character_description = context.get("character_description", "")
        world = context.get("world", "")
        scenario = context.get("scenario", "")
        diary_text = context.get("diary_text", "")
        events_text = context.get("events_text", "")

        prompt = f"""角色信息：
{character_name} — {character_description}
世界观：{world}
场景：{scenario}

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
