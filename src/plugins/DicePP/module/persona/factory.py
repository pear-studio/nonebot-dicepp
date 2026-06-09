"""Persona 模块工厂函数

负责从 Bot 组装所有依赖，创建 ChatSession / LifeSimulator / MessagePort。
"""
import asyncio
import os
from enum import Enum
from typing import Any, Dict, List, Optional
from dataclasses import dataclass
from utils.logger import logger
from core.bot import Bot
from core.config.basic import Paths

from .character.loader import CharacterLoader
from .character.models import Character
from .chat.session import ChatSession
from .chat.chat_config import ChatConfig
from .chat.scoring import ScoringAgent
from .chat.context import ContextBuilder
from .chat.response_handler import ResponseHandler
from .chat.scoring_trigger import ScoringTrigger
from .data.store import PersonaDataStore
from .data.models import MessageType
from .data.protocols import MessageStore, RelationshipStore, ProfileStore, EventStore
from .exceptions import (
    PersonaCharacterLoadError,
    PersonaConfigError,
    PersonaStorageError,
)
from .game.decay import DecayCalculator, DecayConfig
from .gateway.pipeline import MessagePipeline, TruncateStage
from .gateway.port import MessagePort
from .life.action_evaluator import ActionEvaluator
from .life.character_life import CharacterLife, CharacterLifeConfig
from .life.diary import DiaryGenerator, DiaryConfig
from .life.event_agent import EventGenerationAgent
from .life.proactive_config import ProactiveConfig
from .life.proactive_scheduler import ProactiveScheduler
from .life.simulator import LifeSimulator, LifeConfig
from .life.protocols import SleepGate
from .life.target import TargetSelector
from .llm.coordinator import LLMCallCoordinator
from .llm.router import LLMRouter
from .tools.registry import ToolRegistry, ToolDomain
from .tools.read_history import READ_HISTORY_TOOL, make_read_history_executor
from .tools.search_history import SEARCH_HISTORY_TOOL, make_search_history_executor
from .tools.read_profile import READ_PROFILE_TOOL, read_profile_executor
from .tools.read_diary import READ_DIARY_TOOL, make_read_diary_executor
from .tools.search_diary import SEARCH_DIARY_TOOL, make_search_diary_executor
from .tools.read_events import READ_EVENTS_TOOL, make_read_events_executor
from .tools.search_events import SEARCH_EVENTS_TOOL, make_search_events_executor
from .tools.roll_dice import ROLL_DICE_TOOL, roll_dice_executor
from .tools.send_reply_segment import make_tool_def, send_reply_segment_executor
from .tools.list_databases import LIST_QUERY_DATABASES_TOOL, list_query_databases_executor
from .tools.search_knowledge import SEARCH_KNOWLEDGE_TOOL, search_knowledge_executor
from .tools.get_jrrp import GET_JRRP_TOOL, get_jrrp_executor
from .tools.suggest_action import SUGGEST_ACTION_TOOL, make_suggest_action_executor
from .tools.generate_image import make_generate_image_tool_def, make_generate_image_executor
from .tools.look_at_past_image import LOOK_AT_PAST_IMAGE_TOOL, look_at_past_image_executor
from .tools.collecting import (
    RECORD_EVENT_TOOL,
    RECORD_REACTION_TOOL,
    RECORD_DIARY_ENTRY_TOOL,
    RECORD_SHARE_MESSAGE_TOOL,
    life_collecting_executor,
)

from .chat.segment_dispatcher import SegmentDispatcher


