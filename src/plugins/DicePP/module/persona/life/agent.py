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
from ..agent.runtime_types import AgentRunSpec, LoopLimits, OutputSpec, ToolKit
from .types import AgentResult


class Agent(ABC):
    """有状态的 LLM Agent 基类

    每一个 Agent 持有自己的身份、system prompt、工具声明和持久化状态。
    run() 方法一站式完成：加载状态 → 拼 prompt → LLM 执行 → 返回结果。
    子类可覆盖 load_state / save_state / build_system_prompt / _build_user_prompt
    / _get_selection_policy / build_run_spec / interpret_result 等钩子。
    T6: ToolKit 由子类在 build_run_spec() 中直接构建，使用 tools/*.py 中的 build_xxx_tool() 函数。
    """

    name: str = ""
    role: str = ""
    state_model: type[BaseModel] | None = None
    tools: list[str] = []

    def __init__(
        self,
        store: PersonaDataStore,
        router: LLMRouter,
        config=None,
    ):
        self.store = store
        self.router = router
        self.config = config
        self._cached_state = None  # DMAgent 专用：在 super().run() 前设置以跳过 load_state()
        self._cached_system_prompt: Optional[str] = None  # DMAgent 专用：在 super().run() 前设置以跳过重复构建
        self._max_rounds = config.background_llm_max_rounds if config else 10
        self._conversation: Optional["Conversation"] = None
        self._system_prompt: Optional[str] = None

    async def load_state(self) -> Optional[BaseModel]:
        """加载状态，子类可覆盖。默认返回 state_model 实例或 None。"""
        if self.state_model is None:
            return None
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

    # ── ChangeSource 支持 ───────────────────────────────────────

    def _get_change_sources(self) -> "list[ChangeSource]":
        """返回要注册到 Conversation 的 ChangeSource 列表。

        子类覆盖此方法以订阅变更通知。默认返回空列表。
        """
        return []

    def _register_change_sources(self, conv: "Conversation") -> None:
        """将 _get_change_sources() 返回的 source 注册到 Conversation。

        只在 _ensure_conversation 首次创建 Conversation 时调用。
        """
        for source in self._get_change_sources():
            conv.register(source)

    # ── Conversation 支持 ─────────────────────────────────────

    def _init_conversation(self, system_prompt: str) -> "Conversation":
        """创建带 AgentRuntime 的 Conversation。

        子类（如 CharacterAgent）可调用此方法以统一 Conversation 创建路径。

        T3: 使用 AgentRuntime 替代旧 ToolLoop。
        """
        from .conversation import Conversation as _Conv
        from ..agent.runtime import AgentRuntime
        from ..agent.runtime_types import LoopLimits

        runtime = AgentRuntime(
            router=self.router,
            store=self.store,
            limits=LoopLimits(max_rounds=self._max_rounds),
        )
        self._conversation = _Conv(runtime=runtime)
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
                                    build_system_prompt()。
                                    调用方可使用此参数在首次创建 Conversation 时锁定
                                    自定义 system prompt；Conversation 已存在时忽略。
        """
        if self._conversation is not None:
            return self._conversation

        if self._system_prompt is None:
            system_prompt = system_prompt_override
            if system_prompt is None:
                state = None
                if self.state_model is not None:
                    state = self._cached_state if self._cached_state is not None else await self.load_state()
                system_prompt = (
                    self._cached_system_prompt
                    if self._cached_system_prompt is not None
                    else self.build_system_prompt(state, context)
                )
            self._init_conversation(system_prompt)
            # 注册 ChangeSource —— 只在 Conversation 创建时调用一次
            self._register_change_sources(self._conversation)

        # R4: _system_prompt 已设置但 _conversation 为 None 时，防御性创建
        if self._conversation is None:
            self._init_conversation(self._system_prompt or "")
            self._register_change_sources(self._conversation)

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

    async def build_run_spec(self, context: dict) -> "AgentRunSpec":
        """构建本次 run 的规格 — 子类必须覆盖以提供 ToolKit + OutputSpec。

        默认返回空 ToolKit + None output_spec 的 minimal 实现。
        所有子类（DMAgent/CharacterAgent/SAAgent）均已覆盖此方法。
        """
        if self.state_model is not None:
            if self._cached_state is None:
                self._cached_state = await self.load_state()
            system_prompt = (
                self._cached_system_prompt
                if self._cached_system_prompt is not None
                else self.build_system_prompt(self._cached_state, context)
            )
        else:
            system_prompt = (
                self._cached_system_prompt
                if self._cached_system_prompt is not None
                else self.build_system_prompt(None, context)
            )
        user_prompt = self._build_user_prompt(context)

        # 子类覆盖 build_run_spec() 时自行构建 ToolKit
        toolkit = ToolKit()

        return AgentRunSpec(
            system_prompt=system_prompt,
            user_input=user_prompt,
            tools=toolkit,
            output=None,
            selection=self._get_selection_policy(),
            limits=LoopLimits(max_rounds=self._max_rounds),
        )

    async def interpret_result(
        self, result: "ConversationRunResult", context: dict
    ) -> AgentResult:
        """解释 Conversation.run() 的结果 → AgentResult — 子类可覆盖。

        默认实现：读取 result.output_arguments（结构化输出）。
        所有子类（DMAgent/CharacterAgent/SAAgent）均已覆盖此方法。
        """
        if result.output_arguments:
            return AgentResult(success=True, data=dict(result.output_arguments))
        if result.final_text:
            return AgentResult(success=True, data={"text": result.final_text})
        return AgentResult(success=False, data=None, error="LLM 未产生有效输出")

    async def run(
        self, context: dict, *, interaction_id: str,
    ) -> AgentResult:
        """一站式执行：build_run_spec → Conversation.run → interpret_result

        使用 Conversation.run() 新签名（T3），走 AgentRuntime.run() 路径。
        子类可覆盖 build_run_spec() / interpret_result() 替代覆盖 run()。

        Args:
            context: 业务上下文
            interaction_id: 编排层传入的交互 ID（必传）。
        """
        try:
            spec = await self.build_run_spec(context)

            # 配额检查（Runtime 之前执行）。
            # 仅当 router 已配置 data_store 时检查（mock router 无 data_store 则跳过）。
            if _router_has_quota(self.router):
                await self.router.check_daily_quota(spec.user_id)

            conv = await self._ensure_conversation(
                context, system_prompt_override=spec.system_prompt,
            )

            result = await conv.run(
                system_prompt=spec.system_prompt,
                user_input=spec.user_input,
                interaction_id=interaction_id,
                tools=spec.tools,
                output=spec.output,
                selection=spec.selection,
                limits=spec.limits,
                run_tag=spec.run_tag,
                agent_name=self.name,
                user_id=spec.user_id,
                group_id=spec.group_id,
            )

            # 配额计数（LLM 调用已完成）。
            # 仅当 router 已配置 data_store 时计数（mock router 无 data_store 则跳过）。
            if _router_has_quota(self.router):
                await self.router.increment_usage(spec.user_id)

            return await self.interpret_result(result, context)
        except Exception as e:
            logger.exception(f"Agent {self.name} run 失败")
            return AgentResult(
                success=False,
                data=None,
                error=f"{self.name} 执行异常: {e}",
            )


def _router_has_quota(router) -> bool:
    """判断 router 是否配置了配额功能（排除 mock 对象）。"""
    from unittest.mock import Mock
    if isinstance(router, Mock):
        return False
    return getattr(router, "quota_check_enabled", False) and getattr(router, "data_store", None) is not None


# 子类通过 build_run_spec() 直接构建 ToolKit，使用 tools/*.py 中的 build_xxx_tool() 函数。
