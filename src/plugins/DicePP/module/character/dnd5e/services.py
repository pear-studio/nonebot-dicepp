"""
DND5E 角色服务层

设计原则:
- 此文件包含依赖 roll 模块的复杂业务逻辑
- 数据模型定义在 core/data/models/character.py
- 这些函数操作 Pydantic 模型但不负责存储

使用示例:
    from core.data.models import HPInfo, AbilityInfo, DNDCharacter
    from module.character.dnd5e.services import HPService, AbilityService, CharacterService
    
    # 使用生命骰
    result = HPService.use_hp_dice(hp_info, num=2, con_mod=3)
    
    # 进行技能检定
    hint, result_str, result_val = AbilityService.perform_check(ability_info, "运动", 0, "")
    
    # 解析角色卡
    character = CharacterService.parse(input_str, group_id, user_id)
"""
from typing import Dict, List, Literal, Optional, Tuple
from collections import defaultdict

from module.roll import exec_roll_exp, RollDiceError, RollResult

from core.data.models import (
    HPInfo, AbilityInfo, DNDCharacter,
    CHAR_INFO_KEY_HP, CHAR_INFO_KEY_HP_DICE,
    CHAR_INFO_KEY_NAME, CHAR_INFO_KEY_LEVEL, CHAR_INFO_KEY_ABILITY,
    CHAR_INFO_KEY_PROF, CHAR_INFO_KEY_EXT,
    ABILITY_LIST, ABILITY_NUM,
    SKILL_LIST, SKILL_NUM, SKILL_PARENT_DICT, SKILL_SYNONYM_DICT,
    SAVING_LIST, SAVING_PARENT_DICT,
    ATTACK_LIST, ATTACK_PARENT_DICT,
    CHECK_ITEM_LIST, CHECK_ITEM_INDEX_DICT,
    EXT_ITEM_LIST, EXT_ITEM_INDEX_DICT,
    SAVING_ALL_KEY, ATTACK_ALL_KEY,
)


# 角色卡关键字列表
CHAR_INFO_KEY_LIST = [
    CHAR_INFO_KEY_NAME,
    CHAR_INFO_KEY_LEVEL,
    CHAR_INFO_KEY_HP,
    CHAR_INFO_KEY_HP_DICE,
    CHAR_INFO_KEY_ABILITY,
    CHAR_INFO_KEY_PROF,
    CHAR_INFO_KEY_EXT,
]


def _read_data_from_str_to_dict(input_str: str, output_dict: Dict[str, str]) -> None:
    """
    使用类似角色卡描述的字符串填充输出字典，本质只是分割字符串
    默认所有关键字都被$符号包裹，且$符号不会出现在普通信息中
    """
    info_list = input_str.split("$")
    index = 0
    while index < len(info_list) - 1:
        key = f"${info_list[index]}$"
        if key in CHAR_INFO_KEY_LIST:
            content = info_list[index + 1].strip()
            if content and content not in CHAR_INFO_KEY_LIST:
                output_dict[key] = content
                index += 1
        index += 1


