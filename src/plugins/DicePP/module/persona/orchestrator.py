"""
Persona Orchestrator - 核心编排层

协调各组件完成对话流程
"""
from typing import List, Dict, Optional, Any, Tuple, Set
import logging
import time
import random
from datetime import datetime, timedelta

from core.bot import Bot
from .character.loader import CharacterLoader
from .character.models import Character, ScheduledEventConfig
from .llm.router import LLMRouter
from .data.store import PersonaDataStore
from .data.models import ModelTier, UserProfile, RelationshipState, ScoreEvent
from .agents.scoring_agent import ScoringAgent
from .memory.context_builder import ContextBuilder
from .game.decay import DecayCalculator, DecayConfig
from .proactive.character_life import CharacterLife, CharacterLifeConfig
from .proactive.scheduler import ProactiveScheduler, ProactiveConfig
from .agents.event_agent import EventGenerationAgent

logger = logging.getLogger("persona.orchestrator")


class PersonaOrchestrator:
    def __init__(self, bot: Bot):
        self.bot = bot
        self.config = bot.config.persona_ai
        self.character: Optional[Character] = None
        self.character_loader: Optional[CharacterLoader] = None
        self.llm_router: Optional[LLMRouter] = None
        self.data_store: Optional[PersonaDataStore] = None
        self.scoring_agent: Optional[ScoringAgent] = None
        self.context_builder: Optional[ContextBuilder] = None
        self.decay_calculator: Optional[DecayCalculator] = None
        self.character_life: Optional[CharacterLife] = None
        self.event_agent: Optional[EventGenerationAgent] = None
        self.scheduler: Optional[ProactiveScheduler] = None
        self._initialized = False
        self._pending_messages: Dict[str, List[Dict[str, str]]] = {}
        self._last_messages: Dict[str, Tuple[str, float]] = {}  # key -> (message, timestamp)


    async def initialize(self) -> bool:
        """
        初始化 Orchestrator
        
        Returns:
            是否初始化成功
        """
        if self._initialized:
            return True
        
        if not self.config.enabled:
            logger.info("Persona AI 模块已禁用")
            return False
        
        try:
            # 1. 加载角色卡
            self.character_loader = CharacterLoader(self.config.character_path)
            self.character = self.character_loader.load(self.config.character_name)
            
            if not self.character:
                logger.error(f"无法加载角色卡: {self.config.character_name}")
                return False
            
            logger.info(f"角色卡已加载: {self.character.name}")
            
            # 2. 初始化 LLM 路由器
            if not self.config.primary_api_key:
                logger.error("未配置主模型 API Key")
                return False
            
            self.llm_router = LLMRouter(
                primary_api_key=self.config.primary_api_key,
                primary_base_url=self.config.primary_base_url,
                primary_model=self.config.primary_model,
                auxiliary_api_key=self.config.auxiliary_api_key,
                auxiliary_base_url=self.config.auxiliary_base_url,
                auxiliary_model=self.config.auxiliary_model,
                max_concurrent=self.config.max_concurrent_requests,
                timeout=self.config.timeout,
            )
            logger.info("LLM 路由器已初始化")
            
            # 3. 初始化数据存储
            # 获取原始数据库连接
            raw_db = self.bot.db._db
            if raw_db is None:
                logger.error("数据库未连接")
                return False
            
            self.data_store = PersonaDataStore(raw_db)
            await self.data_store.ensure_tables()
            logger.info("数据存储已初始化")

            self.scoring_agent = ScoringAgent(self.llm_router)
            self.context_builder = ContextBuilder(self.character, self.config.max_short_term_chars)

            # 初始化衰减计算器
            decay_config = DecayConfig(
                enabled=self.config.decay_enabled,
                grace_period_hours=self.config.decay_grace_period_hours,
                decay_rate_per_hour=self.config.decay_rate_per_hour,
                daily_cap=self.config.decay_daily_cap,
                floor_offset=self.config.decay_floor_offset,
            )
            self.decay_calculator = DecayCalculator(decay_config)
            logger.info("衰减计算器已初始化")

            # 初始化角色生活模拟
            self.event_agent = EventGenerationAgent(self.llm_router)
            life_config = CharacterLifeConfig(
                enabled=self.config.character_life_enabled,
                event_hours=self.config.character_life_event_hours,
                jitter_minutes=self.config.character_life_jitter_minutes,
                diary_time=self.config.character_life_diary_time,
            )
            self.character_life = CharacterLife(
                config=life_config,
                event_agent=self.event_agent,
                data_store=self.data_store,
                character=self.character,
            )
            logger.info("角色生活模拟已初始化")

            # 初始化主动消息调度器
            scheduler_config = ProactiveConfig(
                enabled=self.config.proactive_enabled,
                quiet_hours=(self.config.proactive_quiet_start, self.config.proactive_quiet_end),
                min_interval_hours=self.config.proactive_min_interval_hours,
                max_shares_per_event=self.config.proactive_max_shares,
                miss_enabled=self.config.proactive_miss_enabled,
                miss_min_hours=self.config.proactive_miss_min_hours,
                miss_min_score=self.config.proactive_miss_min_score,
            )
            self.scheduler = ProactiveScheduler(
                config=scheduler_config,
                data_store=self.data_store,
                character=self.character,
                bot=self.bot,
            )
            logger.info("主动消息调度器已初始化")

            logger.info("评分 Agent 和上下文构建器已初始化")

            self._initialized = True
            return True
            
        except Exception as e:
            logger.exception("初始化失败")
            return False

    async def chat(
        self,
        user_id: str,
        group_id: str,
        message: str,
        nickname: str = "",
    ) -> Optional[str]:
        if not self._initialized or not self.data_store or not self.llm_router or not self.character:
            return "Persona AI 模块未初始化"

        # 5 秒内完全相同的消息去重（防手抖/网络重试）
        dedup_key = f"{user_id}:{group_id}"
        now = time.monotonic()
        last = self._last_messages.get(dedup_key)
        if last and last[0] == message and (now - last[1]) < 5.0:
            logger.debug(f"去重: 5秒内重复消息已忽略 user={user_id}")
            return None
        self._last_messages[dedup_key] = (message, now)

        try:
            # 检查并应用时间衰减
            await self._check_and_apply_decay(user_id, group_id)

            history = await self.data_store.get_recent_messages(user_id, group_id, limit=1)
            is_first = len(history) == 0

            if is_first and self.character.first_mes:
                await self.data_store.add_message(user_id, group_id, "user", message)
                await self.data_store.add_message(user_id, group_id, "assistant", self.character.first_mes)
                return self.character.first_mes

            messages = await self._build_messages(user_id, group_id, message)

            response = await self.llm_router.generate(
                messages=messages,
                model_tier=ModelTier.PRIMARY,
            )

            await self.data_store.add_message(user_id, group_id, "user", message)
            await self.data_store.add_message(user_id, group_id, "assistant", response)
            await self.data_store.prune_old_messages(user_id, group_id, self.config.max_messages)

            await self._update_interaction(user_id, group_id, message, response)

            return response

        except Exception as e:
            logger.exception("对话处理失败")
            return "抱歉，我出错了，请稍后再试..."

    async def _update_interaction(self, user_id: str, group_id: str, user_msg: str, assistant_msg: str) -> None:
        if not self.data_store:
            return

        rel = await self.data_store.get_relationship(user_id, group_id)
        if not rel:
            initial = self.character.extensions.initial_relationship if self.character else 30.0
            rel = await self.data_store.init_relationship(user_id, group_id, initial)

        rel.last_interaction_at = datetime.now()
        await self.data_store.update_relationship(rel)

        key = f"{user_id}:{group_id}"
        if key not in self._pending_messages:
            self._pending_messages[key] = []
        self._pending_messages[key].append({"role": "user", "content": user_msg})
        self._pending_messages[key].append({"role": "assistant", "content": assistant_msg})

        if len(self._pending_messages[key]) >= self.config.scoring_interval * 2:
            try:
                await self._process_batch_scoring(user_id, group_id)
            except Exception as e:
                logger.warning(f"批量评分失败（不影响对话）: {e}")
                self._pending_messages.pop(key, None)

    async def _process_batch_scoring(self, user_id: str, group_id: str) -> None:
        if not self.scoring_agent or not self.data_store:
            return

        key = f"{user_id}:{group_id}"
        messages = self._pending_messages.get(key, [])
        if not messages:
            return

        self._pending_messages[key] = []

        profile = await self.data_store.get_user_profile(user_id)
        rel = await self.data_store.get_relationship(user_id, group_id)

        deltas, new_facts = await self.scoring_agent.batch_analyze(
            messages=messages,
            current_profile=profile,
            relationship=rel
        )

        if rel:
            composite_before = rel.composite_score
            rel.apply_deltas(deltas)
            await self.data_store.update_relationship(rel)

            event = ScoreEvent(
                user_id=user_id,
                group_id=group_id,
                deltas=deltas,
                composite_before=composite_before,
                composite_after=rel.composite_score,
                reason="批量评分"
            )
            await self.data_store.add_score_event(event)

        if new_facts and profile:
            profile.merge_facts(new_facts)
            await self.data_store.save_user_profile(profile)
        elif new_facts:
            new_profile = UserProfile(user_id=user_id, facts=new_facts)
            await self.data_store.save_user_profile(new_profile)

    async def _build_messages(
        self,
        user_id: str,
        group_id: str,
        current_message: str,
    ) -> List[Dict[str, str]]:
        if not self.data_store or not self.character or not self.context_builder:
            return [{"role": "user", "content": current_message}]

        history = await self.data_store.get_recent_messages(user_id, group_id, limit=20)
        history_dicts = [{"role": msg.role, "content": msg.content} for msg in history]
        profile = await self.data_store.get_user_profile(user_id)
        rel = await self.data_store.get_relationship(user_id, group_id)

        warmth_label = "友好"
        if rel:
            _, warmth_label = rel.get_warmth_level(self.character.get_warmth_labels())
        else:
            initial = float(self.character.extensions.initial_relationship)
            temp_rel = RelationshipState(
                user_id=user_id, intimacy=initial, passion=initial,
                trust=initial, secureness=initial
            )
            _, warmth_label = temp_rel.get_warmth_level(self.character.get_warmth_labels())

        return self.context_builder.build(
            short_term_history=history_dicts,
            user_profile=profile,
            current_message=current_message,
            warmth_label=warmth_label
        )

    async def clear_history(self, user_id: str, group_id: str) -> None:
        """清空对话历史"""
        if self.data_store:
            await self.data_store.clear_messages(user_id, group_id)

    def get_character_info(self) -> Dict:
        """获取角色信息"""
        if not self.character:
            return {}
        return {
            "name": self.character.name,
            "description": self.character.description[:100] + "..." if len(self.character.description) > 100 else self.character.description,
            "warmth_labels": self.character.get_warmth_labels(),
        }

    async def _check_and_apply_decay(self, user_id: str, group_id: str) -> None:
        """检查并应用时间衰减"""
        if not self.decay_calculator or not self.data_store:
            return

        try:
            rel = await self.data_store.get_relationship(user_id, group_id)
            if not rel:
                return

            initial = self.character.extensions.initial_relationship if self.character else 30.0
            deltas, reason = self.decay_calculator.calculate_decay(rel, initial)

            # 检查是否有实际衰减
            if abs(deltas.intimacy) > 0.01:
                composite_before = rel.composite_score
                rel.apply_deltas(deltas)
                await self.data_store.update_relationship(rel)

                # 记录衰减事件
                event = ScoreEvent(
                    user_id=user_id,
                    group_id=group_id,
                    deltas=deltas,
                    composite_before=composite_before,
                    composite_after=rel.composite_score,
                    reason=f"time_decay: {reason}",
                )
                await self.data_store.add_score_event(event)
                logger.info(f"应用时间衰减: {user_id} 衰减 {deltas.intimacy:.2f}, 原因: {reason}")

        except Exception as e:
            logger.warning(f"衰减检查失败（不影响对话）: {e}")

    async def reload_character(self) -> Tuple[bool, str]:
        """
        热重新加载角色卡

        Returns:
            (是否成功, 消息)
        """
        if not self.character_loader:
            return False, "角色加载器未初始化"

        try:
            new_character = self.character_loader.load(self.config.character_name)

            if not new_character:
                return False, f"无法加载角色卡: {self.config.character_name}"

            self.character = new_character

            # 重新创建上下文构建器（因为角色变了）
            self.context_builder = ContextBuilder(self.character, self.config.max_short_term_chars)

            logger.info(f"角色卡已热重载: {self.character.name}")
            return True, f"角色卡已重载: {self.character.name}"

        except Exception as e:
            logger.exception("角色卡重载失败")
            return False, f"重载失败: {e}"

    async def tick(self) -> List[Dict]:
        """
        定时调用（从 Command.tick 触发）
        驱动角色生活模拟和主动消息调度

        Returns:
            待发送的消息列表
        """
        if not self._initialized:
            return []

        messages = []

        try:
            # 尝试生成生活事件
            if self.character_life:
                event = await self.character_life.tick()
                if event:
                    logger.info(f"角色生活事件: {event.get('description', '')[:50]}...")
                    # 将事件添加到调度器的分享队列
                    if self.scheduler:
                        self.scheduler.add_event_to_share(event.get('description', ''))

            # 运行主动消息调度器
            if self.scheduler:
                proactive_msgs = await self.scheduler.tick()
                messages.extend(proactive_msgs)

        except Exception as e:
            logger.warning(f"tick 处理失败: {e}")

        return messages

    async def tick_daily(self) -> Optional[str]:
        """
        每日调用（从 Command.tick_daily 触发）
        生成日记

        Returns:
            生成的日记内容
        """
        if not self._initialized or not self.character_life:
            return None

        try:
            diary = await self.character_life.generate_diary()
            if diary:
                logger.info(f"生成日记: {len(diary)} 字")
            return diary
        except Exception as e:
            logger.exception(f"日记生成失败: {e}")
            return None

    async def generate_daily_event(self) -> Optional[dict]:
        """
        手动触发生活事件生成（用于调试）

        Returns:
            事件数据 {"description": ..., "reaction": ..., "time": ...}
        """
        if not self._initialized or not self.character_life:
            return None

        try:
            # 绕过 tick 的时间检查，直接生成事件
            return await self.character_life._generate_daily_event()
        except Exception as e:
            logger.exception(f"手动生成事件失败: {e}")
            return None
