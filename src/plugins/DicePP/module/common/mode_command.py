from typing import Dict, List, Tuple, Any

from plugins.DicePP.core.bot import Bot
from plugins.DicePP.core.data.models.extended import GroupConfig
from plugins.DicePP.core.command.const import *
from plugins.DicePP.core.command import UserCommandBase, custom_user_command
from plugins.DicePP.core.command import BotCommandBase, BotSendMsgCommand
from plugins.DicePP.core.command import CommandTextParser, CommandContextResolver
from plugins.DicePP.core.communication import MessageMetaData, PrivateMessagePort, GroupMessagePort
from plugins.DicePP.core.localization import LOC_PERMISSION_DENIED_NOTICE
from plugins.DicePP.module.common.mode_defs import (
    BUILTIN_MODES,
    default_dice_for_mode,
    query_database_for_mode,
)

# Task 3.3: 统一解析器（替代内嵌前缀判断与参数切分）
_MODE_PARSER_ZH = CommandTextParser(command_prefix="模式", strip_prefix_len=3)
_MODE_PARSER_EN = CommandTextParser(command_prefix="mode", strip_prefix_len=5)

LOC_MODE_SWITCH = "mode_switch"
LOC_MODE_INVALID = "mode_invalid"
LOC_MODE_NOT_EXIST = "mode_not_exist"
LOC_MODE_LIST = "mode_list"
LOC_MODE_LIKELY = "mode_likely"
LOC_MODE_CURRENT = "mode_current"
LOC_MODE_DB_MATCH = "mode_db_match"
LOC_MODE_DB_MULTI_MATCH = "mode_db_multi_match"

@custom_user_command(readable_name="模式指令", priority=-2,
                     flag=DPP_COMMAND_FLAG_MANAGE, group_only=False
                     )
