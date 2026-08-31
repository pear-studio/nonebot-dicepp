"""Persona 模块工厂函数

负责从 Bot 组装所有依赖，创建 ChatOrchestrator / LifeSimulator / MessagePort。
"""
import os
from typing import Any, List, Optional
from dataclasses import dataclass

from dicepp_data import PERSONA_DB_ASSET
from plugins.DicePP.utils.logger import logger
from plugins.DicePP.core.bot import Bot
from plugins.DicePP.core.config.basic import Paths

from .character.loader import CharacterLoader
from .character.models import Character
from ..common.mode_defs import query_database_for_mode
from .chat.orchestrator import ChatOrchestrator, ChatOutcome
from .chat.chat_config import ChatConfig
from .chat.context import ContextBuilder
from .chat.response_handler import ResponseHandler
from .data.store import PersonaDataStore
from .data.models import MessageType
from .exceptions import (
    PersonaCharacterLoadError,
    PersonaConfigError,
    PersonaStorageError,
)
from .gateway.pipeline import MessagePipeline, TruncateStage
from .gateway.port import MessagePort
from .life.action_evaluator import ActionEvaluator
from .life.character_life import CharacterLife, CharacterLifeConfig
from .life.diary import DiaryGenerator, DiaryConfig
from .life.simulator import LifeSimulator, LifeConfig
from .life.protocols import SleepGate
from .life.types import DailyTickResult
from .life.dm_agent import DMAgent
from .life.character_agent import CharacterAgent
from .life.sa_agent import SAAgent
from .life.agent import DEFAULT_BACKGROUND_LLM_MAX_ROUNDS
from .life.conversation_scope import ConversationScope, NS_LIFE_CHARACTER
from .life.conversation_registry import ConversationRegistry
from .life.conversation_summary import ProviderSummarizer
from .life.change_sources import CharacterStateChangeSource
from .llm.client import DeepSeekTextModelClient, TextModelClient


@dataclass
class PersonaApp:
    """Persona 模块入口 — 持有 chat/life/store/port 四个公开句柄"""

    chat: ChatOrchestrator
    life: LifeSimulator
    store: PersonaDataStore
    port: MessagePort
    current_character_name: str = ""

    # ── 角色卡 ──

    async def update_character(self, character: Character) -> None:
        """统一传播新的角色卡到所有子系统。"""
        self.chat.update_character(character)
        self.life.update_character(character)

    async def switch_character_db(self, new_character_name: str) -> None:
        """切换到新角色的 persona_db"""
        await self.store.switch_persona_db(new_character_name)
        self.current_character_name = new_character_name

    async def shutdown(self) -> None:
        """应用关闭钩子。当前无需清理的后台资源（3b 轮换若引入定时器再在此收口）。"""
        return None

    def get_character(self) -> Optional[Character]:
        return self.chat.character

    # ── 对话 ──

    async def chat_with_user(
        self, user_id: str, group_id: str, message: str, nickname: str,
        image_data_urls: Optional[List[str]] = None,
        inbound_message_stream_id: Optional[int] = None,
    ) -> ChatOutcome:
        from .chat.chat_shared import ChatCallContext
        ctx = ChatCallContext(
            image_data_urls=image_data_urls,
            nickname=nickname,
            inbound_message_stream_id=inbound_message_stream_id,
        )
        return await self.chat.chat(user_id, group_id, message, ctx=ctx)

    # ── 消息发送 ──

    async def send_message(
        self,
        user_id: str,
        group_id: str,
        content: str,
        msg_id: Optional[int] = None,
        message_type: MessageType = MessageType.CHAT,
    ) -> bool:
        return await self.port.send(
            user_id, group_id, content,
            msg_id=msg_id,
            message_type=message_type,
        )

    # ── 文本模型 ──

    def get_client(self) -> Optional[TextModelClient]:
        return self.chat.client

    def get_character_agent(self) -> Optional[Any]:
        """返回 LifeSimulator 持有的 CharacterAgent（日报/外部模块使用）"""
        return getattr(self.life, 'character_agent', None)

    # ── 生命周期驱动 ──

    async def is_awake(self) -> bool:
        """角色是否处于唤醒状态。委托给 ``ChatOrchestrator.is_awake()``。"""
        return await self.chat.is_awake()

    async def tick(self) -> None:
        await self.life.tick()

    async def tick_daily(self) -> DailyTickResult:
        return await self.life.tick_daily()

    async def run_daily_planning(self, diary: str, diary_date: str) -> None:
        await self.life.run_daily_planning(diary, diary_date)


@dataclass
class _Infra:
    """基础设施组件 — _build_infra 的返回容器"""
    store: PersonaDataStore
    client: TextModelClient
    port: MessagePort


