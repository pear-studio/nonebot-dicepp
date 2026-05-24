"""队伍指令 .team

跑团群里区分玩家（PC）和观众（OB）。team 内的成员保留原名；team 外
的人会被自动改群名片为 "ob"（前提：骰娘在该群有管理员权限）。

子指令：
  .team set @玩家1 @玩家2 ...   一次添加多名玩家到本群 team
  .team del @玩家                移除玩家
  .team clr                       清空本群 team
  .team show                      列出 team 成员
  .team desc                      列出 team 成员 HP/AC/被动察觉
  .team call [消息]               @ 所有 team 成员

自动改名 OB 触发时机：
  - .team set 时全量刷新一次
  - 新成员进群时单独改一次（在 dicebot.process_notice 里调用
    auto_rename_ob_for_new_member()）

群主、群管理员、骰娘自身永远不会被改名。
"""
import re
from datetime import datetime
from typing import Any, List, Tuple

from core.bot import Bot
from core.command import (
    BotCommandBase,
    BotSendMsgCommand,
    BotSetGroupCardCommand,
    UserCommandBase,
    custom_user_command,
)
from core.command.const import DPP_COMMAND_FLAG_DEFAULT, DPP_COMMAND_PRIORITY_DEFAULT
from core.communication import (
    GroupMessagePort,
    MessageMetaData,
    PrivateMessagePort,
)
from core.data.models import GroupTeam


OB_CARD = "ob"

# preprocess_msg 会把消息转小写，所以匹配时也用小写形式 "[cq:at,qq=NUM]"
# 用 IGNORECASE 兼容 raw_msg（保留原大小写）和 plain_msg（小写）
_AT_RE = re.compile(r'\[cq:at,qq=(\d+)\]', re.IGNORECASE)
_QQ_RE = re.compile(r'(?<!\d)(\d{5,11})(?!\d)')


def _extract_user_ids(text: str) -> List[str]:
    """从消息文本里提取所有 QQ 号（CQ:at 优先；裸数字回退）。去重保序。"""
    seen: List[str] = []
    if not text:
        return seen
    for m in _AT_RE.finditer(text):
        uid = m.group(1)
        if uid not in seen:
            seen.append(uid)
    # 移除 CQ 码部分再扫裸数字，避免重复
    stripped = _AT_RE.sub('', text)
    for m in _QQ_RE.finditer(stripped):
        uid = m.group(1)
        if uid not in seen:
            seen.append(uid)
    return seen


async def _get_team(bot: Bot, group_id: str) -> GroupTeam:
    team = await bot.db.group_team.get(group_id)
    if team is None:
        team = GroupTeam(group_id=group_id, members=[], auto_rename_ob=True)
    return team


async def _save_team(bot: Bot, team: GroupTeam) -> None:
    team.last_update = datetime.now()
    await bot.db.group_team.save(team)


async def _build_rename_ob_commands(bot: Bot, group_id: str,
                                    team_members: List[str]) -> List[BotCommandBase]:
    """对群里非 team、非管理员、非骰娘自身的成员发改名指令（且当前名片不是 ob）。"""
    if bot.proxy is None:
        return []
    try:
        all_members = await bot.proxy.get_group_member_list(group_id)
    except (OSError, RuntimeError, AttributeError):
        return []

    cmds: List[BotCommandBase] = []
    team_set = set(team_members)
    bot_id = bot.account
    for m in all_members:
        uid = str(m.user_id)
        if uid == bot_id:
            continue
        if uid in team_set:
            continue
        if getattr(m, "role", "member") in ("owner", "admin"):
            continue
        cur = (getattr(m, "card", "") or "").strip().lower()
        if cur == OB_CARD:
            continue
        cmds.append(BotSetGroupCardCommand(
            bot_id=bot_id, group_id=group_id, user_id=uid, card=OB_CARD,
        ))
    return cmds


