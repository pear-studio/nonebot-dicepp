"""
Character Agent — 角色第一人称

合并 reaction + diary + opening 三个模式。
Phase 1: 真实 LLM 调用，根据 context.mode 使用不同 prompt 模板。
"""
import asyncio
import json
from typing import Any, List, Optional, TYPE_CHECKING
from plugins.DicePP.utils.logger import logger
from ..data.models import CharacterState
from ..data.store import PersonaDataStore
from ..llm.client import TextModelClient
from ..tools.collecting import (
    SAY_TOOL_CHARACTER,
)
from plugins.DicePP.utils.time import format_timestamp, format_relative_time
from .agent import Agent
from .change_sources import CharacterStateChangeSource
from .types import AgentResult, EventReactionResult

if TYPE_CHECKING:
    from plugins.DicePP.core.config.pydantic_models import PersonaConfig


class CharacterAgent(Agent):
    """Character Agent — 角色第一人称

    状态数据（体力/心情/健康）通过 ChangeSource 通知管道在 Conversation 中注入——
    首轮 fetch_notifications()/apply_notifications() 获得各维度的初始化通知，后续仅在状态变化时产生增量通知
    （如 "体力 -10 (当前 65/100)"）。同天多轮反应且状态无变化时，LLM 仅依靠上下文记忆
    感知状态，不再在每轮 user prompt 中内联注入绝对值。
    """

    name = "Character"
    role = "角色第一人称"
    state_model = CharacterState
    tools = ["say"]

    def __init__(
        self,
        store: PersonaDataStore,
        client: TextModelClient,
    ):
        super().__init__(store, client)

    async def load_state(self) -> CharacterState:
        """从 store 加载角色状态"""
        return await self.store.get_character_state()

    async def save_state(self, state: CharacterState) -> None:
        """持久化角色状态"""
        await self.store.update_character_state(state)

    @staticmethod
    def _format_state_prompt(
        energy: Optional[int],
        mood: Optional[int],
        health: Optional[int],
    ) -> str:
        """构建状态 prompt 片段"""
        lines = []
        if energy is not None:
            lines.append(f"体力: {energy}/100")
        if mood is not None:
            lines.append(f"心情: {mood}/100")
        if health is not None:
            lines.append(f"健康: {health}/100")
        return "\n".join(lines) if lines else "无记录"

    def build_system_prompt(self, state: CharacterState, context: dict) -> str:
        """构建纯人设层 system prompt — 角色身份 + 核心边界。

        不包含 mode 特定任务指令。任务指令由各 user prompt builder 注入。
        state 形参保留给 Agent ABC 接口兼容；角色身份数据实际通过 context dict 注入。
        状态数据（体力/心情/健康）：reaction 模式由 CharacterStateChangeSource
        通过 Conversation 通知管道注入，diary 当前仍在 user prompt 层注入。
        """
        character_name = context.get("character_name", "")
        character_description = context.get("character_description", "")

        system_prompt = f"""你是{character_name}。

角色设定:
{character_description}

核心要求:
1. 使用第一人称"我"
2. 语气符合角色性格
3. 不编造与当前上下文无关的内容
4. 不要替 DM 叙述结果——你只表达自己的感受、想法和行动意图"""
        return system_prompt

    def _get_change_sources(self) -> "list[ChangeSource]":
        """订阅角色状态变更通知（体力/心情/健康三维合并为单 source，一次 DB 查询）。"""
        return [CharacterStateChangeSource(self.store)]

    # ── Opening Prompt ───────────────────────────────────────

    # ── Build User Prompt ────────────────────────────────────

    def _build_user_prompt(self, context: dict) -> str:
        mode = context.get("mode", "reaction")
        if mode == "reaction":
            return self._build_reaction_user_prompt(context)
        elif mode == "diary":
            return self._build_diary_user_prompt(context)
        elif mode == "opening":
            return self._build_opening_user_prompt(context)
        return str(context)

    def _build_reaction_user_prompt(self, context: dict) -> str:
        event = context.get("event", "")
        today_events = context.get("today_events", [])
        dm_want_to_end = context.get("dm_want_to_end", False)

        today_context = ""
        if today_events:
            events_lines = []
            from plugins.DicePP.utils.time import get_clock
            now = get_clock().now()
            for e in today_events:
                created_at = e.get("created_at")
                if created_at:
                    ts = format_timestamp(created_at, now)
                    rel = format_relative_time(created_at, now)
                    time_str = f"{ts} {rel}" if rel else ts
                else:
                    time_str = e.get("time", "??:??")
                desc = e.get("description", "")
                events_lines.append(f"- [{time_str}] {desc}")
            today_context = "\n今天已发生事件:\n" + "\n".join(events_lines)

        context_prefix = f"{today_context}\n" if today_context else ""
        user_prompt = (
            f"{context_prefix}"
            f"当前事件: {event}"
            f"\n\n请对发生的事做出反应。"
            f"\n\n要求: 30-200字，第一人称，反映角色性格和当前状态。"
            f"\n你想做什么就说什么——DM 会根据你的行动决定是否需要裁决并叙述结果。"
            f"\n\n结束场景:"
            f"\n- 如果你觉得场景可以自然收束了，设置 want_to_end=true"
            f"\n- 收到 DM 的结束提议时，同意则设置 want_to_end=true，不同意则继续表达反应"
        )

        # 注入 DM 的 want_to_end 信号
        if dm_want_to_end:
            user_prompt += (
                "\n\n[提示] DM 认为当前场景可以收束了。"
                "如果你也同意，设置 want_to_end=true。"
                "如果你还有想说的或想做的，继续表达即可（会覆盖结束提议）。"
            )

        return user_prompt

    def _build_diary_user_prompt(self, context: dict) -> str:
        events = context.get("events", [])
        yesterday_diary = context.get("yesterday_diary")
        energy = context.get("energy")
        mood = context.get("mood")
        health = context.get("health")
        state_text = self._format_state_prompt(energy, mood, health)

        events_lines = []
        from plugins.DicePP.utils.time import get_clock
        now = get_clock().now()
        for e in events:
            created_at = e.get("created_at")
            if created_at:
                ts = format_timestamp(created_at, now)
                rel = format_relative_time(created_at, now)
                time_str = f"{ts} {rel}" if rel else ts
            else:
                time_str = e.get("time", "??:??")
            summary = e.get("context_summary", "") or e.get("description", "")[:80]
            reaction = e.get("reaction", "")
            events_lines.append(f"- [{time_str}] {summary}\n  我的反应: {reaction}")
        events_text = "\n".join(events_lines)

        yesterday_context = ""
        if yesterday_diary:
            yesterday_context = f"\n\n昨天的日记:\n{yesterday_diary[:200]}..."

        date_str = now.strftime("%Y年%m月%d日")

        user_prompt = f"""当前日期: {date_str}
今天最终状态:
{state_text}

今天经历的一些片段:
{events_text}{yesterday_context}

写今天的日记。
日记是你的私人空间——可以记录，可以反省，可以计划，可以抱怨，可以什么也不写。用你习惯的方式写。
要求:
1. 100-200字
2. 语气符合角色性格
3. 不需要提及今天发生的所有事——选你真正想写的来写"""
        return user_prompt

    def _build_opening_user_prompt(self, context: dict) -> str:
        summary = context.get("summary", "")
        return f"""请用第一人称"我"，以轻松自然的语气写2-3句话，作为每日报告的简短开场白。要点：
1. 提及昨天发生了一些事（参考摘要），语气根据角色个性自然表达
2. 不要复述具体数据，数据会由系统附加在报告中
3. 像日常聊天一样自然，不要生硬的"汇报如下""数据汇总"等公文用语
4. 不需要落款署名

摘要信息:
{summary}"""

    # ── Template Method Overrides ───────────────────────────────

    async def build_run_spec(self, context: dict) -> "AgentRunSpec":
        """按 mode 分派到专用的 spec builder。"""
        mode = context.get("mode", "reaction")
        if mode == "reaction":
            return await self._build_reaction_spec(context)
        elif mode == "diary":
            return await self._build_diary_spec(context)
        return await self._build_reaction_spec(context)

    async def interpret_result(
        self, result: "ConversationRunResult", context: dict
    ) -> AgentResult:
        """按 mode 分派到专用的 result interpreter。"""
        mode = context.get("mode", "reaction")
        if mode == "reaction":
            return self._interpret_reaction_result(result, context)
        elif mode == "diary":
            return self._interpret_diary_result(result)
        return self._interpret_reaction_result(result, context)

    async def run(
        self, context: dict, *, interaction_id: str,
    ) -> AgentResult:
        """统一入口 — 根据 context["mode"] 分派到专用方法。

        mode: "reaction" | "diary" | "opening"
        """
        mode = context.get("mode", "reaction")
        if mode == "reaction":
            return await self.react(context, interaction_id=interaction_id)
        elif mode == "diary":
            return await self.diary(context, interaction_id=interaction_id)
        elif mode == "opening":
            return await self.opening(context, interaction_id=interaction_id)
        else:
            return await self.react(context, interaction_id=interaction_id)

    async def react(
        self, context: dict, *, interaction_id: str,
    ) -> AgentResult:
        """角色对事件做出反应（通过 Conversation 管理消息线程）

        T4: 使用 AgentRunSpec + say OutputSpec 新路径，
        从 result.output_arguments 读取结构化输出，不再从 messages 反解析。

        context 字段:
            event: str — 事件描述
            character_name: str
            character_description: str
            today_events: List[dict]
            dm_want_to_end: bool — DM 是否提议结束

        状态数据（体力/心情/健康）现由 CharacterStateChangeSource 通过
        Conversation 通知管道注入，无需 caller 在 context 中传递。

        Returns:
            AgentResult(data=EventReactionResult)
        """
        context["mode"] = "reaction"
        return await super().run(context, interaction_id=interaction_id)

    async def _build_reaction_spec(self, context: dict) -> "AgentRunSpec":
        """T4: 构建 reaction 的 AgentRunSpec — say 作为 OutputSpec。


        """
        from ..agent.runtime_types import (
            AgentRunSpec, LoopLimits, ToolKit, OutputSpec,
        )
        from ..tools.collecting import SayArgs, SAY_TOOL_CHARACTER

        state = await self.load_state()
        system_prompt = self.build_system_prompt(state, context)
        user_prompt = self._build_reaction_user_prompt(context)

        output_spec = OutputSpec(
            name="say",
            description=SAY_TOOL_CHARACTER.description,
            args_schema=SayArgs,
        )

        return AgentRunSpec(
            system_prompt=system_prompt,
            user_input=user_prompt,
            tools=ToolKit(),
            output=output_spec,
            task="event",
            limits=LoopLimits(max_rounds=self._max_rounds),
            run_tag="reaction",
            user_id=context.get("user_id", ""),
            group_id=context.get("group_id", ""),
        )

    def _interpret_reaction_result(
        self, result: "ConversationRunResult", context: dict
    ) -> AgentResult:
        """T4: 从 output_arguments 读取 say 结构化输出。


        """
        args = result.output_arguments

        # args 为 None：LLM 未调用 say 工具
        if args is None:
            logger.warning("反应生成: LLM 未调用 say 工具")
            return AgentResult(
                success=False,
                data=EventReactionResult(reaction="（默默地想着这件事）"),
                error="LLM 未调用 say 工具",
            )

        # 正常路径：从 output_arguments 解析
        try:
            character_name = context.get("character_name", "")
            reaction = str(args.get("content", "")).strip().strip('"').strip("'")
            if not reaction:
                reaction = f"（{character_name}默默地想着这件事）"
            has_follow_up = bool(args.get("has_follow_up", False))
            want_to_end = bool(args.get("want_to_end", False))
            if len(reaction) > 200:
                reaction = reaction[:197] + "..."
            return AgentResult(
                success=True,
                data=EventReactionResult(
                    reaction=reaction,
                    has_follow_up=has_follow_up,
                    want_to_end=want_to_end,
                    last_say_content=reaction,
                    raw_response=json.dumps(args, ensure_ascii=False),
                ),
                raw_response=json.dumps(args, ensure_ascii=False),
            )
        except Exception as e:
            logger.error(f"反应生成解析失败: {e}", exc_info=True)
            return AgentResult(
                success=False,
                data=EventReactionResult(
                    reaction="（默默地想着这件事）",
                    has_follow_up=False,
                    want_to_end=False,
                ),
                error=str(e),
            )

    async def diary(
        self, context: dict, *, interaction_id: str,
    ) -> AgentResult:
        """生成日记（通过 Conversation 复用天内 reaction 上下文）

        使用 AgentRunSpec + submit_diary OutputSpec 新路径，
        从 result.output_arguments 读取结构化输出。

        context 字段:
            events: List[dict] — 当天事件列表
            character_name: str
            character_description: str
            yesterday_diary: Optional[str]
            energy/mood/health: Optional[int]

        Returns:
            AgentResult(data=str) — 日记文本
        """
        context["mode"] = "diary"
        return await super().run(context, interaction_id=interaction_id)

    async def _build_diary_spec(self, context: dict) -> "AgentRunSpec":
        """T4: 构建 diary 的 AgentRunSpec — submit_diary 作为 OutputSpec。

        submit_diary 复用 RecordDiaryEntryArgs schema，日记文本由外层 DiaryGenerator 保存。
        """
        from ..agent.runtime_types import (
            AgentRunSpec, LoopLimits, OutputSpec, ToolKit,
        )
        from ..tools.collecting import RecordDiaryEntryArgs

        state = await self.load_state()
        system_prompt = self.build_system_prompt(state, context)
        user_prompt = self._build_diary_user_prompt(context)

        output_spec = OutputSpec(
            name="submit_diary",
            description="提交角色的日记内容，由系统保存为当日日记。",
            args_schema=RecordDiaryEntryArgs,
        )

        return AgentRunSpec(
            system_prompt=system_prompt,
            user_input=user_prompt,
            tools=ToolKit(),
            output=output_spec,
            task="diary",
            limits=LoopLimits(max_rounds=self._max_rounds),
            run_tag="diary",
            user_id=context.get("user_id", ""),
            group_id=context.get("group_id", ""),
        )

    def _interpret_diary_result(
        self, result: "ConversationRunResult"
    ) -> AgentResult:
        """T4: 从 output_arguments 读取日记结构化输出。"""
        args = result.output_arguments
        if args is None:
            logger.warning("日记生成: LLM 未调用 submit_diary")
            return AgentResult(
                success=False,
                data="今天发生了一些事，但我太累了，简单记录一下。",
                error="LLM 未调用 submit_diary",
            )

        try:
            diary_text = str(args.get("diary", "")).strip()
            if not diary_text:
                diary_text = "今天发生了一些事，但我太累了，简单记录一下。"
            if len(diary_text) > 300:
                diary_text = diary_text[:297] + "..."
            return AgentResult(success=True, data=diary_text)
        except Exception as e:
            logger.error(f"日记生成解析失败: {e}", exc_info=True)
            return AgentResult(
                success=False,
                data=None,
                error=f"日记解析异常: {e}",
            )

    async def opening(
        self, context: dict, *, interaction_id: str,
    ) -> AgentResult:
        """生成日报开场白

        context 字段:
            character_name: str
            character_description: str
            summary: str — 摘要信息

        Returns:
            AgentResult(data=str) — 开场白文本，失败返回 None
        """
        context["mode"] = "opening"

        state = await self.load_state()
        system_prompt = self.build_system_prompt(state, context)
        user_prompt = self._build_opening_user_prompt(context)

        try:
            from ..agent.runtime_types import LoopLimits, ToolKit

            result = await self._run_conversation(
                context,
                system_prompt_override=system_prompt,
                system_prompt=system_prompt,
                user_input=user_prompt,
                interaction_id=interaction_id,
                tools=ToolKit(),
                output=None,
                task="summary",
                limits=LoopLimits(max_rounds=1),
                run_tag="opening",
                agent_name=self.name,
            )
            text = (result.final_text or "").strip().strip('"').strip("'")
            if not text:
                return AgentResult(success=True, data=None)
            return AgentResult(success=True, data=text[:200])
        except Exception:
            logger.warning("opening 生成失败", exc_info=True)
            return AgentResult(success=False, data=None, error="opening 生成失败")
