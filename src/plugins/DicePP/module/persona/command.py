"""
Persona AI 命令入口

集成 persona 模块完成对话功能
提供 Persona 正常聊天与工具命令
"""
from typing import List, Tuple, Any, Optional
import json
import time
import asyncio
import uuid
from plugins.DicePP.utils.logger import logger, _request_id_var
from plugins.DicePP.utils.time import get_clock

from plugins.DicePP.core.bot import Bot
from plugins.DicePP.core.command.user_cmd import UserCommandBase, custom_user_command
from plugins.DicePP.core.command.bot_cmd import BotCommandBase
from plugins.DicePP.core.communication import MessageMetaData, PostSendEvent
from plugins.DicePP.core.command.const import DPP_COMMAND_PRIORITY_DEFAULT, DPP_COMMAND_FLAG_FUN


from .factory import PersonaApp, create_persona
from .chat.orchestrator import ChatOutcome
from .exceptions import PersonaInitError
from .llm.errors import QuotaExceeded
from .data.store import PersonaDataStore
from .data.models import MessageType
from .admin import AdminDispatcher
from .report.daily_report import DailyReportGenerator
from .gateway.port import MessagePort
from .gateway.pipeline import MessagePipeline, TruncateStage
from .image_cache import ImageCache
from .transcript import format_event_message, format_player_identity
from plugins.DicePP.core.command.cq_extractor import extract_segments


async def resolve_images(
    raw_msg: str, image_cache: ImageCache, *, force_download: bool = False,
    force_emoji: bool = False,
) -> Tuple[Optional[List[dict]], Optional[List[str]]]:
    """从 raw_msg 提取图片元信息，按需下载缓存，返回 (image_meta, data_urls)。

    force_download=True 时始终下载；否则仅在已有缓存时读取。
    force_emoji=True 时同时下载表情包（sub_type=1）。
    返回 (None, None) 表示无图片或提取失败。
    """
    if not raw_msg:
        return None, None
    segments = extract_segments(raw_msg)
    image_segs = [s for s in segments if s.seg_type == "image"]
    if not image_segs:
        return None, None
    image_meta = [
        {
            "url": s.data.get("url", ""),
            "file": s.data.get("file", ""),
            "sub_type": s.data.get("sub_type", ""),
            "size": 0,
            "cache_hash": None,
            "image_hash": ImageCache.compute_image_hash({
                "url": s.data.get("url", ""),
                "file": s.data.get("file", ""),
            }),
        }
        for s in image_segs[:PersonaCommand.MAX_IMAGES_PER_MESSAGE]
    ]
    if force_download:
        await image_cache.download_and_cache(image_meta, force_emoji=force_emoji)
    urls = [
        image_cache.read_cache(e["cache_hash"])
        for e in image_meta
        if e.get("cache_hash")
    ]
    cached_count = sum(1 for e in image_meta if e.get("cache_hash"))
    logger.info(
        f"[Persona] resolve_images: total={len(image_meta)}"
        f" cached={cached_count} download={'forced' if force_download else 'lazy'}"
    )
    return image_meta, urls or None


