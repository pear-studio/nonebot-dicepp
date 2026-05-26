"""COC 7th 属性投骰指令 .coc

输出 9 项基础属性 (STR/CON/SIZ/DEX/APP/INT/POW/EDU/LUCK) 加 5 项衍生
属性 (HP/MP/DB/Build/MOV)，方便玩家直接生成 COC7 调查员。

不做完整角色卡（按 maintainer 决策：β 的 COC 模块实际是 dnd5e 复制
粘贴，没真正实现 COC7，α 暂不补完整规则；.coc 只投属性）。
"""
import random
from typing import Any, List, Tuple

from core.bot import Bot
from core.command.const import (
    DPP_COMMAND_FLAG_DND,
    DPP_COMMAND_FLAG_FUN,
    DPP_COMMAND_PRIORITY_DEFAULT,
)
from core.command import (
    BotCommandBase,
    BotSendMsgCommand,
    UserCommandBase,
    custom_user_command,
)
from core.communication import (
    GroupMessagePort,
    MessageMetaData,
    PrivateMessagePort,
)


LOC_COC_RES = "coc_result"
LOC_COC_RES_NOREASON = "coc_result_noreason"

MAX_COC_TIMES = 10
MAX_COC_REASON_LEN = 50

# COC7 属性下标
_STR, _CON, _SIZ, _DEX, _APP, _INT, _POW, _EDU, _LUCK = range(9)
_ATTR_NAMES = ["力量", "体质", "体型", "敏捷", "外貌", "智力", "意志", "教育", "幸运"]


def _roll_attrs() -> List[int]:
    """生成一组 COC7 基础属性（9 项）"""
    def d6(n: int) -> int:
        return sum(random.randint(1, 6) for _ in range(n))

    return [
        d6(3) * 5,         # 力量 STR
        d6(3) * 5,         # 体质 CON
        (d6(2) + 6) * 5,   # 体型 SIZ
        d6(3) * 5,         # 敏捷 DEX
        d6(3) * 5,         # 外貌 APP
        (d6(2) + 6) * 5,   # 智力 INT
        d6(3) * 5,         # 意志 POW
        (d6(2) + 6) * 5,   # 教育 EDU
        d6(3) * 5,         # 幸运 LUCK
    ]


# DB/Build 表（基于 STR + SIZ 总和）— COC7 核心规则
_DB_TABLE = [
    (64,  "-2",   -2),
    (84,  "-1",   -1),
    (124, "0",     0),
    (164, "+1d4", +1),
    (204, "+1d6", +2),
    (284, "+2d6", +3),
    (364, "+3d6", +4),
    (444, "+4d6", +5),
    (524, "+5d6", +6),
]


def _derive_db_build(str_val: int, siz_val: int) -> Tuple[str, int]:
    """COC7 伤害加值 + 体格。表内查；525+ 时按规则每 80 +1d6/+1。"""
    total = str_val + siz_val
    for upper, db, build in _DB_TABLE:
        if total <= upper:
            return db, build
    # 525+：表外延伸。524 对应 +5d6/+6，525 起进入 +6d6/+7 这一档，
    # 每多 80 再加 1d6/+1。
    # extra=0 → 525~604 → +6d6/+7
    # extra=1 → 605~684 → +7d6/+8
    extra = (total - 525) // 80
    db = f"+{6 + extra}d6"
    build = 7 + extra
    return db, build


def _derive_mov(str_val: int, dex_val: int, siz_val: int) -> int:
    """成人 MOV，简化版（不考虑年龄修正，.coc 没有年龄输入）。

    COC7 规则书 (核心 33 页)：
      - 若 STR 与 DEX **均 < SIZ**           → MOV 7
      - 若 STR 与 DEX **均 ≥ SIZ**           → MOV 9
        （正好等于的边界 case 也算，只要 ≥；其中至少一个严格 > 才不退化）
      - 其余（STR/DEX 一高一低于 SIZ）        → MOV 8

    旧实现错误地要求两者都严格 `>`，把例如 STR=DEX=SIZ 这种平衡角色
    意外降到 MOV=8。修复后例：STR=70 DEX=65 SIZ=65 返回 9。
    """
    if str_val < siz_val and dex_val < siz_val:
        return 7
    if (str_val >= siz_val and dex_val >= siz_val
            and (str_val > siz_val or dex_val > siz_val)):
        return 9
    # 其他：STR/DEX 一边高一边低 → MOV 8
    return 8


