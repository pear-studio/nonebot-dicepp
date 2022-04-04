"""
统计指令, 返回用户或群聊的一些统计信息
"""

from typing import List, Tuple, Any, Dict

from core.bot import Bot
from core.data import DataManagerError
from core.command.const import *
from core.command import UserCommandBase, custom_user_command
from core.command import BotCommandBase, BotSendMsgCommand
from core.communication import MessageMetaData, PrivateMessagePort, GroupMessagePort

from core.data import DC_META, DC_NICKNAME, DC_MACRO, DC_VARIABLE, DC_USER_DATA, DC_GROUP_DATA
from core.data import DCK_TOTAL_NUM, DCK_TODAY_NUM, DCK_LAST_NUM, DCK_LAST_TIME
from core.data import DCP_META_ONLINE_PERIOD, DCP_META_ONLINE_LAST
from core.data import DCP_META_CMD_TOTAL_NUM, DCP_META_CMD_TODAY_NUM, DCP_META_CMD_LAST_NUM
from core.data import DCP_META_MSG_TOTAL_NUM, DCP_META_MSG_TODAY_NUM, DCP_META_MSG_LAST_NUM
from core.data import DCP_USER_CMD_FLAG_A_UID, DCP_USER_META_A_UID, DCP_GROUP_CMD_FLAG_A_GID, DCP_GROUP_INFO_A_GID, DCP_GROUP_META_A_GID,\
    DCP_USER_MSG_A_UID, DCP_GROUP_MSG_A_GID
from module.roll import DCP_GROUP_DATA_ROLL_A_GID, DCP_USER_DATA_ROLL_A_UID, DCP_ROLL_TIME_A_ID_ROLL, DCP_ROLL_D20_A_ID_ROLL, DCK_ROLL_TOTAL, DCK_ROLL_TODAY

# LOC_TEMP = "template_loc"


