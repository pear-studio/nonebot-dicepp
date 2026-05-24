"""群级私设新指令（覆盖 add/del/list/宏/db 子指令，旧 .hb 的导入/重载等保留）

按 #50 设计稿实施 PR-2，配套 PR-1 的 GroupMacro + QueryStore 群私设扩展。
priority=1（早于旧 HomebrewCommand 的 priority=2）确保新子指令先匹配，
旧子指令（加载/重载/模板/帮助/显示/清除/开/关）由旧指令照常处理。
"""
import os
from typing import Any, List, Optional, Tuple

from core.bot import Bot
from core.command import (
    BotCommandBase,
    BotSendMsgCommand,
    UserCommandBase,
    custom_user_command,
)
from core.command.const import DPP_COMMAND_FLAG_QUERY
from core.communication import (
    GroupMessagePort,
    MessageMetaData,
    PrivateMessagePort,
)
from core.config.basic import Paths
from core.data.models import GroupMacro
from core.data.query_store import (
    QUERY_DATA_FIELD_LIST,
    QueryStore,
)

from module.common.macro_command import (
    MACRO_COMMAND_SPLIT,
    parse_macro_definition,
)


_NEW_SUB_PREFIXES = ("add", "del", "list", "宏", "db")
_DEFAULT_HB_DB_FILE = "main.db"


def _group_dir(bot: Bot, group_id: str) -> str:
    return str(Paths.group_homebrew_dir(bot.account, group_id))


def _default_db_path(bot: Bot, group_id: str) -> str:
    return os.path.join(_group_dir(bot, group_id), _DEFAULT_HB_DB_FILE)


def _default_db_name(group_id: str) -> str:
    return QueryStore._hb_db_name(group_id, _DEFAULT_HB_DB_FILE)


async def _ensure_default_db(bot: Bot, group_id: str) -> str:
    """确保该群的默认私设 db 存在并已加载，返回其 db_name。"""
    os.makedirs(_group_dir(bot, group_id), exist_ok=True)
    db_path = _default_db_path(bot, group_id)
    if not os.path.exists(db_path):
        await bot.db.query.create_empty_database(db_path)
    db_name = _default_db_name(group_id)
    if not bot.db.query.has_database(db_name):
        await bot.db.query.connect_group_homebrew(group_id, _group_dir(bot, group_id))
    return db_name


@custom_user_command(readable_name="私设扩展指令",
                     priority=1,  # 早于旧 HomebrewCommand (priority=2)
                     group_only=True,
                     flag=DPP_COMMAND_FLAG_QUERY,
                     permission_require=1)
