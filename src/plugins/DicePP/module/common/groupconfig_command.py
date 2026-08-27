from array import array
from pickle import TRUE
from tokenize import String
from typing import List, Tuple, Any

from plugins.DicePP.core.bot import Bot
from plugins.DicePP.core.data.models import GroupConfig
from plugins.DicePP.core.command.const import *
from plugins.DicePP.core.command import UserCommandBase, custom_user_command
from plugins.DicePP.core.command import BotCommandBase, BotSendMsgCommand
from plugins.DicePP.core.communication import MessageMetaData, PrivateMessagePort, GroupMessagePort

LOC_GROUP_CONFIG_SET = "group_config_set"
LOC_GROUP_CONFIG_GET = "group_config_get"
LOC_GROUP_DICE_SET = "group_dice_set"

DC_GROUPCONFIG = "group_config"

DEFAULT_GROUP_CONFIG = {
    # 基础内容
    "backroom": False,
    "default_dice": "D20",
    "mode": "",
    # 功能开关
    "roll_dnd_enable": True,
    "roll_coc_enable": False,
    "roll_hide_enable": False,
    "deck_enable": True,
    "query_enable": True,
    "random_gen_enable": True,
    "query_database": "DND5E",
    # 娱乐内容
    "cool_jrrp": True,
    "april_fool": False,
}

_TRUE_VALUES = {"真", "是", "开", "开启", "true", "yes", "on"}
_FALSE_VALUES = {"假", "否", "关", "关闭", "false", "no", "off"}


def _parse_config_value(name: str, raw_value: str) -> Any:
    default = DEFAULT_GROUP_CONFIG[name]
    if isinstance(default, bool):
        if raw_value in _TRUE_VALUES:
            return True
        if raw_value in _FALSE_VALUES:
            return False
        raise ValueError(
            f"群配置 {name} 需要布尔值：真/假、是/否、开/关、"
            "true/false、yes/no 或 on/off。"
        )
    if isinstance(default, int):
        digits = raw_value[1:] if raw_value.startswith(("+", "-")) else raw_value
        if not digits or not digits.isdecimal():
            raise ValueError(f"群配置 {name} 需要合法整数。")
        return int(raw_value)
    if isinstance(default, str):
        return raw_value
    raise ValueError(f"群配置 {name} 使用了不支持的值类型。")


@custom_user_command(readable_name="群配置指令", priority=-1,
                     flag=DPP_COMMAND_FLAG_MANAGE, group_only=True,
                     permission_require=1)