@dataclass
class PersonaApp:
    """Persona 模块入口 — 持有 chat/life/store/port 四个公开句柄"""

    chat: ChatSession
    life: LifeSimulator
    store: PersonaDataStore
    port: MessagePort
    session_manager: Any = None
    segment_dispatcher: Optional[SegmentDispatcher] = None
    all_providers_disabled: bool = False
    current_character_name: str = ""

    # ── 角色卡 ────────────────────────────────────────────────

    async def update_character(self, character: Character) -> None:
        """统一传播新的角色卡到所有子系统。"""
        self.chat.update_character(character)
        self.life.update_character(character)

    async def switch_character_db(self, new_character_name: str) -> None:
        """切换到新角色的 persona_db"""
        await self.store.switch_persona_db(new_character_name)
        self.current_character_name = new_character_name

    async def shutdown(self) -> None:
        """应用关闭时取消所有未完成的 background task。"""
        if self.session_manager:
            await self.session_manager.shutdown()

    def get_character(self) -> Optional[Character]:
        return self.chat.character

    def get_relation_labels(self) -> List[str]:
        char = self.chat.character
        return char.get_relation_labels() if char else []

    # ── 对话 ──────────────────────────────────────────────────

    async def clear_chat_history(self, user_id: str, group_id: str) -> None:
        await self.chat.clear_history(user_id, group_id)

    async def chat_with_user(
        self, user_id: str, group_id: str, message: str, nickname: str,
        image_data_urls: Optional[List[str]] = None,
    ) -> Optional[str]:
        return await self.chat.chat(user_id, group_id, message, nickname, image_data_urls=image_data_urls)

    # ── 消息发送 ──────────────────────────────────────────────

    async def send_message(self, user_id: str, group_id: str, content: str, msg_id: Optional[int] = None) -> bool:
        return await self.port.send(user_id, group_id, content, msg_id=msg_id)

    # ── LLM 路由器 ────────────────────────────────────────────

    def get_router(self) -> Optional[LLMRouter]:
        return self.chat.router

    def get_router_stats(self) -> Dict[str, Any]:
        router = self.chat.router
        return router.get_stats() if router else {}

    def get_router_latency_percentiles(self, provider_name: str) -> Dict[str, float]:
        router = self.chat.router
        return router.get_latency_percentiles(provider_name) if router else {}

    # ── 调度器 ────────────────────────────────────────────────

    def get_scheduler(self) -> Optional[ProactiveScheduler]:
        return self.life.scheduler

    def get_scheduler_status(self) -> Dict[str, Any]:
        scheduler = self.life.scheduler
        return scheduler.get_status() if scheduler else {}

    def get_scheduler_event_agent(self) -> Optional[Any]:
        scheduler = self.life.scheduler
        return scheduler.event_agent if scheduler else None

    def pause_scheduler(self) -> None:
        scheduler = self.life.scheduler
        if scheduler:
            scheduler.config.enabled = False

    def resume_scheduler(self) -> None:
        scheduler = self.life.scheduler
        if scheduler:
            scheduler.config.enabled = True

    # ── 衰减计算 ──────────────────────────────────────────────

    def get_decay_calculator(self) -> Optional[DecayCalculator]:
        return self.chat.decay_calculator

    def effective_relationship(self, rel) -> Any:
        calc = self.chat.decay_calculator
        return calc.effective_relationship(rel) if calc else rel

    # ── 生命周期驱动 ──────────────────────────────────────────

    async def is_awake(self) -> bool:
        """角色是否处于唤醒状态。委托给 ``ChatSession.is_awake()``。"""
        return await self.chat.is_awake()

    async def tick(self) -> None:
        await self.life.tick()

    async def tick_daily(self) -> Optional[str]:
        return await self.life.tick_daily()


@dataclass
class _Infra:
    """基础设施组件 — _build_infra 的返回容器"""
    store: PersonaDataStore
    router: LLMRouter
    port: MessagePort
    segment_dispatcher: Optional[SegmentDispatcher]


class _StartupStatus(str, Enum):
    """启动汇总里模型条目的状态枚举。"""
    OK = "ok"
    FAIL = "fail"
    DISABLED = "disabled"


_STATUS_PREFIX: Dict[_StartupStatus, str] = {
    _StartupStatus.OK: "[OK]",
    _StartupStatus.FAIL: "[FAIL]",
    _StartupStatus.DISABLED: "[OFF]",
}


def _status_note(status: _StartupStatus) -> str:
    """根据状态返回附加说明文本。"""
    if status is _StartupStatus.OK:
        return ""
    if status is _StartupStatus.FAIL:
        return " (probe 失败)"
    return " (已禁用)"


