"""
Persona AI 命令入口

集成 orchestrator 完成对话功能
支持白名单访问控制
"""
from typing import List, Dict, Tuple, Any, Optional
import json
import time
import asyncio

from core.bot import Bot
from core.command.user_cmd import UserCommandBase, custom_user_command
from core.command.bot_cmd import BotSendMsgCommand, BotCommandBase
from core.communication import PrivateMessagePort, GroupMessagePort, MessageMetaData
from core.command.const import DPP_COMMAND_PRIORITY_DEFAULT, DPP_COMMAND_FLAG_FUN
from utils.logger import dice_log

from .orchestrator import PersonaOrchestrator
from .llm.router import QuotaExceeded
from .data.store import PersonaDataStore
from .data.persist_keys import PERSONA_SK_OBSERVATION_BUFFERS
from .proactive.observation_buffer import ObservationBuffer
from .utils.privacy import mask_sensitive_string


@custom_user_command("PersonaAI", priority=DPP_COMMAND_PRIORITY_DEFAULT, flag=DPP_COMMAND_FLAG_FUN)
class PersonaCommand(UserCommandBase):
    """Persona AI 命令处理器"""

    def __init__(self, bot: Bot):
        super().__init__(bot)
        self.enabled: bool = False
        self.orchestrator: PersonaOrchestrator = None
        self.data_store: PersonaDataStore = None
        self._whitelist_confirm_pending: Dict[str, float] = {}  # user_id -> timestamp
        self._observation_buffers: Dict[str, ObservationBuffer] = {}  # group_id -> buffer
        self._observation_buffers_loaded: bool = False
        # 主循环在 async 中调用同步 tick() 时，单槽异步任务（避免 orchestrator.tick 慢于 1s 时堆积）
        self._async_tick_task: Optional[asyncio.Task] = None
        self._async_tick_daily_task: Optional[asyncio.Task] = None
        self._observation_persist_monotonic: float = 0.0
        # 管理员子命令分发器（在 delay_init 后由外部补齐）
        self._admin_handlers: Dict[str, Callable] = {}

    def _register_admin_handlers(self) -> None:
        """注册管理员子命令处理器（在 delay_init 后调用）"""
        self._admin_handlers = {
            "code": self._admin_code,
            "whitelist": self._admin_whitelist,
            "trace": self._handle_admin_trace,
            "stats": self._handle_admin_stats,
            "errors": self._handle_admin_errors,
            "debug": self._admin_debug,
            "rel": self._admin_rel,
            "setrel": self._admin_setrel,
            "reload": self._admin_reload,
            "events": self._admin_events,
            "list": self._admin_list,
            "today": self._admin_diary,
            "yesterday": self._admin_diary,
            "diary": self._admin_diary,
            "pause": self._admin_pause,
            "resume": self._admin_resume,
        }

    def delay_init(self) -> List[str]:
        """延迟初始化"""
        self.config = self.bot.config.persona_ai
        config = self.config
        self.enabled = config.enabled
        
        if not self.enabled:
            return ["Persona AI 模块已禁用"]
        
        # 创建 orchestrator（但不立即初始化，因为需要异步）
        self.orchestrator = PersonaOrchestrator(self.bot)
        
        # 注册异步初始化任务
        async def init_orchestrator():
            success = await self.orchestrator.initialize()
            if success:
                self.data_store = self.orchestrator.data_store
                await self._ensure_observation_buffers_loaded()
                dice_log(f"[Persona] 模块初始化成功: {config.character_name}")
            else:
                dice_log(f"[Persona] 模块初始化失败")
                self.enabled = False
            return []
        
        self._register_admin_handlers()
        self.bot.register_task(init_orchestrator, is_async=True, timeout=30)

        return [f"Persona AI 模块加载中 (角色: {config.character_name})"]

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
        if not meta.to_me and not msg.startswith(".ai") and not msg.startswith("。ai"):
            # 群聊旁听模式（观察但不回复）— 不应拦截其他命令
            if meta.group_id and self.config.observe_group_enabled:
                await self._handle_group_observation(
                    meta.group_id or "", meta.user_id, meta.nickname or "", msg_str
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

        # 提取命令内容
        msg = msg_str.strip()
        if msg.startswith(".ai") or msg.startswith("。ai"):
            content = msg[3:].strip()
        else:
            content = msg

        # 特殊提示：群聊中发送 join
        if hint == "join_group_hint":
            response = "请私聊发送此命令"
            port = GroupMessagePort(group_id) if group_id else PrivateMessagePort(user_id)
            return [BotSendMsgCommand(self.bot.account, response, [port])]
        
        # 解析命令
        parts = content.split()
        cmd = parts[0] if parts else ""
        args = parts[1:] if len(parts) > 1 else []
        
        # 处理 join 命令
        if cmd == "join" or hint == "join":
            response = await self._handle_join(user_id, args)
            port = PrivateMessagePort(user_id)
            return [BotSendMsgCommand(self.bot.account, response, [port])]
        
        # 处理 admin 命令
        if cmd == "admin" or hint == "admin":
            response = await self._handle_admin(user_id, group_id, args)
            port = GroupMessagePort(group_id) if group_id else PrivateMessagePort(user_id)
            return [BotSendMsgCommand(self.bot.account, response, [port])]

        # 处理 profile 命令
        if cmd == "profile":
            response = await self._handle_profile(user_id, group_id)
            port = GroupMessagePort(group_id) if group_id else PrivateMessagePort(user_id)
            return [BotSendMsgCommand(self.bot.account, response, [port])]
        
        # 特殊命令处理
        is_at_trigger = meta.to_me and not msg_str.strip().startswith(".ai")

        if content == "ping":
            response = "pong"
        elif content == "clear":
            if self.orchestrator:
                await self.orchestrator.clear_history(user_id, group_id)
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
            if self.orchestrator and self.enabled:
                try:
                    response = await self.orchestrator.chat(
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

        port = GroupMessagePort(group_id) if group_id else PrivateMessagePort(user_id)
        return [BotSendMsgCommand(self.bot.account, response, [port])]

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
        """处理 admin 命令（管理员功能）"""
        if not self._is_admin(user_id):
            return "权限不足"
        if not self.data_store:
            return "模块未初始化"
        if not args:
            return (
                "管理员命令:\n"
                ".ai admin code <新口令> - 设置/更新口令\n"
                ".ai admin code clear - 清除口令\n"
                ".ai admin whitelist - 查看白名单\n"
                ".ai admin whitelist add group <group_id> - 添加群到白名单\n"
                ".ai admin whitelist remove <user_id> - 移除用户\n"
                ".ai admin whitelist remove group <group_id> - 移除群\n"
                ".ai admin whitelist clear - 清空白名单\n"
                ".ai admin trace <user_id> - 导出最近 5 次 LLM trace\n"
                ".ai admin trace <user_id> full - 导出最近 1 次完整 trace\n"
                ".ai admin stats - 查看今日 LLM 调用统计\n"
                ".ai admin errors - 查看最近 24h 错误摘要\n"
                ".ai admin debug - 查看当前上下文\n"
                ".ai admin rel <用户ID> [群组ID] - 查看指定用户关系\n"
                ".ai admin setrel <用户ID> <分数> [群组ID] - 修改好感度\n"
                ".ai admin reload - 热重载角色卡\n"
                ".ai admin events - 查看事件配置\n"
                ".ai admin list - 查看白名单\n"
                ".ai admin today - 查看今天的事件和日记\n"
                ".ai admin yesterday - 查看昨天的事件和日记\n"
                ".ai admin diary - 查看今天的事件和日记\n"
                ".ai admin pause - 暂停主动消息\n"
                ".ai admin resume - 恢复主动消息"
            )
        subcmd = args[0]
        handler = self._admin_handlers.get(subcmd)
        if handler:
            return await handler(user_id, group_id, args)
        return "未知的管理员命令"

    async def _admin_code(self, user_id: str, group_id: str, args: List[str]) -> str:
        if len(args) < 2:
            current_code = await self.data_store.get_setting("code")
            if current_code:
                return f"当前已设置口令（{len(current_code)}位字符）"
            else:
                return "当前未设置口令，白名单功能未激活"
        if args[1] == "clear":
            await self.data_store.delete_setting("code")
            return "口令已清除，白名单功能已停用"
        new_code = args[1]
        await self.data_store.set_setting("code", new_code)
        return "已更新，白名单功能已激活"

    async def _admin_whitelist(self, user_id: str, group_id: str, args: List[str]) -> str:
        if len(args) < 2:
            entries = await self.data_store.list_whitelist()
            if not entries:
                return "白名单为空"
            lines = ["白名单列表:"]
            users = [e for e in entries if e.type == "user"]
            groups = [e for e in entries if e.type == "group"]
            if users:
                lines.append(f"\n用户 ({len(users)}个):")
                for e in users[:10]:
                    lines.append(f"  {e.id}")
                if len(users) > 10:
                    lines.append(f"  ... 还有 {len(users) - 10} 个")
            if groups:
                lines.append(f"\n群聊 ({len(groups)}个):")
                for e in groups[:10]:
                    lines.append(f"  {e.id}")
                if len(groups) > 10:
                    lines.append(f"  ... 还有 {len(groups) - 10} 个")
            return "\n".join(lines)
        action = args[1]
        if action == "add" and len(args) >= 3 and args[2] == "group":
            target_group_id = args[3] if len(args) > 3 else ""
            if not target_group_id:
                return "请提供群ID"
            await self.data_store.add_group_to_whitelist(target_group_id)
            return f"已添加群 {target_group_id} 到白名单"
        if action == "remove":
            if len(args) >= 3 and args[2] == "group":
                target_group_id = args[3] if len(args) > 3 else ""
                if not target_group_id:
                    return "请提供群ID"
                await self.data_store.remove_from_whitelist(target_group_id, "group")
                return f"已移除群 {target_group_id}"
            else:
                target_id = args[2] if len(args) > 2 else ""
                if not target_id:
                    return "请提供用户ID"
                await self.data_store.remove_from_whitelist(target_id, "user")
                return f"已移除用户 {target_id}"
        if action == "clear":
            self._whitelist_confirm_pending[user_id] = time.monotonic()
            return "确认清空？60秒内发 `.ai admin whitelist confirm` 执行"
        if action == "confirm":
            pending_time = self._whitelist_confirm_pending.get(user_id)
            if pending_time and (time.monotonic() - pending_time) < 60.0:
                await self.data_store.clear_whitelist()
                self._whitelist_confirm_pending.pop(user_id, None)
                return "白名单已清空"
            else:
                self._whitelist_confirm_pending.pop(user_id, None)
                return "没有待确认的清空操作（可能已超时）"
        return "未知的管理员命令"

    async def _admin_debug(self, user_id: str, group_id: str, args: List[str]) -> str:
        lines = ["=== Persona AI 调试信息 ==="]
        profile = await self.data_store.get_user_profile(user_id)
        if self.orchestrator:
            rel = await self.orchestrator.get_relationship_for_display(user_id, group_id)
        else:
            rel = await self.data_store.get_relationship(user_id, group_id)
        lines.append(f"\n当前用户: {user_id}")
        if group_id:
            lines.append(f"当前群组: {group_id}")
        if rel:
            lines.extend(self._format_relationship_base(rel))
        else:
            lines.append(f"\n[好感度] 暂无记录")
        if profile and profile.facts:
            lines.append(f"\n[用户画像]")
            for k, v in list(profile.facts.items())[:5]:
                lines.append(f"  {k}: {v}")
            if len(profile.facts) > 5:
                lines.append(f"  ... 还有 {len(profile.facts) - 5} 条")
        else:
            lines.append(f"\n[用户画像] 暂无")
        config = self.bot.config.persona_ai
        lines.append(f"\n[配置]")
        lines.append(f"  角色: {config.character_name}")
        lines.append(f"  日限: {config.daily_limit} 次")
        lines.append(f"  群聊: {'开启' if config.group_chat_enabled else '关闭'}")
        lines.append(f"\n[Phase 2 系统]")
        lines.append(f"  衰减: {'开启' if config.decay_enabled else '关闭'}")
        lines.append(f"  生活模拟: {'开启' if config.character_life_enabled else '关闭'}")
        lines.append(f"  主动消息: {'开启' if config.proactive_enabled else '关闭'}")
        lines.append(f"  群聊观察: {'开启' if config.observe_group_enabled else '关闭'}")
        lines.append(f"  群活跃度: {'开启' if config.group_activity_enabled else '关闭'}")
        if config.decay_enabled:
            lines.append(f"\n[衰减配置]")
            lines.append(f"  免衰减期: {config.decay_grace_period_hours}h")
            lines.append(f"  衰减率: {config.decay_rate_per_hour}/h")
            lines.append(f"  每日上限: {config.decay_daily_cap}")
        if self.orchestrator and self.orchestrator.scheduler:
            scheduler_status = self.orchestrator.scheduler.get_status()
            lines.append(f"\n[调度器状态]")
            lines.append(f"  待分享: {scheduler_status.get('pending_shares', 0)}")
            lines.append(f"  今日触发: {len(scheduler_status.get('scheduled_today', []))}")
            lines.append(f"  安静时段: {'是' if scheduler_status.get('is_quiet_hours') else '否'}")
        tick_p = self._async_tick_task is not None and not self._async_tick_task.done()
        daily_p = (
            self._async_tick_daily_task is not None and not self._async_tick_daily_task.done()
        )
        lines.append(f"\n[异步 tick]")
        lines.append(f"  proactive tick 进行中: {'是' if tick_p else '否'}")
        lines.append(f"  tick_daily 进行中: {'是' if daily_p else '否'}")
        if group_id and config.group_activity_enabled:
            try:
                activity = await self.data_store.get_group_activity(group_id)
                lines.append(f"\n[群活跃度]")
                lines.append(f"  分数: {activity.score:.1f}")
                lines.append(f"  最后互动: {activity.last_interaction_at.strftime('%Y-%m-%d %H:%M') if activity.last_interaction_at else '无'}")
            except Exception:
                pass
        if group_id and group_id in self._observation_buffers:
            buffer = self._observation_buffers[group_id]
            status = buffer.get_status()
            lines.append(f"\n[观察缓冲]")
            lines.append(f"  缓冲消息: {status.get('buffer_size', 0)}")
            lines.append(f"  当前阈值: {status.get('threshold', 0)}")
        return "\n".join(lines)

    async def _admin_rel(self, user_id: str, group_id: str, args: List[str]) -> str:
        rel_args = args[1:]
        if not rel_args:
            return "用法: .ai admin rel <用户ID> [群组ID]"
        target_user = rel_args[0]
        target_group = rel_args[1] if len(rel_args) > 1 else group_id
        if self.orchestrator:
            rel = await self.orchestrator.get_relationship_for_display(target_user, target_group)
        else:
            rel = await self.data_store.get_relationship(target_user, target_group)
        profile = await self.data_store.get_user_profile(target_user)
        lines = [f"=== 用户 {target_user} 的关系详情 ==="]
        if target_group:
            lines.append(f"群组: {target_group}")
        if rel:
            lines.extend(self._format_relationship_base(rel))
            if self.orchestrator and self.orchestrator.character:
                level, label = rel.get_warmth_level(self.orchestrator.character.get_warmth_labels())
                lines.append(f"  等级: {level} ({label})")
        else:
            lines.append("\n暂无关系记录")
        if profile:
            lines.append(f"\n[画像]")
            lines.append(f"  创建时间: {profile.created_at.strftime('%Y-%m-%d') if profile.created_at else '未知'}")
            lines.append(f"  更新时间: {profile.updated_at.strftime('%Y-%m-%d') if profile.updated_at else '未知'}")
            if profile.facts:
                lines.append(f"  已知信息 ({len(profile.facts)}条):")
                for k, v in list(profile.facts.items())[:5]:
                    lines.append(f"    {k}: {v}")
        return "\n".join(lines)

    async def _admin_setrel(self, user_id: str, group_id: str, args: List[str]) -> str:
        setrel_args = args[1:]
        if len(setrel_args) < 2:
            return "用法: .ai admin setrel <用户ID> <综合分数> [群组ID]"
        target_user = setrel_args[0]
        try:
            new_score = float(setrel_args[1])
        except ValueError:
            return "分数必须是数字"
        if new_score < 0 or new_score > 100:
            return "分数必须在 0-100 之间"
        target_group = setrel_args[2] if len(setrel_args) > 2 else group_id
        rel = await self.data_store.get_relationship(target_user, target_group)
        if not rel:
            initial = self.orchestrator.character.extensions.initial_relationship if self.orchestrator and self.orchestrator.character else 30.0
            rel = await self.data_store.init_relationship(target_user, target_group, initial)
        rel.intimacy = new_score
        rel.passion = new_score
        rel.trust = new_score
        rel.secureness = new_score
        await self.data_store.update_relationship(rel)
        return f"已设置用户 {target_user} 的好感度为 {new_score:.2f}"

    async def _admin_reload(self, user_id: str, group_id: str, args: List[str]) -> str:
        if not self.orchestrator:
            return "模块未初始化"
        success, msg = await self.orchestrator.reload_character()
        return f"重载{'成功' if success else '失败'}: {msg}"

    async def _admin_events(self, user_id: str, group_id: str, args: List[str]) -> str:
        if not self.orchestrator or not self.orchestrator.character:
            return "角色未加载"
        char = self.orchestrator.character
        ext = char.extensions
        lines = [f"=== {char.name} 的事件配置 ==="]
        lines.append(f"\n[基础设置]")
        lines.append(f"  每日事件数: {ext.daily_events_count}")
        lines.append(f"  活动时段: {ext.event_day_start_hour}:00 - {ext.event_day_end_hour}:00")
        lines.append(f"  时间抖动: ±{ext.event_jitter_minutes} 分钟")
        lines.append(f"\n[定时事件]")
        for evt in ext.scheduled_events:
            lines.append(f"  {evt.type}: {evt.time_range}")
        if ext.world:
            lines.append(f"\n[世界观]")
            lines.append(f"  {ext.world}")
        labels = char.get_warmth_labels()
        lines.append(f"\n[好感度等级]")
        for i, label in enumerate(labels):
            lines.append(f"  {i*10}-{(i+1)*10}: {label}")
        return "\n".join(lines)

    async def _admin_list(self, user_id: str, group_id: str, args: List[str]) -> str:
        entries = await self.data_store.list_whitelist()
        users = [e for e in entries if e.type == "user"]
        groups = [e for e in entries if e.type == "group"]
        lines = ["=== 白名单列表 ==="]
        lines.append(f"\n用户: {len(users)} 个")
        for u in users[:20]:
            lines.append(f"  {u.id}")
        if len(users) > 20:
            lines.append(f"  ... 还有 {len(users)-20} 个")
        lines.append(f"\n群组: {len(groups)} 个")
        for g in groups[:20]:
            lines.append(f"  {g.id}")
        if len(groups) > 20:
            lines.append(f"  ... 还有 {len(groups)-20} 个")
        return "\n".join(lines)

    async def _admin_diary(self, user_id: str, group_id: str, args: List[str]) -> str:
        from datetime import timedelta
        from .wall_clock import persona_wall_now
        subcmd = args[0]
        wall = persona_wall_now(self.config.timezone)
        if subcmd == "yesterday":
            date = (wall - timedelta(days=1)).strftime("%Y-%m-%d")
            date_label = "昨天"
        else:
            date = wall.strftime("%Y-%m-%d")
            date_label = "今天"
        diary = await self.data_store.get_diary(date)
        events = await self.data_store.get_daily_events(date)
        lines = [f"=== {date_label} ({date}) ==="]
        if diary:
            lines.append(f"\n[日记]")
            lines.append(diary)
        else:
            lines.append(f"\n[日记] 暂无")
        if events:
            lines.append(f"\n[事件] ({len(events)} 个)")
            for i, evt in enumerate(events[:10], 1):
                lines.append(f"  {i}. [{evt.event_type}] {evt.description}")
                if evt.reaction:
                    lines.append(f"     反应: {evt.reaction}")
        else:
            lines.append(f"\n[事件] 暂无")
        return "\n".join(lines)

    async def _admin_pause(self, user_id: str, group_id: str, args: List[str]) -> str:
        if self.orchestrator and self.orchestrator.scheduler:
            self.orchestrator.scheduler.config.enabled = False
            return "已暂停主动消息发送"
        return "调度器未初始化"

    async def _admin_resume(self, user_id: str, group_id: str, args: List[str]) -> str:
        if self.orchestrator and self.orchestrator.scheduler:
            self.orchestrator.scheduler.config.enabled = True
            return "已恢复主动消息发送"
        return "调度器未初始化"

    async def _handle_admin_trace(self, user_id: str, args: List[str]) -> str:
        if not self._is_admin(user_id):
            return "权限不足"
        if not self.data_store or not self.orchestrator.llm_router:
            return "模块未初始化"
        if len(args) < 2:
            return "用法: .ai admin trace <user_id> [full]"
        target_user = args[1]
        full_mode = len(args) >= 3 and args[2] == "full"
        limit = 1 if full_mode else 5
        traces = await self.data_store.get_llm_traces(target_user, limit=limit)
        if not traces:
            return f"用户 {target_user} 暂无 trace 记录"
        lines = [f"用户 {target_user} 的 LLM trace:"]
        for i, t in enumerate(traces, 1):
            latency_str = f"{t.latency_ms}ms" if t.latency_ms is not None else "N/A"
            resp_preview = t.response[:200] + "..." if len(t.response) > 200 else t.response
            lines.append(
                f"\n[{i}] {t.created_at} | model={t.model} tier={t.tier} "
                f"latency={latency_str} status={t.status}\n"
                f"response: {resp_preview}"
            )
            if full_mode:
                try:
                    msgs = json.loads(t.messages)
                    visible_msgs = []
                    for m in msgs:
                        if m.get("role") == "system" and len(str(m.get("content", ""))) > 500:
                            m = {**m, "content": str(m["content"])[:500] + "...(truncated)"}
                        visible_msgs.append(m)
                    msgs_preview = json.dumps(visible_msgs, ensure_ascii=False, indent=None)
                    if len(msgs_preview) > 2000:
                        msgs_preview = msgs_preview[:2000] + "..."
                    lines.append(f"messages: {msgs_preview}")
                except json.JSONDecodeError:
                    lines.append("messages: (invalid json)")
                except Exception as e:
                    lines.append(f"messages: (parse failed: {type(e).__name__})")
                resp_full = t.response[:1000]
                if len(t.response) > 1000:
                    resp_full += "..."
                lines.append(f"response_full: {resp_full}")
        return "\n".join(lines)

    async def _handle_admin_stats(self, user_id: str) -> str:
        if not self._is_admin(user_id):
            return "权限不足"
        if not self.orchestrator.llm_router:
            return "模块未初始化"
        stats = self.orchestrator.llm_router.get_stats()
        p_percentiles = self.orchestrator.llm_router.get_latency_percentiles("primary")
        a_percentiles = self.orchestrator.llm_router.get_latency_percentiles("auxiliary")

        token_in_total: Optional[int] = None
        token_out_total: Optional[int] = None
        if self.data_store and getattr(self.config, "trace_enabled", False):
            token_in_total, token_out_total = await self.data_store.get_today_token_usage()
            token_in_total = token_in_total or 0
            token_out_total = token_out_total or 0

        primary_requests = stats["primary"]["requests"]
        primary_errors = stats["primary"]["errors"]
        aux_requests = stats["auxiliary"]["requests"]
        aux_errors = stats["auxiliary"]["errors"]

        primary_error_rate = f"{(primary_errors / max(1, primary_requests) * 100):.1f}%" if primary_requests else "0.0%"
        aux_error_rate = f"{(aux_errors / max(1, aux_requests) * 100):.1f}%" if aux_requests else "0.0%"

        p50 = p_percentiles["p50"] / 1000.0
        p90 = p_percentiles["p90"] / 1000.0
        p99 = p_percentiles["p99"] / 1000.0
        a50 = a_percentiles["p50"] / 1000.0
        a90 = a_percentiles["p90"] / 1000.0
        a99 = a_percentiles["p99"] / 1000.0

        token_str = (
            f"Token 消耗: 输入 {token_in_total} / 输出 {token_out_total}"
            if token_in_total is not None
            else "Token 消耗: 输入 N/A / 输出 N/A"
        )

        return (
            f"今日调用: {primary_requests + aux_requests} 次\n"
            f"主模型: {primary_requests} 次, 错误率 {primary_error_rate}, "
            f"p50/p90/p99={p50:.1f}s/{p90:.1f}s/{p99:.1f}s\n"
            f"辅助模型: {aux_requests} 次, 错误率 {aux_error_rate}, "
            f"p50/p90/p99={a50:.1f}s/{a90:.1f}s/{a99:.1f}s\n"
            f"{token_str}"
        )

    async def _handle_admin_errors(self, user_id: str) -> str:
        if not self._is_admin(user_id):
            return "权限不足"
        if not self.data_store:
            return "模块未初始化"
        from .wall_clock import persona_wall_now
        since = (persona_wall_now(self.config.timezone) - timedelta(hours=24)).isoformat()
        rows = await self.data_store.get_error_summary_since(since)
        if not rows:
            return "最近 24h 没有错误记录"
        total = sum(count for _, count in rows)
        lines = [f"最近 24h 错误: {total} 次"]
        for status, count in rows:
            lines.append(f"- {status}: {count} 次")
        return "\n".join(lines)

    def _format_relationship_base(self, rel, precision: int = 2) -> List[str]:
        """格式化好感度基础信息，返回字符串列表"""
        fmt = f"  {{}}: {{:.{precision}f}}"
        lines = ["\n[好感度]"]
        lines.append(fmt.format("亲密度", rel.intimacy))
        lines.append(fmt.format("激情", rel.passion))
        lines.append(fmt.format("信任", rel.trust))
        lines.append(fmt.format("安全感", rel.secureness))
        lines.append(fmt.format("综合", rel.composite_score))
        lines.append(f"  最后互动: {rel.last_interaction_at.strftime('%Y-%m-%d %H:%M') if rel.last_interaction_at else '无'}")
        return lines

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
        if self.orchestrator:
            rel = await self.orchestrator.get_relationship_for_display(user_id, group_id)
        else:
            rel = await self.data_store.get_relationship(user_id, group_id)

        lines = ["你的档案"]

        if rel:
            # Get warmth level label
            warmth_level = 0
            warmth_label = "未知"
            if self.orchestrator and self.orchestrator.character:
                warmth_level, warmth_label = rel.get_warmth_level(self.orchestrator.character.get_warmth_labels())

            lines.append(f"\n好感度: {warmth_label} (区间 {warmth_level}/6)")
            base_lines = self._format_relationship_base(rel, precision=1)
            lines.extend(base_lines[1:])  # 去掉 [好感度] 标题

            # Calculate trend from recent score events
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

            # Calculate days known from earliest message
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

            # Count interactions
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
        if not self.orchestrator or not self.orchestrator.character:
            char_name = self.bot.config.persona_ai.character_name
            return f"你好，我是 {char_name}。（@ 我来聊天，.ai status 查看状态）"
        char = self.orchestrator.character
        parts = [f"你好，我是 {char.name}。"]
        if char.description:
            parts.append(char.description)
        parts.append("（@ 我来聊天，.ai status 查看状态）")
        return "\n".join(parts)

    async def _get_status(self, user_id: str, group_id: str, is_private: bool) -> str:
        """获取状态信息"""
        if not self.enabled:
            return "Persona AI 状态: 未启用\n在配置中设置 persona_ai.enabled = true 来启用"
        
        if not self.orchestrator:
            return "Persona AI 状态: 初始化中..."
        
        config = self.bot.config.persona_ai
        char_info = self.orchestrator.get_character_info()
        
        # 检查白名单状态
        whitelist_status = ""
        if config.whitelist_enabled and self.data_store:
            code = await self.data_store.get_setting("code")
            if code:
                whitelisted = await self._check_whitelist(user_id, group_id, is_private)
                whitelist_status = f"\n白名单: {'已通过' if whitelisted else '未加入（发送 .ai join <口令> 加入）'}"
            else:
                whitelist_status = "\n白名单: 未激活（所有人可用）"
        
        if not char_info:
            return (
                f"Persona AI 状态: 初始化中...\n"
                f"角色: {config.character_name}\n"
                f"主模型: {config.primary_model}"
                f"{whitelist_status}"
            )
        
        base = (
            f"Persona AI 状态: 已启用\n"
            f"角色: {char_info.get('name', '未知')}\n"
            f"主模型: {config.primary_model}\n"
            f"辅助模型: {config.auxiliary_model or config.primary_model}"
            f"{whitelist_status}\n"
            f"\n使用方法: @bot <消息>\n"
            f".ai status - 查看状态\n"
            f".ai clear - 清空对话历史"
        )

        if self._is_admin(user_id) and self.orchestrator.llm_router:
            stats = self.orchestrator.llm_router.get_stats()
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
            # 管理员额外显示调试命令
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

    async def _ensure_observation_buffers_loaded(self) -> None:
        if self._observation_buffers_loaded or not self.data_store:
            return
        self._observation_buffers_loaded = True
        raw = await self.data_store.get_setting(PERSONA_SK_OBSERVATION_BUFFERS)
        if not raw:
            return
        try:
            blob = json.loads(raw)
        except json.JSONDecodeError:
            return
        for gid, payload in blob.items():
            if not isinstance(payload, dict):
                continue
            try:
                self._observation_buffers[gid] = ObservationBuffer.from_persist_dict(
                    gid,
                    payload,
                    initial_threshold=self.config.observe_initial_threshold,
                    max_threshold=self.config.observe_max_threshold,
                    min_threshold=self.config.observe_min_threshold,
                    max_buffer_size=self.config.observe_max_buffer_size,
                    max_records_per_group=self.config.observe_max_records,
                    timezone=self.config.timezone,
                )
            except Exception:
                continue

    async def _persist_observation_buffers_to_store(self) -> None:
        if not self.data_store:
            return
        data = {gid: buf.to_persist_dict() for gid, buf in self._observation_buffers.items()}
        await self.data_store.set_setting(
            PERSONA_SK_OBSERVATION_BUFFERS,
            json.dumps(data, ensure_ascii=False),
        )

    async def _maybe_persist_observation_buffers(self, *, force: bool = False) -> None:
        """节流整表 blob 写入；提取观察后应 force=True。"""
        interval = 5.0
        now_m = time.monotonic()
        if (
            not force
            and self._observation_persist_monotonic
            and (now_m - self._observation_persist_monotonic) < interval
        ):
            return
        await self._persist_observation_buffers_to_store()
        self._observation_persist_monotonic = now_m

    async def _handle_group_observation(
        self, group_id: str, user_id: str, nickname: str, msg_str: str
    ) -> None:
        """处理群聊观察"""
        if not self.config.observe_group_enabled or not self.data_store:
            return

        try:
            await self._ensure_observation_buffers_loaded()
            if group_id not in self._observation_buffers:
                self._observation_buffers[group_id] = ObservationBuffer(
                    group_id=group_id,
                    initial_threshold=self.config.observe_initial_threshold,
                    max_threshold=self.config.observe_max_threshold,
                    min_threshold=self.config.observe_min_threshold,
                    max_buffer_size=self.config.observe_max_buffer_size,
                    max_records_per_group=self.config.observe_max_records,
                    timezone=self.config.timezone,
                )

            buffer = self._observation_buffers[group_id]

            should_extract = buffer.add_message(
                user_id=user_id,
                nickname=nickname,
                content=msg_str,
            )

            if should_extract:
                from .proactive.observation_buffer import ObservationExtractor

                messages = buffer.get_messages_for_extraction()

                if self.orchestrator and self.orchestrator.event_agent:
                    extractor = ObservationExtractor(
                        event_agent=self.orchestrator.event_agent,
                        data_store=self.data_store,
                        config=self.config,
                        prune_observations_keep=self.config.observe_max_records,
                    )
                    await extractor.extract_observations(group_id, messages)

                # 观察触发提取时，更新群内容活跃度（减缓衰减）
                if self.config.group_activity_enabled:
                    try:
                        await self.data_store.update_group_content(group_id)
                    except Exception as e:
                        dice_log(f"[Persona] 群内容活跃度更新失败: {e}")

            await self._maybe_persist_observation_buffers(force=should_extract)

        except Exception as e:
            dice_log(f"[Persona] 群聊观察失败: {e}")

    def get_description(self) -> str:
        """获取命令描述"""
        return "Persona AI 对话" if self.enabled else "Persona AI 对话（已禁用）"

    def _proactive_messages_to_commands(self, messages: List[Dict]) -> List[BotCommandBase]:
        cmds: List[BotCommandBase] = []
        for msg in messages:
            user_id = msg.get("user_id", "")
            group_id = msg.get("group_id", "")
            content = msg.get("content", "")

            if group_id:
                port = GroupMessagePort(group_id)
            else:
                port = PrivateMessagePort(user_id)

            cmds.append(BotSendMsgCommand(self.bot.account, content, [port]))
        return cmds

    def tick(self) -> List[BotCommandBase]:
        """
        每秒调用，驱动主动消息调度器。

        主循环在运行中的事件循环里同步调用本方法：通过 create_task 执行异步 tick，
        并在后续每秒收齐已完成任务的结果，避免消息丢失。

        语义为 **at-most-once / 单槽**：同一时刻最多一个未完成的异步 tick；更强投递保证需另行设计（如发件箱）。
        """
        if not self.enabled or not self.orchestrator:
            return []

        try:
            loop = asyncio.get_event_loop()

            async def _run_tick() -> List[BotCommandBase]:
                raw = await self.orchestrator.tick()
                return self._proactive_messages_to_commands(raw)

            out: List[BotCommandBase] = []
            t = self._async_tick_task
            if t is not None and t.done():
                try:
                    if not t.cancelled():
                        out.extend(t.result())
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    dice_log(f"[Persona] tick 异步任务失败: {e}")
                finally:
                    self._async_tick_task = None

            if loop.is_running():
                if self._async_tick_task is None or self._async_tick_task.done():
                    self._async_tick_task = asyncio.create_task(_run_tick())
                return out

            messages = loop.run_until_complete(self.orchestrator.tick())
            return self._proactive_messages_to_commands(messages)
        except Exception as e:
            dice_log(f"[Persona] tick 失败: {e}")
            return []

    def tick_daily(self) -> List[BotCommandBase]:
        """每天调用，生成日记（异步逻辑通过任务队列在运行中的事件循环里执行）。"""
        if not self.enabled or not self.orchestrator:
            return []

        try:
            loop = asyncio.get_event_loop()

            async def _run_daily() -> None:
                diary = await self.orchestrator.tick_daily()
                if diary:
                    dice_log(f"[Persona] 生成日记: {len(diary)} 字")

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

            diary = loop.run_until_complete(self.orchestrator.tick_daily())
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