class HPService:
    """HP 相关服务 - 依赖 roll 模块的方法"""

    @staticmethod
    def use_hp_dice(hp_info: HPInfo, num: int, con_mod: int) -> str:
        """
        使用生命骰，并返回修改结果描述
        
        Args:
            hp_info: HP 信息对象
            num: 使用的生命骰数量
            con_mod: 体质调整值
            
        Returns:
            结果描述字符串，如:
            使用2颗D4生命骰, 体质调整值为1, 回复(4+1)+(2+1)=8点生命值
            HP: 2/4 -> 4/4
        """
        if not hp_info.is_init or hp_info.hp_dice_type <= 0:
            return "尚未设置生命骰"
        if num <= 0 or num > 1000:
            return f"无效的生命骰数量({num})"
        if hp_info.hp_dice_num < num:
            return f"生命骰数量不足, 还有{hp_info.hp_dice_num}颗生命骰"

        roll_exp = f"1D{hp_info.hp_dice_type}+{con_mod}"

        roll_result_list = []
        roll_result_val = 0
        for i in range(num):
            try:
                roll_result = exec_roll_exp(roll_exp)
            except RollDiceError as e:
                return f"未知掷骰错误:{e.info}"
            if num > 1:
                roll_result_list.append(f"({roll_result.get_info()})")
            else:
                roll_result_list.append(roll_result.get_info())
            roll_val = roll_result.get_val()
            if roll_val < 0:
                roll_val = 0
                roll_result_list[-1] = f"({roll_result_list[-1]}->0)"
            roll_result_val += roll_val

        roll_result_str = f"{'+'.join(roll_result_list)}={roll_result_val}"

        mod_info = f"使用{num}颗D{hp_info.hp_dice_type}生命骰, 体质调整值为{con_mod}, 回复{roll_result_str}点生命值\n"
        hp_info_str_prev = hp_info.get_info()
        hp_info.hp_dice_num -= num
        hp_info.heal(roll_result_val)
        mod_info += f"{hp_info_str_prev} -> {hp_info.get_info()}"
        return mod_info

    @staticmethod
    def process_roll_result(
        hp_info: HPInfo,
        cmd_type: Literal["=", "+", "-"],
        hp_cur_mod_result: Optional[RollResult] = None,
        hp_max_mod_result: Optional[RollResult] = None,
        hp_temp_mod_result: Optional[RollResult] = None,
        short_feedback: bool = False
    ) -> str:
        """
        根据掷骰结果修改 HP，返回修改结果描述
        
        Args:
            hp_info: HP 信息对象
            cmd_type: "=" 设置, "+" 增加, "-" 减少
            hp_cur_mod_result: 当前 HP 的掷骰结果
            hp_max_mod_result: 最大 HP 的掷骰结果
            hp_temp_mod_result: 临时 HP 的掷骰结果
            short_feedback: 是否使用短格式反馈
            
        Returns:
            修改结果描述，如:
            当前HP减少 2*4 = 8
            HP: 10 -> HP: 2
        """
        mod_info = ""

        if cmd_type == "=":  # 设置生命值
            hp_info.is_init = True
            if hp_cur_mod_result:
                if hp_info.is_record_normal():
                    hp_info.is_alive = hp_cur_mod_result.get_val() > 0
                hp_info.hp_cur = hp_cur_mod_result.get_val()
                mod_info = f"HP={hp_cur_mod_result.get_result()}"
            if hp_max_mod_result:
                hp_info.hp_max = hp_max_mod_result.get_val()
                if hp_info.hp_cur > hp_info.hp_max:
                    hp_info.take_damage(hp_info.hp_cur - hp_info.hp_max)
                mod_info = f"HP={hp_cur_mod_result.get_result()}/{hp_max_mod_result.get_result()}"
            if hp_temp_mod_result:
                hp_info.hp_temp = hp_temp_mod_result.get_val()
                if "HP=" in mod_info:
                    mod_info += f" ({hp_temp_mod_result.get_result()})"
                else:
                    mod_info = f"临时HP={hp_temp_mod_result.get_result()}"
            mod_info += f"\n当前{hp_info.get_info()}"

        elif cmd_type == "+":  # 增加生命值
            hp_info_str_prev = hp_info.get_info()
            if hp_max_mod_result:  # 先结算生命值上限
                hp_info.hp_max += hp_max_mod_result.get_val()
                mod_info += f"最大HP增加{hp_max_mod_result.get_result()}, "
            if hp_cur_mod_result:
                hp_info.heal(hp_cur_mod_result.get_val())
                mod_info += f"当前HP增加{hp_cur_mod_result.get_result()}"
            if hp_temp_mod_result:
                hp_info.hp_temp += hp_temp_mod_result.get_val()
                if mod_info:
                    mod_info += ", "
                mod_info += f"临时HP增加{hp_temp_mod_result.get_result()}"
            mod_info += f"\n{hp_info_str_prev} -> {hp_info.get_info()}"

        else:  # cmd_type == "-"  扣除生命值
            hp_info_str_prev = hp_info.get_info()
            if hp_temp_mod_result:  # 先结算临时生命值
                hp_info.hp_temp = max(0, hp_info.hp_temp - hp_temp_mod_result.get_val())
                mod_info += f"临时HP减少{hp_temp_mod_result.get_result()}"
            if hp_max_mod_result:  # 先结算生命值上限
                hp_info.hp_max -= hp_max_mod_result.get_val()
                if mod_info:
                    mod_info += ", "
                mod_info += f"最大HP减少{hp_max_mod_result.get_result()}"
                if hp_info.hp_cur > hp_info.hp_max:
                    hp_info.take_damage(hp_info.hp_cur - hp_info.hp_max)
            if hp_cur_mod_result:
                hp_info.take_damage(hp_cur_mod_result.get_val())
                if mod_info:
                    mod_info += ", "
                mod_info += f"当前HP减少{hp_cur_mod_result.get_result()}"
            mod_info += f"\n{hp_info_str_prev} -> {hp_info.get_info()}"

        mod_info = mod_info.strip()
        if short_feedback:
            mod_info = mod_info.replace("\n", "; ")
        return mod_info


