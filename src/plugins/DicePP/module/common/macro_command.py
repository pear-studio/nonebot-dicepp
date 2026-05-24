"""宏指令 (.define)

用户可以自定义文本替换宏，简化常用骰子表达式。基于 α 的 Repository 模式
重写自 β 的 module/common/macro_command.py。

宏定义语法：
  .define [关键字][(参数列表)][空格][目标字符串]

示例：
  .define 一颗D20 .r 1d20
  .define 攻击(目标,武器) .r 1d20+5 攻击{目标}用{武器}

宏的"展开执行"集成在 Bot.process_message 入口，详见
`apply_user_macros()`。第一期实现增删查，展开逻辑预留 hook。
"""
import re
from typing import Any, List, Optional, Tuple

from core.bot import Bot
from core.command import (
    BotCommandBase,
    BotSendMsgCommand,
    UserCommandBase,
    custom_user_command,
)
from core.command.const import (
    DPP_COMMAND_FLAG_DEFAULT,
    DPP_COMMAND_PRIORITY_DEFAULT,
)
from core.communication import (
    GroupMessagePort,
    MessageMetaData,
    PrivateMessagePort,
)
from core.data.models import UserMacro


MACRO_COMMAND_SPLIT = "%%"  # 用户在宏定义里用 %% 表示指令分隔符

LOC_DEFINE_SUCCESS = "define_success"
LOC_DEFINE_FAIL = "define_fail"
LOC_DEFINE_LIST = "define_list"
LOC_DEFINE_INFO = "define_info"
LOC_DEFINE_DEL = "define_delete"

CFG_DEFINE_LEN_MAX = "define_length_max"
CFG_DEFINE_NUM_MAX = "define_number_max"


# ─── 宏解析/编译 helpers ─────────────────────────────────────────────────

def parse_macro_definition(raw: str, command_split: str) -> UserMacro:
    """从 raw 字符串解析出 UserMacro 对象。出错抛 ValueError。"""
    raw = (raw or "").strip()
    if " " not in raw:
        raise ValueError("宏定义中缺少空格，格式应为「关键字 目标字符串」")
    key_args, target = raw.split(" ", 1)
    key_args = key_args.strip()
    target = target.strip()

    if key_args.endswith(")"):
        par_index = key_args.find("(")
        if par_index == -1:
            raise ValueError("参数列表缺少左括号")
        key = key_args[:par_index]
        args = [a for a in key_args[par_index + 1:-1].split(",") if a.strip()]
    else:
        key = key_args
        args = []

    if not key:
        raise ValueError("宏关键字不能为空")
    if key == "all" or key == "del":
        raise ValueError(f"「{key}」是保留关键字")

    # 把 args 出现的地方替换为 {arg} 占位，方便后续 str.format
    transformed = target
    for arg in args:
        transformed = transformed.replace(arg, "{" + arg + "}")
    transformed = transformed.replace(MACRO_COMMAND_SPLIT, command_split)

    return UserMacro(
        user_id="",  # 由调用方填充
        key=key,
        raw=raw,
        args=args,
        target=transformed,
        command_split=command_split,
    )


def apply_macro_once(macro, text: str) -> str:
    """对 text 应用一次宏替换。命中则返回展开后的字符串，否则返回原文。

    接受 UserMacro 或 GroupMacro（鸭子类型 — 都有 key/args/target 字段）。
    """
    pattern_str = ":".join([re.escape(macro.key)] + ["(.*)"] * len(macro.args))
    try:
        pattern = re.compile(pattern_str)
    except re.error:
        return text

    def repl(m: re.Match) -> str:
        if not macro.args:
            return macro.target
        kwargs = {a: m.group(i + 1) for i, a in enumerate(macro.args)}
        try:
            return macro.target.format(**kwargs)
        except (KeyError, IndexError):
            return m.group(0)

    return pattern.sub(repl, text)


def _apply_macro_list(macros: list, text: str,
                      max_passes: int, max_length: int) -> str:
    if not macros:
        return text
    out = text
    for _ in range(max_passes):
        prev = out
        for m in macros:
            out = apply_macro_once(m, out)
            if len(out) > max_length:
                return prev
        if out == prev:
            break
    return out


async def apply_user_macros(bot: Bot, user_id: str, text: str,
                            max_passes: int = 3, max_length: int = 500) -> str:
    """对消息文本应用某用户的所有宏（UserMacro，按 user_id 隔离）。

    bot.db.macro 在 db 未 connect 时会 raise RuntimeError，调用方需自行兜底。
    """
    if not user_id or not text:
        return text
    macros: List[UserMacro] = await bot.db.macro.list_by(user_id=user_id)
    return _apply_macro_list(macros, text, max_passes, max_length)


async def apply_group_macros(bot: Bot, group_id: str, text: str,
                             max_passes: int = 3, max_length: int = 500) -> str:
    """对消息文本应用某群的所有群级宏（GroupMacro）。

    bot.db.group_macro 在 db 未 connect 时会 raise RuntimeError，调用方需自行兜底。
    """
    if not group_id or not text:
        return text
    macros = await bot.db.group_macro.list_by(group_id=group_id)
    return _apply_macro_list(macros, text, max_passes, max_length)


