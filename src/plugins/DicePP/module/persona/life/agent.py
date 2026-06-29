"""
Agent 基类

定义有状态 LLM Agent 的通用接口。
"""
from abc import ABC, abstractmethod
from typing import Any, Optional
from pydantic import BaseModel
from utils.logger import logger
from ..llm.router import LLMRouter
from ..data.store import PersonaDataStore
from ..llm.selection import SelectionPolicy, EVENT_GEN
from ..agent.tool_executor import ToolRegistry as NewToolRegistry
from .types import AgentResult


class Agent(ABC):
    """有状态的 LLM Agent 基类

    每一个 Agent 持有自己的身份、system prompt、工具声明和持久化状态。
    run() 方法一站式完成：加载状态 → 拼 prompt → LLM 执行 → 返回结果。
    子类可覆盖 load_state / save_state / build_system_prompt / _build_user_prompt
    / _get_selection_policy / _get_openai_tools / _build_extra_registry 等钩子。
    """

    name: str = ""
    role: str = ""
    state_model: type[BaseModel] = BaseModel
    tools: list[str] = []

    def __init__(
        self,
        store: PersonaDataStore,
        router: LLMRouter,
        config=None,
        tool_registry: Optional[Any] = None,
    ):
        self.store = store
        self.router = router
        self.config = config
        self._tool_registry = tool_registry
        self._cached_state = None  # DMAgent 专用：在 super().run() 前设置以跳过 load_state()
        self._cached_system_prompt: Optional[str] = None  # DMAgent 专用：在 super().run() 前设置以跳过重复构建
        self._bg_timeout = (
            getattr(config, "background_llm_timeout_seconds", 90)
            if config
            else 90
        )
        self._max_rounds = (
            getattr(config, "background_llm_max_rounds", 10)
            if config
            else 10
        )
        self._conversation: Optional["Conversation"] = None
        self._system_prompt: Optional[str] = None

    async def load_state(self) -> BaseModel:
        """加载状态，子类可覆盖。默认返回空 state_model 实例。"""
        return self.state_model()

    async def save_state(self, state: BaseModel) -> None:
        """持久化状态，子类可覆盖。默认无操作。"""
        pass

    @abstractmethod
    def build_system_prompt(self, state: BaseModel, context: dict) -> str:
        """构建系统提示词。子类必须实现。"""
        ...

    def _build_user_prompt(self, context: dict) -> str:
        """构建用户提示词，子类可覆盖。默认将 context 转为字符串。"""
        return str(context)

    def _get_selection_policy(self) -> SelectionPolicy:
        """获取 LLM 选择策略，子类可覆盖。"""
        return EVENT_GEN

    def _get_openai_tools(self) -> list:
        """获取 OpenAI 格式工具列表，子类可覆盖。"""
        from ..tools.collecting import SAY_TOOL_DM

        return [SAY_TOOL_DM.to_openai_format()]

    def _build_extra_registry(self) -> Any:
        """根据 self.tools 自动构建只读工具注册表供 LLM 按需查询，子类可覆盖。

        Phase 1 中 Agent 基类根据 self.tools 列表自动构建 extra_registry，
        传递只读查询工具给 LLM。
        """
        # 只读工具名集合（查询类工具，不含副作用）
        _READONLY_TOOL_NAMES = {"read_events", "search_events", "roll_dice"}
        want = [t for t in self.tools if t in _READONLY_TOOL_NAMES]
        if not want or not self.config:
            return None
        try:
            from ..tools.registry import ToolDomain
            from ..tools.context import ToolContext
            from ..agent.tool_bridge import build_registry

            # 需要外部 tool_registry 来构建 readonly registry
            if self._tool_registry is None:
                return None
            tz = (
                getattr(self.config, "timezone", "Asia/Shanghai")
                if self.config
                else "Asia/Shanghai"
            )
            ctx = ToolContext(store=self.store, timezone=tz)
            return build_registry(
                self._tool_registry,
                [ToolDomain.CHAT],
                ctx=ctx,
                tool_names=want,
            )
        except Exception:
            logger.warning("_build_extra_registry 构建失败，DM 只读查询工具将不可用", exc_info=True)
            return None

    # ── Conversation 支持 ─────────────────────────────────────

    def _init_conversation(self, system_prompt: str) -> "Conversation":
        """创建 Conversation 并设置 system_prompt。

        子类（如 CharacterAgent）可调用此方法以统一 Conversation 创建路径。
        """
        from .conversation import Conversation as _Conv
        self._conversation = _Conv()
        self._system_prompt = system_prompt
        return self._conversation

    async def _ensure_conversation(self, context: dict,
                                    system_prompt_override: Optional[str] = None) -> "Conversation":
        """懒初始化 Conversation。

        不存在时加载 state、构建 system_prompt、创建 Conversation。
        system_prompt 只在 Conversation 生命周期内构建一次。

        Args:
            context: 用于 build_system_prompt 的上下文（若需要构建）
            system_prompt_override: 若提供，直接使用此 prompt 而非基类的
                                    build_system_prompt()。CharacterAgent 使用此参数
                                    传入特定的 reaction prompt。
        """
        if self._conversation is not None:
            return self._conversation

        if self._system_prompt is None:
            system_prompt = system_prompt_override
            if system_prompt is None:
                state = self._cached_state if self._cached_state is not None else await self.load_state()
                system_prompt = (
                    self._cached_system_prompt
                    if self._cached_system_prompt is not None
                    else self.build_system_prompt(state, context)
                )
            self._init_conversation(system_prompt)

        # R4: _system_prompt 已设置但 _conversation 为 None 时，防御性创建
        if self._conversation is None:
            self._init_conversation(self._system_prompt or "")

        return self._conversation

    async def compact_conversation(self) -> None:
        """每日收尾：清空 conversation 并重置状态。

        调用 conv.clear() 清空所有消息，_conversation/_system_prompt 置 None。
        TODO: 替换为 LLM 压缩（summarize 旧消息为一条摘要消息）
        """
        if self._conversation is None:
            return
        self._conversation.clear()
        self._conversation = None
        self._system_prompt = None

    # ── 核心执行 ──────────────────────────────────────────────

    async def run(self, context: dict) -> AgentResult:
        """一站式执行：加载状态 → 拼 prompt → LLM 执行 → 返回结果

        使用 Conversation 管理消息线程：
        1. _ensure_conversation() → conv.add_user(user_prompt)
        2. 执行层运行 → conv.extend(增量)
        3. 解析 collected[0] 为结果 dataclass

        当前仅 DMAgent 使用此模板方法（通过 tool-bridge 收集结构化输出）。
        CharacterAgent 和 SAAgent 覆盖 run() 使用独立路径：
        - CharacterAgent: 按 mode 分派到 react()/diary()/share()/opening()
        - SAAgent: 委托到 plan()，使用 AgentRuntime.run() 获取纯文本输出
        子类可覆盖此方法以添加解析逻辑。
        """
        from ..life._llm_utils import _run_life_collect_loop

        # 加载状态（如果尚未缓存）
        if self._cached_state is None:
            self._cached_state = await self.load_state()

        # NOTE: 此 Conversation 集成模式在 agent.py 与 character_agent.py 两处重复，
        # 如需新增第三处，应提取为 Agent._run_with_conv()
        conv = await self._ensure_conversation(context)
        user_prompt = self._build_user_prompt(context)
        conv.add_user(user_prompt)

        prev_len = conv.length
        collected, final_msgs = await _run_life_collect_loop(
            router=self.router,
            store=self.store,
            messages=conv.render(self._system_prompt),  # type: ignore[arg-type]
            tools=self._get_openai_tools(),
            temperature=0.9,
            selection=self._get_selection_policy(),
            bg_timeout=self._bg_timeout,
            max_rounds=self._max_rounds,
            extra_registry=self._build_extra_registry(),
        )

        conv.extend(final_msgs[prev_len + 1:])  # +1 跳过 system prompt 位，prev_len 不含 system

        if not collected:
            return AgentResult(success=False, data=None, error="LLM 未调用工具")

        return AgentResult(success=True, data=collected)
