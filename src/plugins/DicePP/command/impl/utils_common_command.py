"""
一些通用指令, 比如.define, .welcome, .point
"""

from typing import List, Tuple, Any, Literal, Optional

from bot_core import Bot
from bot_core import BotMacro, DC_MACRO
from bot_config import CFG_MASTER, CFG_COMMAND_SPLIT
from data_manager import custom_data_chunk, DataChunkBase, DataManagerError
from command.command_config import *
from command.dicepp_command import UserCommandBase, custom_user_command, MessageMetaData
from command.bot_command import BotCommandBase, PrivateMessagePort, GroupMessagePort, BotSendMsgCommand

LOC_DEFINE_SUCCESS = "define_success"
LOC_DEFINE_FAIL = "define_fail"
LOC_DEFINE_LIST = "define_list"
LOC_DEFINE_INFO = "define_info"
LOC_DEFINE_DEL = "define_delete"

CFG_DEFINE_LEN_MAX = "define_length_max"
CFG_DEFINE_NUM_MAX = "define_number_max"


@custom_user_command(readable_name="宏指令", priority=DPP_COMMAND_PRIORITY_DEFAULT)
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
        else:
            macro_new: Optional[BotMacro] = None
            macro_len_limit = int(self.bot.cfg_helper.get_config(CFG_DEFINE_LEN_MAX)[0])
            macro_num_limit = int(self.bot.cfg_helper.get_config(CFG_DEFINE_NUM_MAX)[0])
            try:
                assert len(arg_str) <= macro_len_limit
                macro_new = BotMacro(arg_str, self.bot.cfg_helper.get_config(CFG_COMMAND_SPLIT)[0])
            except ValueError as e:
                feedback = self.format_loc(LOC_DEFINE_FAIL, error=e)
            except AssertionError:
                feedback = self.format_loc(LOC_DEFINE_FAIL, error=f"自定义宏长度超出上限: {macro_len_limit}字符")
            if len(macro_list) >= macro_num_limit:
                feedback = self.format_loc(LOC_DEFINE_FAIL, error=f"自定义宏数量超出上限: {macro_num_limit} 请先删除已有宏")
                macro_new = None

            if macro_new:
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
                            "\t用\\\\来表示指令分隔符, \\\\左右的空格和换行将会被忽略\n" \
                            "\t注意:" \
                            "\t\t第一个空格的位置非常关键, 用来区分替换前的内容和替换后的内容\n" \
                            "\t\t参数名字不要重名, 宏可以嵌套, 但不会处理递归(即不可重入), 先定义的宏会先处理\n" \
                            "\t示例:\n" \
                            "\t\t.define 一颗D20 .rd20\n" \
                            "\t\t.define 掷骰两次(表达式,原因) .r 表达式 原因 \\\\ .r 表达式 原因\n" \
                            "宏的使用方法:\n" \
                            "\t[关键字][用;分隔给定参数]\n" \
                            "\t输入: 一颗D20 这是一颗d20  ->  等同于:  .rd20 这是一颗d20\n" \
                            "\t输入: 掷骰两次:d20+2:某种原因  -> 等同于: 执行指令.r d20+2 某种原因 + 执行指令.r d20+2 某种原因\n" \
                            "查看当前定义的宏:\n" \
                            "\t.define\n" \
                            "删除已经定义的宏:\n" \
                            "\t.define del [关键字]"
            return feedback
        return ""

    def get_description(self) -> str:
        return ".define 定义指令宏"  # help指令中返回的内容


LOC_WELCOME_DEFAULT = "welcome_default"
LOC_WELCOME_SET = "welcome_set"
LOC_WELCOME_RESET = "welcome_reset"
LOC_WELCOME_ILLEGAL = "welcome_illegal"

DC_WELCOME = "welcome"

WELCOME_MAX_LENGTH = 100


# 存放welcome数据
@custom_data_chunk(identifier=DC_WELCOME)
class _(DataChunkBase):
    def __init__(self):
        super().__init__()


