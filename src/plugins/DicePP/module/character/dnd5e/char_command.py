"""
DND角色卡指令
"""

from typing import List, Tuple, Any, Optional
import re

from core.bot import Bot
from core.data import DataChunkBase, custom_data_chunk, DataManagerError
from core.command.const import *
from core.command import UserCommandBase, custom_user_command
from core.command import BotCommandBase, BotSendMsgCommand
from core.communication import MessageMetaData, PrivateMessagePort, GroupMessagePort

from module.character.dnd5e import DNDCharInfo, gen_template_char

LOC_CHAR_SET = "char_set"
LOC_CHAR_MISS = "char_miss"
LOC_CHAR_DEL = "char_delete"
LOC_CHECK_RES = "check_result"

DC_CHAR_DND = "character_dnd"

CMD_TYPE_CHAR = "char"
CMD_TYPE_STATE = "state"
CMD_TYPE_CHECK = "check"
CMD_TYPE_SAVING = "check_saving"
CMD_TYPE_ATTACK = "check_attack"
CMD_TYPE_HP_DICE = "hp_dice"
CMD_TYPE_REST_LONG = "rest_long"


@custom_data_chunk(identifier=DC_CHAR_DND, include_json_object=True)
class _(DataChunkBase):
    def __init__(self):
        super().__init__()


@custom_user_command(readable_name="DND5E角色卡", priority=DPP_COMMAND_PRIORITY_DEFAULT+10,
                     flag=DPP_COMMAND_FLAG_CHAR | DPP_COMMAND_FLAG_DND, group_only=True)
