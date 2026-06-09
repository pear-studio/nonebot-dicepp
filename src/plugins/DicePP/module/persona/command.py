"""
Persona AI 命令入口

集成 persona 模块完成对话功能
支持白名单访问控制
"""
from typing import List, Dict, Tuple, Any, Optional, Callable
import json
import time
import asyncio
import uuid
from utils.logger import logger, _request_id_var

from datetime import timedelta

from core.bot import Bot
from core.command.user_cmd import UserCommandBase, custom_user_command
from core.command.bot_cmd import BotCommandBase
from core.communication import MessageMetaData
from core.command.const import DPP_COMMAND_PRIORITY_DEFAULT, DPP_COMMAND_FLAG_FUN


from .factory import PersonaApp, create_persona
from .exceptions import PersonaInitError
from .llm.router import QuotaExceeded
from .data.store import PersonaDataStore
from .data.models import MessageType
from .admin import AdminDispatcher
from .report.daily_report import DailyReportGenerator
from .gateway.port import MessagePort
from .gateway.pipeline import MessagePipeline, TruncateStage
from .image_cache import ImageCache
from core.command.cq_extractor import extract_segments


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
    _format_relationship_base = staticmethod(AdminDispatcher._format_relationship_base)

    MAX_IMAGES_PER_MESSAGE = 5

    def __init__(self, bot: Bot):
        super().__init__(bot)
        self.enabled: bool = False
        self.app: Optional[PersonaApp] = None
        self.data_store: Optional[PersonaDataStore] = None
        self._session_manager: Optional[Any] = None  # 由 factory 注入，用于群聊旁路
        self.init_error: Optional[str] = None  # create_persona 抛出的具名异常文本，供 _admin_debug 展示
        self._whitelist_confirm_pending: Dict[str, float] = {}  # user_id -> timestamp
        # 主循环在 async 中调用同步 tick() 时，单槽异步任务（避免 life.tick 慢于 1s 时堆积）
        self._async_tick_task: Optional[asyncio.Task] = None
        self._async_tick_daily_task: Optional[asyncio.Task] = None
        # 管理员子命令分发器（在 delay_init 后由外部补齐）
        self._admin_handlers: Dict[str, Callable] = {}
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
                # 注入 SessionManager（供群聊 session 旁路写入）
                self._session_manager = self.app.session_manager
                # 注入 ImageCache 到 data_store（供裁剪时清理图片缓存）
                self.data_store.image_cache = self.image_cache
                # 注入 app 引用到日报生成器
                if self.report_generator:
                    self.report_generator.set_app(self.app)
                # 注册消息发送后跨模块通知 hook（先注销旧 hook 再注册，防止热重载后重复）
                if hasattr(self, "_post_send_hook_unregister"):
                    self._post_send_hook_unregister()
                self._post_send_hook_unregister = self.bot.add_post_send_hook(self._group_chat_recorder)
                # 注册入站消息记录 hook
                if hasattr(self, "_inbound_hook_unregister"):
                    self._inbound_hook_unregister()
                self._inbound_hook_unregister = self.bot.add_inbound_message_hook(self._inbound_message_recorder)
                logger.info(f"[Persona] 模块初始化成功: {config.character_name}")
            else:
                # config.enabled=False 走这里：禁用而非失败
                logger.info("[Persona] 模块未启用")
                self.enabled = False
            return []

        self._register_admin_handlers()
        self.bot.scheduler.schedule(init_persona, is_async=True, timeout=30)

        return [f"Persona AI 模块加载中 (角色: {config.character_name})"]

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

    async def _group_chat_recorder(
        self,
        group_id: str,
        user_id: str,
        role: str,
        type: str,
        content: str,
        display_name: str,
        msg_id: Optional[int] = None,
    ) -> None:
        """消息发送后记录器回调（跨模块 bot 回复统一入流）

        - msg_id 非 None：persona 聊天路径已自行写入，无需处理
        - msg_id 为 None：直接写入 message_stream（非 persona 命令回复路径）
        """
        if not self.data_store:
            return
        try:
            if msg_id is not None:
                return  # persona 聊天路径已在 ChatSession 等调用方写入
            msg_type = MessageType.from_str(type)
            await self.data_store.add_message_stream(
                user_id=user_id,
                group_id=group_id,
                role=role,
                type=msg_type,
                content=content,
                display_name=display_name,
            )
        except Exception as e:
            logger.warning(f"[Persona] 出站记录器写入失败: {e}")

    async def _inbound_message_recorder(
        self,
        user_id: str,
        group_id: str,
        role: str,
        type: str,
        content: str,
        display_name: str,
        raw_msg: str = "",
    ) -> None:
        """入站消息记录器回调（每条用户消息记录一次）"""
        if not self.data_store:
            return
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

            await self.data_store.add_message_stream(
                user_id=user_id,
                group_id=group_id,
                role=role,
                type=msg_type,
                content=content,
                display_name=display_name,
                image_meta=image_meta,
            )

            # 群聊 session 旁路：非触发消息也追加到活跃 session（仅存在活跃 session 时）
            if group_id and self._session_manager:
                try:
                    active = await self.data_store.get_active_session(group_id)
                    if active:
                        from .chat.session import _format_group_message
                        ts_prefix = ""
                        try:
                            from utils.time import wall_now as _wn
                            ts_prefix = _wn(self.config.timezone).strftime("%H:%M")
                        except Exception:
                            pass
                        msg_dict = {"speaker_name": display_name, "display_name": display_name}
                        formatted = _format_group_message(msg_dict, ts_prefix, "", content)
                        await self._session_manager.add_bypass_message(active.session_id, formatted)
                    else:
                        logger.debug(f"[Persona] 群聊 session 旁路：无活跃 session (group_id={group_id})，跳过写入")
                except Exception as e:
                    logger.warning(f"[Persona] 群聊 session 旁路写入失败: {e}")
        except Exception as e:
            logger.warning(f"[Persona] 入站记录器写入失败: {e}")

    def _is_admin(self, user_id: str) -> bool:
        """检查用户是否是管理员"""
        # 使用 DicePP 的 is_admin 检查
        return user_id in self.bot.config.admin or user_id in self.bot.config.master

    async def _check_whitelist(self, user_id: str, group_id: str, is_private: bool) -> bool:
        """
        检查用户/群是否在白名单中

        Returns:
            True = 允许访问，False = 拒绝访问
        """
        config = self.bot.config.persona_ai

        # 白名单功能未启用，允许所有人
        if not config.whitelist_enabled:
            return True

        if not self.data_store:
            return False

        # 检查是否设置了口令
        code = await self.data_store.get_global_setting("code")
        if not code:
            # 未设置口令，白名单不激活，允许所有人
            return True

        # 私聊：检查用户白名单
        if is_private:
            return await self.data_store.is_user_whitelisted(user_id)

        # 群聊：检查群白名单
        if group_id:
            return await self.data_store.is_group_whitelisted(group_id)

        return False

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
            # persona 未初始化或角色睡眠 → 回退到 JrrpCommand
            if not self.app or not await self.app.is_awake():
                return False, False, None
            # 白名单
            is_private = not meta.group_id
            whitelisted = await self._check_whitelist(meta.user_id, meta.group_id or "", is_private)
            if not whitelisted:
                return False, False, None
            # 配置开关
            if not getattr(self.config, 'jrrp_persona_enabled', True):
                return False, False, None
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

        # .ai join 命令：任何人可在私聊执行
        if cmd == "join":
            if not meta.group_id:  # 仅私聊
                return True, False, "join"
            else:
                # 群聊中提示私聊
                return True, False, "join_group_hint"

        # .ai admin 命令：仅管理员
        if cmd == "admin":
            if self._is_admin(meta.user_id):
                return True, False, "admin"
            else:
                # 非管理员尝试执行 admin 命令，静默忽略
                return False, False, None

        # 不调用 LLM 的工具类命令：无需白名单
        if cmd in ("clear", "status", "profile", "mute"):
            return True, False, None
        # 聊天触发（@bot）：无需 .ai 前缀，也无需白名单以外的命令
        if meta.to_me and not msg.startswith(".ai"):
            is_private = not meta.group_id
            whitelisted = await self._check_whitelist(meta.user_id, meta.group_id or "", is_private)
            if not whitelisted:
                return False, False, None
            return True, False, None

        # 其余 .ai 命令（含未知子命令 → 自我介绍）：检查白名单
        is_private = not meta.group_id
        whitelisted = await self._check_whitelist(meta.user_id, meta.group_id or "", is_private)

        if not whitelisted:
            # 不在白名单，静默忽略（不干扰 TRPG 流程）
            return False, False, None

        return True, False, None

    async def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        """处理消息"""
        user_id = meta.user_id
        group_id = meta.group_id or ""
        nickname = meta.nickname or ""
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

                # 特殊提示：群聊中发送 join
                if hint == "join_group_hint":
                    response = "请私聊发送此命令"
                    await self._send(user_id, group_id, response)
                    return []

                # 解析命令
                parts = content.split()
                cmd = parts[0] if parts else ""
                args = parts[1:] if len(parts) > 1 else []

                # 处理 join 命令
                if cmd == "join" or hint == "join":
                    response = await self._handle_join(user_id, args)
                    await self._send(user_id, group_id, response)
                    return []

                # 处理 admin 命令
                if cmd == "admin" or hint == "admin":
                    response = await self._handle_admin(user_id, group_id, args)
                    if response:
                        await self._send(user_id, group_id, response)
                    return []

                # 处理 profile 命令
                if cmd == "profile":
                    response = await self._handle_profile(user_id, group_id)
                    await self._send(user_id, group_id, response)
                    return []

                # .jrrp 路由
                if hint == "jrrp":
                    return await self._handle_jrrp(user_id, group_id, meta)

                # 特殊命令处理
                is_at_trigger = meta.to_me and not msg_str.strip().startswith(".ai")

                response = None

                if content == "clear":
                    if self.app:
                        await self.app.clear_chat_history(user_id, group_id)
                        response = "对话历史已清空"
                    else:
                        response = "模块未初始化"
                elif content == "status" or hint == "status":
                    response = await self._get_status(user_id, group_id, is_private)
                elif content == "mute":
                    response = await self._handle_mute_toggle(user_id)
                elif content == "key" or content.startswith("key "):
                    response = "用户自定义 API Key 功能升级中，暂不可用。当前所有对话使用全局 provider 配置。"
                elif is_at_trigger:
                    if self.app and self.enabled:
                        try:
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
                                )
                                logger.info(
                                    f"[Persona] chat return: user={user_id}"
                                    f" response_type={type(response).__name__}"
                                    f" response_len={len(response) if response else 0}"
                                    f" response_preview={(response or '')[:60]!r}"
                                )
                        except QuotaExceeded as e:
                            logger.warning(f"[Persona] 配额超限: user={user_id}, group={group_id}")
                            response = str(e)
                    else:
                        response = "Persona AI 模块未启用或未初始化"
                else:
                    response = self._get_introduction()

                # 发送回复
                # - response is None：去重命中或未进入 chat 路径，静默早退
                # - response 是空字符串但非 None：分段路径已通过调度器实时发送，
                #   仍需更新群活跃度，但跳过再次 _send
                if response is None:
                    return []

                # 更新群活跃度（群聊且是@触发或AI命令）
                if group_id and self.data_store and self.config.group_activity_enabled:
                    try:
                        is_whitelisted = await self.data_store.is_group_whitelisted(group_id)
                        await self.data_store.update_group_activity(
                            group_id=group_id,
                            score_delta=self.config.group_activity_add_per_interaction,
                            max_daily_add=self.config.group_activity_max_daily_add,
                            is_whitelisted=is_whitelisted,
                        )
                    except Exception as e:
                        logger.warning(f"[Persona] 群活跃度更新失败（已忽略）: {e}")

                # 分段路径下 response 是 falsy sentinel，已通过 dispatcher 发出，不再次 _send
                if response:
                    await self._send(user_id, group_id, response)
                return []
        finally:
            _request_id_var.reset(_trace_token)


    async def _handle_jrrp(self, user_id: str, group_id: str, meta: MessageMetaData) -> List[BotCommandBase]:
        """处理 .jrrp 命令：调 compute_jrrp → 发送数值行 → 事件消息注入 LLM 生成角色评语"""
        from module.misc.jrrp_utils import compute_jrrp, format_jrrp_info_line, format_jrrp_trend_line, format_compact_trend
        from utils.time import get_current_date_raw

        # 1. 计算运势
        result = compute_jrrp(user_id, get_current_date_raw())

        # 2. 获取用户显示名
        user_name = await self.bot.get_nickname(user_id, group_id)

        # 3. 构建注入 LLM 的事件消息（先于 info_line 构建并写入 message_stream，
        #    确保 _build_messages() → _fetch_short_term_history() 能读取到）
        trend = format_compact_trend(result.delta_percent, result.direction)

        event_msg_parts = [
            f"[请简短回复，一两句话即可]",
            f"{user_name} 查询了今日运势。今日: {result.jrrp}/100",
        ]
        if result.is_max:
            event_msg_parts.append("[今日是最高值]")
        elif result.is_min:
            event_msg_parts.append("[今日是最低值]")
        event_msg_parts.append(f"，昨日: {result.zrrp}/100，{trend}。")
        event_msg = "".join(event_msg_parts)

        # 4. 将 event_msg 以 user 角色写入 message_stream，
        #    使 _build_messages() 的 _fetch_short_term_history() 能将其纳入 LLM 上下文
        #    持久化失败时跳过 LLM 调用，直接走模板回退
        event_persisted = False
        if self.data_store:
            try:
                await self.data_store.add_message_stream(
                    user_id=user_id,
                    group_id=group_id,
                    role="user",
                    type=MessageType.COMMAND,
                    content=event_msg,
                )
                event_persisted = True
            except Exception as e:
                logger.warning(f"[Persona] _handle_jrrp event_msg 持久化失败: {e}")

        # 5. 发送数值行（post-send hook 自动持久化，无需手动 add_message_stream）
        info_line = format_jrrp_info_line(user_name, result.jrrp)
        await self._send(user_id, group_id, info_line)

        # 6. 调 LLM 生成角色评语（skip_scoring=True）
        #    segment 启用时：dispatcher 自动发送，chat() 返回空串
        #    segment 未启用时：chat() 返回评语文本，需手动发送
        if not event_persisted:
            # event_msg 未持久化 → LLM 无上下文，直接走模板回退
            logger.warning("[Persona] _handle_jrrp event_msg 未持久化，跳过 LLM 调用")
            trend_line = format_jrrp_trend_line(result.zrrp, result.jrrp,
                                                result.delta_percent, result.direction)
            if trend_line.strip():
                await self._send(user_id, group_id, trend_line.strip())
        else:
            try:
                commentary = await self.app.chat.chat(
                    user_id=user_id,
                    group_id=group_id,
                    message=event_msg,
                    nickname=user_name,
                    skip_scoring=True,
                )
                if commentary:
                    await self._send(user_id, group_id, commentary)
            except Exception as e:
                # LLM 调用失败时，补发趋势信息作为回退
                logger.warning(f"[Persona] _handle_jrrp LLM 调用失败，回退到模板: {e}")
                trend_line = format_jrrp_trend_line(result.zrrp, result.jrrp,
                                                    result.delta_percent, result.direction)
                if trend_line.strip():
                    await self._send(user_id, group_id, trend_line.strip())

        return []

    async def _send(self, user_id: str, group_id: str, content: str, msg_id: Optional[int] = None) -> None:
        """通过 MessagePort 发送单条消息"""
        if self.app:
            logger.debug(
                f"[Persona] _send call: user={user_id} group={group_id}"
                f" content_len={len(content)} msg_id={msg_id}"
            )
            await self.app.send_message(user_id, group_id, content, msg_id=msg_id)
        else:
            logger.error(
                f"[Persona] MessagePort 未初始化，丢弃消息: "
                f"user={user_id}, group={group_id}, content={content[:30]}..."
            )

    async def _handle_join(self, user_id: str, args: List[str]) -> str:
        """处理 join 命令（用户加入白名单）"""
        if not self.data_store:
            return "模块未初始化，请稍后再试"

        config = self.bot.config.persona_ai

        # 检查白名单功能是否启用
        if not config.whitelist_enabled:
            return "AI 功能暂未开放，请联系管理员"

        # 检查是否设置了口令
        code = await self.data_store.get_global_setting("code")
        if not code:
            return "AI 功能暂未开放，请联系管理员"

        # 检查是否已在白名单
        if await self.data_store.is_user_whitelisted(user_id):
            return "你已经在啦~"

        # 检查口令
        if not args:
            return "请输入口令: .ai join <口令>"

        input_code = args[0]
        if input_code != code:
            return "口令不对哦~"

        # 加入白名单
        await self.data_store.add_user_to_whitelist(user_id)
        return "已开启 AI 对话，开始聊天吧！"

    async def _handle_admin(self, user_id: str, group_id: str, args: List[str]) -> str:
        """处理 admin 命令（管理员功能）——分发给 AdminDispatcher"""
        if not self.admin_dispatcher:
            return "模块未初始化"
        tick_p = self._async_tick_task is not None and not self._async_tick_task.done()
        daily_p = (
            self._async_tick_daily_task is not None and not self._async_tick_daily_task.done()
        )
        return await self.admin_dispatcher.dispatch(
            user_id, group_id, args,
            tick_pending=tick_p, daily_pending=daily_p,
        )

    async def _handle_mute_toggle(self, user_id: str) -> str:
        """处理 mute toggle 命令（翻转主动消息开关）"""
        if not self.data_store:
            return "模块未初始化"

        is_muted = await self.data_store.is_user_muted(user_id)

        if is_muted:
            await self.data_store.unmute_user(user_id)
            return "已开启主动消息（再次发送 .ai mute 可重新关闭）"
        else:
            await self.data_store.mute_user(user_id)
            return "已关闭主动消息（再次发送 .ai mute 可重新开启）"

    async def _handle_profile(self, user_id: str, group_id: str) -> str:
        if not self.data_store:
            return "模块未初始化"

        profile = await self.data_store.get_user_profile(user_id)
        rel = await self.data_store.get_relationship(user_id)
        if rel and self.app and self.app.get_decay_calculator() and self.app.get_character():
            rel = self.app.effective_relationship(rel)

        lines = ["你的档案"]

        if rel:
            relation_label = "未知"
            if self.app and self.app.get_character():
                relation_level, relation_label = rel.get_relation_level(self.app.get_relation_labels())

            lines.append(f"\n好感度: {relation_label} (区间 {relation_level}/5)")
            base_lines = self._format_relationship_base(rel, precision=1)
            lines.extend(base_lines[1:])  # 去掉 [好感度] 标题

            try:
                recent_events = await self.data_store.get_recent_score_events(user_id, limit=2)
                if len(recent_events) >= 2:
                    latest = recent_events[-1]
                    previous = recent_events[-2]
                    score_change = latest.composite_after - previous.composite_after

                    if score_change > 0.5:
                        trend_symbol, trend_desc = "↑", "最近上升"
                    elif score_change < -0.5:
                        trend_symbol, trend_desc = "↓", "最近下降"
                    else:
                        trend_symbol, trend_desc = "→", "基本持平"
                    lines.append(f"  趋势: {trend_symbol} ({trend_desc})")
                else:
                    lines.append(f"  趋势: → (暂无变化)")
            except Exception:
                lines.append(f"  趋势: → (计算失败)")

            try:
                earliest_time = await self.data_store.get_earliest_message_time(user_id, group_id)
                if earliest_time:
                    from utils.time import wall_now
                    now = wall_now(self.config.timezone)
                    days_known = max(1, (now - earliest_time).days)
                    lines.append(f"  认识: {days_known} 天")
                else:
                    lines.append(f"  认识: 1 天")
            except Exception:
                lines.append(f"  认识: 1 天")

            try:
                message_count = await self.data_store.count_messages(user_id, group_id)
                lines.append(f"  互动: {message_count} 次")
            except Exception:
                lines.append(f"  互动: 0 次")
        else:
            lines.append("\n好感度: 暂无记录")

        if profile and profile.facts:
            lines.append(f"\n已知信息:")
            for key, value in profile.facts.items():
                lines.append(f"  {key}: {value}")
        else:
            lines.append("\n已知信息: 暂无")

        return "\n".join(lines)

    def _get_introduction(self) -> str:
        if not self.app or not self.app.get_character():
            char_name = self.bot.config.persona_ai.character_name
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

        config = self.bot.config.persona_ai
        char = self.app.get_character()

        # 检查白名单状态
        whitelist_status = ""
        if config.whitelist_enabled and self.data_store:
            code = await self.data_store.get_global_setting("code")
            if code:
                whitelisted = await self._check_whitelist(user_id, group_id, is_private)
                whitelist_status = f"\n白名单: {'已通过' if whitelisted else '未加入（发送 .ai join <口令> 加入）'}"
            else:
                whitelist_status = "\n白名单: 未激活（所有人可用）"

        # 构建已注册模型列表
        provider_models: List[str] = []
        for pname, pconfig in config.providers.items():
            for m in pconfig.models:
                provider_models.append(f"{pname}/{m.name} (category={m.category})")

        models_str = ", ".join(provider_models) if provider_models else "无"

        if not char:
            return (
                f"Persona AI 状态: 初始化中...\n"
                f"角色: {config.character_name}\n"
                f"已注册模型: {models_str}"
                f"{whitelist_status}"
            )

        base = (
            f"Persona AI 状态: 已启用\n"
            f"角色: {char.name}\n"
            f"已注册模型: {models_str}"
            f"{whitelist_status}\n"
            f"\n使用方法: @bot <消息>\n"
            f".ai status - 查看状态\n"
            f".ai clear - 清空对话历史"
        )

        if self._is_admin(user_id) and self.app.get_router():
            stats = self.app.get_router_stats()
            stat_lines = []
            total_req = 0
            total_err = 0
            for pname in sorted(stats.keys()):
                s = stats[pname]
                total_req += s["requests"]
                total_err += s["errors"]
                stat_lines.append(
                    f"{pname}: {s['requests']} 次 / {s['errors']} 错误"
                )
            stat_lines.append(f"合计: {total_req} 次 / {total_err} 错误")
            base += (
                f"\n\n[管理员] LLM 统计（本次运行）\n"
                + "\n".join(stat_lines)
            )

        return base

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        if keyword in ["ai", "persona", "AI", "人格"]:
            lines = [
                ".ai - 自我介绍",
                "@bot <消息> - 与 AI 对话",
                ".ai clear - 清空对话历史",
                ".ai status - 查看状态",
                ".ai profile - 查看你的档案",
                ".ai mute - 切换主动消息开关",
                ".ai join <口令> - 加入白名单（私聊）",
            ]
            if self._is_admin(meta.user_id):
                lines.append("")
                lines.append("[管理员调试]")
                lines.append(".ai admin debug - 运行诊断（LLM统计、错误摘要、调度器状态、群活跃度）")
                lines.append(".ai admin rel <用户ID> - 查看关系")
                lines.append(".ai admin setrel <用户ID> <分数> - 修改好感度")
                lines.append(".ai admin reload - 热重载角色卡")
                lines.append(".ai admin events - 事件配置")
                lines.append(".ai admin diary [日期] - 查看日记（缺省今天，-1=昨天，或日期如2026-05-30）")
                lines.append(".ai admin whitelist code <口令> - 设置/更新口令")
                lines.append(".ai admin pause/resume - 暂停/恢复主动消息")
            return "\n".join(lines)
        return ""

    async def _handle_debug(self, user_id: str, group_id: str, msg: str) -> str:
        """.pa 命令已废弃，请使用 .ai admin 子命令"""
        return ".pa 命令已废弃，请使用 .ai admin 子命令"

    async def shutdown(self) -> None:
        """Bot 关闭时清理资源"""
        if self.app and self.app.segment_dispatcher:
            await self.app.segment_dispatcher.shutdown()
        if self.app and self.app.store:
            await self.app.store.close()

    def get_description(self) -> str:
        """获取命令描述"""
        return "Persona AI 对话" if self.enabled else "Persona AI 对话（已禁用）"

    def tick(self) -> List[BotCommandBase]:
        """
        每秒调用，驱动主动消息调度器。

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
                    logger.warning(f"[Persona] tick 耗时 {elapsed:.1f}s (>10s)，可能阻塞后续槽位")

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

    def tick_daily(self) -> List[BotCommandBase]:
        """每天调用，生成日记（异步逻辑通过任务队列在运行中的事件循环里执行）。"""
        if not self.enabled or not self.app:
            return []

        try:
            loop = asyncio.get_running_loop()

            async def _run_daily() -> None:
                try:
                    diary = await asyncio.wait_for(self.app.tick_daily(), timeout=300)
                    if diary:
                        logger.info(f"[Persona] 生成日记: {len(diary)} 字")
                    if self.report_generator and self.config.daily_report_enabled:
                        await self.report_generator.generate_and_send(diary)
                except asyncio.TimeoutError:
                    logger.error("[Persona] tick_daily 超时(>5min)，日报生成失败")
                    if self.report_generator:
                        await self.report_generator.send_master_notification("日报生成超时(>5min)，请检查 LLM 服务状态")

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
                    self._async_tick_daily_task = asyncio.create_task(_run_daily())
                return []

            # unreachable: get_running_loop() 成功时 loop.is_running() 必为 True
            return []
        except Exception as e:
            logger.error(f"[Persona] tick_daily 失败: {e}")
            return []
