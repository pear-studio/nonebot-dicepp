"""Persona 模块工厂函数

负责从 Bot 组装所有依赖，创建 ChatSession / LifeSimulator / MessagePort。
"""
import asyncio
from typing import Any, Dict, List, Optional
from dataclasses import dataclass
from nonebot.log import logger
from core.bot import Bot

from .character.loader import CharacterLoader
from .character.models import Character
from .chat.session import ChatSession, ChatConfig
from .chat.scoring import ScoringAgent
from .chat.context import ContextBuilder
from .data.store import PersonaDataStore
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
from .life.event_share_queue import EventShareTaskQueue
from .life.simulator import LifeSimulator, LifeConfig
from .life.protocols import SleepGate
from .life.target import TargetSelector
from .llm.coordinator import LLMCallCoordinator
from .llm.router import LLMRouter
from .tools.registry import ToolRegistry, ToolDomain
from .tools.search_memory import SEARCH_MEMORY_TOOL, search_memory_executor
from .tools.search_history import SEARCH_HISTORY_TOOL, make_search_history_executor
from .tools.roll_dice import ROLL_DICE_TOOL, roll_dice_executor
from .tools.send_reply_segment import make_tool_def, send_reply_segment_executor
from .tools.list_databases import LIST_QUERY_DATABASES_TOOL, list_query_databases_executor
from .tools.search_query import SEARCH_QUERY_TOOL, search_query_executor
from .tools.suggest_action import SUGGEST_ACTION_TOOL, make_suggest_action_executor

from .chat.segment_dispatcher import SegmentDispatcher


@dataclass
class PersonaApp:
    """Persona 模块入口 — 持有 chat/life/store/port 四个公开句柄"""

    chat: ChatSession
    life: LifeSimulator
    store: PersonaDataStore
    port: MessagePort
    segment_dispatcher: Optional[SegmentDispatcher] = None
    all_providers_disabled: bool = False

    # ── 角色卡 ────────────────────────────────────────────────

    async def update_character(self, character: Character) -> None:
        """统一传播新的角色卡到所有子系统。"""
        self.chat.update_character(character)
        self.life.update_character(character)

    def get_character(self) -> Optional[Character]:
        return self.chat.character

    def get_warmth_labels(self) -> List[str]:
        char = self.chat.character
        return char.get_warmth_labels() if char else []

    def get_initial_relationship(self) -> float:
        char = self.chat.character
        if char and char.extensions:
            return float(char.extensions.initial_relationship)
        return 40.0

    # ── 对话 ──────────────────────────────────────────────────

    async def clear_chat_history(self, user_id: str, group_id: str) -> None:
        await self.chat.clear_history(user_id, group_id)

    async def chat_with_user(
        self, user_id: str, group_id: str, message: str, nickname: str
    ) -> Optional[str]:
        return await self.chat.chat(user_id, group_id, message, nickname)

    # ── 消息发送 ──────────────────────────────────────────────

    async def send_message(self, user_id: str, group_id: str, content: str) -> bool:
        return await self.port.send(user_id, group_id, content)

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

    async def tick(self) -> None:
        await self.life.tick()

    async def tick_daily(self) -> Optional[str]:
        return await self.life.tick_daily()


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
    """初始化数据存储"""
    raw_db = getattr(getattr(bot, "db", None), "_db", None)
    if raw_db is None:
        raise PersonaStorageError("数据库未初始化（bot.db 或 bot.db._db 为 None）")

    store = PersonaDataStore(
        raw_db,
        group_activity_decay_per_day=config.group_activity_decay_per_day,
        group_activity_floor_whitelist=config.group_activity_floor_whitelist,
        group_activity_decay_with_content=config.group_activity_decay_with_content,
        group_activity_content_window_hours=config.group_activity_content_window_hours,
        timezone=config.timezone,
        unified_message_max_per_group=config.unified_message_max_per_group,
    )
    try:
        await store.ensure_tables()
    except Exception as e:
        raise PersonaStorageError(f"数据库表初始化失败: {e}") from e
    logger.info("数据存储已初始化")
    return store


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
            from .data.models import MessageType
            await store.add_unified_message(
                user_id=user_id, group_id=group_id, role="assistant",
                type=MessageType.SYSTEM_NOTICE, content=f"[发送失败] {content}",
            )
        except Exception:
            logger.exception("on_delivery_failed 二次入库失败")

    port = MessagePort(bot, pipeline=pipeline, on_delivery_failed=_on_delivery_failed)
    logger.info("消息发送端口已初始化")
    return port


