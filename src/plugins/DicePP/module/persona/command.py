"""
Persona AI 命令入口

集成 persona 模块完成对话功能
支持白名单访问控制
"""
from typing import List, Dict, Tuple, Any, Optional, Callable
import json
import time
import asyncio
from nonebot.log import logger
from datetime import timedelta

from core.bot import Bot
from core.command.user_cmd import UserCommandBase, custom_user_command
from core.command.bot_cmd import BotCommandBase
from core.communication import MessageMetaData
from core.command.const import DPP_COMMAND_PRIORITY_DEFAULT, DPP_COMMAND_FLAG_FUN
from utils.logger import dice_log


from .factory import PersonaApp, create_persona
from .exceptions import PersonaInitError
from .llm.router import QuotaExceeded
from .data.store import PersonaDataStore
from .data.observation_buffer_repository import ObservationBufferRepository
from .utils.privacy import mask_sensitive_string
from .admin import AdminDispatcher


@custom_user_command("PersonaAI", priority=DPP_COMMAND_PRIORITY_DEFAULT, flag=DPP_COMMAND_FLAG_FUN)
class PersonaCommand(UserCommandBase):
    """Persona AI 命令处理器"""

    _format_relationship_base = staticmethod(AdminDispatcher._format_relationship_base)

    def __init__(self, bot: Bot):
        super().__init__(bot)
        self.enabled: bool = False
        self.app: Optional[PersonaApp] = None
        self.data_store: Optional[PersonaDataStore] = None
        self.init_error: Optional[str] = None  # create_persona 抛出的具名异常文本，供 _admin_debug 展示
        self._whitelist_confirm_pending: Dict[str, float] = {}  # user_id -> timestamp
        self._observation_repo: Optional[ObservationBufferRepository] = None
        # 主循环在 async 中调用同步 tick() 时，单槽异步任务（避免 life.tick 慢于 1s 时堆积）
        self._async_tick_task: Optional[asyncio.Task] = None
        self._async_tick_daily_task: Optional[asyncio.Task] = None
        # 管理员子命令分发器（在 delay_init 后由外部补齐）
        self._admin_handlers: Dict[str, Callable] = {}

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
        )

    def delay_init(self) -> List[str]:
        """延迟初始化"""
        self.config = self.bot.config.persona_ai
        config = self.config
        self.enabled = config.enabled

        if not self.enabled:
            return ["Persona AI 模块已禁用"]

        # 注册异步初始化任务
        async def init_persona():
            try:
                self.app = await create_persona(self.bot)
            except PersonaInitError as e:
                self.init_error = f"{type(e).__name__}: {e}"
                dice_log(f"[Persona] 模块初始化失败: {self.init_error}")
                self.app = None
                self.data_store = None
                self.enabled = False
                return []
            if self.app:
                self.data_store = self.app.store
                self._observation_repo = ObservationBufferRepository(self.data_store, config)
                # 注册消息发送后跨模块通知 hook（先注销旧 hook 再注册，防止热重载后重复）
                if hasattr(self, "_post_send_hook_unregister"):
                    self._post_send_hook_unregister()
                self._post_send_hook_unregister = self.bot.add_post_send_hook(self._group_chat_recorder)
                dice_log(f"[Persona] 模块初始化成功: {config.character_name}")
            else:
                # config.enabled=False 走这里：禁用而非失败
                dice_log("[Persona] 模块未启用")
                self.enabled = False
            return []

        self._register_admin_handlers()
        self.bot.register_task(init_persona, is_async=True, timeout=30)

        return [f"Persona AI 模块加载中 (角色: {config.character_name})"]

    @staticmethod
    def _is_persona_trigger(meta: MessageMetaData, msg: str) -> bool:
        """判断消息是否为 persona 触发（@bot 或 .ai/。ai 前缀）"""
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
        content: str,
        display_name: str,
    ) -> None:
        """群聊消息记录器回调（跨模块 bot 回复统一入流）"""
        if not self.data_store:
            return
        try:
            await self.data_store.add_group_conversation(
                group_id=group_id,
                user_id=user_id,
                role=role,
                content=content,
                display_name=display_name,
            )
        except Exception as e:
            dice_log(f"[Persona] 群聊记录器写入失败: {e}")

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
        code = await self.data_store.get_setting("code")
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

        # 如果以 "." 或 "。" 开头但不是有效的 AI 命令，不处理
        # 有效的 "." 前缀命令: .ai
        if msg.startswith(".") and not msg.startswith(".ai"):
            return False, False, None
        if msg.startswith("。") and not msg.startswith("。ai"):
            return False, False, None

        # @bot 或 .ai 前缀触发
        if not self._is_persona_trigger(meta, msg):
            # 群聊旁听模式（观察但不回复）— 不应拦截其他命令
            if meta.group_id and self._observation_repo:
                await self._observation_repo.handle_observation(
                    group_id=meta.group_id or "",
                    user_id=meta.user_id,
                    display_name=self._resolve_display_name(meta),
                    msg_str=msg_str,
                    event_agent=self.app.get_scheduler_event_agent() if self.app else None,
                )
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
        if cmd in ("ping", "clear", "status", "profile", "mute", "unmute") or cmd == "":
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

        # 群聊消息写入共享历史（触发消息直接入流；非触发消息由旁听模式处理，避免重复）
        is_trigger = self._is_persona_trigger(meta, msg_str)
        if group_id and self.data_store and (is_trigger or not self.config.observe_group_enabled):
            display_name = self._resolve_display_name(meta)
            try:
                await self.data_store.add_group_conversation(
                    group_id=group_id,
                    user_id=user_id,
                    role="user",
                    content=msg_str,
                    display_name=display_name,
                )
            except Exception as e:
                dice_log(f"[Persona] 群聊用户消息写入失败: {e}")

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
            await self._send(user_id, group_id, response)
            return []

        # 处理 profile 命令
        if cmd == "profile":
            response = await self._handle_profile(user_id, group_id)
            await self._send(user_id, group_id, response)
            return []

        # 特殊命令处理
        is_at_trigger = meta.to_me and not msg_str.strip().startswith(".ai")

        response = None

        if content == "ping":
            response = "pong"
        elif content == "clear":
            if self.app:
                await self.app.clear_chat_history(user_id, group_id)
                response = "对话历史已清空"
            else:
                response = "模块未初始化"
        elif content == "status" or hint == "status":
            response = await self._get_status(user_id, group_id, is_private)
        elif content == "mute":
            if not is_private:
                response = "请在私聊中使用此命令~"
            else:
                response = await self._handle_mute(user_id, True)
        elif content == "unmute":
            if not is_private:
                response = "请在私聊中使用此命令~"
            else:
                response = await self._handle_mute(user_id, False)
        elif content == "key" or content.startswith("key "):
            # Phase 4: 用户 LLM Key 配置
            if not is_private:
                response = "请私聊配置"
            else:
                key_args = content[4:].strip() if content.startswith("key ") else ""
                response = await self._handle_key_command(user_id, key_args)
        elif not content:
            if is_at_trigger:
                response = await self._get_status(user_id, group_id, is_private)
            else:
                response = self._get_introduction()
        elif is_at_trigger:
            if self.app and self.enabled:
                try:
                    response = await self.app.chat_with_user(
                        user_id=user_id,
                        group_id=group_id,
                        message=content,
                        nickname=nickname,
                    )
                except QuotaExceeded as e:
                    dice_log(f"[Persona] 配额超限: user={user_id}, group={group_id}")
                    response = (
                        f"{e}\n\n"
                        "使用 `.ai key config` 配置自己的 API Key 可解除限制"
                    )
            else:
                response = "Persona AI 模块未启用或未初始化"
        else:
            response = self._get_introduction()

        # 发送回复（去重命中时 response 为 None，静默不发送）
        if not response:
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
                dice_log(f"[Persona] 群活跃度更新失败（已忽略）: {e}")

        await self._send(user_id, group_id, response)
        return []

    async def _send(self, user_id: str, group_id: str, content: str) -> None:
        """通过 MessagePort 发送单条消息"""
        if self.app:
            await self.app.send_message(user_id, group_id, content)
        else:
            dice_log(
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
        code = await self.data_store.get_setting("code")
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
        # debug 子命令需要传入 command 的实时状态
        if args and args[0] == "debug":
            tick_p = self._async_tick_task is not None and not self._async_tick_task.done()
            daily_p = (
                self._async_tick_daily_task is not None and not self._async_tick_daily_task.done()
            )
            obs_status = self._observation_repo.get_status(group_id) if self._observation_repo and group_id else None
            return await self.admin_dispatcher._admin_debug(
                user_id, group_id, args,
                tick_pending=tick_p, daily_pending=daily_p, observation_status=obs_status,
            )
        return await self.admin_dispatcher.dispatch(user_id, group_id, args)

    async def _handle_mute(self, user_id: str, mute: bool) -> str:
        """处理 mute/unmute 命令"""
        if not self.data_store:
            return "模块未初始化"

        is_muted = await self.data_store.is_user_muted(user_id)

        if mute:
            if is_muted:
                return "你已经关闭了主动消息~"
            await self.data_store.mute_user(user_id)
            return "已关闭主动消息，我不会再主动发消息给你了~"
        else:
            if not is_muted:
                return "你已经开启了主动消息~"
            await self.data_store.unmute_user(user_id)
            return "已开启主动消息，想我的时候可以找我聊天哦~"

    async def _handle_profile(self, user_id: str, group_id: str) -> str:
        if not self.data_store:
            return "模块未初始化"

        profile = await self.data_store.get_user_profile(user_id)
        rel = await self._get_relationship_for_display(user_id, group_id)

        lines = ["你的档案"]

        if rel:
            warmth_level = 0
            warmth_label = "未知"
            if self.app and self.app.get_character():
                warmth_level, warmth_label = rel.get_warmth_level(self.app.get_warmth_labels())

            lines.append(f"\n好感度: {warmth_label} (区间 {warmth_level}/6)")
            base_lines = self._format_relationship_base(rel, precision=1)
            lines.extend(base_lines[1:])  # 去掉 [好感度] 标题

            try:
                recent_events = await self.data_store.get_recent_score_events(user_id, group_id, limit=2)
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
                    from .wall_clock import persona_wall_now
                    now = persona_wall_now(self.config.timezone)
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
            code = await self.data_store.get_setting("code")
            if code:
                whitelisted = await self._check_whitelist(user_id, group_id, is_private)
                whitelist_status = f"\n白名单: {'已通过' if whitelisted else '未加入（发送 .ai join <口令> 加入）'}"
            else:
                whitelist_status = "\n白名单: 未激活（所有人可用）"

        if not char:
            return (
                f"Persona AI 状态: 初始化中...\n"
                f"角色: {config.character_name}\n"
                f"主模型: {config.primary_model}"
                f"{whitelist_status}"
            )

        base = (
            f"Persona AI 状态: 已启用\n"
            f"角色: {char.name}\n"
            f"主模型: {config.primary_model}\n"
            f"辅助模型: {config.auxiliary_model or config.primary_model}"
            f"{whitelist_status}\n"
            f"\n使用方法: @bot <消息>\n"
            f".ai status - 查看状态\n"
            f".ai clear - 清空对话历史"
        )

        if self._is_admin(user_id) and self.app.get_router():
            stats = self.app.get_router_stats()
            p = stats["primary"]
            a = stats["auxiliary"]
            base += (
                f"\n\n[管理员] LLM 统计（本次运行）\n"
                f"主模型: {p['requests']} 次 / {p['errors']} 错误\n"
                f"辅助模型: {a['requests']} 次 / {a['errors']} 错误"
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
                ".ai join <口令> - 加入白名单（私聊）",
            ]
            if self._is_admin(meta.user_id):
                lines.append("")
                lines.append("[管理员调试]")
                lines.append(".ai admin debug - 调试信息")
                lines.append(".ai admin rel <用户ID> [群组ID] - 查看关系")
                lines.append(".ai admin setrel <用户ID> <分数> [群组ID] - 修改好感度")
                lines.append(".ai admin reload - 热重载角色卡")
                lines.append(".ai admin events - 事件配置")
                lines.append(".ai admin today/yesterday - 查看今天/昨天的事件和日记")
                lines.append(".ai admin pause/resume - 暂停/恢复主动消息")
            return "\n".join(lines)
        return ""

    async def _handle_debug(self, user_id: str, group_id: str, msg: str) -> str:
        """.pa 命令已废弃，请使用 .ai admin 子命令"""
        return ".pa 命令已废弃，请使用 .ai admin 子命令"

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
                await self.app.tick()

            # 清理已完成的任务（消费结果，不返回命令）
            t = self._async_tick_task
            if t is not None and t.done():
                try:
                    if not t.cancelled():
                        t.result()
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    dice_log(f"[Persona] tick 异步任务失败: {e}")
                finally:
                    self._async_tick_task = None

            if loop.is_running():
                if self._async_tick_task is None or self._async_tick_task.done():
                    self._async_tick_task = asyncio.create_task(_run_tick())
                return []

            loop.run_until_complete(self.app.tick())
            return []
        except Exception as e:
            dice_log(f"[Persona] tick 失败: {e}")
            return []

    def tick_daily(self) -> List[BotCommandBase]:
        """每天调用，生成日记（异步逻辑通过任务队列在运行中的事件循环里执行）。"""
        if not self.enabled or not self.app:
            return []

        try:
            loop = asyncio.get_running_loop()

            async def _run_daily() -> None:
                diary = await self.app.tick_daily()
                if diary:
                    dice_log(f"[Persona] 生成日记: {len(diary)} 字")

            # 清理已完成的任务
            dt = self._async_tick_daily_task
            if dt is not None and dt.done():
                try:
                    if not dt.cancelled():
                        dt.result()
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    dice_log(f"[Persona] tick_daily 异步任务失败: {e}")
                finally:
                    self._async_tick_daily_task = None

            if loop.is_running():
                if self._async_tick_daily_task is None or self._async_tick_daily_task.done():
                    self._async_tick_daily_task = asyncio.create_task(_run_daily())
                return []

            diary = loop.run_until_complete(self.app.tick_daily())
            if diary:
                dice_log(f"[Persona] 生成日记: {len(diary)} 字")
            return []
        except Exception as e:
            dice_log(f"[Persona] tick_daily 失败: {e}")
            return []

    # ── Phase 4: 用户 LLM Key 配置 ──

    async def _handle_key_command(self, user_id: str, args: str) -> str:
        """处理 .ai key 命令

        Args:
            user_id: 用户ID
            args: 命令参数，如 "", "config", "config key: value", "clear"
        """
        if not self.data_store:
            return "模块未初始化"

        from .data.models import UserLLMConfig

        # 检查加密密钥是否设置
        if not PersonaDataStore._get_encryption_key():
            return "DICE_PERSONA_SECRET 未设置，请联系管理员配置加密密钥"

        parts = args.split(None, 1) if args else ["", ""]
        subcmd = parts[0] if parts else ""
        config_content = parts[1] if len(parts) > 1 else ""

        # .ai key - 查看当前配置
        if not subcmd:
            config = await self.data_store.get_user_llm_config(user_id)
            if not config:
                return (
                    "你还没有配置个人 API Key\n"
                    "使用 `.ai key config` 进行配置\n"
                    "格式示例:\n"
                    "primary_key: sk-xxx\n"
                    "primary_model: gpt-4o"
                )

            lines = ["你的 LLM 配置:"]
            if config.decrypt_failed:
                lines.append("⚠️ 加密数据无法解密，请重新配置 API Key")
            lines.append(f"主模型 Key: {mask_sensitive_string(config.primary_api_key)}")
            if config.primary_model:
                lines.append(f"主模型: {config.primary_model}")
            if config.primary_base_url:
                lines.append(f"主模型 URL: {config.primary_base_url}")
            if config.auxiliary_api_key:
                lines.append(f"辅助模型 Key: {mask_sensitive_string(config.auxiliary_api_key)}")
            if config.auxiliary_model:
                lines.append(f"辅助模型: {config.auxiliary_model}")
            if config.auxiliary_base_url:
                lines.append(f"辅助模型 URL: {config.auxiliary_base_url}")
            lines.append("\n使用 `.ai key config` 修改配置")
            lines.append("使用 `.ai key clear` 清除配置")
            return "\n".join(lines)

        # .ai key clear - 清除配置
        if subcmd == "clear":
            await self.data_store.clear_user_llm_config(user_id)
            return "个人 LLM 配置已清除"

        # .ai key config - 配置
        if subcmd == "config":
            if not config_content:
                return (
                    "请提供配置内容，格式示例:\n"
                    ".ai key config\n"
                    "primary_key: sk-xxx\n"
                    "primary_model: gpt-4o\n"
                    "primary_base_url: https://api.openai.com/v1\n"
                    "\n可选字段:\n"
                    "- primary_key: 主模型 API Key\n"
                    "- primary_model: 主模型名称 (默认: gpt-4o)\n"
                    "- primary_base_url: 主模型 Base URL\n"
                    "- auxiliary_key: 辅助模型 API Key\n"
                    "- auxiliary_model: 辅助模型名称\n"
                    "- auxiliary_base_url: 辅助模型 Base URL"
                )

            # 解析配置内容（表单格式）
            parsed, errors = self._parse_key_config(config_content)
            if not parsed:
                msg = "配置格式错误，请使用 key: value 格式"
                if errors:
                    msg += "\n\n注意:\n" + "\n".join(f"- {e}" for e in errors)
                return msg

            # 构建 UserLLMConfig
            existing = await self.data_store.get_user_llm_config(user_id)
            config = UserLLMConfig(
                user_id=user_id,
                primary_api_key=parsed.get("primary_key", existing.primary_api_key if existing else ""),
                primary_base_url=parsed.get("primary_base_url", existing.primary_base_url if existing else ""),
                primary_model=parsed.get("primary_model", existing.primary_model if existing else ""),
                auxiliary_api_key=parsed.get("auxiliary_key", existing.auxiliary_api_key if existing else ""),
                auxiliary_base_url=parsed.get("auxiliary_base_url", existing.auxiliary_base_url if existing else ""),
                auxiliary_model=parsed.get("auxiliary_model", existing.auxiliary_model if existing else ""),
            )

            success = await self.data_store.save_user_llm_config(config)
            if success:
                reply = "配置已保存，你的个人 API Key 将用于后续对话"
                if errors:
                    reply += "\n\n以下行未保存:\n" + "\n".join(f"- {e}" for e in errors)
                return reply
            else:
                return "配置保存失败，请联系管理员"

        return f"未知命令: {subcmd}\n可用命令: .ai key, .ai key config, .ai key clear"

    # R11: 有效的配置 key 名白名单
    _VALID_KEY_CONFIG_KEYS = {
        "primary_key", "primary_model", "primary_base_url",
        "auxiliary_key", "auxiliary_model", "auxiliary_base_url",
    }

    @classmethod
    def _parse_key_config(cls, content: str) -> tuple[Dict[str, str], List[str]]:
        """解析 key config 内容（R11: 添加 key 名白名单和 URL 验证）

        格式: key: value（每行一个）

        Returns:
            (解析结果字典, 错误/警告列表)
        """
        result: Dict[str, str] = {}
        errors: List[str] = []
        for line in content.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            if ":" not in line:
                errors.append(f"无法识别的行: {line}")
                continue
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()
            if not key or not value:
                errors.append(f"key 或 value 为空: {line}")
                continue
            # R11: 检查 key 名是否在白名单中
            if key not in cls._VALID_KEY_CONFIG_KEYS:
                errors.append(f"未知的配置项: {key}")
                continue
            # R11: 对 URL 字段进行基础格式验证
            if "base_url" in key and value:
                from urllib.parse import urlparse
                parsed = urlparse(value)
                if parsed.scheme not in ("http", "https") or not parsed.netloc:
                    errors.append(f"{key} 必须是有效的 http:// 或 https:// URL")
                    continue
            result[key] = value
        return result, errors