@dataclass
class ChatDeps:
    """_build_chat 的依赖参数集合 — 替代 16 个独立 keyword 参数"""
    store: PersonaDataStore
    message_store: MessageStore
    rel_store: RelationshipStore
    profile_store: ProfileStore
    event_store: EventStore
    router: LLMRouter
    tool_registry: ToolRegistry
    coordinator: LLMCallCoordinator
    character: Character
    config: Any
    decay_calculator: DecayCalculator
    port: MessagePort
    segment_dispatcher: Optional[SegmentDispatcher] = None
    query_store: Any = None
    resolve_db: Any = None
    sleep_gate: Optional[SleepGate] = None


def _load_character(config) -> Character:
    """加载角色卡"""
    character_loader = CharacterLoader(config.character_path)
    character = character_loader.load(config.character_name)
    if not character:
        raise PersonaCharacterLoadError(
            f"无法加载角色卡: {config.character_name} (path={config.character_path})"
        )
    logger.info(f"角色卡已加载: {character.name}")
    return character


async def _build_store(bot: Bot, config) -> PersonaDataStore:
    """初始化数据存储（双连接：persona_db + core_db）"""
    core_db = getattr(getattr(bot, "db", None), "_db", None)
    if core_db is None:
        raise PersonaStorageError("数据库未初始化（bot.db 或 bot.db._db 为 None）")

    # 拼接 persona_db 路径: data/bots/{bot_id}/personas_data_{character_name}.db
    bot_dir = Paths.bot_data_dir(bot.account)
    persona_db_path = str(bot_dir / f"personas_data_{config.character_name}.db")
    os.makedirs(str(bot_dir), exist_ok=True)

    store = PersonaDataStore(
        persona_db_path,
        core_db,
        group_activity_decay_per_day=config.group_activity_decay_per_day,
        group_activity_floor_whitelist=config.group_activity_floor_whitelist,
        timezone=config.timezone,
        message_stream_max_per_group=config.message_stream_max_per_group,
    )
    try:
        await store.open()
    except Exception as e:
        raise PersonaStorageError(f"数据库表初始化失败: {e}") from e
    logger.info(f"数据存储已初始化: persona_db={persona_db_path}")

    # 迁移旧 persona_settings 中的 'code' 到 persona_global_settings
    await _migrate_code_setting(store)

    return store


async def _migrate_code_setting(store: PersonaDataStore) -> None:
    """首次启动时将旧 persona_settings 中的 'code' 迁移到 persona_global_settings"""
    existing = await store.get_global_setting("code")
    if existing is not None:
        await store.delete_setting("code")  # 清理 persona_db 侧残留
        return
    old_code = await store.get_setting("code")
    if old_code is not None:
        await store.set_global_setting("code", old_code)
        await store.delete_setting("code")
        logger.info("已将口令 'code' 从 persona_settings 迁移到 persona_global_settings")


def _build_router(config, store: PersonaDataStore) -> LLMRouter:
    """初始化 LLM 路由器（依赖 store 做配额检查）"""
    llm_router = LLMRouter(
        providers=config.providers,
        global_max_concurrent=config.max_concurrent_requests,
        timeout=config.chat_llm_timeout_seconds,
        daily_limit=config.daily_limit,
        quota_check_enabled=config.quota_check_enabled,
        data_store=store,
        config=config,
        trace_enabled=config.trace_enabled,
        trace_max_age_days=config.trace_max_age_days,
    )
    logger.info("LLM 路由器已初始化")
    return llm_router


def _build_port(bot: Bot, store: PersonaDataStore) -> MessagePort:
    """初始化消息发送端口"""
    pipeline = MessagePipeline()
    pipeline.add(TruncateStage(max_chars=2000))
    async def _on_delivery_failed(user_id: str, group_id: str, content: str, error: str = "") -> None:
        try:
            await store.add_message_stream(
                user_id=user_id, group_id=group_id, role="assistant",
                type=MessageType.SYSTEM_NOTICE, content=f"[发送失败] {content}",
            )
        except Exception:
            logger.exception("on_delivery_failed 二次入库失败")

    port = MessagePort(bot, pipeline=pipeline, on_delivery_failed=_on_delivery_failed)
    logger.info("消息发送端口已初始化")
    return port