@custom_user_command("PersonaAI", priority=DPP_COMMAND_PRIORITY_DEFAULT, flag=DPP_COMMAND_FLAG_FUN)
class PersonaCommand(UserCommandBase):
    """Persona AI 命令处理器"""

    message_type: MessageType = MessageType.CHAT
    MAX_IMAGES_PER_MESSAGE = 5

    def __init__(self, bot: Bot):
        super().__init__(bot)
        self.enabled: bool = False
        self.app: Optional[PersonaApp] = None
        self.data_store: Optional[PersonaDataStore] = None
        self.init_error: Optional[str] = None  # create_persona 抛出的具名异常文本，供 _admin_debug 展示
        # 主循环在 async 中调用同步 tick() 时，单槽异步任务（避免 life.tick 慢于 1s 时堆积）
        self._async_tick_task: Optional[asyncio.Task] = None
        self._async_tick_daily_task: Optional[asyncio.Task] = None
        self._async_sa_daily_task: Optional[asyncio.Task] = None
        self._shutting_down = False
        # 管理员子命令分发器（PersonaApp 成功初始化后注册）
        self.admin_dispatcher: Optional[AdminDispatcher] = None
        # 日报生成器（生命周期独立于 PersonaApp）
        self.report_generator: Optional[DailyReportGenerator] = None
        # 图片缓存
        self.image_cache: ImageCache = ImageCache()

    def _require_app(self) -> Optional[str]:
        """检查 app 和 data_store 是否已初始化，返回错误信息或 None"""
        if self.app is None or self.data_store is None:
            return "Persona 模块未初始化"
        return None

    def _register_admin_handlers(self) -> None:
        """注册管理员子命令处理器（在 delay_init 后调用）"""
        self.admin_dispatcher = AdminDispatcher(
            bot=self.bot,
            app=self.app,
            data_store=self.data_store,
            init_error=self.init_error,
            report_generator=self.report_generator,
        )

    def delay_init(self) -> List[str]:
        """延迟初始化"""
        self.config = self.bot.config.persona_ai
        config = self.config
        self.enabled = config.enabled

        if not self.enabled:
            return ["Persona AI 模块已禁用"]

        # 创建日报生成器（独立于 PersonaApp，app 初始为 None）
        pipeline = MessagePipeline()
        pipeline.add(TruncateStage(2000))
        report_port = MessagePort(self.bot, pipeline=pipeline)
        self.report_generator = DailyReportGenerator(
            bot=self.bot,
            port=report_port,
            config=config,
        )

        # 注册异步初始化任务
        async def init_persona():
            try:
                self.app = await create_persona(self.bot)
            except PersonaInitError as e:
                self.init_error = f"{type(e).__name__}: {e}"
                logger.error(f"[Persona] 模块初始化失败: {self.init_error}")
                self.app = None
                self.data_store = None
                self.enabled = False
                return []
            if self.app:
                self.data_store = self.app.store
                # 注入 ImageCache 到 data_store（供裁剪时清理图片缓存）
                self.data_store.image_cache = self.image_cache
                # 注入 app 引用到日报生成器
                if self.report_generator:
                    self.report_generator.set_app(self.app)
                self._register_admin_handlers()
                # 注册消息发送后跨模块通知 hook（先注销旧 hook 再注册，防止热重载后重复）
                if hasattr(self, "_post_send_hook_unregister"):
                    self._post_send_hook_unregister()
                self._post_send_hook_unregister = self.bot.add_post_send_hook(self._group_chat_recorder)
                # 注册入站消息记录 hook
                if hasattr(self, "_inbound_hook_unregister"):
                    self._inbound_hook_unregister()
                self._inbound_hook_unregister = self.bot.add_inbound_message_hook(self._inbound_message_recorder)
                logger.info(
                    f"[Persona] 模块初始化成功: "
                    f"{self.bot.config.persona_ai.character_name}"
                )
            else:
                # create_persona 仅在 enabled=False 时返回 None。
                logger.info("[Persona] 模块未启用（enabled=False）")
                self.enabled = False
            return []

        self.bot.scheduler.schedule(init_persona, is_async=True, timeout=30)

        return [
            "Persona AI 模块加载中 "
            f"(角色: {self.bot.config.persona_ai.character_name or '未指定'})"
        ]

    @staticmethod
    def _is_persona_trigger(meta: MessageMetaData, msg: str) -> bool:
        """判断消息是否为 persona 触发（@bot 或 .ai/。ai 前缀，或私聊）"""
        # 私聊自动触发
        if not meta.group_id:
            return True
        return meta.to_me or msg.strip().startswith(".ai") or msg.strip().startswith("。ai")

    @staticmethod
    def _resolve_display_name(meta: MessageMetaData) -> str:
        """统一解析用户显示名称（优先级：群名片 > 昵称 > user_id）"""
        return meta.sender.card or meta.sender.nickname or meta.nickname or meta.user_id

    async def _resolve_persona_nickname(
        self,
        user_id: str,
        group_id: str,
        fallback: str = "",
    ) -> str:
        """按 DicePP 正式 fallback 规则解析 Persona 对玩家的称呼。"""
        try:
            nickname = await self.bot.get_nickname(user_id, group_id)
            if nickname:
                return nickname
        except Exception:
            logger.opt(exception=True).warning(
                f"[Persona] 解析玩家称呼失败，使用消息显示名: "
                f"user={user_id} group={group_id}"
            )
        return fallback or user_id

    async def _group_chat_recorder(
        self,
        event: PostSendEvent,
    ) -> None:
        """消息发送后记录器回调（跨模块 bot 回复统一入流）

        - history_stream_id 非 None：persona 聊天路径已自行写入，无需处理
        - history_managed_by_sender：发送方会在投递成功后自行写入，无需处理
        - 其他事件：直接写入 message_stream（非 persona 命令回复路径）
        """
        if not self.data_store:
            return
        try:
            if (
                event.history_stream_id is not None
                or event.history_managed_by_sender
            ):
                return  # persona 聊天路径已在 ChatSession 等调用方写入
            msg_type = MessageType.from_str(event.message_type)
            await self.data_store.add_message_stream(
                user_id=event.user_id or "",
                group_id=event.group_id or "",
                role=event.role,
                type=msg_type,
                content=event.content,
                display_name=event.display_name,
            )
        except Exception as e:
            logger.warning(f"[Persona] 出站记录器写入失败: {e}")

    def _chat_registry(self):
        """返回 orchestrator 与 hook 共享的 ConversationRegistry（未就绪时 None）。"""
        app = self.app
        chat = getattr(app, "chat", None) if app is not None else None
        return getattr(chat, "registry", None) if chat is not None else None

    async def _inbound_message_recorder(
        self,
        user_id: str,
        group_id: str,
        role: str,
        type: str,
        content: str,
        display_name: str,
        raw_msg: str = "",
    ) -> Optional[int]:
        """入站消息记录器回调（每条用户消息记录一次）。

        先记录 message_stream 权威事实，再把该消息以 ref 追加进正确 scope
        的 Conversation（群聊旁观消息也进入活跃期，但不触发 LLM）。
        """
        if not self.data_store:
            return None
        try:
            msg_type = MessageType.from_str(type)
            known_types = {m.value for m in MessageType}
            if type not in known_types:
                logger.warning(f"[Persona] 未知 message_type='{type}'，from_str 回退为 '{msg_type.value}'")

            # 检测图片（私聊立即下载缓存）
            is_private = not group_id
            image_meta, _ = await resolve_images(
                raw_msg, self.image_cache, force_download=is_private,
            )

            resolved_name = display_name
            if role == "user":
                resolved_name = await self._resolve_persona_nickname(
                    user_id, group_id, fallback=display_name,
                )

            msg_id = await self.data_store.add_message_stream(
                user_id=user_id,
                group_id=group_id,
                role=role,
                type=msg_type,
                content=content,
                display_name=resolved_name,
                image_meta=image_meta,
            )

            # 把用户可见消息以 ref 追加进该 scope 的 Conversation。
            # get_or_create 会开启/延续该 scope 活跃期；旁观消息也进入，但不触发 LLM。
            registry = self._chat_registry()
            if registry is not None and msg_id:
                from .life.conversation_scope import ConversationScope
                scope = ConversationScope.from_chat(user_id, group_id)
                await registry.append_visible(scope, msg_id, role or "user")
                return msg_id
        except Exception as e:
            logger.warning(f"[Persona] 入站记录器写入失败: {e}", exc_info=True)
        return None

    def _is_admin(self, user_id: str) -> bool:
        """检查用户是否是管理员"""
        # 使用 DicePP 的 is_admin 检查
        return bool(self.bot.config.master) and user_id == self.bot.config.master

    async def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        """判断是否处理消息"""
        if not self.enabled:
            # 即使未启用，也响应 .ai status
            if msg_str.strip() == ".ai" or msg_str.strip().startswith(".ai "):
                return True, False, "status"
            return False, False, None

        msg = msg_str.strip()

        # 过滤掉单独的 "." 或 "。"（可能是输错了指令）
        if msg in [".", "。", "..", "。。", ". ", "。 "]:
            return False, False, None

        # .jrrp 拦截：persona 接管运势回复（需绕过下方 . 前缀守卫）
        if msg in (".jrrp", "。jrrp"):
            if not self.app:
                logger.info("[Persona] .jrrp 未拦截：模块未初始化，回退到 JrrpCommand")
                return False, False, None
            logger.info("[Persona] .jrrp 已拦截，路由到 _handle_jrrp")
            return True, False, "jrrp"

        # 如果以 "." 或 "。" 开头但不是有效的 AI 命令，不处理
        # 有效的 "." 前缀命令: .ai
        if msg.startswith(".") and not msg.startswith(".ai"):
            return False, False, None
        if msg.startswith("。") and not msg.startswith("。ai"):
            return False, False, None

        # @bot 或 .ai 前缀触发
        if not self._is_persona_trigger(meta, msg):
            return False, False, None

        # 提取命令内容
        if msg.startswith(".ai") or msg.startswith("。ai"):
            content = msg[3:].strip()
        else:
            content = msg

        # 解析命令
        parts = content.split()
        cmd = parts[0] if parts else ""

        # .ai admin 命令：仅管理员
        if cmd == "admin":
            if self._is_admin(meta.user_id):
                return True, False, "admin"
            else:
                # 非管理员尝试执行 admin 命令，静默忽略
                return False, False, None

        # 不调用 LLM 的工具类命令
        if cmd in ("clear", "status"):
            return True, False, None
        # 聊天触发（@bot）
        if meta.to_me and not msg.startswith(".ai"):
            return True, False, None
        return True, False, None

    async def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        """处理消息"""
        inbound_message_stream_id = getattr(meta, "inbound_message_stream_id", None)
        user_id = meta.user_id
        group_id = meta.group_id or ""
        fallback_name = self._resolve_display_name(meta)
        is_private = not meta.group_id

        # 设置本次请求的 trace_id 上下文；finally 块保证异常路径也能 reset
        _trace_token = _request_id_var.set(uuid.uuid4().hex[:8])
        try:
            with logger.contextualize(request_id=_request_id_var.get()):
                # 提取命令内容
                msg = msg_str.strip()
                if msg.startswith(".ai") or msg.startswith("。ai"):
                    content = msg[3:].strip()
                else:
                    content = msg

                # 解析命令
                parts = content.split()
                cmd = parts[0] if parts else ""
                args = parts[1:] if len(parts) > 1 else []

                # 处理 admin 命令
                if cmd == "admin" or hint == "admin":
                    response = await self._handle_admin(user_id, group_id, args)
                    if response:
                        await self._send(user_id, group_id, response)
                    return []

                # .jrrp 路由
                if hint == "jrrp":
                    return await self._handle_jrrp(
                        user_id, group_id, meta,
                        inbound_message_stream_id=inbound_message_stream_id,
                    )

                # 特殊命令处理
                is_at_trigger = meta.to_me and not msg_str.strip().startswith(".ai")

                response = None

                if content == "status" or hint == "status":
                    response = await self._get_status(user_id, group_id, is_private)
                elif is_at_trigger:
                    if self.app and self.enabled:
                        try:
                            nickname = await self._resolve_persona_nickname(
                                user_id, group_id, fallback=fallback_name,
                            )
                            # 检测当前消息中的图片（始终下载，包括表情包）
                            image_meta, image_data_urls = await resolve_images(
                                meta.raw_msg, self.image_cache, force_download=True,
                                force_emoji=True,
                            )
                            if image_data_urls:
                                logger.info(
                                    f"[Persona] CHAT_WITH_IMAGE trigger: source=current_message"
                                    f" image_count={len(image_data_urls)} user={user_id}"
                                )

                            if not content and not image_data_urls:
                                if image_meta:
                                    # 全部图片下载失败（已尝试下载包括表情在内的所有图片）
                                    response = await self.app.chat_with_user(
                                        user_id=user_id,
                                        group_id=group_id,
                                        message="[图片下载失败，请重试]",
                                        nickname=nickname,
                                        image_data_urls=None,
                                        inbound_message_stream_id=inbound_message_stream_id,
                                    )
                                else:
                                    # 真的没发任何内容（纯 @bot 无附带）
                                    response = await self._get_status(user_id, group_id, is_private)
                            else:
                                logger.debug(
                                    f"[Persona] chat enter: user={user_id} group={group_id}"
                                    f" content_len={len(content) if content else 0}"
                                    f" image_count={len(image_data_urls) if image_data_urls else 0}"
                                )
                                response = await self.app.chat_with_user(
                                    user_id=user_id,
                                    group_id=group_id,
                                    message=content,
                                    nickname=nickname,
                                    image_data_urls=image_data_urls,
                                    inbound_message_stream_id=inbound_message_stream_id,
                                )
                                logger.info(
                                    f"[Persona] chat return: user={user_id}"
                                    f" response_type={type(response).__name__}"
                                    f" status={getattr(response, 'status', '')}"
                                    f" sent_count={getattr(response, 'sent_count', '')}"
                                )
                        except QuotaExceeded as e:
                            logger.warning(f"[Persona] 配额超限: user={user_id}, group={group_id}")
                            response = str(e)
                    else:
                        response = "Persona AI 模块未启用或未初始化"
                else:
                    response = self._get_introduction()

                # ChatOutcome 表示 chat 层已完成用户可见输出；command 不再二次 _send。
                if isinstance(response, ChatOutcome):
                    if response.skipped:
                        return []
                    return []

                # 发送非 chat outcome 回复
                if response is None:
                    return []
                if response:
                    await self._send(user_id, group_id, response)
                return []
        finally:
            _request_id_var.reset(_trace_token)


    async def _handle_jrrp(
        self,
        user_id: str,
        group_id: str,
        meta: MessageMetaData,
        *,
        inbound_message_stream_id: Optional[int] = None,
    ) -> List[BotCommandBase]:
        """处理 .jrrp 命令：调 compute_jrrp → 事件消息注入 LLM 生成角色评语"""
        from plugins.DicePP.module.misc.jrrp_utils import compute_jrrp, format_jrrp_text
        from plugins.DicePP.utils.time import get_current_date_raw

        # 1. 计算运势
        result = compute_jrrp(user_id, get_current_date_raw())

        # 2. 与普通 Persona 消息共用 DicePP 正式称呼解析语义。
        user_name = await self._resolve_persona_nickname(
            user_id, group_id, fallback=self._resolve_display_name(meta),
        )

        # 3. 构建注入 LLM 的事件消息
        #    通过 transient_message 旁路直接注入 LLM 上下文，不写入 message_stream，
        #    避免在 session 中累积形成 few-shot 污染链（详见 P0 修复方案）。
        if result.direction == 'up':
            change_text = f"上涨 {result.delta_percent}%"
        elif result.direction == 'down':
            change_text = f"下跌 {result.delta_percent}%"
        else:
            change_text = "与昨日相同"

        event_content = (
            f"{format_player_identity(user_id, user_name)} 查询了今日运势\n"
            f"今日: {result.jrrp}/100 | 昨日: {result.zrrp}/100 | 变化: {change_text}\n"
            f"\n"
            f"请以角色身份就此说一两句话，自然地提及运势数值。"
        )
        event_msg = format_event_message(event_content, get_clock().now())

        # 4. 调 LLM 生成角色评语
        #    transient_message=event_msg：事件仅注入当前 LLM 上下文，不持久化。
        #    message=".jrrp"：仅用于去重/缓冲，不进入 LLM 上下文。
        #    参考 SillyTavern 的 injected 标记方案——若未来多源注入可考虑消息级标记字段。
        #    角色评语由 chat_command() 通过 delivery 发送并按 CHAT 入库；
        #    empty/failed 时回退到模板，partial_sent 不再补额外 fallback。
        from .chat.chat_shared import ChatCallContext
        ctx = ChatCallContext(
            is_command=True,
            transient_message=event_msg,
            nickname=user_name,
            inbound_message_stream_id=inbound_message_stream_id,
        )
        try:
            outcome = await self.app.chat.chat_command(
                user_id=user_id,
                group_id=group_id,
                message=".jrrp",
                ctx=ctx,
            )
            if outcome.status in {"empty", "failed"}:
                # LLM 返回空串时，回退到模板确保用户可见
                fallback = format_jrrp_text(user_name, result.jrrp, result.zrrp,
                                            result.delta_percent, result.direction)
                # fallback 发送后追加为 assistant ref 到 Conversation，
                # 与 persona 聊天路径一致（确保上下文可检索）
                await self._send_and_record(user_id, group_id, fallback)
        except Exception as e:
            # LLM 调用失败时，回退到模板
            logger.warning(f"[Persona] _handle_jrrp LLM 调用失败，回退到模板: {e}")
            fallback = format_jrrp_text(user_name, result.jrrp, result.zrrp,
                                        result.delta_percent, result.direction)
            await self._send_and_record(user_id, group_id, fallback)

        return []

    async def _send(
        self,
        user_id: str,
        group_id: str,
        content: str,
        msg_id: Optional[int] = None,
        message_type: MessageType = MessageType.CHAT,
    ) -> None:
        """通过 MessagePort 发送单条消息"""
        if self.app:
            logger.debug(
                f"[Persona] _send call: user={user_id} group={group_id}"
                f" content_len={len(content)} msg_id={msg_id}"
            )
            await self.app.send_message(
                user_id, group_id, content,
                msg_id=msg_id,
                message_type=message_type,
            )
        else:
            logger.error(
                f"[Persona] MessagePort 未初始化，丢弃消息: "
                f"user={user_id}, group={group_id}, content={content[:30]}..."
            )

    async def _send_and_record(
        self, user_id: str, group_id: str, content: str,
    ) -> None:
        """发送消息并追加为 assistant ref 到当前 Conversation（R11）。

        顺序：先发送，成功后再写 message_stream + 追加 ref。
        发送失败时不落任何记录，避免 stream 残留。
        """
        if not self.app or not self.data_store:
            await self._send(user_id, group_id, content)
            return
        try:
            # R11(1): 先发送（发送方自行维护 stream，其他发送后订阅者仍会收到事件）
            if not await self.app.port.send(
                user_id, group_id, content,
                skip_history_record=True,
            ):
                logger.warning(
                    f"[Persona] _send_and_record 发送失败: "
                    f"user={user_id} group={group_id}"
                )
                return

            # R11(2): 发送成功：写入 message_stream（获取 stream_id）
            msg_type = MessageType.CHAT
            display_name = self.app.get_character().name if self.app.get_character() else "bot"
            msg_id = await self.data_store.add_message_stream(
                user_id=user_id,
                group_id=group_id,
                role="assistant",
                type=msg_type,
                content=content,
                display_name=display_name,
            )

            # R11(3): 追加 ref 到 Conversation
            from plugins.DicePP.module.persona.life.conversation_scope import ConversationScope
            scope = ConversationScope.from_chat(user_id, group_id)
            chat_registry = self.app.chat.registry if self.app.chat else None
            if chat_registry:
                await chat_registry.append_visible(scope, msg_id, "assistant")
        except Exception:
            logger.warning(
                f"[Persona] _send_and_record 失败: "
                f"user={user_id} group={group_id}", exc_info=True,
            )

    async def _handle_admin(self, user_id: str, group_id: str, args: List[str]) -> str:
        """处理 admin 命令（管理员功能）——分发给 AdminDispatcher"""
        if not self.admin_dispatcher:
            return "模块未初始化"
        daily_p = (
            self._async_tick_daily_task is not None and not self._async_tick_daily_task.done()
        )
        return await self.admin_dispatcher.dispatch(
            user_id, group_id, args,
            daily_pending=daily_p,
        )

    def _get_introduction(self) -> str:
        if not self.app or not self.app.get_character():
            char_name = self.bot.config.persona_ai.character_name or "未知"
            return f"你好，我是 {char_name}。（@ 我来聊天，.ai status 查看状态）"
        char = self.app.get_character()
        parts = [f"你好，我是 {char.name}。"]
        if char.description:
            parts.append(char.description)
        parts.append("（@ 我来聊天，.ai status 查看状态）")
        return "\n".join(parts)

    async def _get_status(self, user_id: str, group_id: str, is_private: bool) -> str:
        """获取状态信息"""
        if not self.enabled:
            return "Persona AI 状态: 未启用\n在配置中设置 persona_ai.enabled = true 来启用"

        if not self.app:
            return "Persona AI 状态: 初始化中..."

        char = self.app.get_character()

        model_name = self.bot.user_config.deepseek_model

        if not char:
            return (
                f"Persona AI 状态: 初始化中...\n"
                f"角色: {self.bot.config.persona_ai.character_name or '未指定'}\n"
                f"模型: DeepSeek/{model_name}"
            )

        base = (
            f"Persona AI 状态: 已启用\n"
            f"角色: {char.name}\n"
            f"模型: DeepSeek/{model_name}\n"
            f"\n使用方法: @bot <消息>\n"
            f".ai status - 查看状态"
        )

        return base

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        if keyword in ["ai", "persona", "AI", "人格"]:
            lines = [
                ".ai - 自我介绍",
                "@bot <消息> - 与 AI 对话",
                ".ai status - 查看状态",
            ]
            if self._is_admin(meta.user_id):
                lines.append("")
                lines.append("[管理员调试]")
                lines.append(".ai admin debug - 运行诊断（错误摘要）")
                lines.append(".ai admin reload - 热重载角色卡")
                lines.append(".ai admin events - 事件配置")
                lines.append(".ai admin diary [日期] - 查看日记（缺省今天，-1=昨天，或日期如2026-05-30）")
            return "\n".join(lines)
        return ""

    async def _handle_debug(self, user_id: str, group_id: str, msg: str) -> str:
        """.pa 命令已废弃，请使用 .ai admin 子命令"""
        return ".pa 命令已废弃，请使用 .ai admin 子命令"

    async def shutdown(self) -> None:
        """Bot 关闭时清理资源"""
        self._shutting_down = True
        for task_attr in ("_async_tick_daily_task", "_async_sa_daily_task"):
            task = getattr(self, task_attr)
            if task is None:
                continue
            if not task.done():
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception(f"[Persona] shutdown 等待 {task_attr} 失败")
            finally:
                if getattr(self, task_attr) is task:
                    setattr(self, task_attr, None)
        if self.app and self.app.store:
            await self.app.store.close()

    def get_description(self) -> str:
        """获取命令描述"""
        return "Persona AI 对话" if self.enabled else "Persona AI 对话（已禁用）"

    def tick(self) -> List[BotCommandBase]:
        """
        每秒调用，驱动角色生活事件。

        主循环在运行中的事件循环里同步调用本方法：通过 create_task 执行异步 tick，
        并在后续每秒收齐已完成任务的结果，避免消息丢失。

        语义为 **at-most-once / 单槽**：同一时刻最多一个未完成的异步 tick；更强投递保证需另行设计（如发件箱）。
        """
        if not self.enabled or not self.app:
            return []

        try:
            loop = asyncio.get_running_loop()

            async def _run_tick() -> None:
                t0 = time.monotonic()
                await self.app.tick()
                elapsed = time.monotonic() - t0
                if elapsed > 10:
                    logger.warning(f"[Persona] tick 耗时 {elapsed:.1f}s (>10s)，可能阻塞后续事件槽位")

            # 清理已完成的任务（消费结果，不返回命令）
            t = self._async_tick_task
            if t is not None and t.done():
                try:
                    if not t.cancelled():
                        t.result()
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.error(f"[Persona] tick 异步任务失败: {e}")
                finally:
                    self._async_tick_task = None

            if loop.is_running():
                if self._async_tick_task is None or self._async_tick_task.done():
                    self._async_tick_task = asyncio.create_task(_run_tick())
                return []

            loop.run_until_complete(self.app.tick())
            return []
        except Exception as e:
            logger.error(f"[Persona] tick 失败: {e}")
            return []

    def _schedule_daily_planning(self, diary: str, diary_date: str) -> None:
        """在独立单槽任务中运行 SA 日终规划。"""
        if self._shutting_down:
            return
        task = self._async_sa_daily_task
        if task is not None and not task.done():
            logger.warning("[Persona] SA 日终规划仍在运行，跳过重复调度")
            return

        async def _run_sa_daily() -> None:
            try:
                await self.app.run_daily_planning(diary, diary_date)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[Persona] SA 日终规划异步任务失败")
            finally:
                current = asyncio.current_task()
                if self._async_sa_daily_task is current:
                    self._async_sa_daily_task = None

        self._async_sa_daily_task = asyncio.create_task(_run_sa_daily())

    async def _run_daily(self) -> None:
        """生成日记与日报，全部投递结束后再调度独立 SA 规划。"""
        result = await self.app.tick_daily()
        diary = result.diary
        if diary:
            logger.info(f"[Persona] 生成日记: {len(diary)} 字")
        if self.report_generator and self.config.daily_report_enabled:
            await self.report_generator.generate_and_send(diary)

        if not diary:
            return
        diary_date = result.diary_date
        if not diary_date:
            logger.warning("[Persona] 日记日期缺失，跳过 SA 日终规划")
            return
        self._schedule_daily_planning(diary, diary_date)

    def tick_daily(self) -> List[BotCommandBase]:
        """每天调用，生成日记（异步逻辑通过任务队列在运行中的事件循环里执行）。"""
        if self._shutting_down or not self.enabled or not self.app:
            return []

        try:
            loop = asyncio.get_running_loop()

            # 清理已完成的任务
            dt = self._async_tick_daily_task
            if dt is not None and dt.done():
                try:
                    if not dt.cancelled():
                        dt.result()
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.error(f"[Persona] tick_daily 异步任务失败: {e}")
                finally:
                    self._async_tick_daily_task = None

            if loop.is_running():
                if self._async_tick_daily_task is None or self._async_tick_daily_task.done():
                    self._async_tick_daily_task = asyncio.create_task(self._run_daily())
                return []

            # unreachable: get_running_loop() 成功时 loop.is_running() 必为 True
            return []
        except Exception as e:
            logger.error(f"[Persona] tick_daily 失败: {e}")
            return []
