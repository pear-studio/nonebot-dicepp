from typing import List, Tuple, Any, Optional

from core.bot import Bot, BotMacro, MACRO_COMMAND_SPLIT
from core.data import DataManagerError, DC_MACRO
from core.command.const import *
from core.command import UserCommandBase, custom_user_command
from core.command import BotCommandBase, BotSendMsgCommand
from core.communication import MessageMetaData, PrivateMessagePort, GroupMessagePort
from core.config import CFG_COMMAND_SPLIT

LOC_DEFINE_SUCCESS = "define_success"
LOC_DEFINE_FAIL = "define_fail"
LOC_DEFINE_LIST = "define_list"
LOC_DEFINE_INFO = "define_info"
LOC_DEFINE_DEL = "define_delete"

CFG_DEFINE_LEN_MAX = "define_length_max"
CFG_DEFINE_NUM_MAX = "define_number_max"


@custom_user_command(readable_name="宏指令", priority=DPP_COMMAND_PRIORITY_DEFAULT,
                     flag=DPP_COMMAND_FLAG_MACRO)
class MacroCommand(UserCommandBase):
    """
    定义和查看宏指令, 关键字为define
    """

    def __init__(self, bot: Bot):
        super().__init__(bot)
        bot.loc_helper.register_loc_text(LOC_DEFINE_SUCCESS, "Define {macro} as {target}, args are: {args}",
                                         "成功定义宏, macro为宏, target为目标字符串, args为参数列表")
        bot.loc_helper.register_loc_text(LOC_DEFINE_FAIL, "Fail to define macro, reason is {error}",
                                         "未成功定义宏, error为失败原因")
        bot.loc_helper.register_loc_text(LOC_DEFINE_LIST, "Macro list:\n{macro_list}",
                                         f"查看当前宏列表, macro_list中的每一个元素由{LOC_DEFINE_INFO}定义")
        bot.loc_helper.register_loc_text(LOC_DEFINE_INFO, "Keywords: {macro} Args: {args} -> {target}",
                                         f"宏列表中每一个元素的信息, macro为宏, args为参数列表, target为目标字符串")
        bot.loc_helper.register_loc_text(LOC_DEFINE_DEL, "Delete macro: {macro}",
                                         f"删除宏, macro为宏关键字")

        bot.cfg_helper.register_config(CFG_DEFINE_LEN_MAX, "300", "每个用户可以定义的宏的上限长度")
        bot.cfg_helper.register_config(CFG_DEFINE_NUM_MAX, "50", "每个用户可以定义的宏上限数量")

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        should_proc: bool = msg_str.startswith(".define")
        should_pass: bool = False
        return should_proc, should_pass, msg_str[7:].strip()

    def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        port = GroupMessagePort(meta.group_id) if meta.group_id else PrivateMessagePort(meta.user_id)
        # 解析语句
        arg_str: str = hint
        feedback: str = ""

        macro_list: List[BotMacro]
        try:
            macro_list = self.bot.data_manager.get_data(DC_MACRO, [meta.user_id])
        except DataManagerError:
            macro_list = []

        if not arg_str:
            def format_macro_info(i, macro):
                return f"{i + 1}. " + self.format_loc(LOC_DEFINE_INFO, macro=macro.key, args=macro.args, target=macro.target)
            macro_list_str = "\n".join((format_macro_info(i, macro) for i, macro in enumerate(macro_list)))
            feedback = self.format_loc(LOC_DEFINE_LIST, macro_list=macro_list_str)
        elif arg_str.startswith("del"):
            macro_key = arg_str[3:].strip()
            if macro_key == "all":
                self.bot.data_manager.delete_data(DC_MACRO, [meta.user_id])
                feedback = self.format_loc(LOC_DEFINE_DEL, macro=str([macro.key for macro in macro_list]))
            else:
                del_index = -1
                for i, macro in enumerate(macro_list):
                    if macro.key == macro_key:
                        del_index = i
                        break
                if del_index != -1:
                    del macro_list[del_index]
                    self.bot.data_manager.set_data(DC_MACRO, [meta.user_id], macro_list)
                    feedback = self.format_loc(LOC_DEFINE_DEL, macro=macro_key)
                else:
                    feedback = self.format_loc(LOC_DEFINE_FAIL, error=f"找不到关键字为{macro_key}的宏")
        else:  # 定义新宏
            macro_new: Optional[BotMacro] = None
            macro_len_limit = int(self.bot.cfg_helper.get_config(CFG_DEFINE_LEN_MAX)[0])
            macro_num_limit = int(self.bot.cfg_helper.get_config(CFG_DEFINE_NUM_MAX)[0])
            try:
                assert len(arg_str) <= macro_len_limit
                macro_new = BotMacro()
                macro_new.initialize(arg_str, self.bot.cfg_helper.get_config(CFG_COMMAND_SPLIT)[0])
            except ValueError as e:
                feedback = self.format_loc(LOC_DEFINE_FAIL, error=e)
            except AssertionError:
                feedback = self.format_loc(LOC_DEFINE_FAIL, error=f"自定义宏长度超出上限: {macro_len_limit}字符")
            if len(macro_list) >= macro_num_limit:
                feedback = self.format_loc(LOC_DEFINE_FAIL, error=f"自定义宏数量超出上限: {macro_num_limit} 请先删除已有宏")
                macro_new = None
            if macro_new.key == "all":
                feedback = self.format_loc(LOC_DEFINE_FAIL, error=f"宏关键字为保留字: {macro_new.key}")
                macro_new = None

            if macro_new:
                # 先移除同名宏
                for macro_prev in macro_list:
                    if macro_prev.key == macro_new.key:
                        macro_list.remove(macro_prev)
                macro_list.append(macro_new)
                self.bot.data_manager.set_data(DC_MACRO, [meta.user_id], macro_list)
                feedback = self.format_loc(LOC_DEFINE_SUCCESS, macro=macro_new.key, args=macro_new.args, target=macro_new.target)

        return [BotSendMsgCommand(self.bot.account, feedback, [port])]

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        if keyword == "define":  # help后的接着的内容
            feedback: str = "宏的定义方法:" \
                            "\t[关键字][参数列表, 形如(参数1,参数2,...), 可选][空格][目标字符串]\n" \
                            "\t目标字符串中与参数同名的字符串将在使用宏时被替换为给定的参数\n" \
                            "\t在定义时给定参数就必须在使用时给出, 否则不会被认定为宏\n" \
                            f"\t用{MACRO_COMMAND_SPLIT}来表示指令分隔符, {MACRO_COMMAND_SPLIT}左右的空格和换行将会被忽略\n" \
                            "\t注意:\n" \
                            "\t\t第一个空格的位置非常关键, 用来区分替换前的内容和替换后的内容\n" \
                            "\t\t参数名字不要重名, 宏可以嵌套, 但不会处理递归(即不可重入), 先定义的宏会先处理\n" \
                            "\t示例:\n" \
                            "\t\t.define 一颗D20 .rd20\n" \
                            f"\t\t.define 掷骰两次(表达式,原因) .r 表达式 原因 {MACRO_COMMAND_SPLIT} .r 表达式 原因\n" \
                            "宏的使用方法:\n" \
                            "\t[关键字][用:分隔给定参数]\n" \
                            "\t输入: 一颗D20 这是一颗d20  ->  等同于:  .rd20 这是一颗d20\n" \
                            "\t输入: 掷骰两次:d20+2:某种原因  -> 等同于: 执行指令.r d20+2 某种原因 + 执行指令.r d20+2 某种原因\n" \
                            "查看当前定义的宏:\n" \
                            "\t.define\n" \
                            "删除某个已经定义的宏:\n" \
                            "\t.define del [关键字]\n" \
                            "删除所有宏:\n" \
                            "\t.define del all"
            return feedback
        return ""

    def get_description(self) -> str:
        return ".define 定义指令宏"  # help指令中返回的内容