@custom_user_command(readable_name="指令模板", priority=DPP_COMMAND_PRIORITY_DEFAULT, flag=DPP_COMMAND_FLAG_INFO)
class StatisticsCommand(UserCommandBase):
    """
    统计指令, 返回用户或群聊的一些统计信息
    """

    def __init__(self, bot: Bot):
        super().__init__(bot)
        # bot.loc_helper.register_loc_text(LOC_TEMP, "内容", "注释")

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        should_proc: bool = msg_str.startswith(".统计")
        should_pass: bool = False
        return should_proc, should_pass, msg_str[3:].strip()

    def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        port = GroupMessagePort(meta.group_id) if meta.group_id else PrivateMessagePort(meta.user_id)
        # 解析语句
        arg_str = hint
        feedback: str = ""
        if not arg_str:  # 统计当前用户信息
            # 统计处理信息情况
            try:
                msg_info: Dict[str, int] = self.bot.data_manager.get_data(DC_USER_DATA, [meta.user_id] + DCP_USER_MSG_A_UID)
            except DataManagerError:
                msg_info = {}
            feedback += f"今日收到信息:{msg_info.get(DCK_TODAY_NUM, 0)}, 昨日:{msg_info.get(DCK_LAST_NUM, 0)}, 总计:{msg_info.get(DCK_TOTAL_NUM)}\n"
            # 统计指令使用情况
            try:
                cmd_flag_info: Dict = self.bot.data_manager.get_data(DC_USER_DATA, [meta.user_id] + DCP_USER_CMD_FLAG_A_UID)
                feedback += stat_cmd_info(cmd_flag_info)
            except DataManagerError:
                feedback += "暂无指令记录\n"
            # 统计掷骰情况
            try:
                roll_time_path = [meta.user_id] + DCP_USER_DATA_ROLL_A_UID + DCP_ROLL_TIME_A_ID_ROLL
                roll_time_data: Dict[str, int] = self.bot.data_manager.get_data(DC_USER_DATA, roll_time_path)
                roll_d20_path = [meta.user_id] + DCP_USER_DATA_ROLL_A_UID + DCP_ROLL_D20_A_ID_ROLL
                d20_data: Dict[str, List[int]] = self.bot.data_manager.get_data(DC_USER_DATA, roll_d20_path)
                today_info = f"今日掷骰次数:{roll_time_data[DCK_ROLL_TODAY]} D20统计:{d20_data[DCK_ROLL_TODAY][1:]}"
                d20_avg = sum([(i+1)*num for i, num in enumerate(d20_data[DCK_ROLL_TODAY][1:])]) / sum(d20_data[DCK_ROLL_TODAY][1:])
                today_info += " 平均值: {:.3f}".format(d20_avg)
                total_info = f"总计掷骰次数:{roll_time_data[DCK_ROLL_TOTAL]} D20统计:{d20_data[DCK_ROLL_TOTAL][1:]}"
                d20_avg = sum([(i+1)*num for i, num in enumerate(d20_data[DCK_ROLL_TOTAL][1:])]) / sum(d20_data[DCK_ROLL_TOTAL][1:])
                total_info += " 平均值: {:.3f}".format(d20_avg)
                feedback += f"{today_info}\n{total_info}\n"
            except DataManagerError:
                pass
        elif arg_str == "群聊":
            if not meta.group_id:
                feedback += f"当前不在群聊中..."
            # 统计处理信息情况
            try:
                msg_info: Dict[str, int] = self.bot.data_manager.get_data(DC_GROUP_DATA, [meta.group_id] + DCP_GROUP_MSG_A_GID)
            except DataManagerError:
                msg_info = {}
            feedback += f"今日收到信息:{msg_info.get(DCK_TODAY_NUM, 0)}, 昨日:{msg_info.get(DCK_LAST_NUM, 0)}, 总计:{msg_info.get(DCK_TOTAL_NUM)}\n"
            # 统计指令使用情况
            try:
                cmd_flag_info: Dict = self.bot.data_manager.get_data(DC_GROUP_DATA, [meta.group_id] + DCP_GROUP_CMD_FLAG_A_GID)
                feedback += stat_cmd_info(cmd_flag_info)
            except DataManagerError:
                feedback += "暂无指令记录\n"
            # 统计掷骰情况
            try:
                roll_time_path = [meta.group_id] + DCP_GROUP_DATA_ROLL_A_GID + DCP_ROLL_TIME_A_ID_ROLL
                roll_time_data: Dict[str, int] = self.bot.data_manager.get_data(DC_GROUP_DATA, roll_time_path)
                roll_d20_path = [meta.user_id] + DCP_GROUP_DATA_ROLL_A_GID + DCP_ROLL_D20_A_ID_ROLL
                d20_data: Dict[str, List[int]] = self.bot.data_manager.get_data(DC_GROUP_DATA, roll_d20_path)
                today_info = f"今日掷骰次数:{roll_time_data[DCK_ROLL_TODAY]} D20情况:{d20_data[DCK_ROLL_TODAY][1:]}"
                total_info = f"总计掷骰次数:{roll_time_data[DCK_ROLL_TOTAL]} D20情况:{d20_data[DCK_ROLL_TOTAL][1:]}"
                feedback += f"{today_info}\n{total_info}\n"
            except DataManagerError:
                pass
        elif arg_str == "所有用户":
            if meta.user_id not in self.bot.get_master_ids():
                feedback = "权限不足"
            else:
                msg_info: Dict[str, int] = {DCK_TODAY_NUM: 0, DCK_LAST_NUM: 0, DCK_TOTAL_NUM: 0}
                cmd_flag_info = {}
                for user_id in self.bot.data_manager.get_keys(DC_USER_DATA, []):
                    # 统计处理信息情况
                    try:
                        user_msg_info: Dict[str, int] = self.bot.data_manager.get_data(DC_USER_DATA, [user_id] + DCP_USER_MSG_A_UID)
                    except DataManagerError:
                        user_msg_info = {}
                    msg_info[DCK_TODAY_NUM] += user_msg_info.get(DCK_TODAY_NUM, 0)
                    msg_info[DCK_LAST_NUM] += user_msg_info.get(DCK_LAST_NUM, 0)
                    msg_info[DCK_TOTAL_NUM] += user_msg_info.get(DCK_TOTAL_NUM, 0)
                    # 统计指令使用情况
                    try:
                        user_cmd_flag_info: Dict = self.bot.data_manager.get_data(DC_USER_DATA, [user_id] + DCP_USER_CMD_FLAG_A_UID)
                        merge_cmd_num(user_cmd_flag_info, cmd_flag_info)
                    except DataManagerError:
                        pass
                feedback += f"今日收到信息:{msg_info.get(DCK_TODAY_NUM, 0)}, 昨日:{msg_info.get(DCK_LAST_NUM, 0)}, 总计:{msg_info.get(DCK_TOTAL_NUM)}\n"
                feedback += stat_cmd_info(cmd_flag_info)
        elif meta.user_id in self.bot.get_master_ids() and arg_str == "所有群聊":
            if meta.user_id not in self.bot.get_master_ids():
                feedback = "权限不足"
            else:
                group_info_list: List[List[str, int, str]] = []  # id, sort_key, info_str
                for group_id in self.bot.data_manager.get_keys(DC_GROUP_DATA, []):
                    group_info = [group_id, 0, ""]
                    # 统计元数据
                    try:
                        info_dict = self.bot.data_manager.get_data(DC_GROUP_DATA, [group_id] + DCP_GROUP_INFO_A_GID)
                    except DataManagerError:
                        info_dict = {}
                    group_info[1] = info_dict.get("member_count", 0)  # 最小优先级
                    group_info[2] += f"{group_id}({info_dict.get('name', '未知')}) 成员:{info_dict.get('member_count', 0)} "
                    # 统计处理信息情况
                    try:
                        msg_info: Dict[str, int] = self.bot.data_manager.get_data(DC_GROUP_DATA, [group_id] + DCP_GROUP_MSG_A_GID)
                    except DataManagerError:
                        msg_info = {}
                    group_info[1] += ((msg_info.get(DCK_TODAY_NUM, 0) + msg_info.get(DCK_LAST_NUM, 0)) // 1000) << 32  # 最大优先级
                    group_info[2] += f"信息:[{msg_info.get(DCK_TODAY_NUM, 0)}, {msg_info.get(DCK_LAST_NUM, 0)}, {msg_info.get(DCK_TOTAL_NUM, 0)}] "
                    # 统计指令使用情况
                    try:
                        cmd_flag_info: Dict = self.bot.data_manager.get_data(DC_GROUP_DATA, [meta.group_id] + DCP_GROUP_CMD_FLAG_A_GID)
                        cmd_score = stat_cmd_score(cmd_flag_info, msg_info[DCK_LAST_NUM], msg_info[DCK_TOTAL_NUM])
                        group_info[1] += cmd_score << 16  # 次大优先级
                        group_info[2] += f"评分:{cmd_score}"
                    except (DataManagerError, KeyError):
                        group_info[2] += f"评分暂无"
                    group_info_list.append(group_info)
                group_info_list = sorted(group_info_list, key=lambda x: -x[1])
                feedback += f"共{len(group_info_list)}条群组信息:\n"
                feedback += "\n".join([group_info[2] for group_info in group_info_list[:50]])
                if len(group_info_list) > 50:
                    feedback += f"\n{len(group_info_list) - 50}条信息限于篇幅未显示完全"

        feedback = feedback.strip()
        return [BotSendMsgCommand(self.bot.account, feedback, [port])]

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        if keyword == "统计":  # help后的接着的内容
            feedback: str = "可以统计用户和群聊的各种信息\n" \
                            ".统计 显示当前用户的统计信息\n" \
                            ".统计群聊 显示当前群聊的统计信息\n" \
                            "[Master专用]\n" \
                            ".统计所有用户 可以显示当前所有用户对指令的使用情况\n" \
                            ".统计所有群聊 可以显示每一个群聊对指令的使用情况"
            return feedback
        return ""

    def get_description(self) -> str:
        return ".统计 统计用户与群聊信息"  # help指令中返回的内容


def stat_cmd_info(cmd_flag_info) -> str:
    total_info_list, today_info_list = [], []
    for flag, name in DPP_COMMAND_FLAG_DICT.items():
        if flag not in cmd_flag_info:
            continue
        if flag & DPP_COMMAND_FLAG_SET_HIDE_IN_STAT:
            continue
        total_num, today_num = cmd_flag_info[flag][DCK_TOTAL_NUM], cmd_flag_info[flag][DCK_TODAY_NUM]
        if total_num:
            total_info_list.append(f"{name}:{total_num}")
        if today_num:
            today_info_list.append(f"{name}:{today_num}")
    if total_info_list:
        total_info = ", ".join(total_info_list)
    else:
        total_info = "暂无记录"
    if today_info_list:
        today_info = ", ".join(today_info_list)
    else:
        today_info = "暂无记录"
    return f"今日指令记录: {today_info}\n总计: {total_info}\n"


def merge_cmd_num(cur_cmd_flag_info, merged_cmd_flag_info) -> None:
    for flag in DPP_COMMAND_FLAG_DICT:
        if flag not in cur_cmd_flag_info:
            continue
        if flag not in merged_cmd_flag_info:
            merged_cmd_flag_info[flag] = {DCK_TODAY_NUM: 0, DCK_LAST_NUM: 0, DCK_TOTAL_NUM: 0}
        merged_cmd_flag_info[flag][DCK_TODAY_NUM] += cur_cmd_flag_info[flag][DCK_TODAY_NUM]
        merged_cmd_flag_info[flag][DCK_LAST_NUM] += cur_cmd_flag_info[flag][DCK_LAST_NUM]
        merged_cmd_flag_info[flag][DCK_TOTAL_NUM] += cur_cmd_flag_info[flag][DCK_TOTAL_NUM]


def stat_cmd_score(cmd_flag_info, last_msg_num, total_msg_num) -> int:
    res = 0
    for flag in DPP_COMMAND_FLAG_DICT:
        if flag not in cmd_flag_info:
            continue
        total_num, last_num = cmd_flag_info[flag][DCK_TOTAL_NUM], cmd_flag_info[flag][DCK_LAST_NUM]
        if flag & DPP_COMMAND_FLAG_SET_STD:
            weight: float = (total_num/total_msg_num/50 + last_num/last_msg_num) * 100
            res += min(weight, 1)
        if flag & DPP_COMMAND_FLAG_SET_EXT_0:
            weight: float = (total_num/total_msg_num/50 + last_num/last_msg_num) * 100
            res -= min(weight, 1)
    return int(res * 100)