class ModeCommand(UserCommandBase):
    """
    .mode 模式设置指令
    """

    def __init__(self, bot: Bot):
        super().__init__(bot)
        bot.loc_helper.register_loc_text(LOC_MODE_SWITCH, "已切换至{new_mode}模式（默认{dice}面骰点，查询数据库使用{database}.db（如果有））。",
                                         "。mode切换群模式指令，切换模式等于一次性修改多个群配置。\nnew_mode：切换后的模式，dice：默认骰面，database：查询使用数据库")
        bot.loc_helper.register_loc_text(
            LOC_MODE_INVALID, "该模式配置有误，无法切换，请询问骰主。", "。mode切换模式，但模式定义有问题时返回")
        bot.loc_helper.register_loc_text(
            LOC_MODE_NOT_EXIST, "该模式不存在！", "。mode切换模式，但没有匹配到内置模式或数据库时返回")
        bot.loc_helper.register_loc_text(
            LOC_MODE_LIST, "以下是可用的模式列表：{modes}", "。mode模式指令查看可用模式列表\nmodes：可用模式列表")
        bot.loc_helper.register_loc_text(
            LOC_MODE_LIKELY, "找到多个选项，你要找的是不是：{modes}", "。mode模式指令，模糊匹配出现多个结果\nmodes：模糊匹配结果列表")
        bot.loc_helper.register_loc_text(LOC_MODE_CURRENT, "当前模式为{new_mode}（默认{dice}面骰点，查询数据库使用{database}.db（如果有））。", ".mode 不带参数时显示当前模式")
        bot.loc_helper.register_loc_text(LOC_MODE_DB_MATCH, "已自动匹配到数据库{database}（默认{dice}面骰点）。", ".mode自动匹配数据库时返回")
        bot.loc_helper.register_loc_text(LOC_MODE_DB_MULTI_MATCH, "找到多个匹配的数据库：{databases}，请使用更精确的名称。", ".mode自动匹配到多个数据库时返回")


        self.mode_dict: Dict[str, List[str]] = {
            definition.mode: [definition.default_dice, definition.query_database]
            for definition in BUILTIN_MODES
        }
        # 大写模式名到原始模式名映射，减少重复遍历
        self.mode_upper_map: Dict[str, str] = {
            name.upper(): name for name in self.mode_dict
        }

    def _mode_is_available(self, mode_name: str) -> bool:
        values = self.mode_dict.get(mode_name, [])
        database = values[1] if len(values) > 1 else ""
        return not database or not self.bot.db.query.is_database_disabled(database)

    def _available_mode_names(self) -> List[str]:
        return [name for name in self.mode_dict if self._mode_is_available(name)]

    def delay_init(self) -> List[str]:
        return ["已载入内置模式。"]

    async def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        # ── Task 3.3: 统一解析层前缀识别（仅做文本解析，不触库）──────────────
        if msg_str.startswith(".模式"):
            parse_result = _MODE_PARSER_ZH.parse(msg_str)
        elif msg_str.startswith(".mode"):
            parse_result = _MODE_PARSER_EN.parse(msg_str)
        else:
            return False, False, None

        # hint 携带 CommandParseResult 供 process_msg 消费（避免重复解析）
        # 群模式初始化检查移入 process_msg，保持 can_process_msg 纯解析、不触库
        return True, False, parse_result

    async def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        port = GroupMessagePort(
            meta.group_id) if meta.group_id else PrivateMessagePort(meta.user_id)
        # 判断权限：群内需要权限>=0才能执行；私聊允许用户修改自己的私聊模式
        if meta.group_id and meta.permission < 0:
            feedback = self.bot.loc_helper.format_loc_text(LOC_PERMISSION_DENIED_NOTICE)
            return [BotSendMsgCommand(self.bot.account, feedback, [port])]

        # ── 构建本次 invocation 的唯一 CommandContext（per-invocation 缓存）──
        ctx = await CommandContextResolver.resolve(self.bot, meta)
        target_id = ctx.user_id if ctx.is_private else ctx.group_id
        is_private = ctx.is_private

        # ── 群模式初始化检查（原在 can_process_msg，移此处以符合"不触库"约定）──
        config = await ctx.group_config()
        current_mode = config.data.get("mode", "") if config else ""
        if current_mode == "":
            default_mode = self.bot.config.default_mode
            if default_mode != "":
                await self.switch_mode(target_id, default_mode, is_private=is_private)
            else:
                from plugins.DicePP.core.data.models.extended import GroupConfig
                if config:
                    config.data["mode"] = "NULL"
                    await self.bot.db.group_config.upsert(config)
                else:
                    new_config = GroupConfig(group_id=ctx.config_key, data={"mode": "NULL"})
                    await self.bot.db.group_config.upsert(new_config)

        # ── Task 3.3: 从 CommandParseResult 消费解析结果 ───────────────────
        arg_var = hint.first_arg("").strip().upper()

        if arg_var == "DEFAULT" or arg_var == "CLEAR":
            feedback = await self.switch_mode(
                target_id, self.bot.config.default_mode, is_private=is_private)
        elif arg_var != "":
            feedback = await self.switch_mode(target_id, arg_var, is_private=is_private)
        else:
            # 显示当前目标（群/私聊）的模式（使用 ctx 缓存读取，避免重复 DB 访问）
            config = await ctx.group_config()

            stored_mode = config.data.get("mode", "") if config else ""

            # 处理空/NULL
            if not stored_mode or stored_mode == "NULL":
                stored_mode = self.bot.config.default_mode

            # 内置模式使用固定定义，动态模式沿用数据库名和简单骰面猜测。
            dice = default_dice_for_mode(stored_mode)
            database = query_database_for_mode(stored_mode)
            if config and config.data:
                dice = config.data.get("default_dice", dice)
                database = config.data.get("query_database", database)

            current_text = self.bot.loc_helper.format_loc_text(LOC_MODE_CURRENT, new_mode=stored_mode, dice=dice, database=database)
            list_text = self.bot.loc_helper.format_loc_text(
                LOC_MODE_LIST,
                modes="、".join(self._available_mode_names()),
            )
            feedback = current_text + "\n" + list_text

        return [BotSendMsgCommand(self.bot.account, feedback, [port])]

    async def switch_mode(self, target_id: str, mode: str, is_private: bool = False) -> str:
        # 更新配置数据的内部异步函数
        # 私聊配置统一存储在 group_config 表，以 "__user__{user_id}" 作为 group_id
        async def update_group_config(tid: str, values: Dict[str, Any], is_private_inner: bool = False):
            config_key = f"__user__{tid}" if is_private_inner else tid
            config = await self.bot.db.group_config.get(config_key)
            if config is None:
                config = GroupConfig(group_id=config_key, data={})
            config.data.update(values)
            await self.bot.db.group_config.upsert(config)

        def find_matching_databases(query: str) -> List[str]:
            """在已连接数据库中查找匹配项"""
            query_upper = query.upper()
            results: List[str] = []
            for db_name in self.bot.db.query.list_databases():
                db_upper = db_name.upper()
                # 精确匹配（忽略大小写）
                if db_upper == query_upper:
                    return [db_name]  # 精确匹配直接返回
                # 模糊匹配：查询字符串是数据库名的子串
                if query_upper in db_upper:
                    results.append(db_name)
            return results

        matched = False
        feedback = ""
        # 尝试精准匹配预定义模式
        exact_key = self.mode_upper_map.get(mode.upper())
        if exact_key is not None and self._mode_is_available(exact_key):
            dice, database = self.mode_dict[exact_key][:2]
            await update_group_config(
                target_id,
                {"mode": exact_key, "default_dice": dice, "query_database": database},
                is_private_inner=is_private,
            )
            feedback = self.bot.loc_helper.format_loc_text(
                LOC_MODE_SWITCH, new_mode=exact_key, dice=self.mode_dict[exact_key][0], database=self.mode_dict[exact_key][1])
            matched = True
        # 尝试模糊匹配预定义模式
        if not matched:
            result: List[str] = []
            for key in self.mode_dict.keys():
                if not self._mode_is_available(key):
                    continue
                ukey = key.upper()
                if mode.casefold() in key.casefold():
                    result.append(key)
            if len(result) > 1:
                feedback = self.bot.loc_helper.format_loc_text(
                    LOC_MODE_LIKELY, modes="、".join(result))
                matched = True  # 有多个候选，不继续尝试数据库匹配
            elif len(result) == 1:
                orig_key = result[0]
                if orig_key is not None:
                    dice, database = self.mode_dict[orig_key][:2]
                    await update_group_config(
                        target_id,
                        {"mode": orig_key, "default_dice": dice, "query_database": database},
                        is_private_inner=is_private,
                    )
                    feedback = self.bot.loc_helper.format_loc_text(
                        LOC_MODE_SWITCH, new_mode=orig_key, dice=dice, database=database)
                    matched = True

        # 如果预定义模式未匹配，尝试匹配已加载的数据库
        if not matched:
            db_matches = find_matching_databases(mode)
            if len(db_matches) == 1:
                # 唯一匹配，创建动态模式
                db_name = db_matches[0]
                dice = default_dice_for_mode(db_name)
                await update_group_config(
                    target_id,
                    {"mode": mode, "default_dice": dice, "query_database": db_name},
                    is_private_inner=is_private,
                )
                feedback = self.bot.loc_helper.format_loc_text(
                    LOC_MODE_DB_MATCH, database=db_name, dice=dice)
                matched = True
            elif len(db_matches) > 1:
                # 多个匹配，提示用户选择
                feedback = self.bot.loc_helper.format_loc_text(
                    LOC_MODE_DB_MULTI_MATCH, databases="、".join(db_matches))
                matched = True
            else:
                # 没有找到任何匹配
                feedback = self.bot.loc_helper.format_loc_text(
                    LOC_MODE_NOT_EXIST) + self.bot.loc_helper.format_loc_text(
                        LOC_MODE_LIST,
                        modes="、".join(self._available_mode_names()),
                    )

        return feedback

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        if keyword == "mode":  # help后的接着的内容
            feedback: str = ".mode dnd/coc/ygo" \
                            "套用模式设置"
            return feedback
        return ""

    def get_description(self) -> str:
        return ".mode 模式系统"  # help指令中返回的内容
