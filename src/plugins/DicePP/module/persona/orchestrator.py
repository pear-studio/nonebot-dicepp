"""
Persona Orchestrator - 核心编排层

协调各组件完成对话流程
"""
from typing import List, Dict, Optional, Any, Tuple, Set
import json
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
from .wall_clock import persona_wall_now, PERSONA_EPOCH
from .proactive.character_life import CharacterLife, CharacterLifeConfig
from .proactive.scheduler import ProactiveScheduler, ProactiveConfig
from .proactive.target_selector import TargetSelector
from .proactive.delayed_task_queue import DelayedTaskQueue
from .agents.event_agent import EventGenerationAgent

# Phase 4: 掷骰工具
from .utils.roll_adapter import RollAdapter

logger = logging.getLogger("persona.orchestrator")


class PersonaOrchestrator:
    DIGEST_MAX_MESSAGES = 6
    DIGEST_MAX_CHARS = 80

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
        self.delayed_task_queue: Optional[DelayedTaskQueue] = None
        self._initialized = False
        self._pending_messages: Dict[str, List[Dict[str, str]]] = {}
        self._last_messages: Dict[str, Tuple[str, float]] = {}  # key -> (message, timestamp)

    def _create_context_builder(self, character: Character) -> ContextBuilder:
        return ContextBuilder(
            character,
            max_short_term_chars=self.config.max_short_term_chars,
            timezone=self.config.timezone,
            lore_token_budget=self.config.lore_token_budget,
        )

    @staticmethod
    def _build_conversation_digest(history: List[Dict[str, str]]) -> str:
        lines = []
        prefix_map = {"user": "U", "assistant": "A", "tool": "T", "system": "S"}
        for msg in history[-PersonaOrchestrator.DIGEST_MAX_MESSAGES:]:
            prefix = prefix_map.get(msg.get("role"), "?")
            text = msg.get("content", "")
            if len(text) > PersonaOrchestrator.DIGEST_MAX_CHARS:
                text = text[:PersonaOrchestrator.DIGEST_MAX_CHARS - 3] + "..."
            lines.append(f"{prefix}: {text}")
        return "; ".join(lines)

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
            
            self.data_store = PersonaDataStore(
                raw_db,
                group_activity_decay_per_day=self.config.group_activity_decay_per_day,
                group_activity_floor_whitelist=self.config.group_activity_floor_whitelist,
                group_activity_decay_with_content=self.config.group_activity_decay_with_content,
                group_activity_content_window_hours=self.config.group_activity_content_window_hours,
                timezone=self.config.timezone,
            )
            await self.data_store.ensure_tables()
            logger.info("数据存储已初始化")

            # Phase 4: 设置 LLMRouter 的配额检查依赖
            if self.llm_router:
                self.llm_router.data_store = self.data_store
                self.llm_router.config = self.config
                self.llm_router.daily_limit = self.config.daily_limit
                self.llm_router.quota_check_enabled = self.config.quota_check_enabled
                self.llm_router.trace_enabled = self.config.trace_enabled
                self.llm_router.trace_max_age_days = self.config.trace_max_age_days

            self.scoring_agent = ScoringAgent(self.llm_router)
            self.context_builder = self._create_context_builder(self.character)

            # 初始化衰减计算器
            self.decay_calculator = DecayCalculator(
                DecayConfig.from_persona(self.config),
                timezone_name=self.config.timezone,
            )
            logger.info("衰减计算器已初始化")

            # 初始化角色生活模拟
            self.event_agent = EventGenerationAgent(self.llm_router)
            life_config = CharacterLifeConfig(
                enabled=self.config.character_life_enabled,
                slot_match_window_minutes=self.config.character_life_jitter_minutes,
                diary_time=self.config.character_life_diary_time,
                timezone=self.config.timezone,
            )
            self.character_life = CharacterLife(
                config=life_config,
                event_agent=self.event_agent,
                data_store=self.data_store,
                character=self.character,
            )
            await self.character_life.load_persistent_state()
            logger.info("角色生活模拟已初始化")

            # 初始化主动消息调度器
            target_selector = TargetSelector(
                data_store=self.data_store,
                bot_config=self.config,
                decay_calculator=self.decay_calculator,
                character=self.character,
            )
            scheduler_config = ProactiveConfig(
                enabled=self.config.proactive_enabled,
                min_interval_hours=self.config.proactive_min_interval_hours,
                max_shares_per_event=self.config.proactive_max_shares,
                share_time_window_minutes=self.config.proactive_share_time_window_minutes,
                miss_enabled=self.config.proactive_miss_enabled,
                miss_min_hours=self.config.proactive_miss_min_hours,
                miss_min_score=self.config.proactive_miss_min_score,
                greeting_phrases=dict(self.config.proactive_greeting_phrases),
                timezone=self.config.timezone,
                share_threshold=self.config.proactive_event_share_threshold,
            )
            self.scheduler = ProactiveScheduler(
                config=scheduler_config,
                data_store=self.data_store,
                character=self.character,
                event_agent=self.event_agent,
                bot=self.bot,
                decay_calculator=self.decay_calculator,
                target_selector=target_selector,
            )
            await self.scheduler.load_persistent_state()
            logger.info("主动消息调度器已初始化")

            self.delayed_task_queue = DelayedTaskQueue(
                data_store=self.data_store,
                share_threshold=self.config.proactive_event_share_threshold,
                timezone=self.config.timezone,
            )
            logger.info("延迟任务队列已初始化")

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

            # Phase 3d: 厌倦拒绝机制
            # 只在非首次对话且是聊天消息（非骰子/AI指令）时检查
            # 反向排除：只有纯聊天消息和 .ai 命令才检查拒绝，所有 . 开头指令视为骰子指令
            is_chat_message = not message.startswith(".") or message.lower().startswith(".ai")
            if self.config.relationship_refuse_enabled and not is_first and is_chat_message:
                rel = await self.data_store.get_relationship(user_id, group_id)
                if rel:
                    # 应用时间衰减获取有效关系状态
                    if self.decay_calculator:
                        initial = float(self.character.extensions.initial_relationship)
                        rel = self.decay_calculator.effective_relationship(rel, initial)
                    warmth_level, _ = rel.get_warmth_level(self.character.get_warmth_labels())
                    if warmth_level == 0:
                        # 厌倦区间（0-10分），计算拒绝概率
                        score = rel.composite_score
                        base = self.config.relationship_refuse_prob_base
                        max_p = self.config.relationship_refuse_prob_max
                        p_refuse = base + (max_p - base) * (1 - score / 10)
                        if random.random() < p_refuse:
                            # 随机选择拒绝语：优先使用角色卡配置，回退到系统默认
                            default_refuse_messages = [
                                "...（对方似乎没有兴趣理你）",
                                "...（已读不回）",
                                "嗯。",
                            ]
                            char_refuse = self.character.extensions.refuse_messages
                            # None 表示未配置（使用默认），空列表表示明确不拒绝
                            if char_refuse is None:
                                refuse_messages = default_refuse_messages
                            else:
                                refuse_messages = char_refuse  # 空列表也表示明确配置

                            if refuse_messages:
                                # 非空列表才执行拒绝
                                refuse_response = random.choice(refuse_messages)
                                logger.info(
                                    f"厌倦拒绝触发: user={user_id}, score={score:.2f}, "
                                    f"p_refuse={p_refuse:.2%}"
                                )
                                # 记录用户消息和拒绝回复
                                await self.data_store.add_message(user_id, group_id, "user", message)
                                await self.data_store.add_message(user_id, group_id, "assistant", refuse_response)
                                return refuse_response
                            # 空列表表示明确不拒绝，继续正常对话

            messages = await self._build_messages(user_id, group_id, message)

            # Phase 3: 工具调用
            if self.config.tools_enabled:
                logger.debug(f"对话走 tools 路径: user={user_id}, tools_enabled=true")
                response = await self._chat_with_tools(user_id, group_id, messages)
            else:
                logger.debug(f"对话走普通路径: user={user_id}, tools_enabled=false")
                response = await self.llm_router.generate(
                    messages=messages,
                    model_tier=ModelTier.PRIMARY,
                    user_id=user_id,
                    group_id=group_id,
                )

            await self.data_store.add_message(user_id, group_id, "user", message)
            await self.data_store.add_message(user_id, group_id, "assistant", response)
            await self.data_store.prune_old_messages(user_id, group_id, self.config.max_messages)

            await self._update_interaction(user_id, group_id, message, response)

            return response

        except Exception as e:
            logger.exception("对话处理失败")
            return "抱歉，我出错了，请稍后再试..."

    # ========== Phase 3: 工具调用 ==========

    async def _chat_with_tools(
        self,
        user_id: str,
        group_id: str,
        messages: List[Dict],
    ) -> str:
        """
        支持工具调用的对话（完整循环实现）

        使用 tool_executor 回调在 LLMClient 内部完成工具调用循环，
        支持最多 max_tool_rounds 轮工具调用。
        """
        tools = self._get_tools()

        # 创建工具执行器回调
        async def tool_executor(tool_calls: List[Dict]) -> List[Dict]:
            """执行工具调用并返回结果"""
            results = []
            for tc in tool_calls:
                result = await self._execute_tool(
                    tool_name=tc["name"],
                    arguments=tc["arguments"],
                    user_id=user_id,
                    group_id=group_id,
                )
                results.append({
                    "tool_call_id": tc["id"],
                    "content": str(result),
                })
            return results

        # 调用带工具的生成，在内部完成完整循环
        content, metadata = await self.llm_router.generate_with_tools(
            messages=messages,
            tools=tools,
            tool_executor=tool_executor,
            model_tier=ModelTier.PRIMARY,
            max_tool_rounds=self.config.tools_max_rounds,
            user_id=user_id,
            group_id=group_id,
        )

        # 记录工具调用元数据便于调试
        if metadata.get("tool_rounds", 0) > 0:
            logger.debug(
                f"工具调用完成: user={user_id}, "
                f"rounds={metadata.get('tool_rounds')}, "
                f"tools={metadata.get('tool_names')}, "
                f"cached={metadata.get('cached_tokens', 0)}"
            )

        return content

    def _get_tools(self) -> List[Dict]:
        """获取工具定义"""
        return [
            {
                "type": "function",
                "function": {
                    "name": "search_memory",
                    "description": "搜索关于用户或特定话题的记忆，包括用户档案、群聊观察记录、日记等",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "搜索关键词，如用户提到的内容、话题、名字等"
                            },
                            "type": {
                                "type": "string",
                                "enum": ["all", "profile", "observation", "diary"],
                                "description": "搜索类型：all=全部, profile=用户档案, observation=群聊观察, diary=日记",
                                "default": "all"
                            },
                            "days": {
                                "type": "integer",
                                "description": "日记搜索天数限制（仅对 diary 有效）",
                                "default": 7
                            },
                            "limit": {
                                "type": "integer",
                                "description": "最多返回几条结果（1-20）",
                                "default": 5,
                                "minimum": 1,
                                "maximum": 20
                            }
                        },
                        "required": ["query"]
                    }
                }
            },
            # Phase 4: 掷骰工具
            {
                "type": "function",
                "function": {
                    "name": "roll_dice",
                    "description": "执行 TRPG 骰子表达式（如 1d20, 2d6+3, 1d20adv 等），返回掷骰结果",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "expression": {
                                "type": "string",
                                "description": "骰子表达式，如 '1d20'（掷一个d20）、'2d6+3'（掷两个d6加3）、'1d20adv'（优势掷骰）"
                            }
                        },
                        "required": ["expression"]
                    }
                }
            }
        ]

    async def _execute_tool(
        self,
        tool_name: str,
        arguments: str,
        user_id: str,
        group_id: str,
    ) -> str:
        """执行工具调用"""
        try:
            args = json.loads(arguments)
        except json.JSONDecodeError:
            return "工具参数解析失败"

        if tool_name == "search_memory":
            query = args.get("query", "")
            search_type = args.get("type", "all")
            days = args.get("days", 7)
            limit = args.get("limit", 5)
            # 限制范围 1-20
            limit = max(1, min(20, limit))

            result = await self.data_store.search_memory(
                user_id=user_id,
                group_id=group_id,
                query=query,
                search_type=search_type,
                days=days,
                limit=limit,
            )
            return result

        # Phase 4: 掷骰工具
        if tool_name == "roll_dice":
            expression = args.get("expression", "")
            return await self._handle_roll_dice(expression)

        return f"未知工具: {tool_name}"

    async def _handle_roll_dice(self, expression: str) -> str:
        """处理掷骰工具调用

        Args:
            expression: 骰子表达式，如 "1d20", "2d6+3"

        Returns:
            掷骰结果文本
        """
        result = RollAdapter.roll(expression)
        if not result["success"]:
            error = result["error"]
            if "暂时不可用" in error:
                logger.exception(f"掷骰工具执行失败: {expression}")
            return error

        val = result["value"]
        info = result["info"]
        exp = result["exp"]

        if info and exp:
            return f"掷骰: {exp} = {info} = {val}"
        elif info:
            return f"掷骰: {expression} = {info} = {val}"
        else:
            return f"掷骰: {expression} = {val}"

    async def _update_interaction(self, user_id: str, group_id: str, user_msg: str, assistant_msg: str) -> None:
        if not self.data_store:
            return

        rel = await self.data_store.get_relationship(user_id, group_id)
        initial = float(self.character.extensions.initial_relationship) if self.character else 30.0
        if not rel:
            rel = await self.data_store.init_relationship(user_id, group_id, initial)

        now = persona_wall_now(self.config.timezone)
        decay_event: Optional[ScoreEvent] = None
        if self.decay_calculator and self.decay_calculator.should_apply_decay(rel, now):
            deltas, reason = self.decay_calculator.calculate_decay(rel, initial, now)
            if abs(deltas.intimacy) > 0.01:
                composite_before = rel.composite_score
                rel.apply_deltas(deltas, updated_at=now)
                decay_event = ScoreEvent(
                    user_id=user_id,
                    group_id=group_id,
                    deltas=deltas,
                    composite_before=composite_before,
                    composite_after=rel.composite_score,
                    reason=f"time_decay: {reason}",
                    conversation_digest="",
                )

        rel.last_interaction_at = now
        rel.last_relationship_decay_applied_at = now
        await self.data_store.update_relationship(rel)
        if decay_event:
            await self.data_store.add_score_event(decay_event)
            logger.info(
                f"应用时间衰减: {user_id} 衰减 {decay_event.deltas.intimacy:.2f}, 原因: {decay_event.reason}"
            )

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

        rel_for_scoring = rel
        if rel and self.decay_calculator and self.character:
            initial = float(self.character.extensions.initial_relationship)
            rel_for_scoring = self.decay_calculator.effective_relationship(rel, initial)

        deltas, new_facts = await self.scoring_agent.batch_analyze(
            messages=messages,
            current_profile=profile,
            relationship=rel_for_scoring,
        )

        now = persona_wall_now(self.config.timezone)
        if rel:
            composite_before = rel.composite_score
            rel.apply_deltas(deltas, updated_at=now)
            await self.data_store.update_relationship(rel)

            event = ScoreEvent(
                user_id=user_id,
                group_id=group_id,
                deltas=deltas,
                composite_before=composite_before,
                composite_after=rel.composite_score,
                reason="批量评分",
                conversation_digest=self._build_conversation_digest(messages),
            )
            await self.data_store.add_score_event(event)

        if new_facts and profile:
            profile.merge_facts(new_facts, updated_at=now)
            await self.data_store.save_user_profile(profile)
        elif new_facts:
            new_profile = UserProfile(user_id=user_id, facts=new_facts)
            await self.data_store.save_user_profile(new_profile)

    async def _fetch_short_term_history(
        self,
        user_id: str,
        group_id: str,
        limit: int = 15,
    ) -> List[Dict[str, str]]:
        """获取并格式化近期对话历史"""
        history = await self.data_store.get_recent_messages(user_id, group_id, limit=limit)
        return [{"role": msg.role, "content": msg.content} for msg in history]

    def _resolve_warmth_label(self, user_id: str, rel: Optional[RelationshipState]) -> str:
        """根据关系状态（含衰减计算）解析温暖度标签"""
        initial = float(self.character.extensions.initial_relationship)

        if rel:
            if self.decay_calculator:
                rel = self.decay_calculator.effective_relationship(rel, initial)
            _, warmth_label = rel.get_warmth_level(self.character.get_warmth_labels())
        else:
            temp_rel = RelationshipState(
                user_id=user_id, intimacy=initial, passion=initial,
                trust=initial, secureness=initial
            )
            _, warmth_label = temp_rel.get_warmth_level(self.character.get_warmth_labels())

        return warmth_label

    async def _build_diary_context(self) -> str:
        """构建日记/事件上下文：优先今日事件，fallback 昨日日记"""
        wall = persona_wall_now(self.config.timezone)
        today = wall.strftime("%Y-%m-%d")
        yesterday = (wall - timedelta(days=1)).strftime("%Y-%m-%d")
        max_diary_len = self.config.max_diary_context_chars

        events = await self.data_store.get_daily_events(today)
        if events:
            valid_events = [e for e in events if e.description and e.description.strip()]
            valid_events.sort(key=lambda e: e.created_at or PERSONA_EPOCH, reverse=True)

            if valid_events:
                descriptions = [e.description for e in valid_events]
                diary_context = "今天发生的事：" + "；".join(descriptions)
                if len(diary_context) > max_diary_len:
                    diary_context = diary_context[:max_diary_len].rsplit('；', 1)[0] + "..."
                return diary_context

        diary = await self.data_store.get_diary(yesterday)
        if diary:
            if len(diary) > max_diary_len:
                diary = diary[:max_diary_len] + "..."
            return "昨天的日记：" + diary

        return ""

    def _build_lore_sections(
        self,
        history_dicts: List[Dict[str, str]],
        current_message: str,
    ) -> Dict[str, List[str]]:
        """构建世界书 lore 段落"""
        return self.context_builder._build_lore_text(history_dicts, current_message)

    async def _build_messages(
        self,
        user_id: str,
        group_id: str,
        current_message: str,
    ) -> List[Dict[str, str]]:
        if not self.data_store or not self.character or not self.context_builder:
            return [{"role": "user", "content": current_message}]

        history_dicts = await self._fetch_short_term_history(user_id, group_id)
        profile = await self.data_store.get_user_profile(user_id)
        rel = await self.data_store.get_relationship(user_id, group_id)
        warmth_label = self._resolve_warmth_label(user_id, rel)
        diary_context = await self._build_diary_context()
        lore_sections = self._build_lore_sections(history_dicts, current_message)

        if self.context_builder:
            debug_info = self.context_builder.build_debug_info(
                short_term_history=history_dicts,
                user_profile=profile,
                diary_context=diary_context,
                warmth_label=warmth_label,
                lore_sections=lore_sections,
            )
            logger.debug(f"context_debug: {debug_info}")

        return self.context_builder.build(
            short_term_history=history_dicts,
            user_profile=profile,
            diary_context=diary_context,
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

    async def get_relationship_for_display(
        self, user_id: str, group_id: str
    ) -> Optional[RelationshipState]:
        """读取关系并应用惰性时间衰减（展示用，不写库）。"""
        if not self.data_store:
            return None
        rel = await self.data_store.get_relationship(user_id, group_id)
        if not rel or not self.decay_calculator or not self.character:
            return rel
        initial = float(self.character.extensions.initial_relationship)
        return self.decay_calculator.effective_relationship(rel, initial)

    async def apply_relationship_decay_batch(self) -> int:
        """每日批处理：将长时间未互动用户的时间衰减写入数据库。返回写库条数。"""
        if not self.decay_calculator or not self.data_store or not self.character:
            return 0
        initial = float(self.character.extensions.initial_relationship)
        n = 0
        now = persona_wall_now(self.config.timezone)
        try:
            for rel in await self.data_store.list_all_relationships_raw():
                if not self.decay_calculator.should_apply_decay(rel, now):
                    continue
                deltas, reason = self.decay_calculator.calculate_decay(rel, initial, now)
                rel.last_relationship_decay_applied_at = now
                if abs(deltas.intimacy) <= 0.01:
                    continue  # 无实际衰减，不写库
                composite_before = rel.composite_score
                rel.apply_deltas(deltas, updated_at=now)
                await self.data_store.update_relationship(rel)
                await self.data_store.add_score_event(
                    ScoreEvent(
                        user_id=rel.user_id,
                        group_id=rel.group_id,
                        deltas=deltas,
                        composite_before=composite_before,
                        composite_after=rel.composite_score,
                        reason=f"time_decay_batch: {reason}",
                        conversation_digest="",
                    )
                )
                n += 1
            if n:
                logger.info(f"每日衰减批处理: 更新 {n} 条关系")
        except Exception as e:
            logger.warning(f"每日衰减批处理失败: {e}")
        return n

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
            self.context_builder = self._create_context_builder(self.character)

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
            # 尝试生成生活事件（随机槽位）
            if self.character_life:
                event = await self.character_life.tick()
                if event:
                    logger.info(f"角色生活事件: {event.get('description', '')[:50]}...")
                    # 随机事件进入延迟队列
                    if self.delayed_task_queue and event.get("share_desire", 0.0) >= self.config.proactive_event_share_threshold:
                        import random
                        delay = random.randint(
                            self.config.proactive_event_share_delay_min,
                            self.config.proactive_event_share_delay_max,
                        )
                        await self.delayed_task_queue.enqueue_event_share(
                            event_id=event.get("event_id", ""),
                            event_description=event.get("description", ""),
                            share_desire=event.get("share_desire", 0.0),
                            delay_minutes=delay,
                        )

            # 运行主动消息调度器（定时事件 + 想念触发）
            if self.scheduler:
                proactive_msgs = await self.scheduler.tick()
                messages.extend(proactive_msgs)

            # 处理延迟队列中的随机事件分享
            if self.delayed_task_queue and self.scheduler:
                async def _on_share(description: str, share_desire: float) -> List[Dict]:
                    return await self.scheduler.share_event_to_targets(
                        description,
                        self.scheduler.config.max_shares_per_event,
                    )

                delayed_msgs = await self.delayed_task_queue.tick(on_share=_on_share)
                messages.extend(delayed_msgs)

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
        if not self._initialized:
            return None

        try:
            await self._prune_traces()

            if not self.character_life:
                return None

            await self.apply_relationship_decay_batch()
            diary = await self.character_life.generate_diary()
            if diary:
                logger.info(f"生成日记: {len(diary)} 字")

            return diary
        except Exception as e:
            logger.exception(f"日记生成失败: {e}")
            return None

    async def _prune_traces(self) -> None:
        """清理过期 LLM trace"""
        if not self.config.trace_enabled or not self.data_store:
            return
        try:
            deleted = await self.data_store.prune_llm_traces(self.config.trace_max_age_days)
            if deleted:
                logger.info(f"清理了 {deleted} 条过期 LLM trace")
        except Exception as e:
            logger.warning(f"清理 LLM trace 失败: {e}")

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