def _build_chat(
    store: PersonaDataStore,
    router: LLMRouter,
    tool_registry: ToolRegistry,
    coordinator: LLMCallCoordinator,
    character: Character,
    config,
    decay_calculator: DecayCalculator,
    port: MessagePort,
    segment_dispatcher: Optional[SegmentDispatcher] = None,
    query_store: Any = None,
    resolve_db: Any = None,
    sleep_gate: Optional[SleepGate] = None,
) -> ChatSession:
    """组装 ChatSession"""
    scoring_agent = ScoringAgent(router, timezone=config.timezone,
                                 max_tool_rounds=config.background_llm_max_tool_rounds)
    from .chat.context import SegmentGuide

    segment_guide = None
    if config.segment_enabled:
        segment_guide = SegmentGuide(
            enabled=True,
            target_chars=config.segment_target_chars,
            max_chars=config.segment_max_chars,
            soft_limit=config.segment_soft_limit,
            hard_limit=config.segment_hard_limit,
        )

    context_builder = ContextBuilder(
        character,
        max_history_turns=config.max_history_turns,
        max_history_tokens=config.max_history_tokens,
        timezone=config.timezone,
        lore_token_budget=config.lore_token_budget,
        segment_guide=segment_guide,
    )
    chat_config = ChatConfig.from_persona(config)
    return ChatSession(
        store=store,
        router=router,
        tool_registry=tool_registry,
        coordinator=coordinator,
        character=character,
        config=chat_config,
        scoring_agent=scoring_agent,
        context_builder=context_builder,
        decay_calculator=decay_calculator,
        port=port,
        segment_dispatcher=segment_dispatcher,
        query_store=query_store,
        resolve_db=resolve_db,
        sleep_gate=sleep_gate,
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
    event_share_queue: EventShareTaskQueue,
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
        event_share_queue=event_share_queue,
        diary_generator=diary_generator,
        character=character,
        config=life_config_obj,
        port=port,
        decay_calculator=decay_calculator,
    )


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

    # Providers 前置检查
    if not config.providers:
        raise PersonaConfigError(
            "未配置任何 LLM 提供者 (persona_ai.providers)。"
            "请参考新格式: persona_ai.providers.<name>.api_key / .base_url / .models[*]"
        )

    # ── Step 1: 基础设施 (store, router, port)
    store = await _build_store(bot, config)
    router = _build_router(config, store)
    port = _build_port(bot, store)

    segment_dispatcher = None
    if config.segment_enabled:
        segment_dispatcher = SegmentDispatcher(
            message_port=port,
        )

    # ── Step 2: event_agent (life 和 scheduler 共用)
    event_agent = EventGenerationAgent(router, config=config)

    # ── Step 3: event_share_queue (提前构造，供 character_life 和 _build_life 共用)
    event_share_queue = EventShareTaskQueue(
        data_store=store,
        share_threshold=config.proactive_event_share_threshold,
        max_retries=config.proactive_share_max_retries,
        timezone=config.timezone,
    )
    logger.info("延迟任务队列已初始化")

    # ── Step 4: character_life (提前构造，供 sleep_gate 和 suggest_action 引用)
    life_config = CharacterLifeConfig.from_persona(config)
    character_life = CharacterLife(
        config=life_config,
        event_agent=event_agent,
        data_store=store,
        character=character,
        event_share_queue=event_share_queue,
        share_threshold=config.proactive_event_share_threshold,
        share_delay_min=config.proactive_event_share_delay_min,
        share_delay_max=config.proactive_event_share_delay_max,
    )

    # ── Step 5: action_evaluator
    action_evaluator = ActionEvaluator(
        store=store,
        router=router,
        config=config,
        timezone=config.timezone,
    )
    logger.info("ActionEvaluator 已初始化")

    # ── Step 6: tool_registry (含 suggest_action executor)
    suggest_action_executor = make_suggest_action_executor(
        store=store,
        action_evaluator=action_evaluator,
        character_life=character_life,
        min_relationship=config.suggest_action_min_relationship,
        life_lock=character_life._state_lock,
    )

    tool_registry = ToolRegistry()
    tool_registry.register(ToolDomain.CHAT, SEARCH_MEMORY_TOOL, search_memory_executor)
    tool_registry.register(
        ToolDomain.CHAT,
        SEARCH_HISTORY_TOOL,
        make_search_history_executor(config.search_chat_history_max_chars),
    )
    tool_registry.register(ToolDomain.CHAT, ROLL_DICE_TOOL, roll_dice_executor)
    tool_registry.register(ToolDomain.CHAT, LIST_QUERY_DATABASES_TOOL, list_query_databases_executor)
    tool_registry.register(ToolDomain.CHAT, SEARCH_QUERY_TOOL, search_query_executor)
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
    logger.info("工具注册表与分段调度器已初始化")

    # ── Step 7: coordinator, decay_calculator
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

    # ── Step 8: _build_chat (注入 sleep_gate)
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

    chat = _build_chat(
        store, router, tool_registry, coordinator, character, config,
        decay_calculator, port, segment_dispatcher,
        query_store=bot.db.query, resolve_db=_resolve_query_db,
        sleep_gate=character_life,
    )

    # ── Step 9: _build_life (注入预构造组件)
    life = await _build_life(
        store, character, config, coordinator, port, decay_calculator,
        character_life=character_life,
        event_agent=event_agent,
        event_share_queue=event_share_queue,
    )

    # 启动探针
    try:
        probe_results = await router.probe_all_models()
    except Exception as e:
        logger.error(f"启动探针异常: {e}")
        probe_results = {}
    all_disabled = router.all_providers_disabled()
    if all_disabled:
        logger.warning("所有模型 probe 失败！Persona AI 功能将不可用")

    # 启动后台探针任务
    router.start_probe_task()

    logger.info("Persona 模块初始化完成")
    return PersonaApp(
        chat=chat, life=life, store=store, port=port,
        segment_dispatcher=segment_dispatcher,
        all_providers_disabled=all_disabled,
    )
