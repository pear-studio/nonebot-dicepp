from typing import List, Tuple, Any, Optional, Literal

from core.bot import Bot
from core.data import DataChunkBase, custom_data_chunk, DataManagerError, DC_USER_DATA
from core.command.const import *
from core.command import UserCommandBase, custom_user_command
from core.command import BotCommandBase, BotSendMsgCommand
from core.communication import MessageMetaData, PrivateMessagePort, GroupMessagePort
from core.config import CFG_MASTER


LOC_FAVOR_SHOW = "favor_show"
LOC_FAVOR_BLOCK = "favor_block"
LOC_FAVOR_PHASE_HAPPY = "favor_phase_happy"
LOC_FAVOR_PHASE_COMMON = "favor_phase_common"
LOC_FAVOR_PHASE_UNGOOD = "favor_phase_ungood"
LOC_FAVOR_PHASE_ANGRY = "favor_phase_angry"
LOC_FAVOR_PHASE_BLOCK = "favor_phase_block"

CFG_FAVOR_DEFAULT = "favor_default"
CFG_FAVOR_MAX = "favor_max"
CFG_FAVOR_MIN = "favor_min"
CFG_FAVOR_DALIY_CHANGE_MAX = "favor_daliy_change_max"
CFG_FAVOR_HAPPY_VAR = "favor_happy_var"
CFG_FAVOR_COMMON_VAR = "favor_common_var"
CFG_FAVOR_UNGOOD_VAR = "favor_ungood_var"
CFG_FAVOR_ANGRY_VAR = "favor_angry_var"
CFG_FAVOR_BLOCK_VAR = "favor_block_var"

CFG_FAVOR_MOD_BLOCK = "favor_mod_block"
CFG_FAVOR_MOD_USE = "favor_mod_use"
CFG_FAVOR_MOD_NATURE = "favor_mod_nature"
CFG_FAVOR_MOD_REPEAT = "favor_mod_repeat"
CFG_FAVOR_MOD_WRONG_USE = "favor_mod_wrong_use"

DC_FAVOR = "favor"

@custom_data_chunk(identifier=DC_POINT)
class _(DataChunkBase):
    def __init__(self):
        super().__init__()


@custom_user_command(readable_name="好感指令", priority=DPP_COMMAND_PRIORITY_DEFAULT,
                     flag=DPP_COMMAND_FLAG_INFO)
class FavorCommand(UserCommandBase):
    """
    .point 和.m point指令
    """

    def __init__(self, bot: Bot):
        super().__init__(bot)
        bot.loc_helper.register_loc_text(LOC_FAVOR_SHOW , "{name}的好感度为{favor}点，处于{phase}状态", "用户查看点数的回复")

        bot.cfg_helper.register_config(CFG_FAVOR_DEFAULT, "1000", "新用户初始好感度")

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
