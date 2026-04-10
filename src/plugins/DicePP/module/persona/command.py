"""
Persona AI 命令入口

集成 orchestrator 完成对话功能
支持白名单访问控制
"""
from typing import List, Dict, Tuple, Any
import time

from core.bot import Bot
from core.command.user_cmd import UserCommandBase, custom_user_command
from core.command.bot_cmd import BotSendMsgCommand, BotCommandBase
from core.communication import PrivateMessagePort, GroupMessagePort, MessageMetaData
from core.command.const import DPP_COMMAND_PRIORITY_DEFAULT, DPP_COMMAND_FLAG_FUN
from utils.logger import dice_log

from .orchestrator import PersonaOrchestrator
from .data.store import PersonaDataStore
from .proactive.observation_buffer import ObservationBuffer


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

    def delay_init(self) -> List[str]:
        """延迟初始化"""
        config = self.bot.config.persona_ai
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
                dice_log(f"[Persona] 模块初始化成功: {config.character_name}")
            else:
                dice_log(f"[Persona] 模块初始化失败")
                self.enabled = False
            return []
        
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
        # 有效的 "." 前缀命令: .ai, .pa
        if msg.startswith(".") and not msg.startswith(".ai") and not msg.startswith(".pa"):
            return False, False, None
        if msg.startswith("。") and not msg.startswith("。ai") and not msg.startswith("。pa"):
            return False, False, None

        # @bot 或 .ai 前缀触发
        if not meta.to_me and not msg.startswith(".ai") and not msg.startswith("。ai"):
            # 群聊旁听模式（观察但不回复）
            if meta.group_id and self.config.observe_group_enabled:
                return True, False, "observe"
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

        # .pa 调试命令：仅管理员
        if msg.startswith(".pa "):
            if self._is_admin(meta.user_id):
                return True, False, "debug"
            else:
                return False, False, None

        # 不调用 LLM 的工具类命令：无需白名单
        if cmd in ("ping", "clear", "status", "profile") or cmd == "":
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

        # 处理群聊旁听（观察模式）
        if hint == "observe" and group_id:
            await self._handle_group_observation(group_id, user_id, nickname, msg_str)
            return []

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
            response = await self._handle_admin(user_id, args)
            port = GroupMessagePort(group_id) if group_id else PrivateMessagePort(user_id)
            return [BotSendMsgCommand(self.bot.account, response, [port])]

        # 处理 debug 命令 (.pa / 。pa)
        if hint == "debug" or msg.startswith(".pa ") or msg.startswith("。pa "):
            response = await self._handle_debug(user_id, group_id, msg)
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
        elif not content:
            if is_at_trigger:
                response = await self._get_status(user_id, group_id, is_private)
            else:
                response = self._get_introduction()
        elif is_at_trigger:
            if self.orchestrator and self.enabled:
                response = await self.orchestrator.chat(
                    user_id=user_id,
                    group_id=group_id,
                    message=content,
                    nickname=nickname,
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
                # 活跃度更新失败不影响主流程
                pass

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

    async def _handle_admin(self, user_id: str, args: List[str]) -> str:
        """处理 admin 命令（管理员功能）"""
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
                ".ai admin whitelist clear - 清空白名单"
            )
        
        subcmd = args[0]
        
        # code 子命令
        if subcmd == "code":
            if len(args) < 2:
                current_code = await self.data_store.get_setting("code")
                if current_code:
                    return f"当前已设置口令（{len(current_code)}位字符）"
                else:
                    return "当前未设置口令，白名单功能未激活"
            
            if args[1] == "clear":
                await self.data_store.delete_setting("code")
                return "口令已清除，白名单功能已停用"
            
            # 设置新口令
            new_code = args[1]
            await self.data_store.set_setting("code", new_code)
            return "已更新，白名单功能已激活"
        
        # whitelist 子命令
        if subcmd == "whitelist":
            if len(args) < 2:
                # 查看白名单
                entries = await self.data_store.list_whitelist()
                if not entries:
                    return "白名单为空"
                
                lines = ["白名单列表:"]
                users = [e for e in entries if e.type == "user"]
                groups = [e for e in entries if e.type == "group"]
                
                if users:
                    lines.append(f"\n用户 ({len(users)}个):")
                    for e in users[:10]:  # 最多显示10个
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
            
            # add group
            if action == "add" and len(args) >= 3 and args[2] == "group":
                group_id = args[3] if len(args) > 3 else ""
                if not group_id:
                    return "请提供群ID"
                await self.data_store.add_group_to_whitelist(group_id)
                return f"已添加群 {group_id} 到白名单"
            
            # remove
            if action == "remove":
                if len(args) >= 3 and args[2] == "group":
                    group_id = args[3] if len(args) > 3 else ""
                    if not group_id:
                        return "请提供群ID"
                    await self.data_store.remove_from_whitelist(group_id, "group")
                    return f"已移除群 {group_id}"
                else:
                    target_id = args[2] if len(args) > 2 else ""
                    if not target_id:
                        return "请提供用户ID"
                    await self.data_store.remove_from_whitelist(target_id, "user")
                    return f"已移除用户 {target_id}"
            
            # clear
            if action == "clear":
                self._whitelist_confirm_pending[user_id] = time.monotonic()
                return "确认清空？60秒内发 `.ai admin whitelist confirm` 执行"
            
            # confirm
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

    async def _handle_profile(self, user_id: str, group_id: str) -> str:
        if not self.data_store:
            return "模块未初始化"

        profile = await self.data_store.get_user_profile(user_id)
        rel = await self.data_store.get_relationship(user_id, group_id)

        lines = ["你的档案"]

        if rel:
            lines.append(f"\n好感度:")
            lines.append(f"  亲密度: {rel.intimacy:.1f}")
            lines.append(f"  激情: {rel.passion:.1f}")
            lines.append(f"  信任: {rel.trust:.1f}")
            lines.append(f"  安全感: {rel.secureness:.1f}")
            lines.append(f"  综合: {rel.composite_score:.1f}")
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
                lines.append(".pa debug - 调试信息")
                lines.append(".pa rel <用户ID> - 查看关系")
                lines.append(".pa setrel <用户ID> <分数> - 修改好感度")
                lines.append(".pa reload - 热重载角色卡")
                lines.append(".pa events - 事件配置")
                lines.append(".pa today/yesterday - 查看今天/昨天的事件和日记")
                lines.append(".pa pause/resume - 暂停/恢复主动消息")
            return "\n".join(lines)
        return ""

    async def _handle_debug(self, user_id: str, group_id: str, msg: str) -> str:
        """处理调试命令 (.pa / 。pa) - 仅管理员"""
        if not self._is_admin(user_id):
            return "权限不足"

        if not self.data_store:
            return "模块未初始化"

        # 去掉前缀 (.pa 或 。pa)
        if msg.startswith(".pa"):
            content = msg[3:].strip()
        elif msg.startswith("。pa"):
            content = msg[3:].strip()
        else:
            content = msg

        parts = content.split()
        cmd = parts[0] if parts else ""
        args = parts[1:] if len(parts) > 1 else []

        # .pa debug - 查看当前上下文信息
        if cmd == "debug" or cmd == "":
            lines = ["=== Persona AI 调试信息 ==="]

            # 当前用户信息
            profile = await self.data_store.get_user_profile(user_id)
            rel = await self.data_store.get_relationship(user_id, group_id)

            lines.append(f"\n当前用户: {user_id}")
            if group_id:
                lines.append(f"当前群组: {group_id}")

            # 好感度信息
            if rel:
                lines.append(f"\n[好感度]")
                lines.append(f"  亲密度: {rel.intimacy:.2f}")
                lines.append(f"  激情: {rel.passion:.2f}")
                lines.append(f"  信任: {rel.trust:.2f}")
                lines.append(f"  安全感: {rel.secureness:.2f}")
                lines.append(f"  综合: {rel.composite_score:.2f}")
                lines.append(f"  最后互动: {rel.last_interaction_at.strftime('%Y-%m-%d %H:%M') if rel.last_interaction_at else '无'}")
            else:
                lines.append(f"\n[好感度] 暂无记录")

            # 用户信息
            if profile and profile.facts:
                lines.append(f"\n[用户画像]")
                for k, v in list(profile.facts.items())[:5]:  # 最多显示5条
                    lines.append(f"  {k}: {v}")
                if len(profile.facts) > 5:
                    lines.append(f"  ... 还有 {len(profile.facts) - 5} 条")
            else:
                lines.append(f"\n[用户画像] 暂无")

            # 模块配置
            config = self.bot.config.persona_ai
            lines.append(f"\n[配置]")
            lines.append(f"  角色: {config.character_name}")
            lines.append(f"  日限: {config.daily_limit} 次")
            lines.append(f"  群聊: {'开启' if config.group_chat_enabled else '关闭'}")

            # Phase 2 新增调试信息
            lines.append(f"\n[Phase 2 系统]")
            lines.append(f"  衰减: {'开启' if config.decay_enabled else '关闭'}")
            lines.append(f"  生活模拟: {'开启' if config.character_life_enabled else '关闭'}")
            lines.append(f"  主动消息: {'开启' if config.proactive_enabled else '关闭'}")
            lines.append(f"  群聊观察: {'开启' if config.observe_group_enabled else '关闭'}")
            lines.append(f"  群活跃度: {'开启' if config.group_activity_enabled else '关闭'}")

            # 衰减详情
            if config.decay_enabled:
                lines.append(f"\n[衰减配置]")
                lines.append(f"  免衰减期: {config.decay_grace_period_hours}h")
                lines.append(f"  衰减率: {config.decay_rate_per_hour}/h")
                lines.append(f"  每日上限: {config.decay_daily_cap}")

            # 调度器状态
            if self.orchestrator and self.orchestrator.scheduler:
                scheduler_status = self.orchestrator.scheduler.get_status()
                lines.append(f"\n[调度器状态]")
                lines.append(f"  待分享: {scheduler_status.get('pending_shares', 0)}")
                lines.append(f"  今日触发: {len(scheduler_status.get('scheduled_today', []))}")
                lines.append(f"  安静时段: {'是' if scheduler_status.get('is_quiet_hours') else '否'}")

            # 群活跃度（如果在群聊中）
            if group_id and config.group_activity_enabled:
                try:
                    activity = await self.data_store.get_group_activity(group_id)
                    lines.append(f"\n[群活跃度]")
                    lines.append(f"  分数: {activity.score:.1f}")
                    lines.append(f"  最后互动: {activity.last_interaction_at.strftime('%Y-%m-%d %H:%M') if activity.last_interaction_at else '无'}")
                except Exception:
                    pass

            # 观察缓冲状态（如果在群聊中）
            if group_id and group_id in self._observation_buffers:
                buffer = self._observation_buffers[group_id]
                status = buffer.get_status()
                lines.append(f"\n[观察缓冲]")
                lines.append(f"  缓冲消息: {status.get('buffer_size', 0)}")
                lines.append(f"  当前阈值: {status.get('threshold', 0)}")

            return "\n".join(lines)

        # .pa rel <用户ID> - 查看指定用户的关系
        if cmd == "rel":
            if not args:
                return "用法: .pa rel <用户ID> [群组ID]"

            target_user = args[0]
            target_group = args[1] if len(args) > 1 else group_id

            rel = await self.data_store.get_relationship(target_user, target_group)
            profile = await self.data_store.get_user_profile(target_user)

            lines = [f"=== 用户 {target_user} 的关系详情 ==="]
            if target_group:
                lines.append(f"群组: {target_group}")

            if rel:
                lines.append(f"\n[好感度]")
                lines.append(f"  亲密度: {rel.intimacy:.2f}")
                lines.append(f"  激情: {rel.passion:.2f}")
                lines.append(f"  信任: {rel.trust:.2f}")
                lines.append(f"  安全感: {rel.secureness:.2f}")
                lines.append(f"  综合: {rel.composite_score:.2f}")
                lines.append(f"  互动次数: {rel.interaction_count}")
                lines.append(f"  最后互动: {rel.last_interaction_at.strftime('%Y-%m-%d %H:%M') if rel.last_interaction_at else '无'}")

                # 好感度等级
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

        # .pa setrel <用户ID> <分数> - 修改好感度
        if cmd == "setrel":
            if len(args) < 2:
                return "用法: .pa setrel <用户ID> <综合分数> [群组ID]"

            target_user = args[0]
            try:
                new_score = float(args[1])
            except ValueError:
                return "分数必须是数字"

            if new_score < 0 or new_score > 100:
                return "分数必须在 0-100 之间"

            target_group = args[2] if len(args) > 2 else group_id

            # 获取或创建关系
            rel = await self.data_store.get_relationship(target_user, target_group)
            if not rel:
                # 创建新关系，使用默认初始值
                initial = self.orchestrator.character.extensions.initial_relationship if self.orchestrator and self.orchestrator.character else 30.0
                rel = await self.data_store.init_relationship(target_user, target_group, initial)

            # 设置所有维度为相同值以达到目标综合分
            rel.intimacy = new_score
            rel.passion = new_score
            rel.trust = new_score
            rel.secureness = new_score

            await self.data_store.update_relationship(rel)

            return f"已设置用户 {target_user} 的好感度为 {new_score:.2f}"

        # .pa reload - 热重载角色卡
        if cmd == "reload":
            if not self.orchestrator:
                return "模块未初始化"

            success, msg = await self.orchestrator.reload_character()
            return f"重载{'成功' if success else '失败'}: {msg}"

        # .pa events - 查看事件配置
        if cmd == "events":
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

            # 世界观
            if ext.world:
                lines.append(f"\n[世界观]")
                lines.append(f"  {ext.world}")

            # 好感度标签
            labels = char.get_warmth_labels()
            lines.append(f"\n[好感度等级]")
            for i, label in enumerate(labels):
                lines.append(f"  {i*10}-{(i+1)*10}: {label}")

            return "\n".join(lines)

        # .pa list - 列出白名单/用户
        if cmd == "list":
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

        # .pa today / .pa yesterday - 查看今天/昨天的事件和日记
        if cmd in ["today", "yesterday", "diary"]:
            from datetime import datetime, timedelta

            if cmd == "yesterday":
                date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
                date_label = "昨天"
            else:
                date = datetime.now().strftime("%Y-%m-%d")
                date_label = "今天"

            # 获取日记
            diary = await self.data_store.get_diary(date)

            # 获取事件
            events = await self.data_store.get_daily_events(date)

            lines = [f"=== {date_label} ({date}) ==="]

            # 日记
            if diary:
                lines.append(f"\n[日记]")
                lines.append(diary)
            else:
                lines.append(f"\n[日记] 暂无")

            # 事件
            if events:
                lines.append(f"\n[事件] ({len(events)} 个)")
                for i, evt in enumerate(events[:10], 1):
                    lines.append(f"  {i}. [{evt.event_type}] {evt.description}")
                    if evt.reaction:
                        lines.append(f"     反应: {evt.reaction}")
            else:
                lines.append(f"\n[事件] 暂无")

            return "\n".join(lines)

        # .pa pause - 暂停主动消息
        if cmd == "pause":
            if self.orchestrator and self.orchestrator.scheduler:
                self.orchestrator.scheduler.config.enabled = False
                return "已暂停主动消息发送"
            return "调度器未初始化"

        # .pa resume - 恢复主动消息
        if cmd == "resume":
            if self.orchestrator and self.orchestrator.scheduler:
                self.orchestrator.scheduler.config.enabled = True
                return "已恢复主动消息发送"
            return "调度器未初始化"

        return (
            "=== Persona AI 调试命令 ===\n"
            ".pa debug - 查看当前上下文\n"
            ".pa rel <用户ID> [群组ID] - 查看指定用户关系\n"
            ".pa setrel <用户ID> <分数> [群组ID] - 修改好感度\n"
            ".pa reload - 热重载角色卡\n"
            ".pa events - 查看事件配置\n"
            ".pa list - 查看白名单\n"
            ".pa today - 查看今天的事件和日记\n"
            ".pa yesterday - 查看昨天的事件和日记\n"
            ".pa pause - 暂停主动消息\n"
            ".pa resume - 恢复主动消息"
        )

    async def _handle_group_observation(
        self, group_id: str, user_id: str, nickname: str, msg_str: str
    ) -> None:
        """处理群聊观察"""
        if not self.config.observe_group_enabled or not self.data_store:
            return

        try:
            # 获取或创建缓冲区
            if group_id not in self._observation_buffers:
                self._observation_buffers[group_id] = ObservationBuffer(
                    group_id=group_id,
                    initial_threshold=self.config.observe_initial_threshold,
                    max_threshold=self.config.observe_max_threshold,
                    min_threshold=self.config.observe_min_threshold,
                    max_records_per_group=self.config.observe_max_records,
                )

            buffer = self._observation_buffers[group_id]

            # 添加消息到缓冲
            should_extract = buffer.add_message(
                user_id=user_id,
                nickname=nickname,
                content=msg_str,
            )

            # 触发提取
            if should_extract:
                from .proactive.observation_buffer import ObservationExtractor

                messages = buffer.get_messages_for_extraction()

                # 如果有 event_agent，进行提取
                if self.orchestrator and self.orchestrator.event_agent:
                    extractor = ObservationExtractor(
                        event_agent=self.orchestrator.event_agent,
                        data_store=self.data_store,
                    )
                    await extractor.extract_observations(group_id, messages)

        except Exception as e:
            # 观察失败不影响主流程
            dice_log(f"[Persona] 群聊观察失败: {e}")

    def get_description(self) -> str:
        """获取命令描述"""
        return "Persona AI 对话" if self.enabled else "Persona AI 对话（已禁用）"

    def tick(self) -> List[BotCommandBase]:
        """
        每秒调用，驱动主动消息调度器

        TODO: 当前实现存在同步/异步混合问题。当事件循环已在运行时，
        使用 asyncio.create_task() 不等待结果，可能导致消息丢失。
        长期解决方案：考虑将框架的 tick() 改为 async def，或使用消息队列模式。
        参见: review-250410-persona-phase2.md 设计质量部分
        """
        if not self.enabled or not self.orchestrator:
            return []

        # 使用 asyncio 运行异步 tick
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            # 检查是否已经在事件循环中
            # 注意：这是妥协方案。当 loop 在运行时，create_task 不等待结果，
            # 可能导致本次 tick 的消息丢失。但避免了 RuntimeError。
            if loop.is_running():
                # 如果在运行中，创建新任务（不等待结果）
                asyncio.create_task(self.orchestrator.tick())
                return []
            # loop 不在运行时，可以安全地使用 run_until_complete
            messages = loop.run_until_complete(self.orchestrator.tick())

            # 转换为 BotSendMsgCommand
            cmds = []
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
        except Exception as e:
            dice_log(f"[Persona] tick 失败: {e}")
            return []

    def tick_daily(self) -> List[BotCommandBase]:
        """
        每天调用，生成日记

        TODO: 与 tick() 方法相同的同步/异步混合问题。参见 tick() 文档。
        """
        if not self.enabled or not self.orchestrator:
            return []

        import asyncio
        try:
            loop = asyncio.get_event_loop()
            # 检查是否已经在事件循环中
            # 注意：这是妥协方案。当 loop 在运行时，create_task 不等待结果。
            if loop.is_running():
                asyncio.create_task(self.orchestrator.tick_daily())
                return []
            # loop 不在运行时，可以安全地使用 run_until_complete
            diary = loop.run_until_complete(self.orchestrator.tick_daily())

            if diary:
                dice_log(f"[Persona] 生成日记: {len(diary)} 字")

            return []
        except Exception as e:
            dice_log(f"[Persona] tick_daily 失败: {e}")
            return []