async def _build_infra(bot: Bot, config) -> _Infra:
    """创建基础设施组件: store / router / port / segment_dispatcher"""
    store = await _build_store(bot, config)
    router = _build_router(config, store)
    port = _build_port(bot, store)

    segment_dispatcher = None
    if config.segment_enabled:
        segment_dispatcher = SegmentDispatcher(message_port=port)

    return _Infra(
        store=store,
        router=router,
        port=port,
        segment_dispatcher=segment_dispatcher,
    )


def _build_tooling(
    store: PersonaDataStore,
    router: LLMRouter,
    config,
    character: Character,
) -> tuple[ToolRegistry, EventGenerationAgent, CharacterLife, ActionEvaluator]:
    """创建工具注册表、事件代理、角色生活、动作评估器"""
    tool_registry = ToolRegistry()
    tool_registry.register(ToolDomain.LIFE, RECORD_EVENT_TOOL, life_collecting_executor)
    tool_registry.register(ToolDomain.LIFE, RECORD_REACTION_TOOL, life_collecting_executor)
    tool_registry.register(ToolDomain.LIFE, RECORD_DIARY_ENTRY_TOOL, life_collecting_executor)
    tool_registry.register(ToolDomain.LIFE, RECORD_SHARE_MESSAGE_TOOL, life_collecting_executor)

    event_agent = EventGenerationAgent(router, tool_registry, config=config, store=store)

    life_config = CharacterLifeConfig.from_persona(config)
    character_life = CharacterLife(
        config=life_config,
        event_agent=event_agent,
        data_store=store,
        character=character,
    )

    action_evaluator = ActionEvaluator(
        store=store,
        router=router,
        config=config,
        timezone=config.timezone,
    )
    logger.info("ActionEvaluator 已初始化")

    suggest_action_executor = make_suggest_action_executor(
        store=store,
        action_evaluator=action_evaluator,
        character_life=character_life,
        min_relationship=config.suggest_action_min_relationship,
        life_lock=character_life._state_lock,
    )

    tool_registry.register(
        ToolDomain.CHAT,
        READ_HISTORY_TOOL,
        make_read_history_executor(config.search_max_chars),
    )
    tool_registry.register(
        ToolDomain.CHAT,
        SEARCH_HISTORY_TOOL,
        make_search_history_executor(config.search_max_chars),
    )
    tool_registry.register(ToolDomain.CHAT, READ_PROFILE_TOOL, read_profile_executor)
    tool_registry.register(
        ToolDomain.CHAT,
        READ_DIARY_TOOL,
        make_read_diary_executor(),
    )
    tool_registry.register(
        ToolDomain.CHAT,
        SEARCH_DIARY_TOOL,
        make_search_diary_executor(),
    )
    tool_registry.register(
        ToolDomain.CHAT,
        READ_EVENTS_TOOL,
        make_read_events_executor(),
    )
    tool_registry.register(
        ToolDomain.CHAT,
        SEARCH_EVENTS_TOOL,
        make_search_events_executor(),
    )
    tool_registry.register(ToolDomain.CHAT, ROLL_DICE_TOOL, roll_dice_executor)
    tool_registry.register(ToolDomain.CHAT, GET_JRRP_TOOL, get_jrrp_executor)
    tool_registry.register(ToolDomain.CHAT, LIST_QUERY_DATABASES_TOOL, list_query_databases_executor)
    tool_registry.register(ToolDomain.CHAT, SEARCH_KNOWLEDGE_TOOL, search_knowledge_executor)
    tool_registry.register(ToolDomain.CHAT, SUGGEST_ACTION_TOOL, suggest_action_executor)
    if config.segment_enabled:
        tool_registry.register(
            ToolDomain.CHAT,
            make_tool_def(
                target_chars=config.segment_target_chars,
                max_chars=config.segment_max_chars,
                max_delay=config.segment_max_delay,
            ),
            send_reply_segment_executor,
        )

    base_style = (
        character.extensions.image_gen_style
        or config.image_gen_style
    )
    character_appearance = character.extensions.image_gen_appearance
    gen_tool_def = make_generate_image_tool_def(
        base_style=base_style,
        character_appearance=character_appearance,
    )
    gen_executor = make_generate_image_executor(
        get_gen_provider=router.get_gen_provider,
        handle_model_error=router.handle_model_error,
        base_style=base_style,
        character_appearance=character_appearance,
    )
    tool_registry.register(ToolDomain.CHAT, gen_tool_def, gen_executor)
    tool_registry.register(ToolDomain.CHAT, LOOK_AT_PAST_IMAGE_TOOL, look_at_past_image_executor)

    logger.info("工具注册表与分段调度器已初始化")
    return tool_registry, event_agent, character_life, action_evaluator