@dataclass
class ChatDeps:
    """_build_chat 的依赖参数集合 — 替代 16 个独立 keyword 参数"""
    store: PersonaDataStore
    client: TextModelClient
    character: Character
    config: Any
    port: MessagePort
    action_evaluator: ActionEvaluator
    character_life: CharacterLife
    daily_ai_limit: int
    master_user_id: str
    query_store: Any = None
    resolve_db: Any = None
    sleep_gate: Optional[SleepGate] = None


def _load_character(character_path: str, character_name: str) -> Character:
    """加载角色卡"""
    character_loader = CharacterLoader(character_path)
    character = character_loader.load(character_name)
    if not character:
        raise PersonaCharacterLoadError(
            f"无法加载角色卡: {character_name} (path={character_path})"
        )
    logger.info(f"角色卡已加载: {character.name}")
    return character


async def _build_store(bot: Bot, config, character_name: str) -> PersonaDataStore:
    """初始化数据存储（双连接：persona_db + core_db）"""
    core_db = getattr(getattr(bot, "db", None), "_db", None)
    if core_db is None:
        raise PersonaStorageError("数据库未初始化（bot.db 或 bot.db._db 为 None）")

    bot_dir = Paths.bot_data_dir(bot.account)
    persona_db_path = str(
        PERSONA_DB_ASSET.resolve(
            Paths.instance_layout(),
            bot_id=str(bot.account),
            character=character_name,
        )
    )
    os.makedirs(str(bot_dir), exist_ok=True)

    store = PersonaDataStore(
        persona_db_path,
        core_db,
    )
    try:
        await store.open()
    except Exception as e:
        raise PersonaStorageError(f"数据库表初始化失败: {e}") from e
    logger.info(f"数据存储已初始化: persona_db={persona_db_path}")

    return store


def _build_client(bot: Bot, config, store: PersonaDataStore) -> TextModelClient:
    """从实例级 user.json 构建唯一的 DeepSeek 文本客户端。"""
    user_config = bot.user_config
    if not user_config.deepseek_api_key:
        raise PersonaConfigError(
            "未配置 DeepSeek API Key，请在 config/user.json 中设置 deepseek_api_key"
        )
    client = DeepSeekTextModelClient(
        api_key=user_config.deepseek_api_key,
        model=user_config.deepseek_model,
        base_url=user_config.deepseek_base_url,
        data_store=store,
        llm_debug_enabled=user_config.llm_debug_enabled,
    )
    logger.info("DeepSeek 文本客户端已初始化: model=%s", client.model)
    return client


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


async def _build_infra(bot: Bot, config, character_name: str) -> _Infra:
    """创建基础设施组件: store / client / port"""
    store = await _build_store(bot, config, character_name)
    client = _build_client(bot, config, store)
    port = _build_port(bot, store)

    return _Infra(
        store=store,
        client=client,
        port=port,
    )


def _build_agents(
    store: PersonaDataStore,
    client: TextModelClient,
    config,
    character: Character,
) -> tuple[DMAgent, CharacterAgent, SAAgent, CharacterLife, ActionEvaluator]:
    """创建 Agent 实例 — 工具由各 Agent 自建。"""
    dm_agent = DMAgent(store, client)
    character_agent = CharacterAgent(store, client)
    sa_agent = SAAgent(store, client)

    life_config = CharacterLifeConfig.from_persona(config)
    character_life = CharacterLife(
        config=life_config,
        data_store=store,
        character=character,
        dm_agent=dm_agent,
        character_agent=character_agent,
    )

    action_evaluator = ActionEvaluator(
        store=store,
        client=client,
        timezone="Asia/Shanghai",
    )
    logger.info("ActionEvaluator 已初始化")
    logger.info("Agent 实例已初始化")
    return dm_agent, character_agent, sa_agent, character_life, action_evaluator


def _make_resolve_query_db(bot: Bot):
    """返回 _build_chat 所需的 resolve_db 回调（闭包捕获 bot）"""
    async def _resolve_query_db(user_id: str, group_id: str) -> str:
        config_key = group_id or f"__user__{user_id}"
        row = await bot.db.group_config.get(config_key)
        default_database = query_database_for_mode(bot.config.default_mode)
        if row and row.data:
            return row.data.get("query_database", default_database)
        return default_database
    return _resolve_query_db