@custom_user_command(readable_name="欢迎词指令", priority=DPP_COMMAND_PRIORITY_DEFAULT, group_only=True)
class WelcomeCommand(UserCommandBase):
    """
    .welcome 欢迎词指令
    """

    def __init__(self, bot: Bot):
        super().__init__(bot)
        bot.loc_helper.register_loc_text(LOC_WELCOME_DEFAULT, "Welcome!", "默认入群欢迎词")
        bot.loc_helper.register_loc_text(LOC_WELCOME_SET, "Welcoming word is \"{word}\" now", "设定入群欢迎词, word为当前设定的入群欢迎词")
        bot.loc_helper.register_loc_text(LOC_WELCOME_RESET, "Welcoming word has been reset", "重置入群欢迎词为空")
        bot.loc_helper.register_loc_text(LOC_WELCOME_ILLEGAL, "Welcoming word is illegal: {reason}", "非法的入群欢迎词, reason为原因")

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        should_proc: bool = msg_str.startswith(".welcome")
        should_pass: bool = False
        return should_proc, should_pass, meta.raw_msg[8:].strip()

    def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        port = GroupMessagePort(meta.group_id) if meta.group_id else PrivateMessagePort(meta.user_id)
        # 解析语句
        arg_str = hint
        feedback: str

        if not arg_str:
            self.bot.data_manager.set_data(DC_WELCOME, [meta.group_id], "")
            feedback = self.format_loc(LOC_WELCOME_RESET)
        else:
            if len(arg_str) > WELCOME_MAX_LENGTH:
                feedback = self.format_loc(LOC_WELCOME_ILLEGAL, reason=f"欢迎词长度大于{WELCOME_MAX_LENGTH}")
            elif arg_str == "default":
                self.bot.data_manager.delete_data(DC_WELCOME, [meta.group_id])
                feedback = self.format_loc(LOC_WELCOME_SET, word=self.format_loc(LOC_WELCOME_DEFAULT))
            else:
                self.bot.data_manager.set_data(DC_WELCOME, [meta.group_id], arg_str)
                feedback = self.format_loc(LOC_WELCOME_SET, word=arg_str)

        return [BotSendMsgCommand(self.bot.account, feedback, [port])]

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        if keyword == "welcome":  # help后的接着的内容
            feedback: str = ".welcome [入群欢迎词]" \
                            "welcome后接想要设置的入群欢迎词, 不输入欢迎词则不开启入群欢迎" \
                            ".welcome default 使用默认入群欢迎词"
            return feedback
        return ""

    def get_description(self) -> str:
        return ".welcome 设置入群欢迎词"  # help指令中返回的内容


LOC_POINT_SHOW = "point_show"
LOC_POINT_CHECK = "point_check"
LOC_POINT_EDIT = "point_edit"
LOC_POINT_EDIT_ERROR = "point_edit_error"

CFG_POINT_INIT = "point_init"
CFG_POINT_ADD = "point_add"
CFG_POINT_MAX = "point_max"
CFG_POINT_LIMIT = "point_limit"

DC_POINT = "point"
DCK_POINT_CUR = "current"
DCK_POINT_TODAY = "today"


# 存放点数数据
@custom_data_chunk(identifier=DC_POINT)
class _(DataChunkBase):
    def __init__(self):
        super().__init__()


