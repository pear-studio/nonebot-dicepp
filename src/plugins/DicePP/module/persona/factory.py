"""Persona 模块工厂函数

负责从 Bot 组装所有依赖，创建 ChatSession / LifeSimulator / MessagePort。
"""
from typing import Optional
from dataclasses import dataclass
import logging

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
from .life.character_life import CharacterLife, CharacterLifeConfig
from .life.diary import DiaryGenerator, DiaryConfig
from .life.event_agent import EventGenerationAgent
from .life.proactive_config import ProactiveConfig
from .life.proactive_scheduler import ProactiveScheduler
from .life.event_share_queue import EventShareTaskQueue
from .life.simulator import LifeSimulator, LifeConfig
from .life.target import TargetSelector
from .llm.coordinator import LLMCallCoordinator
from .llm.router import LLMRouter
from .tools.registry import ToolRegistry, ToolDomain
from .tools.search_memory import SEARCH_MEMORY_TOOL, search_memory_executor
from .tools.search_history import SEARCH_HISTORY_TOOL, make_search_history_executor
from .tools.roll_dice import ROLL_DICE_TOOL, roll_dice_executor

logger = logging.getLogger("persona.factory")


@dataclass
class PersonaApp:
    """Persona 模块入口 — 持有 chat/life/store/port 四个公开句柄"""

    chat: ChatSession
    life: LifeSimulator
    store: PersonaDataStore
    port: MessagePort

    async def update_character(self, character: Character) -> None:
        """统一传播新的角色卡到所有子系统。"""
        self.chat.update_character(character)
        self.life.update_character(character)
        diary_generator = getattr(self.life, "diary_generator", None)
        if diary_generator is not None:
            diary_generator.update_character(character.name, character.description)


