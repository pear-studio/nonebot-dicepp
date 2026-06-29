"""
ActionEvaluator — 独立的 LLM 评估管线

评估用户对话中产生的行动灵感是否适合角色执行。
不依赖 CharacterLife 或 EventGenerationAgent，仅通过 store 读取上下文。
"""
import re
from typing import List, Optional, Tuple, TYPE_CHECKING

from utils.logger import logger

from ..data.store import PersonaDataStore
from ..llm.selection import SelectionPolicy, SCORING

if TYPE_CHECKING:
    from core.config.pydantic_models import PersonaConfig
    from ..llm.router import LLMRouter

# 常见中文地点词匹配
_LOCATION_RE = re.compile(
    r"(家|学校|学院|大学|商店|便利店|超市|市场|公园|花园|酒馆|酒吧|"
    r"咖啡厅|咖啡馆|餐厅|饭店|食堂|街道|广场|医院|诊所|药房|图书馆|"
    r"书店|健身房|体育馆|游泳馆|电影院|剧场|商场|办公室|公司|工厂|"
    r"车站|机场|码头|河边|海边|山上|森林|田野|寺庙|教堂)"
)

_SYSTEM_PROMPT = """你是行为可行性评估专家。根据以下信息判断角色是否可以在当前状态下执行所述行动。

硬约束：
1. 反瞬移：角色当前位置基于今日事件推断。不能从一个地点瞬间跳到另一个。
2. 反并发：进行中活动未结束时，不能同时做另一件事。
3. 时间合理性：深夜不适合外出活动，体力低不适合剧烈活动。

你必须通过调用 record_evaluation 工具来输出结果，不要直接回复文本。"""


class ActionEvaluator:
    """独立的行动可行性评估器"""

    def __init__(
        self,
        store: PersonaDataStore,
        router: "LLMRouter",
        config: "PersonaConfig",
        timezone: str = "Asia/Shanghai",
    ):
        self._store = store
        self._router = router
        self._timezone = timezone
        self._timeout = config.suggest_action_evaluation_timeout

    def _get_today_str(self) -> str:
        from utils.time import wall_now
        return wall_now(self._timezone).strftime("%Y-%m-%d")

    @staticmethod
    def _extract_location(today_events: List) -> str:
        if not today_events:
            return "unknown"
        last_event = today_events[-1]
        text = getattr(last_event, "description", "") or ""
        m = _LOCATION_RE.search(text)
        return m.group(1) if m else "unknown"

    def _build_user_prompt(
        self,
        action_idea: str,
        character_state,
        location_context: str,
        today_events: List,
        ongoing_descriptions: List[str],
    ) -> str:
        now = self._now()
        time_str = now.strftime("%H:%M")
        energy = character_state.energy if character_state.energy is not None else 50
        mood = character_state.mood if character_state.mood is not None else 50
        health = character_state.health if character_state.health is not None else 50

        events_lines = []
        for e in today_events[-5:]:
            t = getattr(e, "created_at", None)
            ts = t.strftime("%H:%M") if t else "??:??"
            desc = getattr(e, "description", "") or ""
            events_lines.append(f"- [{ts}] {desc}")
        events_text = "\n".join(events_lines) if events_lines else "无"

        ongoing_text = "\n".join(f"- {d}" for d in ongoing_descriptions) if ongoing_descriptions else "无"

        return (
            f"当前时间: {time_str}\n"
            f"角色状态: energy={energy}/100 mood={mood}/100 health={health}/100\n"
            f"当前位置推断: {location_context}\n"
            f"进行中活动:\n{ongoing_text}\n"
            f"今日事件:\n{events_text}\n"
            f"行动建议: {action_idea}"
        )

    def _now(self):
        from utils.time import wall_now
        return wall_now(self._timezone)

    async def evaluate(
        self,
        action_idea: str,
        ongoing_descriptions: Optional[List[str]] = None,
        user_id: str = "",
    ) -> Tuple[str, str]:
        """评估行动可行性，返回 (result, reason)。"""
        try:
            character_state = await self._store.get_character_state()
            if not character_state:
                return ("rejected", "角色状态不存在")

            today_str = self._get_today_str()
            today_events = await self._store.get_daily_events(today_str)
            location_context = self._extract_location(today_events)

            user_prompt = self._build_user_prompt(
                action_idea=action_idea,
                character_state=character_state,
                location_context=location_context,
                today_events=today_events,
                ongoing_descriptions=ongoing_descriptions or [],
            )

            return await self._call_llm(user_prompt, user_id=user_id)

        except Exception:
            logger.exception("[ActionEvaluator] 评估失败")
            return ("rejected", "评估异常，默认拒绝")

    async def _call_llm(self, user_prompt: str, user_id: str = "") -> Tuple[str, str]:
        from ..llm.router import ServiceUnavailableError
        from ..agent.tool_bridge import run_structured_collect

        try:
            collected_args, runtime_result, _ = await run_structured_collect(
                router=self._router,
                store=self._store,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                user_id=user_id,
                required_tools=["record_evaluation"],
                temperature=0.3,
                timeout=self._timeout,
                selection=SCORING,
                max_rounds=1,
            )
            runtime_result.log_if_failed("ActionEvaluator")
            if runtime_result.status != "completed":
                return ("rejected", "LLM 协议错误")
        except ServiceUnavailableError:
            logger.warning("[ActionEvaluator] 无可用 LLM provider")
            return ("rejected", "无可用 LLM 服务")
        except Exception:
            logger.exception("[ActionEvaluator] LLM 调用异常")
            return ("rejected", "LLM 调用失败")

        if not collected_args:
            return ("rejected", "LLM 未生成评估结果")

        args = collected_args[0]
        result_val = str(args.get("result", "rejected")).strip().lower()
        reason = str(args.get("reason", "评估失败")).strip()

        if result_val not in ("approved", "rejected", "deferred"):
            result_val = "rejected"

        logger.info(f"[ActionEvaluator] result={result_val} reason={reason[:80]}")
        return (result_val, reason)