class AbilityService:
    """属性相关服务 - 依赖 roll 模块的方法"""

    @staticmethod
    def initialize(
        ability_info: AbilityInfo,
        level_str: str,
        ability_info_list: List[int],
        prof_list: List[str],
        ext_dict: Dict[str, str]
    ) -> None:
        """
        通过用户输入初始化属性，初始化失败则抛出 AssertionError
        
        Args:
            ability_info: 属性信息对象
            level_str: 正整数字符串，代表等级
            ability_info_list: 长度为6的正整数列表，代表六项属性
            prof_list: 字符串列表，代表熟练的检定条目，如 ["2*奥秘", "智力豁免"]
            ext_dict: key为检定条目，value为对应检定的额外加值
        """
        # 初始化等级
        try:
            assert int(level_str) > 0
        except (AssertionError, ValueError):
            raise AssertionError("等级必须为正整数")
        level = int(level_str)

        # 初始化属性值
        ability = [0] * ABILITY_NUM
        assert len(ability_info_list) == ABILITY_NUM, f"必须设定全部的{ABILITY_NUM}项属性"
        for index, val in enumerate(ability_info_list):
            try:
                assert int(val) > 0
            except (AssertionError, ValueError):
                raise AssertionError(f"{ABILITY_LIST[index]}属性值{val}必须为正整数")
            ability[index] = int(val)

        # 初始化熟练加值
        check_prof = [0] * len(CHECK_ITEM_LIST)
        for attack_name in ATTACK_LIST:  # 默认熟练所有攻击
            attack_index = CHECK_ITEM_INDEX_DICT[attack_name]
            check_prof[attack_index] = 1
        for check_name in prof_list:
            prof_scale = 1
            if "*" in check_name:
                scale_str, check_name = check_name.split("*", 1)
                try:
                    prof_scale = int(scale_str)
                    assert prof_scale >= 0
                except (ValueError, AssertionError):
                    raise AssertionError(f"{check_name}熟练加值倍数必须为非负整数")
            if check_name in SKILL_SYNONYM_DICT:
                check_name = SKILL_SYNONYM_DICT[check_name]
            assert check_name in CHECK_ITEM_LIST, f"{check_name}为无效的检定条目, 可用条目:{CHECK_ITEM_LIST}"
            check_index = CHECK_ITEM_INDEX_DICT[check_name]
            check_prof[check_index] = prof_scale

        # 初始化调整值
        check_ext = [""] * len(EXT_ITEM_LIST)
        check_adv = [0] * len(EXT_ITEM_LIST)
        for check_name, ext_str in ext_dict.items():
            if not ext_str:
                continue
            if check_name in SKILL_SYNONYM_DICT:
                check_name = SKILL_SYNONYM_DICT[check_name]
            assert check_name in EXT_ITEM_LIST, f"{check_name}为无效条目, 可用条目:\n{EXT_ITEM_LIST}"
            check_index = EXT_ITEM_INDEX_DICT[check_name]

            # 处理优劣势
            if ext_str.startswith("优势") or ext_str.startswith("劣势"):
                if ext_str.startswith("优势"):
                    check_adv[check_index] = 1
                if ext_str.startswith("劣势"):
                    check_adv[check_index] = -1
                ext_str = ext_str[2:]
            if not ext_str:
                continue

            # 校验开头
            assert ext_str[0] in ["+", "-"], f"调整值无效: 必须以[优势/劣势]+/-开头\n{check_name}:{ext_str}"
            # 校验表达式合法性
            try:
                res = exec_roll_exp("D20" + ext_str)
                res.get_complete_result()
            except RollDiceError as e:
                raise AssertionError(f"无效的调整值: {check_name}:{ext_str} {e.info}")
            check_ext[check_index] = ext_str

        ability_info.is_init = True
        ability_info.level = level
        ability_info.ability = ability
        ability_info.check_ext = check_ext
        ability_info.check_adv = check_adv
        ability_info.check_prof = check_prof

    @staticmethod
    def perform_check(
        ability_info: AbilityInfo,
        check_name: str,
        advantage: int,
        mod_str: str
    ) -> Tuple[str, str, int]:
        """
        进行技能检定，失败抛出 AssertionError
        
        Args:
            ability_info: 属性信息对象
            check_name: 必须为 CHECK_ITEM_LIST 中的元素，如: 力量/运动
            advantage: 该次检定是否具有优势，1为优势，-1为劣势，0为无优无劣
            mod_str: 该次检定的临时加值
            
        Returns:
            (hint_str, result_str, result_val): 提示信息、检定计算过程、检定结果
        """
        hint_str = ""
        assert ability_info.is_init, "未初始化属性值"

        if check_name in SKILL_SYNONYM_DICT:
            check_name = SKILL_SYNONYM_DICT[check_name]
        assert check_name in CHECK_ITEM_LIST, f"{check_name}无效, 可用检定条目:\n{CHECK_ITEM_LIST}"

        is_skill = check_name in SKILL_LIST
        is_saving = check_name in SAVING_LIST
        is_attack = check_name in ATTACK_LIST

        check_index = CHECK_ITEM_INDEX_DICT[check_name]
        parent_index = -1
        if is_skill:
            parent_index = EXT_ITEM_INDEX_DICT[SKILL_PARENT_DICT[check_name]]
        if is_saving:
            parent_index = EXT_ITEM_INDEX_DICT[SAVING_PARENT_DICT[check_name]]
        if is_attack:
            parent_index = EXT_ITEM_INDEX_DICT[ATTACK_PARENT_DICT[check_name]]

        # 计算熟练加值
        prof_bonus = ability_info.check_prof[check_index] * ability_info.get_prof_bonus()
        if ability_info.check_prof[check_index] == 0:
            hint_str += "无熟练加值 "
        elif ability_info.check_prof[check_index] == 1:
            hint_str += f"熟练加值:{prof_bonus} "
        else:
            hint_str += f"熟练加值:{ability_info.get_prof_bonus()}*{ability_info.check_prof[check_index]} "
        prof_bonus_str = ""
        if prof_bonus != 0:
            prof_bonus_str = "+" + str(prof_bonus) if prof_bonus > 0 else str(prof_bonus)

        # 计算属性调整值
        if parent_index != -1:
            ability_modifier = ability_info.get_modifier(parent_index)
            hint_str += f"{ABILITY_LIST[parent_index]}调整值:{ability_modifier} "
        else:
            ability_modifier = ability_info.get_modifier(check_index)
            hint_str += f"{ABILITY_LIST[check_index]}调整值:{ability_modifier} "
        ability_modifier_str = ""
        if ability_modifier != 0:
            ability_modifier_str = "+" + str(ability_modifier) if ability_modifier > 0 else str(ability_modifier)

        # 得到额外加值
        ext_str = ability_info.check_ext[check_index]
        if parent_index != -1:
            ext_str += ability_info.check_ext[parent_index]
        if is_saving:
            all_index = EXT_ITEM_INDEX_DICT[SAVING_ALL_KEY]
            ext_str += ability_info.check_ext[all_index]
        if is_attack:
            all_index = EXT_ITEM_INDEX_DICT[ATTACK_ALL_KEY]
            ext_str += ability_info.check_ext[all_index]
        if ext_str:
            hint_str += f"额外加值:{ext_str} "

        # 临时加值
        if mod_str:
            hint_str += f"临时加值:{mod_str} "

        # 计算优劣势
        assert advantage in [-1, 0, 1], "Unexpected Code: 0"
        adv_flag = ability_info.check_adv[check_index]
        parent_flag = 0
        if is_skill:
            parent_flag = ability_info.check_adv[parent_index]
        adv_flag = max(min(adv_flag + parent_flag, 1), -1)
        is_counter = (adv_flag == 0 and parent_flag != 0)
        adv_flag = max(min(adv_flag + advantage, 1), -1)
        is_counter = is_counter or (adv_flag == 0 and advantage != 0)
        if is_counter:
            adv_flag = 0
            hint_str += "优劣抵消 "
        if adv_flag != 0 and advantage == 0:
            if adv_flag > 0:
                hint_str += "自带优势 "
            else:
                hint_str += "自带劣势 "

        hint_str = hint_str.strip()

        # 组合成掷骰表达式
        if adv_flag != 0:
            roll_exp = "D20优势" if adv_flag > 0 else "D20劣势"
        else:
            roll_exp = "D20"

        roll_exp = f"{roll_exp}{prof_bonus_str}{ability_modifier_str}{ext_str}{mod_str}"
        try:
            roll_result = exec_roll_exp(roll_exp)
            result_str = roll_result.get_complete_result()
            result_val = roll_result.get_val()
        except RollDiceError as e:
            raise AssertionError(f"Unexpected Code: {roll_exp}->{e.info}")

        return hint_str, result_str, result_val