class GroupconfigCommand(UserCommandBase):
    """
    .config 群配置指令
    """

    def __init__(self, bot: Bot):
        super().__init__(bot)
        bot.loc_helper.register_loc_text(LOC_GROUP_CONFIG_SET, "已将本群 {var_name} 的值改为 {var}。", "群配置指令（需要骰管理），将一项群配置设定为特定值时的回复，var_name：变量名，var：具体值")
        bot.loc_helper.register_loc_text(LOC_GROUP_CONFIG_GET, "本群 {var_name} 的值是 {var}。", "群配置指令，将一项群配置设定为特定值时的回复，var_name：变量名，var：具体值")
        bot.loc_helper.register_loc_text(LOC_GROUP_DICE_SET, "本群的默认掷骰面数已改为{var}面。", "修改群内默认掷骰骰面的回复，var：具体骰面")

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        should_proc: bool = True
        should_pass: bool = False
        arg_str: str = ""
        show_mode: str = ""

        if msg_str.startswith(".设置"):
            arg_str = meta.plain_msg[3:].strip()
        elif msg_str.startswith(".config"):
            arg_str = meta.plain_msg[7:].strip()
        elif msg_str.startswith(".骰面"):
            arg_str = "set default_dice " + msg_str[3:].strip()
            show_mode = "dice"
        elif msg_str.startswith(".dice"):
            arg_str = "set default_dice " + msg_str[5:].strip()
            show_mode = "dice"
        else:
            should_proc = False

        hint = (arg_str, show_mode)
        return should_proc, should_pass, hint

    async def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        port = GroupMessagePort(meta.group_id)
        if hint[0] == "":
            arg_list = []
            arg_num = 0
        else:
            arg_list = hint[0].split()
            arg_num = len(arg_list)
        feedback: str = ""

        if arg_num == 0:
            feedback = self.get_help(".config", meta)
        elif arg_list[0] == "set":
            if arg_num == 3 and arg_list[1] in DEFAULT_GROUP_CONFIG:
                try:
                    value = _parse_config_value(arg_list[1], arg_list[2])
                except ValueError as exc:
                    feedback = str(exc)
                else:
                    await self.set_group_config(meta.group_id, arg_list[1], value)
                    feedback = "已将群配置 " + arg_list[1] + " 的值改为 " + arg_list[2] + "。"
            else:
                feedback = "参数错误"
        elif arg_list[0] == "get":
            if arg_num == 2 and arg_list[1] in DEFAULT_GROUP_CONFIG:
                feedback = str(await self.get_group_config(meta.group_id, arg_list[1]))
                feedback = "群配置 " + arg_list[1] + " 的值为 " + feedback + "。"
            else:
                feedback = "参数错误"
        elif arg_list[0] == "show":
            _row = await self.bot.db.group_config.get(meta.group_id)
            config_dict = _row.data if _row and _row.data else {}
            feedback = "当前已配置的群配置: "
            for key in DEFAULT_GROUP_CONFIG:
                if key in config_dict:
                    feedback += "\n · " + key + " : " + str(config_dict[key])
        elif arg_list[0] == "list":
            feedback = "以下是所有可用的群配置与默认值: "
            for key in DEFAULT_GROUP_CONFIG.keys():
                feedback += "\n · " + str(key) + " : " + str(DEFAULT_GROUP_CONFIG[key])
        elif arg_list[0] == "clear":
            await self.clear_group_config(meta.group_id)
            feedback = "群配置已清空"
        else:
            feedback = "未知指令。"

        return [BotSendMsgCommand(self.bot.account, feedback, [port])]

    async def set_group_config(self, group_id: str, name: str, data: Any) -> None:
        if name not in DEFAULT_GROUP_CONFIG:
            raise ValueError(f"不支持的群配置: {name}")
        _row = await self.bot.db.group_config.get(group_id)
        config_dict = dict(_row.data) if _row and _row.data else {}
        config_dict[name] = data
        await self.bot.db.group_config.upsert(GroupConfig(group_id=group_id, data=config_dict))

    async def get_group_config(self, group_id: str, name: str) -> Any:
        if name not in DEFAULT_GROUP_CONFIG:
            raise ValueError(f"不支持的群配置: {name}")
        _row = await self.bot.db.group_config.get(group_id)
        if _row and _row.data and name in _row.data:
            return _row.data[name]
        return DEFAULT_GROUP_CONFIG[name]

    async def clear_group_config(self, group_id: str) -> None:
        await self.bot.db.group_config.delete(group_id)

    async def update_group_config(self, group_id: str, setting: List[str], var: List[str]):
        await self.clear_group_config(group_id)
        for index in range(len(setting)):
            await self.set_group_config(group_id, setting[index], var[index])

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        if keyword == "config":
            feedback: str = (".config set [设置名] [参数值] 设置群配置"
                             "\n.config get [设置名] 获取群当前设置"
                             "\n.config clear 清空本群的群配置"
                             "\n.config show 显示当前群已设置的全部设置名"
                             "\n.config list 显示全部可用设置")
            return feedback
        elif keyword == "dice":
            return ".dice [骰面] 设置群内默认投掷的骰子面数"
        return ""

    def get_description(self) -> str:
        return ".config 群配置系统"