class HBExtCommand(UserCommandBase):
    """`.hb` 新子指令族 — add / del / list / 宏 / db。

    旧 `.hb` 子指令（加载/重载/模板/帮助/显示/清除/开/关）继续由
    `HomebrewCommand` 处理，本指令只接管新子指令，should_proc 为
    False 时让旧指令继续。
    """

    PREFIXES = (".hb", ".私设", ".房规", ".homebrew")

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        for pfx in self.PREFIXES:
            if msg_str.startswith(pfx):
                rest = msg_str[len(pfx):].strip()
                if not rest:
                    return False, False, None
                # 只在新子指令前缀命中时拦截，否则放回旧 HomebrewCommand
                lower = rest.lower()
                for sub in _NEW_SUB_PREFIXES:
                    if lower == sub or lower.startswith(sub + " ") or lower.startswith(sub + "　"):
                        return True, False, rest
                return False, False, None
        return False, False, None

    async def process_msg(self, msg_str: str, meta: MessageMetaData,
                          hint: Any) -> List[BotCommandBase]:
        port = (GroupMessagePort(meta.group_id) if meta.group_id
                else PrivateMessagePort(meta.user_id))
        if not meta.group_id:
            return [BotSendMsgCommand(self.bot.account, "私设指令仅限群聊使用", [port])]

        arg: str = (hint or "").strip()
        parts = arg.split(None, 1)
        sub = parts[0].lower() if parts else ""
        rest = parts[1] if len(parts) > 1 else ""

        try:
            if sub == "add":
                feedback = await self._handle_add(meta, rest)
            elif sub == "del":
                feedback = await self._handle_del(meta, rest)
            elif sub == "list":
                feedback = await self._handle_list(meta, rest)
            elif sub == "宏":
                feedback = await self._handle_macro(meta, rest)
            elif sub == "db":
                feedback = await self._handle_db(meta, rest)
            else:
                feedback = self._help_text()
        except ValueError as e:
            feedback = f"私设处理出错: {e}"

        return [BotSendMsgCommand(self.bot.account, feedback, [port])]

    # ── add ────────────────────────────────────────────────────────────
    async def _handle_add(self, meta: MessageMetaData, rest: str) -> str:
        if not rest or "|" not in rest:
            raise ValueError("用法：.hb add 名称|英文|来源|分类|标签|内容")
        fields = [f.strip() for f in rest.split("|")]
        if len(fields) < 6:
            fields += [""] * (6 - len(fields))
        elif len(fields) > 6:
            # 内容里可能含 |，把多余的合并回内容字段
            fields = fields[:5] + ["|".join(fields[5:])]
        if not fields[0]:
            raise ValueError("名称不能为空")

        db_name = await _ensure_default_db(self.bot, meta.group_id)
        cols = ", ".join(QUERY_DATA_FIELD_LIST)
        placeholders = ", ".join(["?"] * len(QUERY_DATA_FIELD_LIST))
        await self.bot.db.query.execute(
            db_name,
            f"INSERT INTO data ({cols}) VALUES ({placeholders})",
            fields,
            commit=True,
        )
        return f"已添加私设条目「{fields[0]}」到 {_DEFAULT_HB_DB_FILE}"

    # ── del ────────────────────────────────────────────────────────────
    async def _handle_del(self, meta: MessageMetaData, rest: str) -> str:
        name = rest.strip()
        if not name:
            raise ValueError("用法：.hb del 名称")
        db_name = await _ensure_default_db(self.bot, meta.group_id)
        cur = await self.bot.db.query.execute(
            db_name, "DELETE FROM data WHERE 名称 = ?", [name], commit=True,
        )
        n = cur.rowcount if cur else 0
        return f"已删除 {n} 条名为「{name}」的私设条目" if n else f"未找到名为「{name}」的私设条目"

    # ── list ───────────────────────────────────────────────────────────
    async def _handle_list(self, meta: MessageMetaData, rest: str) -> str:
        cata = rest.strip()
        db_name = await _ensure_default_db(self.bot, meta.group_id)
        if cata:
            rows = await self.bot.db.query.fetchall(
                db_name,
                "SELECT 名称, 分类 FROM data WHERE 分类 LIKE ? ORDER BY 名称 LIMIT 50",
                [f"%{cata}%"],
            )
        else:
            rows = await self.bot.db.query.fetchall(
                db_name,
                "SELECT 名称, 分类 FROM data ORDER BY 名称 LIMIT 50",
            )
        if not rows:
            return "该群私设为空" if not cata else f"该群私设里没有分类「{cata}」的条目"
        lines = [f"该群私设条目（前 50 条{'，分类 ' + cata if cata else ''}）："]
        for name, c in rows:
            lines.append(f"  · {name}" + (f" [{c}]" if c else ""))
        return "\n".join(lines)

    # ── 宏 ─────────────────────────────────────────────────────────────
    async def _handle_macro(self, meta: MessageMetaData, rest: str) -> str:
        arg = rest.strip()
        if not arg or arg == "list":
            macros = await self.bot.db.group_macro.list_by(group_id=meta.group_id)
            if not macros:
                return ("本群没有定义群宏。例：\n"
                        "  .hb 宏 队友 队友A,队友B,队友C\n"
                        "  .hb 宏 攻击(目标) .r 1d20+5 攻击{目标}")
            lines = [f"本群群宏（{len(macros)} 条）："]
            for m in macros:
                args_str = ",".join(m.args) if m.args else "无"
                lines.append(f"  · {m.key}（参数: {args_str}）-> {m.target}")
            return "\n".join(lines)

        if arg.startswith("del"):
            key = arg[3:].strip()
            if key == "all":
                macros = await self.bot.db.group_macro.list_by(group_id=meta.group_id)
                for m in macros:
                    await self.bot.db.group_macro.delete(m.group_id, m.key)
                return f"已清空本群所有群宏（共 {len(macros)} 条）"
            if not key:
                raise ValueError("用法：.hb 宏 del <关键字> 或 .hb 宏 del all")
            existing = await self.bot.db.group_macro.get(meta.group_id, key)
            if not existing:
                return f"本群没有关键字为「{key}」的群宏"
            await self.bot.db.group_macro.delete(meta.group_id, key)
            return f"已删除群宏「{key}」"

        # 定义群宏
        split_token = ";"
        try:
            split_token = self.bot.config.command_split
        except AttributeError:
            pass
        try:
            new_macro_user = parse_macro_definition(arg, split_token)
        except ValueError as e:
            raise ValueError(f"群宏定义失败: {e}")
        # 把 UserMacro 转 GroupMacro（鸭子类型字段一致 + 加 group/creator）
        gm = GroupMacro(
            group_id=meta.group_id,
            key=new_macro_user.key,
            raw=new_macro_user.raw,
            args=new_macro_user.args,
            target=new_macro_user.target,
            command_split=new_macro_user.command_split,
            creator_id=meta.user_id,
        )
        await self.bot.db.group_macro.save(gm)
        args_str = ",".join(gm.args) if gm.args else "无"
        return f"已定义群宏「{gm.key}」（参数: {args_str}）-> {gm.target}"

    # ── db ─────────────────────────────────────────────────────────────
    async def _handle_db(self, meta: MessageMetaData, rest: str) -> str:
        arg = rest.strip().lower()
        if not arg or arg == "list":
            await _ensure_default_db(self.bot, meta.group_id)  # 确保至少加载默认 db
            dbs = self.bot.db.query.list_group_databases(meta.group_id)
            if not dbs:
                return "本群尚未加载任何私设 db。.hb add 一条条目会自动创建 main.db"
            lines = ["本群已加载私设 db："]
            for d in dbs:
                # 显示文件部分（去掉前缀 __hb__<group>__）
                prefix = QueryStore._HB_PREFIX + meta.group_id + "__"
                display = d[len(prefix):] if d.startswith(prefix) else d
                lines.append(f"  · {display}")
            return "\n".join(lines)
        if arg == "reload":
            # 先 disconnect 本群所有，再重新 connect
            for d in self.bot.db.query.list_group_databases(meta.group_id):
                await self.bot.db.query.disconnect_database(d)
            loaded = await self.bot.db.query.connect_group_homebrew(
                meta.group_id, _group_dir(self.bot, meta.group_id),
            )
            return f"已重新加载 {len(loaded)} 个私设 db"
        return "用法：.hb db list / .hb db reload"

    # ── help ───────────────────────────────────────────────────────────
    def _help_text(self) -> str:
        return (
            "私设扩展指令（群管限定）：\n"
            "  .hb add 名称|英文|来源|分类|标签|内容     添加条目到本群 main.db\n"
            "  .hb del 名称                              删除条目\n"
            "  .hb list [分类]                           列出条目（限 50 条）\n"
            "  .hb 宏 关键字 目标                        定义群宏\n"
            "  .hb 宏 list                               列群宏\n"
            "  .hb 宏 del 关键字 / .hb 宏 del all        删群宏\n"
            "  .hb db list                               列已加载的私设 db\n"
            "  .hb db reload                             重新加载本群私设 db\n"
            "\n旧子指令（加载/重载/模板/帮助/显示/清除/开/关）见 `.hb 帮助`"
        )

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        if keyword in ("hb", "私设", "房规", "homebrew"):
            return self._help_text()
        return ""

    def get_description(self) -> str:
        return ".hb 私设条目/群宏/数据库（新版）"