class CharacterService:
    """角色相关服务 - 依赖 roll 模块的方法"""

    @staticmethod
    def parse(input_str: str, group_id: str, user_id: str) -> DNDCharacter:
        """
        通过用户输入解析角色卡，任意内容初始化失败则抛出 AssertionError
        
        Args:
            input_str: 角色卡描述字符串
            group_id: 群组 ID
            user_id: 用户 ID
            
        Returns:
            解析后的 DNDCharacter 对象
            
        角色卡示例:
        $姓名$ 伊丽莎白
        $等级$ 5
        $生命值$ 5/10(4)
        $生命骰$ 4/10 D6
        $属性$ 10/11/12/15/12
        $熟练$ 力量/2*隐匿/奥秘
        $额外加值$ 运动:优势/隐匿:优势+2/游说:-2
        """
        hp_info = HPInfo()
        ability_info = AbilityInfo()

        char_info_dict: Dict[str, str] = defaultdict(str)
        _read_data_from_str_to_dict(input_str, char_info_dict)

        # 记录姓名
        name_str = char_info_dict[CHAR_INFO_KEY_NAME]

        # 处理生命值相关信息
        hp_info_str = char_info_dict[CHAR_INFO_KEY_HP]
        if hp_info_str:
            hp_cur: int
            hp_max: int = 0
            hp_temp: int = 0

            hp_str: str
            hp_max_str: str
            hp_temp_str: str
            if hp_info_str.find("/") != -1:
                hp_str, hp_max_str = hp_info_str.split("/", maxsplit=1)
            else:
                hp_str, hp_max_str = hp_info_str, ""
            if hp_info_str.find("(") != -1:
                if hp_str.find("(") != -1:
                    hp_str, hp_temp_str = hp_str.split("(", maxsplit=1)
                else:
                    hp_max_str, hp_temp_str = hp_max_str.split("(", maxsplit=1)
                hp_temp_str = hp_temp_str.replace(")", "")
            else:
                hp_temp_str = ""

            try:
                hp_cur = int(hp_str)
            except ValueError:
                raise AssertionError(f"无效的生命数值:{hp_str}")

            if hp_max_str:
                try:
                    hp_max = int(hp_max_str)
                except ValueError:
                    raise AssertionError(f"无效的最大生命值:{hp_max_str}")
            if hp_temp_str:
                try:
                    hp_temp = int(hp_temp_str)
                except ValueError:
                    raise AssertionError(f"无效的临时生命值:{hp_temp_str}")

            # 处理生命骰信息
            hp_dice_type: int = 0
            hp_dice_num: int = 0
            hp_dice_max: int = 0

            hp_dice_info_str = char_info_dict[CHAR_INFO_KEY_HP_DICE]
            if hp_dice_info_str:
                hp_dice_type_str: str
                hp_dice_num_str: str
                hp_dice_max_str: str
                if hp_dice_info_str.find("d") == -1 and hp_dice_info_str.find("D") == -1:
                    raise AssertionError(f"生命骰信息不完整:{hp_dice_info_str}, 必须指定生命骰大小")
                # 统一转小写处理
                hp_dice_info_lower = hp_dice_info_str.lower()
                hp_dice_max_str, hp_dice_type_str = hp_dice_info_lower.split("d", maxsplit=1)
                if hp_dice_max_str.find("/") != -1:
                    hp_dice_num_str, hp_dice_max_str = hp_dice_max_str.split("/", maxsplit=1)
                else:
                    hp_dice_num_str = hp_dice_max_str

                try:
                    hp_dice_type = int(hp_dice_type_str)
                    hp_dice_num = int(hp_dice_num_str)
                    hp_dice_max = int(hp_dice_max_str)
                except ValueError:
                    raise AssertionError(f"生命骰信息不正确{hp_dice_info_str}, 示例: 8/10 D6")

            hp_info.initialize(hp_cur, hp_max, hp_temp, hp_dice_type, hp_dice_num, hp_dice_max)

        # 处理属性相关信息
        level_str = char_info_dict[CHAR_INFO_KEY_LEVEL]
        ability_str = char_info_dict[CHAR_INFO_KEY_ABILITY]
        prof_str = char_info_dict[CHAR_INFO_KEY_PROF]
        ext_str = char_info_dict[CHAR_INFO_KEY_EXT]

        assert level_str and ability_str, "必须设定等级与属性"

        ability_info_list: List[int] = []
        prof_list: List[str] = []
        ext_dict: Dict[str, str] = {}

        for ability_item_str in ability_str.split("/"):
            try:
                ability_info_list.append(int(ability_item_str))
            except ValueError:
                raise AssertionError("属性值必须为正整数!")
        if prof_str:
            prof_list = prof_str.split("/")
        if ext_str:
            ext_list = ext_str.split("/")
            for ext_item_str in ext_list:
                assert ext_item_str.find(":") > 0 and not ext_item_str.endswith(":"), \
                    "必须使用冒号(:)分割额外加值条目和内容"
                key_str, content_str = ext_item_str.split(":", maxsplit=1)
                ext_dict[key_str] = content_str

        AbilityService.initialize(ability_info, level_str, ability_info_list, prof_list, ext_dict)

        # 创建角色
        character = DNDCharacter(
            group_id=group_id,
            user_id=user_id,
            name=name_str,
            hp_info=hp_info,
            ability_info=ability_info,
            is_init=True,
        )

        return character

    @staticmethod
    def use_hp_dice(character: DNDCharacter, num: int) -> str:
        """
        角色使用生命骰
        
        Args:
            character: 角色对象
            num: 使用的生命骰数量
            
        Returns:
            结果描述字符串
        """
        con_mod = character.ability_info.get_modifier(2)  # 体质是第3个属性
        result = character.name + HPService.use_hp_dice(character.hp_info, num, con_mod)
        return result