@custom_user_command(readable_name="点数指令", priority=DPP_COMMAND_PRIORITY_DEFAULT)
class PointCommand(UserCommandBase):
    """
    .point 和.m point指令
    """

    def __init__(self, bot: Bot):
        super().__init__(bot)
        bot.loc_helper.register_loc_text(LOC_POINT_SHOW, "Point of {name}: {point}", "用户查看点数的回复")
        bot.loc_helper.register_loc_text(LOC_POINT_CHECK, "Point of {id}: {result}", "Master查看某人的点数")
        bot.loc_helper.register_loc_text(LOC_POINT_EDIT, "Point: {result}", "Master调整某人的点数")
        bot.loc_helper.register_loc_text(LOC_POINT_EDIT_ERROR, "Error when editing points: {error}", "Master调整某人的点数时出现错误")

        bot.cfg_helper.register_config(CFG_POINT_INIT, "100", "新用户初始拥有的点数")
        bot.cfg_helper.register_config(CFG_POINT_ADD, "100", "每天给活跃用户增加的点数")
        bot.cfg_helper.register_config(CFG_POINT_MAX, "500", "用户能持有的点数上限")
        bot.cfg_helper.register_config(CFG_POINT_LIMIT, "300", "每天使用点数的上限")

    def tick_daily(self) -> List[BotCommandBase]:
        # 根据用户当日是否掷骰决定是否增加点数(暂定)
        from bot_core import DC_USER_DATA
        from command.impl import DCP_USER_DATA_ROLL_A_UID, DCP_ROLL_TIME_A_ID_ROLL, DCK_ROLL_TODAY
        from data_manager import DataManagerError
        point_init = int(self.bot.cfg_helper.get_config(CFG_POINT_INIT))
        point_add = int(self.bot.cfg_helper.get_config(CFG_POINT_ADD))
        point_max = int(self.bot.cfg_helper.get_config(CFG_POINT_MAX))
        user_ids = self.bot.data_manager.get_keys(DC_USER_DATA, [])
        dcp_roll_total_a_uid = DCP_USER_DATA_ROLL_A_UID + DCP_ROLL_TIME_A_ID_ROLL + [DCK_ROLL_TODAY]
        for user_id in user_ids:
            try:
                roll_time = self.bot.data_manager.get_data(DC_USER_DATA, [user_id] + dcp_roll_total_a_uid)
                assert roll_time > 0
            except (DataManagerError, AssertionError):
                continue
            prev_point = self.bot.data_manager.get_data(DC_POINT, [user_id, DCK_POINT_CUR], default_val=point_init)
            # 若已经超过上限, 说明是Master手动调整的, 不进行修改, 否则增加point_add
            cur_point = prev_point if prev_point > point_max else min(point_max, prev_point + point_add)
            self.bot.data_manager.set_data(DC_POINT, [user_id, DCK_POINT_CUR], cur_point)
            self.bot.data_manager.set_data(DC_POINT, [user_id, DCK_POINT_TODAY], 0)
        return []

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        should_proc: bool = False
        should_pass: bool = False
        if msg_str.startswith(".point"):
            return True, should_pass, ("show", None)
        elif msg_str.startswith(".m"):
            msg_str = msg_str[2:].lstrip()
            master_list = self.bot.cfg_helper.get_config(CFG_MASTER)
            if msg_str.startswith("point") and meta.user_id in master_list:
                return True, should_pass, ("mod", msg_str[5:].strip())
        return should_proc, should_pass, None

    def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        port = GroupMessagePort(meta.group_id) if meta.group_id else PrivateMessagePort(meta.user_id)
        # 解析语句
        cmd_type: Literal["show", "mod"] = hint[0]
        msg_str: Optional[str] = hint[1]
        feedback: str = ""
        point_init = int(self.bot.cfg_helper.get_config(CFG_POINT_INIT)[0])
        if cmd_type == "show":
            cur_point = self.bot.data_manager.get_data(DC_POINT, [meta.user_id, DCK_POINT_CUR], default_val=point_init)
            max_point = int(self.bot.cfg_helper.get_config(CFG_POINT_MAX)[0])
            nickname = self.bot.get_nickname(meta.user_id, meta.group_id)
            feedback = self.format_loc(LOC_POINT_SHOW, name=nickname, point=f"{cur_point}/{max_point}")
        elif cmd_type == "mod":
            if "=" in msg_str:
                target_id, target_point = msg_str.split("=", 1)
                try:
                    target_point = int(target_point)
                    nickname = self.bot.get_nickname(meta.user_id, target_id)
                    prev_point = self.bot.data_manager.get_data(DC_POINT, [target_id, DCK_POINT_CUR], default_val=point_init)
                    self.bot.data_manager.set_data(DC_POINT, [target_id, DCK_POINT_CUR], target_point)
                    feedback = self.format_loc(LOC_POINT_EDIT, result=f"{target_id}({nickname}) {prev_point}->{target_point}")
                except ValueError:
                    feedback = self.format_loc(LOC_POINT_EDIT_ERROR, error=str(ValueError))
            else:
                target_id = msg_str
                nickname = self.bot.get_nickname(meta.user_id, target_id)
                prev_point = self.bot.data_manager.get_data(DC_POINT, [target_id, DCK_POINT_CUR], default_val=point_init)
                feedback = self.format_loc(LOC_POINT_EDIT, result=f"{target_id}({nickname}): {prev_point}")

        return [BotSendMsgCommand(self.bot.account, feedback, [port])]

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        if keyword == "point":  # help后的接着的内容
            feedback: str = "输入.point查看当前点数, 点数在使用消耗较大的指令时消耗"
            master_list = self.bot.cfg_helper.get_config(CFG_MASTER)
            if meta.user_id in master_list:
                feedback += "\n.m point [目标账号] 查看对方点数" \
                            "\n.m point [目标账号]=[目标数值] 将目标账号的点数设为指定数值"
            return feedback
        return ""

    def get_description(self) -> str:
        return ".point 查看当前点数"  # help指令中返回的内容
