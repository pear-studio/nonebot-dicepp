"""变量指令 (.set / .get / .del)

用户在群内定义临时变量，可用于辅助跑团。基于 α 已有的 UserVariable
+ variable Repository。重写自 β 的 module/common/variable_command.py。

支持：
  .set 变量名 = 数值或骰子表达式
  .set 变量名 + 5      累加
  .set 变量名 - 3      累减
  .get 变量名          查看
  .get                 查看所有
  .del 变量名          删除
  .del all             清空全部
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
from core.command.const import DPP_COMMAND_FLAG_DEFAULT
from core.communication import (
    GroupMessagePort,
    MessageMetaData,
    PrivateMessagePort,
)
from core.data.models import UserVariable

try:
    from module.roll import exec_roll_exp, RollDiceError  # type: ignore
except ImportError:  # pragma: no cover
    exec_roll_exp = None  # type: ignore
    RollDiceError = Exception  # type: ignore


LOC_VAR_SET = "var_set"
LOC_VAR_GET = "var_get"
LOC_VAR_GET_ALL = "var_get_all"
LOC_VAR_DEL = "var_del"
LOC_VAR_ERROR = "var_error"

_VAR_NAME_RE = re.compile(r"^[A-Za-z_一-鿿][A-Za-z0-9_一-鿿]*$")

# 严格匹配 .set / .get / .del 必须后跟空白或行尾，避免误判 .search / .delete 等
# 例如 ".set"、".set x = 1" 命中，".search foo"、".delete bar" 不命中
_VAR_CMD_RE = re.compile(r"^\.(set|get|del)(?:\s+(.*))?$")


def _validate_var_name(name: str) -> None:
    if not name:
        raise ValueError("变量名不能为空")
    if len(name) > 32:
        raise ValueError("变量名过长（上限 32 字符）")
    if not _VAR_NAME_RE.match(name):
        raise ValueError("变量名只能由中文/字母/数字/下划线组成且不能以数字开头")


def _eval_value(expr: str) -> int:
    expr = expr.strip()
    try:
        return int(expr)
    except ValueError:
        pass
    if exec_roll_exp is None:
        raise ValueError("无法解析数值表达式")
    try:
        return exec_roll_exp(expr).get_val()
    except RollDiceError as e:  # type: ignore[attr-defined]
        raise ValueError(getattr(e, "info", str(e)))


@custom_user_command(readable_name="变量指令", priority=1,
                     flag=DPP_COMMAND_FLAG_DEFAULT, group_only=False)
class VariableCommand(UserCommandBase):
    """.set / .get / .del 用户自定义变量

    Priority=1 让本指令早于 query (priority=2)、roll 等执行；配合严格的
    `^\\.(set|get|del)(?:\\s+(.*))?$` 正则确保 `.search` `.delete` 等不会
    被误判，双重防护避免与 `.s*` 前缀命令冲突（β 注释中提到的风险）。
    """

    def __init__(self, bot: Bot):
        super().__init__(bot)
        bot.loc_helper.register_loc_text(LOC_VAR_SET, "设置变量 {name} = {val}",
                                         "设置变量的回复")
        bot.loc_helper.register_loc_text(LOC_VAR_GET, "{name} = {val}",
                                         "查询变量的回复")
        bot.loc_helper.register_loc_text(LOC_VAR_GET_ALL, "你的变量列表：\n{info}",
                                         "查询所有变量的回复")
        bot.loc_helper.register_loc_text(LOC_VAR_DEL, "已删除变量 {name}",
                                         "删除变量的回复")
        bot.loc_helper.register_loc_text(LOC_VAR_ERROR, "变量处理出错：{error}",
                                         "出错的回复")

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        m = _VAR_CMD_RE.match(msg_str)
        if not m:
            return False, False, None
        cmd_type = m.group(1)
        arg_str = (m.group(2) or "").strip()
        return True, False, (cmd_type, arg_str)

    async def process_msg(self, msg_str: str, meta: MessageMetaData,
                          hint: Any) -> List[BotCommandBase]:
        port = (GroupMessagePort(meta.group_id)
                if meta.group_id else PrivateMessagePort(meta.user_id))
        cmd_type, arg_str = hint
        group_id = meta.group_id or "_private"
        feedback = ""

        try:
            if cmd_type == "set":
                feedback = await self._handle_set(meta.user_id, group_id, arg_str)
            elif cmd_type == "get":
                feedback = await self._handle_get(meta.user_id, group_id, arg_str)
            elif cmd_type == "del":
                feedback = await self._handle_del(meta.user_id, group_id, arg_str)
        except ValueError as e:
            feedback = self.format_loc(LOC_VAR_ERROR, error=str(e))

        return [BotSendMsgCommand(self.bot.account, feedback, [port])]

    async def _handle_set(self, user_id: str, group_id: str, arg_str: str) -> str:
        if not arg_str:
            raise ValueError("用法：.set 变量名 = 数值 或 .set 变量名 +/- 数值")
        # 找到第一个 = / + / - 作为操作符
        op_idx = -1
        op = ""
        for i, ch in enumerate(arg_str):
            if ch in ("=", "+", "-"):
                op_idx = i
                op = ch
                break
        if op_idx <= 0:
            raise ValueError("必须用 = 或 +/- 分隔变量名和数值")
        name = arg_str[:op_idx].strip()
        val_str = arg_str[op_idx + 1:].strip()
        _validate_var_name(name)
        val = _eval_value(val_str)

        if op == "=":
            await self.bot.db.variable.save(UserVariable(
                user_id=user_id, group_id=group_id, name=name, val=val
            ))
            return self.format_loc(LOC_VAR_SET, name=name, val=val)
        else:
            existing = await self.bot.db.variable.get(user_id, group_id, name)
            if not existing:
                raise ValueError(f"变量 {name} 不存在，请先用 = 赋值")
            delta = val if op == "+" else -val
            existing.val += delta
            await self.bot.db.variable.save(existing)
            return self.format_loc(LOC_VAR_SET, name=name,
                                   val=f"{existing.val - delta} {op} {val} = {existing.val}")

    async def _handle_get(self, user_id: str, group_id: str, arg_str: str) -> str:
        if not arg_str:
            items = await self.bot.db.variable.list_by(user_id=user_id, group_id=group_id)
            if not items:
                return "你还没有定义任何变量。用 .set 变量名 = 数值 来定义。"
            info = "\n".join(f"  {v.name} = {v.val}" for v in items)
            return self.format_loc(LOC_VAR_GET_ALL, info=info)
        name = arg_str.strip()
        _validate_var_name(name)
        item = await self.bot.db.variable.get(user_id, group_id, name)
        if not item:
            raise ValueError(f"变量 {name} 不存在")
        return self.format_loc(LOC_VAR_GET, name=name, val=item.val)

    async def _handle_del(self, user_id: str, group_id: str, arg_str: str) -> str:
        if not arg_str:
            raise ValueError("用法：.del 变量名 或 .del all")
        if arg_str == "all":
            items = await self.bot.db.variable.list_by(user_id=user_id, group_id=group_id)
            for v in items:
                await self.bot.db.variable.delete(v.user_id, v.group_id, v.name)
            return self.format_loc(LOC_VAR_DEL, name=f"(共 {len(items)} 个)")
        name = arg_str.strip()
        _validate_var_name(name)
        existing = await self.bot.db.variable.get(user_id, group_id, name)
        if not existing:
            raise ValueError(f"变量 {name} 不存在")
        await self.bot.db.variable.delete(user_id, group_id, name)
        return self.format_loc(LOC_VAR_DEL, name=name)

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        if keyword in ("set", "get", "del", "var"):
            return (
                "变量指令：\n"
                "  .set 变量名 = 数值          赋值（数值可以是骰子表达式如 2d6+3）\n"
                "  .set 变量名 + 数值          累加\n"
                "  .set 变量名 - 数值          累减\n"
                "  .get 变量名                 查看单个\n"
                "  .get                        查看所有\n"
                "  .del 变量名                 删除单个\n"
                "  .del all                    清空所有"
            )
        return ""

    def get_description(self) -> str:
        return ".set/.get/.del 自定义变量"
