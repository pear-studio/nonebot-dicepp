import random, datetime
from typing import List, Tuple, Any

from core.bot import Bot
from core.command.const import *
from core.command import UserCommandBase, custom_user_command
from core.command import BotCommandBase, BotSendMsgCommand
from core.communication import MessageMetaData, PrivateMessagePort, GroupMessagePort
from utils.time import datetime_to_str_day, get_current_date_raw, datetime_to_str

LOC_JRRP = "jrrp"
LOC_JRRP_LOWER = "jrrp_lower"
LOC_JRRP_SAME = "jrrp_same"
LOC_JRRP_HIGHER = "jrrp_higher"
LOC_JRRP_MIN = "jrrp_min"
LOC_JRRP_MAX = "jrrp_max"

@custom_user_command(readable_name="今日人品", priority=DPP_COMMAND_PRIORITY_DEFAULT,
                     flag=DPP_COMMAND_FLAG_FUN)
class JrrpCommand(UserCommandBase):

    def __init__(self, bot: Bot):
        super().__init__(bot)
        bot.loc_helper.register_loc_text(LOC_JRRP, "{name}的今日人品是:{jrrp}", ".jrrp返回内容,{name}:用户名,{jrrp}:今日人品值.")
        bot.loc_helper.register_loc_text(LOC_JRRP_LOWER, "\n人品比昨天下降了{delta_percent}%呢...", ".jrrp比昨天值更低时追加的内容,{zrrp}:昨日人品值,{delta}:差值,{delta_percent}:百分比差值")
        bot.loc_helper.register_loc_text(LOC_JRRP_SAME, "\n人品与昨天相同呢。", ".jrrp和昨天相同时追加的内容,{zrrp}:昨日人品值")
        bot.loc_helper.register_loc_text(LOC_JRRP_HIGHER, "\n人品比昨天上升了{delta_percent}%！", ".jrrp比昨天值更低时追加的内容,{zrrp}:昨日人品值,{delta}:差值,{delta_percent}:百分比差值")
        bot.loc_helper.register_loc_text(LOC_JRRP_MIN, "{name}的今日人品是...你确定要听么..是大凶的{jrrp}哦...", ".jrrp出最小值时返回的内容,{name}:用户名,{jrrp}:今日人品值.")
        bot.loc_helper.register_loc_text(LOC_JRRP_MAX, "{name}的今日人品是...这是！这是大吉的{jrrp}哦！", ".jrrp出最大值时返回的内容,{name}:用户名,{jrrp}:今日人品值.")

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        should_proc: bool = msg_str.startswith(".jrrp")
        should_pass: bool = False
        return should_proc, should_pass, msg_str[5:].strip()

    def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        port = GroupMessagePort(meta.group_id) if meta.group_id else PrivateMessagePort(meta.user_id)
        # 解析语句
        random.seed(datetime_to_str_day(get_current_date_raw() - datetime.timedelta(days=1)) + str(meta.user_id))  # 获取昨日数据与用户id，拼接形成一个固定的seed
        zrrp: int = random.randint(1, 100)  # 根据上面的seed获取昨日人品
        
        random.seed(datetime_to_str_day(get_current_date_raw()) + str(meta.user_id))  # 获取今日数据与用户id，拼接形成一个固定的seed
        jrrp: int = random.randint(1, 100)  # 根据上面的seed获取今日人品

        random.seed(datetime_to_str(get_current_date_raw()))  #  复原seed

        user_name: str = self.bot.get_nickname(meta.user_id, meta.group_id)
        feedback: str = ""
        if jrrp == 1:  #  最小值
            feedback += self.format_loc(LOC_JRRP_MIN, name=user_name, jrrp=str(jrrp))
        elif jrrp == 100:  #  最大值
            feedback += self.format_loc(LOC_JRRP_MAX, name=user_name, jrrp=str(jrrp))
        else:  #  正常情况
            feedback += self.format_loc(LOC_JRRP, name=user_name, jrrp=str(jrrp))
        
        if jrrp > zrrp:  #  今日人品比昨日高
            feedback += self.format_loc(LOC_JRRP_HIGHER, zrrp=str(zrrp), jrrp=str(jrrp), delta=str(jrrp - zrrp), delta_percent=str(round((jrrp - zrrp) / zrrp * 100,2)))
        elif jrrp < zrrp:  #  今日人品比昨日低
            feedback += self.format_loc(LOC_JRRP_LOWER, zrrp=str(zrrp), jrrp=str(jrrp), delta=str(zrrp - jrrp), delta_percent=str(round((zrrp - jrrp) / zrrp * 100,2)))
        else:  #  两日人品相同
            feedback += self.format_loc(LOC_JRRP_SAME, zrrp=str(zrrp), jrrp=str(jrrp))

        return [BotSendMsgCommand(self.bot.account, feedback, [port])]

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        if keyword == "jrrp":  # help后的接着的内容
            feedback: str = ".jrrp 获取今日人品，每日0点刷新"
            return feedback
        return ""

    def get_description(self) -> str:
        return ".jrrp 获取今日人品"  # help指令中返回的内容