def _format_one(attrs: List[int]) -> str:
    """渲染一组属性 + 衍生属性"""
    base_line = "[" + " ".join(
        f"{_ATTR_NAMES[i]}{attrs[i]}" for i in range(9)
    ) + "]"

    hp = (attrs[_CON] + attrs[_SIZ]) // 10
    mp = attrs[_POW] // 5
    db, build = _derive_db_build(attrs[_STR], attrs[_SIZ])
    mov = _derive_mov(attrs[_STR], attrs[_DEX], attrs[_SIZ])

    derived = f"HP {hp} | MP {mp} | DB {db} | 体格 {build} | MOV {mov}"
    total_no_luck = sum(attrs[:8])
    total_all = sum(attrs)
    return f"{base_line}\n  → {derived}\n  → 合计 {total_no_luck}（不含幸运 {attrs[_LUCK]}） / 总和 {total_all}"


@custom_user_command(readable_name="COC属性指令",
                     priority=DPP_COMMAND_PRIORITY_DEFAULT,
                     flag=DPP_COMMAND_FLAG_FUN | DPP_COMMAND_FLAG_DND)
class UtilsCOCCommand(UserCommandBase):
    """.coc 指令：生成 COC 7th 调查员属性（含衍生 HP/MP/DB/Build/MOV）。

    用法：
        .coc           生成一组
        .coc 3         生成 3 组
        .coc 2 测试    生成 2 组并标注原因
    """

    def __init__(self, bot: Bot):
        super().__init__(bot)
        bot.loc_helper.register_loc_text(
            LOC_COC_RES,
            "{name} COC人物作成——{reason}:\n{result}",
            ".coc 返回的内容 name 为用户昵称, reason 为原因",
        )
        bot.loc_helper.register_loc_text(
            LOC_COC_RES_NOREASON,
            "{name} COC人物作成:\n{result}",
            ".coc 返回的内容（无原因） name 为用户昵称",
        )

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        if not msg_str.startswith(".coc"):
            return False, False, None
        rest = msg_str[4:].strip()
        args = rest.split(" ", 1)
        reason = args[1].strip()[:MAX_COC_REASON_LEN] if len(args) > 1 else ""
        try:
            times = int(args[0])
            if not (1 <= times <= MAX_COC_TIMES):
                raise ValueError
        except (ValueError, IndexError):
            times = 1
        return True, False, (times, reason)

    async def process_msg(self, msg_str: str, meta: MessageMetaData,
                          hint: Any) -> List[BotCommandBase]:
        port = (GroupMessagePort(meta.group_id)
                if meta.group_id else PrivateMessagePort(meta.user_id))
        times, reason = hint

        groups = [_format_one(_roll_attrs()) for _ in range(times)]
        result = "\n\n".join(groups) if times > 1 else groups[0]

        user_name = await self.bot.get_nickname(meta.user_id, meta.group_id)
        if reason:
            feedback = self.format_loc(LOC_COC_RES, name=user_name,
                                       reason=reason, result=result)
        else:
            feedback = self.format_loc(LOC_COC_RES_NOREASON, name=user_name,
                                       result=result)
        return [BotSendMsgCommand(self.bot.account, feedback, [port])]

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        if keyword == "coc":
            return (
                ".coc 生成 COC 7th 调查员属性（9 项基础 + HP/MP/DB/体格/MOV 衍生）\n"
                "  .coc           生成一组\n"
                "  .coc 3         生成 3 组\n"
                "  .coc 2 测试    生成 2 组并标注原因"
            )
        return ""

    def get_description(self) -> str:
        return ".coc COC 7th 调查员属性生成（含衍生属性）"