def _make_resolve_query_db(bot: Bot):
    """返回 _build_chat 所需的 resolve_db 回调（闭包捕获 bot）"""
    async def _resolve_query_db(user_id: str, group_id: str) -> str:
        if group_id:
            row = await bot.db.group_config.get(group_id)
            if row and row.data:
                return row.data.get("query_database", bot.config.mode.default)
            return bot.config.mode.default
        else:
            row = await bot.db.user_stat.get(user_id)
            if row and row.data:
                db = row.data.get("query_database")
                if db:
                    return db
            return bot.config.query.private_database
    return _resolve_query_db


def _build_chat(deps: ChatDeps) -> ChatSession:
    """组装 ChatSession"""
    scoring_agent = ScoringAgent(deps.router, timezone=deps.config.timezone,
                                 max_tool_rounds=deps.config.background_llm_max_tool_rounds,
                                 store=deps.store)
    from .chat.context import SegmentGuide

    segment_guide = None
    if deps.config.segment_enabled:
        segment_guide = SegmentGuide(
            enabled=True,
            target_chars=deps.config.segment_target_chars,
            max_chars=deps.config.segment_max_chars,
            soft_limit=deps.config.segment_soft_limit,
            hard_limit=deps.config.segment_hard_limit,
        )

    context_builder = ContextBuilder(
        deps.character,
        max_history_turns=deps.config.max_history_turns,
        max_history_tokens=deps.config.max_history_tokens,
        timezone=deps.config.timezone,
        lore_token_budget=deps.config.lore_token_budget,
        segment_guide=segment_guide,
    )
    chat_config = ChatConfig.from_persona(deps.config)

    response_handler = ResponseHandler(store=deps.store, port=deps.port)
    scoring_trigger = ScoringTrigger(
        store=deps.store, scoring_agent=scoring_agent,
        decay_calculator=deps.decay_calculator, character=deps.character,
        config=chat_config,
    )
    return ChatSession(
        store=deps.store,
        router=deps.router,
        tool_registry=deps.tool_registry,
        coordinator=deps.coordinator,
        character=deps.character,
        config=chat_config,
        scoring_trigger=scoring_trigger,
        response_handler=response_handler,
        context_builder=context_builder,
        decay_calculator=deps.decay_calculator,
        query_store=deps.query_store,
        resolve_db=deps.resolve_db,
        sleep_gate=deps.sleep_gate,
    )


async def _build_life(
    store: PersonaDataStore,
    character: Character,
    config,
    coordinator: LLMCallCoordinator,
    port: MessagePort,
    decay_calculator: DecayCalculator,
    character_life: CharacterLife,
    event_agent: EventGenerationAgent,
) -> LifeSimulator:
    """组装 LifeSimulator — 仅构造 scheduler, diary_generator 等外围组件"""
    target_selector = TargetSelector(
        data_store=store,
        bot_config=config,
        decay_calculator=decay_calculator,
        character=character,
    )
    scheduler_config = ProactiveConfig.from_persona(config)
    scheduler = ProactiveScheduler(
        config=scheduler_config,
        data_store=store,
        character=character,
        event_agent=event_agent,
        decay_calculator=decay_calculator,
        target_selector=target_selector,
        coordinator=coordinator,
    )
    await scheduler.load_persistent_state()
    logger.info("主动消息调度器已初始化")

    character_life.set_boundary_receiver(scheduler)
    await character_life.load_persistent_state()
    logger.info("角色生活模拟已初始化")

    diary_config = DiaryConfig(
        diary_time=config.character_life_diary_time,
        timezone=config.timezone,
    )
    diary_generator = DiaryGenerator(
        store=store,
        event_agent=event_agent,
        character=character,
        config=diary_config,
    )

    life_config_obj = LifeConfig.from_persona(config)
    return LifeSimulator(
        store=store,
        character_life=character_life,
        scheduler=scheduler,
        diary_generator=diary_generator,
        character=character,
        config=life_config_obj,
        port=port,
        decay_calculator=decay_calculator,
    )