async def auto_rename_ob_for_new_member(bot: Bot, group_id: str,
                                        user_id: str) -> List[BotCommandBase]:
    """新成员进群时调用：若群启用 team 且新成员不在 team 中，改名 ob。

    供 dicebot.process_notice 在 GroupIncreaseNoticeData 分支末尾调用。
    """
    try:
        team = await bot.db.group_team.get(group_id)
    except RuntimeError:
        return []
    if team is None or not team.auto_rename_ob:
        return []
    if user_id == bot.account or user_id in team.members:
        return []
    return [BotSetGroupCardCommand(
        bot_id=bot.account, group_id=group_id, user_id=user_id, card=OB_CARD,
    )]


@custom_user_command(readable_name="队伍指令", priority=DPP_COMMAND_PRIORITY_DEFAULT,
                     flag=DPP_COMMAND_FLAG_DEFAULT, group_only=True)
class TeamCommand(UserCommandBase):
    """跑团群队伍管理 + 自动改名 ob"""

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        if msg_str.startswith(".team"):
            # 用 raw_msg 解析以保留 CQ:at 大小写；hint 传 raw_msg 中 .team 后的部分
            raw_arg = ""
            try:
                idx = meta.raw_msg.lower().find(".team")
                if idx >= 0:
                    raw_arg = meta.raw_msg[idx + 5:].strip()
            except AttributeError:
                raw_arg = msg_str[5:].strip()
            return True, False, raw_arg
        return False, False, None

    async def process_msg(self, msg_str: str, meta: MessageMetaData,
                          hint: Any) -> List[BotCommandBase]:
        port = (GroupMessagePort(meta.group_id)
                if meta.group_id else PrivateMessagePort(meta.user_id))
        if not meta.group_id:
            return [BotSendMsgCommand(self.bot.account, ".team 仅限群聊使用", [port])]

        arg = (hint or "").strip()
        parts = arg.split(None, 1)
        sub = parts[0].lower() if parts else ""
        rest = parts[1] if len(parts) > 1 else ""

        if sub == "set":
            return await self._handle_set(meta, rest, port)
        if sub == "del":
            return await self._handle_del(meta, rest, port)
        if sub in ("clr", "clear"):
            return await self._handle_clr(meta, port)
        if sub == "show":
            return await self._handle_show(meta, port)
        if sub == "desc":
            return await self._handle_desc(meta, port)
        if sub == "call":
            return await self._handle_call(meta, rest, port)
        return [BotSendMsgCommand(self.bot.account, self._help_text(), [port])]

    async def _handle_set(self, meta: MessageMetaData, rest: str,
                          port) -> List[BotCommandBase]:
        ids = _extract_user_ids(rest)
        if not ids:
            return [BotSendMsgCommand(self.bot.account,
                "请 @ 一个或多个玩家。例：.team set @玩家A @玩家B", [port])]
        team = await _get_team(self.bot, meta.group_id)
        added: List[str] = []
        for uid in ids:
            if uid not in team.members:
                team.members.append(uid)
                added.append(uid)
        await _save_team(self.bot, team)

        cmds: List[BotCommandBase] = []
        if team.auto_rename_ob:
            cmds.extend(await _build_rename_ob_commands(self.bot, meta.group_id, team.members))

        if added:
            head = f"已添加 {len(added)} 位玩家（team 当前共 {len(team.members)} 位）：{', '.join(added)}"
        else:
            head = f"team 中已有这 {len(ids)} 位玩家（共 {len(team.members)} 位）"
        if cmds:
            head += f"\n同步改名 {len(cmds)} 位非 team 成员为 ob"
        cmds.insert(0, BotSendMsgCommand(self.bot.account, head, [port]))
        return cmds

    async def _handle_del(self, meta: MessageMetaData, rest: str,
                          port) -> List[BotCommandBase]:
        ids = _extract_user_ids(rest)
        if not ids:
            return [BotSendMsgCommand(self.bot.account, "请 @ 要移除的玩家", [port])]
        team = await _get_team(self.bot, meta.group_id)
        removed: List[str] = []
        for uid in ids:
            if uid in team.members:
                team.members.remove(uid)
                removed.append(uid)
        await _save_team(self.bot, team)
        msg = (f"已从 team 移出 {len(removed)} 位：{', '.join(removed)}（共 {len(team.members)} 位）"
               if removed else "team 中找不到这些玩家")
        return [BotSendMsgCommand(self.bot.account, msg, [port])]

    async def _handle_clr(self, meta: MessageMetaData,
                          port) -> List[BotCommandBase]:
        team = await _get_team(self.bot, meta.group_id)
        n = len(team.members)
        team.members = []
        await _save_team(self.bot, team)
        return [BotSendMsgCommand(self.bot.account,
                                  f"已清空本群 team（原有 {n} 位玩家）", [port])]

    async def _handle_show(self, meta: MessageMetaData,
                           port) -> List[BotCommandBase]:
        team = await _get_team(self.bot, meta.group_id)
        if not team.members:
            return [BotSendMsgCommand(self.bot.account,
                "本群 team 为空。用 .team set @玩家 添加。", [port])]
        lines = [f"本群 team（{len(team.members)} 人）："]
        for uid in team.members:
            try:
                nick = await self.bot.get_nickname(uid, meta.group_id) or uid
            except (RuntimeError, AttributeError):
                nick = uid
            lines.append(f"  · {nick} ({uid})")
        return [BotSendMsgCommand(self.bot.account, "\n".join(lines), [port])]

    async def _handle_desc(self, meta: MessageMetaData,
                           port) -> List[BotCommandBase]:
        team = await _get_team(self.bot, meta.group_id)
        if not team.members:
            return [BotSendMsgCommand(self.bot.account, "本群 team 为空", [port])]
        lines = ["本群 team 状态："]
        for uid in team.members:
            try:
                nick = await self.bot.get_nickname(uid, meta.group_id) or uid
            except (RuntimeError, AttributeError):
                nick = uid
            char = await self.bot.db.characters_dnd.get(meta.group_id, uid)
            if char and char.is_init:
                hp = char.hp_info.hp_cur
                hp_max = char.hp_info.hp_max
                # α 的 DNDCharacter 当前没有 AC 和 被动察觉 字段，按 0 占位（C3 移植角色卡时可补）
                ac = getattr(char, "ac", 0) or 0
                pp = getattr(char, "passive_perception", 0) or 0
                lines.append(f"  · {nick}：HP {hp}/{hp_max} | AC {ac} | 被动察觉 {pp}")
            else:
                lines.append(f"  · {nick}：未设置角色卡")
        return [BotSendMsgCommand(self.bot.account, "\n".join(lines), [port])]

    async def _handle_call(self, meta: MessageMetaData, rest: str,
                           port) -> List[BotCommandBase]:
        team = await _get_team(self.bot, meta.group_id)
        if not team.members:
            return [BotSendMsgCommand(self.bot.account, "本群 team 为空", [port])]
        ats = " ".join(f"[CQ:at,qq={uid}]" for uid in team.members)
        body = rest.strip() if rest else "请集合"
        return [BotSendMsgCommand(self.bot.account, f"{ats}\n{body}", [port])]

    def _help_text(self) -> str:
        return (
            "队伍指令 .team —— 跑团群里管理玩家（PC）/ 观众（OB）：\n"
            "  .team set @玩家1 @玩家2 ...   添加玩家到 team\n"
            "  .team del @玩家                移除\n"
            "  .team clr                       清空\n"
            "  .team show                      列出成员\n"
            "  .team desc                      列出成员 HP/AC/被动察觉\n"
            "  .team call [消息]               @ 所有 team 成员\n"
            "骰娘有群管理员权限时，team 外的人会自动改群名片为 ob"
        )

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        if keyword == "team":
            return self._help_text()
        return ""

    def get_description(self) -> str:
        return ".team 队伍管理 + 观众改名"