def _build_chat(deps: ChatDeps) -> ChatOrchestrator:
    """组装 ChatOrchestrator（替代 ChatSession）"""
    # ChatConfig owns all chat-only policy defaults.
    chat_config = ChatConfig()
    from .chat.context import SegmentGuide

    segment_guide = SegmentGuide(
        enabled=True,
        target_chars=chat_config.segment_target_chars,
        max_chars=chat_config.segment_max_chars,
        soft_limit=chat_config.segment_soft_limit,
        hard_limit=chat_config.segment_hard_limit,
    )

    context_builder = ContextBuilder(
        deps.character,
        max_history_turns=chat_config.max_history_turns,
        max_history_tokens=chat_config.max_history_tokens,
        timezone=chat_config.timezone,
        lore_token_budget=chat_config.lore_token_budget,
        segment_guide=segment_guide,
    )

    response_handler = ResponseHandler(store=deps.store, port=deps.port)
    return ChatOrchestrator(
        store=deps.store,
        client=deps.client,
        character=deps.character,
        config=chat_config,
        response_handler=response_handler,
        context_builder=context_builder,
        sleep_gate=deps.sleep_gate,
        action_evaluator=deps.action_evaluator,
        character_life=deps.character_life,
        daily_ai_limit=deps.daily_ai_limit,
        master_user_id=deps.master_user_id,
    )


async def _build_life(
    store: PersonaDataStore,
    character: Character,
    character_life: CharacterLife,
    dm_agent: DMAgent,
    character_agent: CharacterAgent,
    sa_agent: SAAgent,
) -> LifeSimulator:
    """组装 LifeSimulator。

    A2: 创建 Life ConversationRegistry 并注入 DM / Character Agent。
    """
    # A2: 创建 Life ConversationRegistry
    max_rounds = DEFAULT_BACKGROUND_LLM_MAX_ROUNDS
    from .agent.runtime_types import LoopLimits
    from .agent.runtime import AgentRuntime
    # 所有 Life Agent 共享同一文本客户端。
    client = dm_agent.client
    life_registry = ConversationRegistry(
        store,
        runtime_factory=lambda: AgentRuntime(
            client=client, store=store,
            limits=LoopLimits(max_rounds=max_rounds),
        ),
        change_source_factory=lambda scope: (
            [CharacterStateChangeSource(store)]
            if scope.namespace == NS_LIFE_CHARACTER
            else []
        ),
        character_id_provider=lambda: character.character_id,
        summarizer=ProviderSummarizer(client),
    )
    dm_agent.inject_registry(
        life_registry,
        ConversationScope.for_life_dm(character.character_id),
    )
    character_agent.inject_registry(
        life_registry,
        ConversationScope.for_life_character(character.character_id),
    )
    # SA 不注入 registry（保持内存后即弃）
    logger.info("A2: Life ConversationRegistry 已创建并注入 DM/Character Agent")

    await character_life.load_persistent_state()
    logger.info("角色生活模拟已初始化")

    diary_config = DiaryConfig(
        diary_time="23:30",
        timezone="Asia/Shanghai",
    )
    diary_generator = DiaryGenerator(
        store=store,
        character_agent=character_agent,
        character=character,
        config=diary_config,
    )

    life_config_obj = LifeConfig()
    return LifeSimulator(
        store=store,
        character_life=character_life,
        diary_generator=diary_generator,
        character=character,
        config=life_config_obj,
        dm_agent=dm_agent,
        character_agent=character_agent,
        sa_agent=sa_agent,
    )

async def create_persona(bot: Bot) -> Optional[PersonaApp]:
    """从 Bot 组装 Persona 模块所有组件

    Returns:
        PersonaApp 实例；模块禁用时返回 ``None``。

    Raises:
        PersonaCharacterLoadError: 角色卡加载失败。
        PersonaConfigError: DeepSeek API Key 缺失。
        PersonaStorageError: 数据库句柄不可用。
    """
    config = bot.config.persona_ai
    if not config.enabled:
        logger.info("Persona AI 模块已禁用")
        return None

    character_name = config.character_name
    character = _load_character(str(Paths.CONTENT_CHARACTERS_DIR), character_name)
    if not bot.user_config.deepseek_api_key:
        raise PersonaConfigError(
            "未配置 DeepSeek API Key，请在 config/user.json 中设置 deepseek_api_key"
        )
    infra = await _build_infra(bot, config, character_name)
    dm_agent, character_agent, sa_agent, character_life, action_evaluator = _build_agents(
        infra.store, infra.client, config, character,
    )

    chat = _build_chat(ChatDeps(
        store=infra.store,
        client=infra.client,
        character=character,
        config=config,
        port=infra.port,
        query_store=bot.db.query,
        resolve_db=_make_resolve_query_db(bot),
        sleep_gate=character_life,
        action_evaluator=action_evaluator,
        character_life=character_life,
        daily_ai_limit=bot.user_config.daily_ai_limit,
        master_user_id=bot.config.master,
    ))

    life = await _build_life(
        infra.store, character,
        character_life=character_life,
        dm_agent=dm_agent,
        character_agent=character_agent,
        sa_agent=sa_agent,
    )

    logger.info("Persona 模块初始化完成")
    return PersonaApp(
        chat=chat, life=life, store=infra.store, port=infra.port,
        current_character_name=character_name,
    )