def gen_template_char(group_id: str = "test_group", user_id: str = "test_user") -> DNDCharacter:
    """生成模板角色用于测试"""
    character = DNDCharacter(group_id=group_id, user_id=user_id)
    character.name = "张三"
    character.is_init = True

    character.hp_info.is_init = True
    character.hp_info.hp_cur = 20
    character.hp_info.hp_max = 30
    character.hp_info.hp_temp = 5
    character.hp_info.hp_dice_type = 8
    character.hp_info.hp_dice_num = 3
    character.hp_info.hp_dice_max = 4

    character.ability_info.is_init = True
    character.ability_info.level = 4
    character.ability_info.ability = [10, 15, 12, 13, 8, 11]
    character.ability_info.check_prof[CHECK_ITEM_INDEX_DICT["敏捷攻击"]] = 1
    character.ability_info.check_prof[CHECK_ITEM_INDEX_DICT["敏捷豁免"]] = 1
    character.ability_info.check_prof[CHECK_ITEM_INDEX_DICT["体操"]] = 1
    character.ability_info.check_prof[CHECK_ITEM_INDEX_DICT["隐匿"]] = 2

    character.ability_info.check_adv[EXT_ITEM_INDEX_DICT["隐匿"]] = 1

    character.ability_info.check_ext[EXT_ITEM_INDEX_DICT["豁免"]] = "+2"
    character.ability_info.check_ext[EXT_ITEM_INDEX_DICT["敏捷攻击"]] = "+1d4"

    return character