async def create_persona(bot: Bot) -> Optional[PersonaApp]:
    """从 Bot 组装 Persona 模块所有组件

    Returns:
        PersonaApp 实例；模块禁用时返回 ``None``。

    Raises:
        PersonaCharacterLoadError: 角色卡加载失败。
        PersonaConfigError: 必填配置（如 ``primary_api_key``）缺失。
        PersonaStorageError: 数据库句柄不可用。
    """
    config = bot.config.persona_ai
    if not config.enabled:
        logger.info("Persona AI 模块已禁用")
        return None

    # 1. 加载角色卡
    character_loader = CharacterLoader(config.character_path)
    character = character_loader.load(config.character_name)
    if not character:
        raise PersonaCharacterLoadError(
            f"无法加载角色卡: {config.character_name} (path={config.character_path})"
        )
    logger.info(f"角色卡已加载: {character.name}")

    # 2. 初始化 LLM 路由器
    if not config.primary_api_key:
        raise PersonaConfigError("未配置主模型 API Key (persona_ai.primary_api_key)")

    llm_router = LLMRouter(
        primary_api_key=config.primary_api_key,
        primary_base_url=config.primary_base_url,
        primary_model=config.primary_model,
        auxiliary_api_key=config.auxiliary_api_key,
        auxiliary_base_url=config.auxiliary_base_url,
        auxiliary_model=config.auxiliary_model,
        max_concurrent=config.max_concurrent_requests,
        timeout=config.timeout,
    )
    logger.info("LLM 路由器已初始化")

    # 3. 初始化数据存储
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
        group_max_messages=config.group_max_messages,
    )
    try:
        await store.ensure_tables()
    except Exception as e:
        raise PersonaStorageError(f"数据库表初始化失败: {e}") from e
    logger.info("数据存储已初始化")

    # 4. 设置 LLMRouter 配额检查依赖
    llm_router.data_store = store
    llm_router.config = config
    llm_router.daily_limit = config.daily_limit
    llm_router.quota_check_enabled = config.quota_check_enabled
    llm_router.trace_enabled = config.trace_enabled
    llm_router.trace_max_age_days = config.trace_max_age_days

    # 5. 评分和上下文构建
    scoring_agent = ScoringAgent(llm_router)
    context_builder = ContextBuilder(
        character,
        max_short_term_chars=config.max_short_term_chars,
        timezone=config.timezone,
        lore_token_budget=config.lore_token_budget,
    )
    logger.info("评分 Agent 和上下文构建器已初始化")

    # 6. 衰减计算器
    decay_calculator = DecayCalculator(
        DecayConfig.from_persona(config),
        timezone_name=config.timezone,
    )
    logger.info("衰减计算器已初始化")

    # 7. 工具注册表
    tool_registry = ToolRegistry()
    tool_registry.register(ToolDomain.CHAT, SEARCH_MEMORY_TOOL, search_memory_executor)
    tool_registry.register(
        ToolDomain.CHAT,
        SEARCH_HISTORY_TOOL,
        make_search_history_executor(config.search_chat_history_max_chars),
    )
    tool_registry.register(ToolDomain.CHAT, ROLL_DICE_TOOL, roll_dice_executor)
    logger.info("工具注册表已初始化")

    # 8. MessagePort（on_delivery_failed 写入聊天记录）
    pipeline = MessagePipeline()
    pipeline.add(TruncateStage(max_chars=2000))

    async def on_delivery_failed(user_id, group_id, content, error):
        try:
            await store.add_message(user_id, group_id, "assistant", f"[发送失败] {content}")
        except Exception:
            logger.exception("on_delivery_failed 二次入库失败")

    port = MessagePort(bot, pipeline=pipeline, on_delivery_failed=on_delivery_failed)
    logger.info("消息发送端口已初始化")

    # 9. coordinator（chat 和 scheduler 共享）
    coordinator = LLMCallCoordinator(
        max_failures=config.proactive_coordinator_max_failures,
        max_iterations=config.proactive_coordinator_max_iterations,
    )
    logger.info("LLM 调用协调器已初始化")

    # 10. 角色生活模拟
    event_agent = EventGenerationAgent(llm_router, config=config)
    life_config = CharacterLifeConfig.from_persona(config)
    character_life = CharacterLife(
        config=life_config,
        event_agent=event_agent,
        data_store=store,
        character=character,
    )

    # 11. 主动消息调度器
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

    # 12. 同步波动边界（必须在 character_life.load_persistent_state 之前注入）
    character_life.boundary_notifier = scheduler
    await character_life.load_persistent_state()
    logger.info("角色生活模拟已初始化")

    # 13. 延迟任务队列
    delayed_task_queue = EventShareTaskQueue(
        data_store=store,
        share_threshold=config.proactive_event_share_threshold,
        max_retries=config.proactive_share_max_retries,
        timezone=config.timezone,
    )
    logger.info("延迟任务队列已初始化")

    # 14. 日记生成器
    diary_config = DiaryConfig(
        diary_time=config.character_life_diary_time,
        timezone=config.timezone,
    )
    diary_generator = DiaryGenerator(
        store=store,
        event_agent=event_agent,
        character_name=character.name,
        character_description=character.description,
        config=diary_config,
    )

    # 15. ChatSession
    chat_config = ChatConfig.from_persona(config)
    chat = ChatSession(
        store=store,
        router=llm_router,
        tool_registry=tool_registry,
        coordinator=coordinator,
        character=character,
        config=chat_config,
        scoring_agent=scoring_agent,
        context_builder=context_builder,
        decay_calculator=decay_calculator,
        port=port,
    )

    # 16. LifeSimulator
    life_config_obj = LifeConfig.from_persona(config)
    life = LifeSimulator(
        store=store,
        character_life=character_life,
        scheduler=scheduler,
        delayed_task_queue=delayed_task_queue,
        diary_generator=diary_generator,
        character=character,
        config=life_config_obj,
        port=port,
        decay_calculator=decay_calculator,
    )

    logger.info("Persona 模块初始化完成")
    return PersonaApp(chat=chat, life=life, store=store, port=port)