async def apply_user_and_group_macros(bot: Bot, user_id: str,
                                      group_id: str, text: str) -> str:
    """先群宏后用户宏的统一入口。

    群宏先于用户宏执行（让群宏铺路，用户宏在此基础上进一步定制）。
    集成在 Bot.process_message — preprocess_msg 之后、命令分发之前。
    """
    text = await apply_group_macros(bot, group_id, text)
    text = await apply_user_macros(bot, user_id, text)
    return text


# ─── 命令实现 ───────────────────────────────────────────────────────────

@custom_user_command(readable_name="宏指令", priority=DPP_COMMAND_PRIORITY_DEFAULT,
                     flag=DPP_COMMAND_FLAG_DEFAULT)
class MacroCommand(UserCommandBase):
    """.define 系列：定义、查看、删除用户的宏"""

    def __init__(self, bot: Bot):
        super().__init__(bot)
        bot.loc_helper.register_loc_text(LOC_DEFINE_SUCCESS,
            "已定义宏 {macro}（参数：{args}）-> {target}",
            "成功定义宏的回复")
        bot.loc_helper.register_loc_text(LOC_DEFINE_FAIL,
            "定义失败：{error}",
            "定义宏失败的回复")
        bot.loc_helper.register_loc_text(LOC_DEFINE_LIST,
            "你的宏列表：\n{macro_list}",
            "查看宏列表的回复")
        bot.loc_helper.register_loc_text(LOC_DEFINE_INFO,
            "{macro}（参数：{args}）-> {target}",
            "宏列表中单个宏的展示")
        bot.loc_helper.register_loc_text(LOC_DEFINE_DEL,
            "已删除宏 {macro}",
            "删除宏的回复")

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        should_proc = msg_str.startswith(".define")
        should_pass = False
        hint = msg_str[7:].strip() if should_proc else None
        return should_proc, should_pass, hint

    async def process_msg(self, msg_str: str, meta: MessageMetaData,
                          hint: Any) -> List[BotCommandBase]:
        port = (GroupMessagePort(meta.group_id)
                if meta.group_id else PrivateMessagePort(meta.user_id))
        arg_str: str = hint or ""

        feedback = ""
        macros = await self.bot.db.macro.list_by(user_id=meta.user_id)

        if not arg_str:
            # 查看
            if not macros:
                feedback = "你还没有定义任何宏。使用 .define 关键字 目标 来定义一个。"
            else:
                lines = []
                for i, m in enumerate(macros):
                    info = self.format_loc(LOC_DEFINE_INFO, macro=m.key,
                                           args=",".join(m.args) or "无", target=m.target)
                    lines.append(f"{i + 1}. {info}")
                feedback = self.format_loc(LOC_DEFINE_LIST, macro_list="\n".join(lines))
        elif arg_str.startswith("del"):
            # 删除
            key = arg_str[3:].strip()
            if key == "all":
                for m in macros:
                    await self.bot.db.macro.delete(m.user_id, m.key)
                feedback = self.format_loc(LOC_DEFINE_DEL,
                                           macro=",".join(m.key for m in macros) or "(空)")
            elif key:
                found = next((m for m in macros if m.key == key), None)
                if found:
                    await self.bot.db.macro.delete(found.user_id, found.key)
                    feedback = self.format_loc(LOC_DEFINE_DEL, macro=key)
                else:
                    feedback = self.format_loc(LOC_DEFINE_FAIL, error=f"找不到关键字为 {key} 的宏")
            else:
                feedback = self.format_loc(LOC_DEFINE_FAIL, error="del 后请跟宏关键字或 all")
        else:
            # 定义
            split_token = ";"
            try:
                split_token = self.bot.config.command_split
            except AttributeError:
                pass
            try:
                length_max = 300
                num_max = 50
                if len(arg_str) > length_max:
                    raise ValueError(f"宏定义长度超过 {length_max} 字符")
                if len(macros) >= num_max:
                    raise ValueError(f"宏数量已达上限 {num_max}，请先删除一些")
                new_macro = parse_macro_definition(arg_str, split_token)
                new_macro.user_id = meta.user_id
                # 同名直接覆盖
                await self.bot.db.macro.save(new_macro)
                feedback = self.format_loc(LOC_DEFINE_SUCCESS, macro=new_macro.key,
                                           args=",".join(new_macro.args) or "无",
                                           target=new_macro.target)
            except ValueError as e:
                feedback = self.format_loc(LOC_DEFINE_FAIL, error=str(e))

        return [BotSendMsgCommand(self.bot.account, feedback, [port])]

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        if keyword == "define":
            return (
                "宏指令 .define 用于自定义文本替换：\n"
                "  定义：.define 关键字[(参数1,参数2)] 目标字符串\n"
                "  查看：.define\n"
                "  删除：.define del 关键字  或  .define del all\n"
                "\n示例：\n"
                "  .define 一颗D20 .r 1d20\n"
                "  .define 攻击(目标,武器) .r 1d20+5 攻击{目标}用{武器}\n"
                f"\n用 {MACRO_COMMAND_SPLIT} 表示指令分隔符。"
            )
        return ""

    def get_description(self) -> str:
        return ".define 定义自定义宏"