async def _startup_summary(
    character: Character,
    providers: Dict[str, object],
    probe_results: Dict[tuple, bool],
    infra: _Infra,
    bot: Bot,
) -> None:
    """输出启动汇总：结构化日志 + master 消息（仅可用模型）。

    状态语义：
    - OK: enabled 且 probe 成功
    - FAIL: enabled 但 probe 失败
    - DISABLED: provider 或 model 配置为 enabled=False（不会触发 probe）
    """
    llm_entries: List[tuple] = []
    gen_entries: List[tuple] = []
    for pname, pconfig in providers.items():
        for mconfig in pconfig.models:
            if not pconfig.enabled or not mconfig.enabled:
                status = _StartupStatus.DISABLED
            else:
                key = (pname, mconfig.name)
                status = (
                    _StartupStatus.OK
                    if probe_results.get(key, False)
                    else _StartupStatus.FAIL
                )
            if mconfig.category == "llm":
                llm_entries.append((pname, mconfig, status))
            elif mconfig.category == "gen":
                gen_entries.append((pname, mconfig, status))

    desc = character.description or ""
    if len(desc) > 60:
        desc = desc[:60] + "..."

    lines: List[str] = []
    lines.append("══════ Persona AI 启动报告 ══════")
    lines.append(f"角色卡: {character.name}" + (f" — {desc}" if desc else ""))

    if llm_entries:
        ok_count = sum(1 for _, _, s in llm_entries if s is _StartupStatus.OK)
        fail_count = sum(1 for _, _, s in llm_entries if s is _StartupStatus.FAIL)
        lines.append(f"LLM 模型 ({ok_count} 可用 / {fail_count} 失败 / {len(llm_entries)} 总计):")
        for pname, mconfig, status in llm_entries:
            thinking_note = " (thinking: on)" if getattr(mconfig, "thinking", False) else ""
            lines.append(
                f"  {_STATUS_PREFIX[status]} {pname}/{mconfig.name}"
                f"{thinking_note}{_status_note(status)}"
            )

    if gen_entries:
        ok_count = sum(1 for _, _, s in gen_entries if s is _StartupStatus.OK)
        fail_count = sum(1 for _, _, s in gen_entries if s is _StartupStatus.FAIL)
        lines.append(f"图像生成模型 ({ok_count} 可用 / {fail_count} 失败 / {len(gen_entries)} 总计):")
        for pname, mconfig, status in gen_entries:
            lines.append(
                f"  {_STATUS_PREFIX[status]} {pname}/{mconfig.name}{_status_note(status)}"
            )

    lines.append("════════════════════════════════════")

    for line in lines:
        logger.info(line)

    master_ids: List[str] = bot.config.master
    if not master_ids:
        return

    available_llm = [f"{p}/{m.name}" for p, m, s in llm_entries if s is _StartupStatus.OK]
    failed_llm = [f"{p}/{m.name}" for p, m, s in llm_entries if s is _StartupStatus.FAIL]
    available_gen = [f"{p}/{m.name}" for p, m, s in gen_entries if s is _StartupStatus.OK]
    failed_gen = [f"{p}/{m.name}" for p, m, s in gen_entries if s is _StartupStatus.FAIL]

    msg_lines = ["Persona AI 启动完成"]
    msg_lines.append(f"角色卡: {character.name}" + (f" — {desc}" if desc else ""))

    # 全失败告警（开 LLM 全部不可用时单独提示）
    if llm_entries and not available_llm and failed_llm:
        msg_lines.append(
            f"[ALERT] 所有 {len(failed_llm)} 个 LLM 模型 probe 失败"
        )
    if available_llm:
        msg_lines.append(f"可用 LLM: {', '.join(available_llm)}")
    if failed_llm:
        msg_lines.append(f"不可用 (probe 失败): {', '.join(failed_llm)}")
    if available_gen:
        msg_lines.append(f"可用图像生成: {', '.join(available_gen)}")
    if failed_gen:
        msg_lines.append(f"不可用 (probe 失败): {', '.join(failed_gen)}")

    try:
        await infra.port.send(
            master_ids[0], "", "\n".join(msg_lines),
            message_type=MessageType.SYSTEM_LOG,
        )
    except Exception:
        logger.exception("发送启动报告到 master 失败")


