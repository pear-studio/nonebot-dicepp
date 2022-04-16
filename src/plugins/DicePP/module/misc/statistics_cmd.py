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

from core.data import DC_META, DC_NICKNAME, DC_MACRO, DC_VARIABLE, DC_USER_DATA, DC_GROUP_DATA, DCK_USER_STAT, DCK_GROUP_STAT
from core.statistics import GroupStatInfo, UserStatInfo, UserCommandStatInfo, RollStatInfo

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
                user_stat: UserStatInfo = self.bot.data_manager.get_data(DC_USER_DATA, [meta.user_id, DCK_USER_STAT])
            except DataManagerError:
                user_stat = UserStatInfo()
            feedback += f"今日收到信息:{user_stat.msg.cur_day_val}, 昨日:{user_stat.msg.last_day_val}, 总计:{user_stat.msg.total_val}\n"
            # 统计指令使用情况
            feedback += stat_cmd_info(user_stat.cmd)
            # 统计掷骰情况
            feedback += stat_roll_info(user_stat.roll)
        elif arg_str == "群聊":
            if not meta.group_id:
                feedback += f"当前不在群聊中..."
            # 统计处理信息情况
            try:
                group_stat: GroupStatInfo = self.bot.data_manager.get_data(DC_GROUP_DATA, [meta.group_id, DCK_GROUP_STAT])
            except DataManagerError:
                group_stat = GroupStatInfo()
            feedback += f"今日收到信息:{group_stat.msg.cur_day_val}, 昨日:{group_stat.msg.last_day_val}, 总计:{group_stat.msg.total_val}\n"
            # 统计指令使用情况
            feedback += stat_cmd_info(group_stat.cmd)
            # 统计掷骰情况
            feedback += stat_roll_info(group_stat.roll)
        elif arg_str == "所有用户":
            if meta.user_id not in self.bot.get_master_ids():
                feedback = "权限不足"
            else:
                merge_user_stat = UserStatInfo()
                for user_id in self.bot.data_manager.get_keys(DC_USER_DATA, []):
                    try:
                        user_stat: UserStatInfo = self.bot.data_manager.get_data(DC_USER_DATA, [user_id, DCK_USER_STAT])
                    except DataManagerError:
                        continue
                    # 统计处理信息情况
                    merge_user_stat.msg += user_stat.msg
                    # 统计指令使用情况
                    merge_user_stat.cmd += user_stat.cmd
                feedback += f"今日收到信息:{merge_user_stat.msg.cur_day_val}," \
                            f" 昨日:{merge_user_stat.msg.last_day_val}," \
                            f" 总计:{merge_user_stat.msg.total_val}\n"
                feedback += stat_cmd_info(merge_user_stat.cmd)
        elif meta.user_id in self.bot.get_master_ids() and arg_str == "所有群聊":
            if meta.user_id not in self.bot.get_master_ids():
                feedback = "权限不足"
            else:
                group_info_list: List[List[str, int, str]] = []  # id, sort_key, info_str
                for group_id in self.bot.data_manager.get_keys(DC_GROUP_DATA, []):
                    group_info = [group_id, 0, ""]
                    try:
                        group_stat: GroupStatInfo = self.bot.data_manager.get_data(DC_GROUP_DATA, [group_id, DCK_GROUP_STAT])
                    except DataManagerError:
                        continue

                    group_info[1] = group_stat.meta.member_count  # 最小优先级
                    group_info[2] += f"{group_id}({group_stat.meta.name}) 成员:{group_stat.meta.member_count} "

                    group_info[1] += (group_stat.msg.cur_day_val + group_stat.msg.total_val // 1000) << 32  # 最大优先级
                    group_info[2] += f"信息:[{group_stat.msg.cur_day_val}, {group_stat.msg.last_day_val}, {group_stat.msg.total_val}] "
                    # 统计指令使用情况
                    cmd_score = stat_cmd_score(group_stat.cmd, group_stat.msg.last_day_val, group_stat.msg.total_val)
                    group_info[1] += cmd_score << 16  # 次大优先级
                    group_info[2] += f"评分:{cmd_score}"

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


def stat_cmd_info(cmd_stat: UserCommandStatInfo) -> str:
    total_info_list, today_info_list = [], []
    for flag, name in DPP_COMMAND_FLAG_DICT.items():
        if flag not in cmd_stat.flag_dict:
            continue
        if flag & DPP_COMMAND_FLAG_SET_HIDE_IN_STAT:
            continue
        total_num, today_num = cmd_stat.flag_dict[flag].total_val, cmd_stat.flag_dict[flag].cur_day_val
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


def stat_roll_info(roll_stat: RollStatInfo) -> str:
    # 今日
    today_info = f"今日掷骰次数:{roll_stat.times.cur_day_val} D20统计:{roll_stat.d20.cur_list}"
    if sum(roll_stat.d20.cur_list) == 0:
        d20_avg = 0
    else:
        d20_avg = sum([(i + 1) * num for i, num in enumerate(roll_stat.d20.cur_list)]) / sum(roll_stat.d20.cur_list)
    today_info += " 平均值: {:.3f}".format(d20_avg)
    # 总计
    total_info = f"总计掷骰次数:{roll_stat.times.total_val} D20统计:{roll_stat.d20.total_list}"
    if sum(roll_stat.d20.total_list) == 0:
        d20_avg = 0
    else:
        d20_avg = sum([(i + 1) * num for i, num in enumerate(roll_stat.d20.total_list)]) / sum(roll_stat.d20.total_list)
    total_info += " 平均值: {:.3f}".format(d20_avg)
    return f"{today_info}\n{total_info}\n"


def stat_cmd_score(cmd_flag_info: UserCommandStatInfo, last_msg_num: int, total_msg_num: int) -> int:
    res = 0
    for flag in DPP_COMMAND_FLAG_DICT:
        if flag not in cmd_flag_info.flag_dict:
            continue
        total_num, last_num = cmd_flag_info.flag_dict[flag].total_val, cmd_flag_info.flag_dict[flag].last_day_val
        if not last_msg_num:
            last_num, last_msg_num = 0, 1
        if flag & DPP_COMMAND_FLAG_SET_STD:
            weight: float = (total_num/total_msg_num/50 + last_num/last_msg_num) * 100
            res += min(weight, 1)
        if flag & DPP_COMMAND_FLAG_SET_EXT_0:
            weight: float = (total_num/total_msg_num/50 + last_num/last_msg_num) * 100
            res -= min(weight, 1)
    return int(res * 100)
