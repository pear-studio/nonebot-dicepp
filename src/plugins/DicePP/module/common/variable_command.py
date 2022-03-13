"""
变量指令, 如.set
"""

import re
from typing import Dict, List, Tuple, Any, Literal, Optional
from core.bot import Bot, BotVariable
from core.data import DataManagerError, DC_VARIABLE
from core.command.const import *
from core.command import UserCommandBase, custom_user_command
from core.command import BotCommandBase, BotSendMsgCommand
from core.communication import MessageMetaData, PrivateMessagePort, GroupMessagePort


from module.roll import exec_roll_exp, RollDiceError

LOC_VAR_SET = "var_set"
LOC_VAR_GET = "var_get"
LOC_VAR_GET_ALL = "var_get_all"
LOC_VAR_DEL = "var_del"
LOC_VAR_ERROR = "var_error"


@custom_user_command(readable_name="变量指令", priority=0,  # priority要大于搜索, 否则set会被s覆盖
                     flag=DPP_COMMAND_FLAG_MACRO, group_only=True)
class VariableCommand(UserCommandBase):
    """
    用户自定义变量 包括.set .get .del
    """

    def __init__(self, bot: Bot):
        super().__init__(bot)
        bot.loc_helper.register_loc_text(LOC_VAR_SET, "set variable {name} as {val}", "设置变量, name为变量名, val为数值")
        bot.loc_helper.register_loc_text(LOC_VAR_GET, "{name} = {val}", "获取变量, name为变量名, val为数值")
        bot.loc_helper.register_loc_text(LOC_VAR_GET_ALL, "All Variables:\n{info}", "获取所有变量, info为逐条变量信息")
        bot.loc_helper.register_loc_text(LOC_VAR_DEL, "Delete variable: {name}", "删除所有变量, name为变量名")
        bot.loc_helper.register_loc_text(LOC_VAR_ERROR, "Error when process var: {error}", "error为处理变量时发生的错误")

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        should_proc: bool = False
        should_pass: bool = False
        for cmd_type in ["set", "get", "del"]:
            if msg_str.startswith(f".{cmd_type}"):
                should_proc = True
                return should_proc, should_pass, (cmd_type, msg_str[4:].strip())
        return should_proc, should_pass, None

    def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        port = GroupMessagePort(meta.group_id) if meta.group_id else PrivateMessagePort(meta.user_id)
        # 解析语句
        cmd_type: Literal["set", "get", "del"]
        arg_str: str
        cmd_type, arg_str = hint
        feedback: str

        try:
            cur_var_list = self.bot.data_manager.get_keys(DC_VARIABLE, [meta.user_id, meta.group_id])
        except DataManagerError:
            cur_var_list = []

        if cmd_type == "set":
            # 判断操作类型
            set_type_list = ["=", "+", "-"]
            set_type: Optional[Literal["=", "+", "-"]]
            # 此处any为Literal["=", "+", "-"]
            set_info_tmp: List[Tuple[any, int]] = [(s_type, arg_str.find(s_type)) for s_type in set_type_list if arg_str.find(s_type) != -1]
            if not set_info_tmp:
                feedback = self.format_loc(LOC_VAR_ERROR, error=f"至少包含{set_type_list}其中之一")
                return [BotSendMsgCommand(self.bot.account, feedback, [port])]
            set_info_tmp.sort(key=lambda x: x[1])
            set_type = set_info_tmp[0][0]
            split_index = set_info_tmp[0][1]
            var_name, var_val_str = arg_str[:split_index].strip(), arg_str[split_index+1:].strip()
            # 验证变量名
            try:
                assert_valid_variable_name(var_name)
            except ValueError as e:
                feedback = self.format_loc(LOC_VAR_ERROR, error=f"{var_name}: {e.args}")
                return [BotSendMsgCommand(self.bot.account, feedback, [port])]
            # 获取修改值
            try:
                var_val: int = int(var_val_str)
            except ValueError:
                try:
                    var_val: int = exec_roll_exp(var_val_str).get_val()
                except RollDiceError as e:   # ToDo 支持依赖其他变量
                    feedback = self.format_loc(LOC_VAR_ERROR, error=f"{var_val_str}: {e.info}")
                    return [BotSendMsgCommand(self.bot.account, feedback, [port])]
            if set_type == "=":
                bot_var = BotVariable()
                bot_var.initialize(var_name, var_val)
                self.bot.data_manager.set_data(DC_VARIABLE, [meta.user_id, meta.group_id, var_name], bot_var)
                feedback = self.format_loc(LOC_VAR_SET, name=var_name, val=var_val)
            else:
                if var_name not in cur_var_list:
                    feedback = self.format_loc(LOC_VAR_ERROR, error=f"{var_name}不存在, 当前可用变量: {list(cur_var_list)}")
                    return [BotSendMsgCommand(self.bot.account, feedback, [port])]
                bot_var: BotVariable = self.bot.data_manager.get_data(DC_VARIABLE, [meta.user_id, meta.group_id, var_name])
                if set_type == "+":
                    feedback = self.format_loc(LOC_VAR_SET, name=var_name, val=f"{bot_var.val}+{var_val}={bot_var.val+var_val}")
                    bot_var.val = bot_var.val + var_val
                else:  # set_type == "-"
                    feedback = self.format_loc(LOC_VAR_SET, name=var_name, val=f"{bot_var.val}-{var_val}={bot_var.val-var_val}")
                    bot_var.val = bot_var.val - var_val
                self.bot.data_manager.set_data(DC_VARIABLE, [meta.user_id, meta.group_id, var_name], bot_var)
        elif cmd_type == "get":
            var_name = arg_str
            if var_name:
                if var_name not in cur_var_list:
                    feedback = self.format_loc(LOC_VAR_ERROR, error=f"{var_name}不存在, 当前可用变量: {list(cur_var_list)}")
                    return [BotSendMsgCommand(self.bot.account, feedback, [port])]
                bot_var: BotVariable = self.bot.data_manager.get_data(DC_VARIABLE, [meta.user_id, meta.group_id, var_name])
                feedback = self.format_loc(LOC_VAR_GET, name=var_name, val=bot_var.val)
            else:
                bot_var_dict: Dict[str, BotVariable]
                try:
                    bot_var_dict = self.bot.data_manager.get_data(DC_VARIABLE, [meta.user_id, meta.group_id])
                except DataManagerError:
                    bot_var_dict = {}
                if not bot_var_dict:
                    info = "暂无任何变量"
                else:
                    var_info = [f"{var.name}={var.val}" for var in bot_var_dict.values()]
                    info = "; ".join(var_info)
                feedback = self.format_loc(LOC_VAR_GET_ALL, info=info)
        else:  # cmd_type == "del"
            var_name = arg_str
            if not var_name:
                feedback = self.format_loc(LOC_VAR_ERROR, error="请指定变量名")
            elif var_name == "all":
                self.bot.data_manager.delete_data(DC_VARIABLE, [meta.user_id, meta.group_id])
                feedback = self.format_loc(LOC_VAR_DEL, name="; ".join(cur_var_list))
            elif var_name not in cur_var_list:
                feedback = self.format_loc(LOC_VAR_ERROR, error=f"{var_name}不存在, 当前可用变量: {list(cur_var_list)}")
            else:
                self.bot.data_manager.delete_data(DC_VARIABLE, [meta.user_id, meta.group_id, var_name])
                feedback = self.format_loc(LOC_VAR_DEL, name=var_name)

        return [BotSendMsgCommand(self.bot.account, feedback, [port])]

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        feedback = ""
        if keyword == "set":  # help后的接着的内容
            feedback: str = ".set [变量名] [=/+/-] [数值或掷骰表达式]" \
                            "\n通过=设置变量后可以对变量进行加减操作, 为每个群独立记录变量" \
                            "\n变量名不能含有空格或换行" \
                            "\n可以在语句中通过%变量名%来引用存在的变量"
        elif keyword == "get":
            feedback: str = ".get [变量名]" \
                            "\n查看目标变量名, 不输入变量名则获取所有当前变量"
        elif keyword == "del":
            feedback: str = ".del [变量名]" \
                            "\n删除目标变量名, 若目标变量名为\"all\"则删除所有变量"
        return feedback

    def get_description(self) -> str:
        return ".set/get/del 记录和修改变量"  # help指令中返回的内容


def assert_valid_variable_name(name: str):
    if not name:
        raise ValueError("变量名不能为空")
    # 变量名中不能包含空格或换行
    if re.search(r"\s", name):
        raise ValueError("变量名中不能含有空格")
    if name in ["all"]:
        raise ValueError("变量名为保留字")