async def create_persona(bot: Bot) -> Optional[PersonaApp]:
    """从 Bot 组装 Persona 模块所有组件

    Returns:
        PersonaApp 实例；模块禁用时返回 ``None``。

    Raises:
        PersonaCharacterLoadError: 角色卡加载失败。
        PersonaConfigError: 必填配置（如 ``providers``）缺失。
        PersonaStorageError: 数据库句柄不可用。
    """
    config = bot.config.persona_ai
    if not config.enabled:
        logger.info("Persona AI 模块已禁用")
        return None

    character = _load_character(config)

    if not config.providers:
        raise PersonaConfigError(
            "未配置任何 LLM 提供者 (persona_ai.providers)。"
            "请参考新格式: persona_ai.providers.<name>.api_key / .base_url / .models[*]"
        )

    infra = await _build_infra(bot, config)
    tool_registry, event_agent, character_life, _ = _build_tooling(
        infra.store, infra.router, config, character,
    )

    coordinator = LLMCallCoordinator(
        max_failures=config.proactive_coordinator_max_failures,
        max_iterations=config.proactive_coordinator_max_iterations,
    )
    logger.info("LLM 调用协调器已初始化")

    decay_calculator = DecayCalculator(
        DecayConfig.from_persona(config),
        timezone_name=config.timezone,
    )
    logger.info("衰减计算器已初始化")

    from .chat.session_manager import SessionManager
    session_manager = SessionManager(
        store=infra.store,
        config=ChatConfig.from_persona(config),
        timezone=config.timezone,
    )
    logger.info("SessionManager 已初始化")

    chat = _build_chat(ChatDeps(
        store=infra.store,
        message_store=infra.store,
        rel_store=infra.store,
        profile_store=infra.store,
        event_store=infra.store,
        router=infra.router,
        tool_registry=tool_registry,
        coordinator=coordinator,
        character=character,
        config=config,
        decay_calculator=decay_calculator,
        port=infra.port,
        segment_dispatcher=infra.segment_dispatcher,
        query_store=bot.db.query,
        resolve_db=_make_resolve_query_db(bot),
        sleep_gate=character_life,
    ))

    # 注入 session_manager 到 ChatSession
    chat.session_manager = session_manager

    life = await _build_life(
        infra.store, character, config, coordinator, infra.port, decay_calculator,
        character_life=character_life,
        event_agent=event_agent,
    )

    probe_results: Dict[tuple, bool] = {}
    try:
        probe_results = await infra.router.probe_all_models()
    except Exception as e:
        logger.error(f"启动探针异常: {e}")
    all_disabled = infra.router.all_providers_disabled()
    if all_disabled:
        logger.warning("所有模型 probe 失败！Persona AI 功能将不可用")

    infra.router.start_probe_task()

    await _startup_summary(character, config.providers, probe_results, infra, bot)

    logger.info("Persona 模块初始化完成")
    return PersonaApp(
        chat=chat, life=life, store=infra.store, port=infra.port,
        session_manager=session_manager,
        segment_dispatcher=infra.segment_dispatcher,
        all_providers_disabled=all_disabled,
        current_character_name=config.character_name,
    )