class CharacterDNDCommand(UserCommandBase):
    """
    DND角色卡指令
    """

    def __init__(self, bot: Bot):
        super().__init__(bot)
        bot.loc_helper.register_loc_text(LOC_CHAR_SET, "Already set your character", "成功设置角色卡")
        bot.loc_helper.register_loc_text(LOC_CHAR_MISS, "Cannot find your character", "找不到有效的角色卡")
        bot.loc_helper.register_loc_text(LOC_CHAR_DEL, "Already delete your character", "删除角色卡")
        bot.loc_helper.register_loc_text(LOC_CHECK_RES, "{name} throw {check}\n{hint}\n{result}", "删除角色卡")

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        should_proc: bool = True
        should_pass: bool = False
        cmd_type = ""
        info: Any = 0
        if msg_str.startswith("."):
            if msg_str.startswith(".角色卡"):
                cmd_type = CMD_TYPE_CHAR
                info = msg_str[4:].strip()
            elif msg_str.startswith(".状态"):
                cmd_type = CMD_TYPE_STATE
            elif re.match(r"\.([1-9]#)?..检定", msg_str):
                cmd_type = CMD_TYPE_CHECK
                msg_str = msg_str[1:]
                time: int = 1
                check_name, mod_str = msg_str.split("检定", maxsplit=1)
                if "#" in check_name:
                    time_str, check_name = check_name.split("#")
                    time = int(time_str)
                info = (time, check_name, mod_str)
            elif re.match(r"\.([1-9]#)?..豁免", msg_str):
                cmd_type = CMD_TYPE_SAVING
                msg_str = msg_str[1:]
                time: int = 1
                check_name, mod_str = msg_str.split("豁免", maxsplit=1)
                if "#" in check_name:
                    time_str, check_name = check_name.split("#")
                    time = int(time_str)
                info = (time, check_name+"豁免", mod_str)
            elif re.match(r"\.([1-9]#)?..攻击", msg_str):
                cmd_type = CMD_TYPE_ATTACK
                msg_str = msg_str[1:]
                time: int = 1
                check_name, mod_str = msg_str.split("攻击", maxsplit=1)
                if "#" in check_name:
                    time_str, check_name = check_name.split("#")
                    time = int(time_str)
                info = (time, check_name+"攻击", mod_str)
            elif re.match(r"\.([1-9][0-9]?#)?生命骰", msg_str):
                cmd_type = CMD_TYPE_HP_DICE
                msg_str = msg_str[1:]
                time: int = 1
                if "#" in msg_str:
                    time_str, _ = msg_str.split("#", maxsplit=1)
                    time = int(time_str)
                info = time
            elif msg_str.startswith(".长休"):
                cmd_type = CMD_TYPE_REST_LONG
            else:
                should_proc = False
        else:
            should_proc = False

        return should_proc, should_pass, (cmd_type, info)

    def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        port = GroupMessagePort(meta.group_id) if meta.group_id else PrivateMessagePort(meta.user_id)
        # 解析语句
        cmd_type: str = hint[0]
        feedback: str = ""
        # 获取当前角色卡
        char_info: Optional[DNDCharInfo]
        try:
            char_info = self.bot.data_manager.get_data(DC_CHAR_DND, [meta.group_id, meta.user_id])
        except DataManagerError:
            char_info = None

        if cmd_type == CMD_TYPE_CHAR:
            content: str = hint[1]
            if not content:  # 查看角色卡
                if not char_info:
                    feedback = self.format_loc(LOC_CHAR_MISS)
                else:
                    feedback = char_info.get_char_info()

            elif content.startswith("记录"):  # 记录角色卡
                content = content[2:].strip()
                char_info = DNDCharInfo()
                try:
                    char_info.initialize(content)
                    self.bot.data_manager.set_data(DC_CHAR_DND, [meta.group_id, meta.user_id], char_info)
                    feedback = self.format_loc(LOC_CHAR_SET)
                except AssertionError as e:
                    char_info = None
                    feedback = e.args[0]
                # 设置昵称
                if char_info and char_info.name:
                    self.bot.update_nickname(meta.user_id, meta.group_id, char_info.name)

            elif content.startswith("清除"):  # 清除角色卡
                self.bot.data_manager.delete_data(DC_CHAR_DND, [meta.group_id, meta.user_id], ignore_miss=True)
                feedback = self.format_loc(LOC_CHAR_DEL)

            elif content.startswith("模板"):  # 查看角色卡模板
                temp_char = gen_template_char()
                feedback = temp_char.get_char_info()
            else:
                feedback = "可用的角色卡指令: [记录, 清除, 模板]"

        elif cmd_type == CMD_TYPE_STATE:  # 查看角色卡状态
            if not char_info:
                feedback = self.format_loc(LOC_CHAR_MISS)
            else:
                feedback = char_info.hp_info.get_info()
                if char_info.hp_info.hp_dice_type != 0:
                    feedback += f"\n生命骰:{char_info.hp_info.hp_dice_num}/{char_info.hp_info.hp_dice_max} D{char_info.hp_info.hp_dice_type}"

        elif cmd_type in [CMD_TYPE_CHECK, CMD_TYPE_SAVING, CMD_TYPE_ATTACK]:  # 执行检定
            time, check_name, mod_str = hint[1]
            advantage: int = 0
            if mod_str.startswith("优势"):
                advantage = 1
                mod_str = mod_str[2:]
            if mod_str.startswith("劣势"):
                advantage = -1
                mod_str = mod_str[2:]
            if not char_info:
                feedback = self.format_loc(LOC_CHAR_MISS)
            else:
                check_result_list: List[str] = []
                check_value_list: List[int] = []
                name_str = ""
                if check_name == "先攻":
                    time = 1
                try:
                    hint_str = ""
                    for t in range(time):
                        hint_str, result_str, result_val = char_info.ability_info.perform_check(check_name, advantage, mod_str)
                        check_result_list.append(result_str)
                        check_value_list.append(result_val)
                    name_str = char_info.name
                    if not name_str:
                        name_str = self.bot.get_nickname(meta.user_id, meta.group_id)
                    check_name = check_name + "检定" if time <= 1 else f"{time}次{check_name}检定"
                    result_str = "\n".join(check_result_list)
                    feedback = self.format_loc(LOC_CHECK_RES, name=name_str, check=check_name, hint=hint_str, result=result_str)
                except AssertionError as e:
                    feedback = e.args[0]
                if check_name == "先攻检定" and len(check_value_list) > 0:
                    try:
                        from module.initiative import InitiativeCommand
                        assert InitiativeCommand.__name__ in self.bot.command_dict, "未注册先攻指令"
                        init_cmd: InitiativeCommand = self.bot.command_dict[InitiativeCommand.__name__]
                        assert name_str, "Unexpected Code: name_str is empty"
                        result_dict = {name_str: (check_value_list[0], check_result_list[0])}
                        init_feedback = init_cmd.add_initiative_entities(result_dict, meta.user_id, meta.group_id)
                        if check_result_list[0] in feedback:
                            feedback = feedback.replace(check_result_list[0], init_feedback)
                        else:
                            feedback += f"\n{init_feedback}"
                    except (ImportError, AssertionError) as e:
                        feedback += f"\n加入先攻列表失败: {e}"

        elif cmd_type == CMD_TYPE_HP_DICE:  # 使用生命骰
            time = hint[1]
            if not char_info:
                feedback = self.format_loc(LOC_CHAR_MISS)
            else:
                feedback = char_info.use_hp_dice(time)
                self.bot.data_manager.set_data(DC_CHAR_DND, [meta.group_id, meta.user_id], char_info)
        elif cmd_type == CMD_TYPE_REST_LONG:  # 进行长休
            if not char_info:
                feedback = self.format_loc(LOC_CHAR_MISS)
            else:
                feedback = char_info.long_rest()
                self.bot.data_manager.set_data(DC_CHAR_DND, [meta.group_id, meta.user_id], char_info)

        return [BotSendMsgCommand(self.bot.account, feedback, [port])]

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        if keyword == "角色卡":  # help后的接着的内容
            feedback: str = ".角色卡  #查看角色卡\n" \
                            ".角色卡记录 [角色卡内容]  #生成角色卡\n" \
                            ".角色卡清除  # 删除当前角色卡\n" \
                            ".角色卡模板  # 查看角色卡模板\n" \
                            "使用方法:\n" \
                            "先使用角色卡模板指令获得模板, 自行修改模板中的内容后再使用角色卡记录指令记录角色卡\n" \
                            "示例:\n" \
                            ".角色卡记录\n" \
                            "$姓名$ 伊丽莎白\n" \
                            "$等级$ 20\n" \
                            "... (以下略)" \
                            "将上述内容作为一条指令输入即可记录伊丽莎白的角色卡\n" \
                            "每人在每个群中只能拥有一张角色卡, 但通过记录[.角色卡]指令返回的内容即可自行保存多张角色卡\n" \
                            "输入 .help角色卡内容 可查看每个条目的含义, .help角色卡使用 可查看角色卡衍生指令的说明"
            return feedback
        elif keyword == "角色卡内容":
            feedback: str = "$姓名$ (可选) 若给定姓名, 将会在设置角色卡时自动设置玩家昵称(.nn)\n" \
                            "$等级$ 正整数\n" \
                            "$生命值$ (可选) [当前生命值]/[最大生命值] ([临时生命值]) 最大生命值和临时生命值可以不给出\n" \
                            "$生命骰$ (可选) [当前生命骰]/[最大生命骰] D[生命骰面数] 必须先给定生命值才有效\n" \
                            "$属性$ 六个用/分隔的正整数, 依次为力量,敏捷,体质,智力,感知,魅力\n" \
                            "$熟练$ (可选) 不同熟练项用/分隔, 可选熟练项为18种技能, 先攻, 6大属性及对应的攻击和豁免. 所有攻击默认熟练. " \
                            "熟练项默认倍数为1, 若拥有双倍或多倍熟练可通过[倍数]*[熟练项]的方式指定, 倍数为非负正整数\n" \
                            "$额外加值$ (可选) 不同额外加值项目用/分割, 每个项目由[项目名]:[加值内容]构成, 项目除上述熟练项以外还可以从[攻击, 豁免, 熟练加值]中选择 " \
                            "加值内容必须以[+, -, 优势, 劣势]其中之一开头, 除优劣势以外的加值内容会直接叠加到掷骰表达式末尾"
            return feedback
        elif keyword == "角色卡使用":
            feedback: str = "角色卡目前的衍生指令包括[检定, 生命骰, 长休]功能\n" \
                            "--- 检定功能 ---\n" \
                            ".[次数#][检定条目][临时调整值]\n" \
                            "次数必须为1~9之间的整数, 检定条目包括以下几种:\n" \
                            "1. [力量/敏捷/...]检定\n2. [运动/体操/...]检定\n3. 先攻检定(会将角色加入到先攻列表中)\n4. [力量/敏捷/...]豁免\n5. [力量/敏捷/...]攻击\n" \
                            "临时调整值规则和额外加值相同, 必须以[+, -, 优势, 劣势]开头\n" \
                            "示例:\n" \
                            ".运动检定\n" \
                            ".2#力量检定\n" \
                            ".智力豁免+d4\n" \
                            ".3#敏捷攻击优势+d8\n" \
                            "--- 生命骰功能 ---\n" \
                            ".[次数#]生命骰\n" \
                            "次数必须为1~99之间的整数, 不输入则默认为1, 回复后的生命值不会超过上限\n" \
                            "--- 长休功能 ---\n" \
                            ".长休\n" \
                            "长休时会回复生命至上限, 并按规则回复生命骰"
            return feedback
        return ""

    def get_description(self) -> str:
        return ".角色卡 DND5E角色卡, 衍生功能包括检定/生命骰/长休等"  # help指令中返回的内容
