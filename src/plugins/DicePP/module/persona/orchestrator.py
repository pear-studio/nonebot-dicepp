"""
Persona Orchestrator - 核心编排层

协调各组件完成对话流程
"""
from typing import List, Dict, Optional, Any, Tuple
import logging
import time
from datetime import datetime

from core.bot import Bot
from .character.loader import CharacterLoader
from .character.models import Character
from .llm.router import LLMRouter
from .data.store import PersonaDataStore
from .data.models import ModelTier, UserProfile, RelationshipState, ScoreEvent
from .agents.scoring_agent import ScoringAgent
from .memory.context_builder import ContextBuilder

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
