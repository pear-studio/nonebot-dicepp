from typing import List, Tuple, Dict
import json

from core.data import JsonObject, custom_json_object
from module.roll import exec_roll_exp, RollDiceError

CHAR_INFO_KEY_LEVEL = "$等级$"
CHAR_INFO_KEY_ABILITY = "$属性$"
CHAR_INFO_KEY_PROF = "$熟练$"
CHAR_INFO_KEY_EXT = "$额外加值$"


# 属性值
ability_list = ["力量", "敏捷", "体质", "智力", "感知", "魅力", ]
ability_num = len(ability_list)
assert ability_num == 6

# 技能
skill_list = ["运动",
              "体操", "巧手", "隐匿", "先攻",
              "奥秘", "历史", "调查", "自然", "宗教",
              "驯兽", "洞悉", "医药", "察觉", "求生",
              "欺瞒", "威吓", "表演", "游说", ]
skill_num = len(skill_list)
assert skill_num == 19

skill_parent_dict = {
    "运动": "力量",
    "体操": "敏捷", "巧手": "敏捷", "隐匿": "敏捷", "先攻": "敏捷",
    "奥秘": "智力", "历史": "智力", "调查": "智力", "自然": "智力", "宗教": "智力",
    "驯兽": "感知", "洞悉": "感知", "医药": "感知", "察觉": "感知", "求生": "感知",
    "欺瞒": "魅力", "威吓": "魅力", "表演": "魅力", "游说": "魅力",

}
assert len(skill_parent_dict) == skill_num
assert sum([1 for v in skill_parent_dict.values() if v not in ability_list]) == 0
skill_synonym_dict = {
    "特技": "体操", "妙手": "巧手", "潜行": "隐匿", "隐蔽": "隐匿", "隐秘": "隐匿", "躲藏": "隐匿",
    "驯养": "驯兽", "驯服": "驯兽", "医疗": "医药", "医术": "医药", "观察": "察觉", "生存": "求生",
    "欺骗": "欺瞒", "欺诈": "欺瞒", "哄骗": "欺瞒", "唬骗": "欺瞒", "威胁": "威吓", "说服": "游说"}
assert sum([1 for v in skill_synonym_dict.values() if v not in skill_list]) == 0

# 豁免
saving_list = ["力量豁免", "敏捷豁免", "体质豁免", "智力豁免", "感知豁免", "魅力豁免", ]
saving_num = len(saving_list)
assert saving_num == ability_num
saving_parent_dict = {
    "力量豁免": "力量", "敏捷豁免": "敏捷", "体质豁免": "体质", "智力豁免": "智力", "感知豁免": "感知", "魅力豁免": "魅力",
}
assert len(saving_parent_dict) == saving_num
assert sum([1 for v in saving_parent_dict.values() if v not in ability_list]) == 0

# 攻击
attack_list = ["力量攻击", "敏捷攻击", "体质攻击", "智力攻击", "感知攻击", "魅力攻击", ]
attack_num = len(attack_list)
assert attack_num == ability_num
attack_parent_dict = {
    "力量攻击": "力量", "敏捷攻击": "敏捷", "体质攻击": "体质", "智力攻击": "智力", "感知攻击": "感知", "魅力攻击": "魅力",
}
assert len(attack_parent_dict) == attack_num
assert sum([1 for v in attack_parent_dict.values() if v not in ability_list]) == 0

# 可检定条目
check_item_list = ability_list + skill_list + saving_list + attack_list
check_item_num = ability_num * 3 + skill_num

check_item_index_dict = dict(list((k, i) for i, k in enumerate(check_item_list)))
assert len(check_item_list) == len(check_item_index_dict) == check_item_num

# 可额外加值条目
saving_all_key = "豁免"
attack_all_key = "攻击"
ext_item_list = check_item_list + [saving_all_key, attack_all_key]
ext_item_num = check_item_num + 2

ext_item_index_dict = dict(list((k, i) for i, k in enumerate(ext_item_list)))
assert len(ext_item_list) == len(ext_item_index_dict) == ext_item_num


@custom_json_object
class AbilityInfo(JsonObject):
    def serialize(self) -> str:
        json_dict = self.__dict__
        for key in json_dict.keys():
            value = json_dict[key]
            if isinstance(value, JsonObject):
                json_dict[key] = value.serialize()
        return json.dumps(json_dict)

    def deserialize(self, json_str: str) -> None:
        json_dict: dict = json.loads(json_str)
        for key, value in json_dict.items():
            if key in self.__dict__:
                value_init = self.__getattribute__(key)
                if isinstance(value_init, JsonObject):
                    value_init.deserialize(value)
                else:
                    self.__setattr__(key, value)

    def __init__(self):
        self.is_init: bool = False
        self.version = 1  # 版本号

        self.level: int = 0  # 等级

        self.ability: List[int] = [0] * ability_num  # 力量, 敏捷, 体质, 智力, 感知, 魅力
        self.check_prof: List[int] = [0] * check_item_num  # 检定的熟练加值的倍数
        self.check_ext: List[str] = [""] * ext_item_num  # 描述检定的额外加值的表达式
        self.check_adv: List[int] = [0] * ext_item_num  # 是否自带优势或劣势

    def initialize(self, level_str: str, ability_info_list: List[int], prof_list: List[str], ext_dict: Dict[str, str]):
        """
        通过用户输入初始化属性, 初始化失败则抛出AssertionError
        Args:
            level_str: 正整数字符串, 代表等级
            ability_info_list: 长度为6的正整数字符串列表, 代表六项属性
            prof_list: 长度任意的字符串列表, 代表熟练的检定条目, 如["2*奥秘", "智力豁免"]代表奥秘有双倍熟练加值, 智力豁免拥有单倍熟练加值, 其余检定没有熟练加值
            ext_dict: key为检定条目, value为对应检定的额外加值
        """
        # 初始化等级
        try:
            assert int(level_str) > 0
        except (AssertionError, ValueError):
            raise AssertionError("等级必须为正整数")
        level = int(level_str)

        # 初始化属性值
        ability = [0] * ability_num
        assert len(ability_info_list) == ability_num, f"必须设定全部的{ability_num}项属性"
        for index, val_str in enumerate(ability_info_list):
            try:
                assert int(val_str) > 0
            except(AssertionError, ValueError):
                raise AssertionError(f"{ability_list[index]}属性值{val_str}必须为正整数")
            ability[index] = int(val_str)

        # 初始化熟练加值
        check_prof = [0] * check_item_num
        for attack_name in attack_list:  # 默认熟练所有攻击
            attack_index: int = check_item_index_dict[attack_name]
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
            if check_name in skill_synonym_dict:
                check_name = skill_synonym_dict[check_name]
            assert check_name in check_item_list, f"{check_name}为无效的检定条目, 可用条目:{check_item_list}"
            check_index: int = check_item_index_dict[check_name]
            check_prof[check_index] = prof_scale

        # 初始化调整值
        check_ext = [""] * ext_item_num
        check_adv = [0] * ext_item_num
        for check_name, ext_str in ext_dict.items():
            if not ext_str:
                continue
            if check_name in skill_synonym_dict:
                check_name = skill_synonym_dict[check_name]
            assert check_name in ext_item_list, f"{check_name}为无效条目, 可用条目:\n{ext_item_list}"
            check_index: int = ext_item_index_dict[check_name]

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
            assert ext_str[0] in ["+", "-"], f"调整值无效: 必须以[优势/劣势]+/-开头\n{check_name}:{ext_str} "
            # 校验表达式合法性
            try:
                res = exec_roll_exp("D20" + ext_str)
                res.get_complete_result()
            except RollDiceError as e:
                raise AssertionError(f"无效的调整值: {check_name}:{ext_str} {e.info}")
            check_ext[check_index] = ext_str

        self.is_init = True
        self.level = level
        self.ability = ability
        self.check_ext = check_ext
        self.check_adv = check_adv
        self.check_prof = check_prof

    def get_prof_bonus(self) -> int:
        prof_bonus = 2 + (self.level - 1) // 4
        return prof_bonus

    def get_modifier(self, ability_index: int) -> int:
        ability_score = self.ability[ability_index]
        modifier = (ability_score - 10) // 2
        return modifier

    def perform_check(self, check_name: str, advantage: int, mod_str: str) -> Tuple[str, str, int]:
        """
        进行技能检定, 失败抛出AssertionError
        Args:
            check_name: 必须为skill_dict中的元素, 如: 力量/运动
            advantage: 该次检定是否具有优势, 1为优势, -1为劣势, =0为无优无劣
            mod_str: 该次检定的临时加值
        Returns:
            hint_str: 提示信息
            result_str: 检定的计算过程
            result_val: 检定结果
        """
        hint_str: str = ""
        result_str: str
        result_val: int
        assert self.is_init, "未初始化属性值"
        if check_name in skill_synonym_dict:
            check_name = skill_synonym_dict[check_name]
        assert check_name in check_item_list, f"{check_name}无效, 可用检定条目:\n{check_item_list}"
        is_skill: bool = check_name in skill_list  # 计算技能检定时 会 叠加基础属性的额外加值与优劣势
        is_saving: bool = check_name in saving_list  # 计算豁免检定时 不会 叠加基础属性的额外加值与优劣势
        is_attack: bool = check_name in attack_list  # 计算攻击检定时 不会 叠加基础属性的额外加值与优劣势

        check_index = check_item_index_dict[check_name]
        parent_index = -1
        if is_skill:
            parent_index = ext_item_index_dict[skill_parent_dict[check_name]]
        if is_saving:
            parent_index = ext_item_index_dict[saving_parent_dict[check_name]]
        if is_attack:
            parent_index = ext_item_index_dict[attack_parent_dict[check_name]]

        # 计算熟练加值
        prof_bonus = self.check_prof[check_index] * self.get_prof_bonus()
        if self.check_prof[check_index] == 0:
            hint_str += f"无熟练加值 "
        elif self.check_prof[check_index] == 1:
            hint_str += f"熟练加值:{prof_bonus} "
        else:
            hint_str += f"熟练加值:{self.get_prof_bonus()}*{self.check_prof[check_index]} "
        prof_bonus_str: str = ""
        if prof_bonus != 0:
            prof_bonus_str = "+" + str(prof_bonus) if prof_bonus > 0 else str(prof_bonus)

        # 计算属性调整值
        ability_modifier: int
        if parent_index != -1:
            ability_modifier = self.get_modifier(parent_index)
            hint_str += f"{ability_list[parent_index]}调整值:{ability_modifier} "
        else:
            ability_modifier = self.get_modifier(check_index)
            hint_str += f"{ability_list[check_index]}调整值:{ability_modifier} "
        ability_modifier_str: str = ""
        if ability_modifier != 0:
            ability_modifier_str = "+" + str(ability_modifier) if ability_modifier > 0 else str(ability_modifier)

        # 得到额外加值
        ext_str = self.check_ext[check_index]
        if parent_index != -1:
            ext_str += self.check_ext[parent_index]
        if is_saving:
            all_index = ext_item_index_dict[saving_all_key]
            ext_str += self.check_ext[all_index]
        if is_attack:
            all_index = ext_item_index_dict[attack_all_key]
            ext_str += self.check_ext[all_index]
        if ext_str:
            hint_str += f"额外加值:{ext_str} "

        # 临时加值
        if mod_str:
            hint_str += f"临时加值:{mod_str} "

        # 计算优劣势
        assert advantage in [-1, 0, 1], "Unexpected Code: 0"
        is_counter: bool  # 优劣势是否被抵消过
        adv_flag = self.check_adv[check_index]
        parent_flag = 0
        if is_skill:
            parent_flag = self.check_adv[parent_index]
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
        roll_exp: str
        if adv_flag != 0:
            if adv_flag > 0:
                roll_exp = "D20优势"
            else:
                roll_exp = "D20劣势"
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

    def get_char_info(self) -> str:
        """
        返回可用来组合成初始化角色卡的字符串
        例子:
        $等级$ 5
        $属性$ 10/11/12/15/12
        $熟练$ 力量/2*隐匿/奥秘
        $额外加值$ 运动:优势/隐匿:优势+2/游说:-2
        """
        info = ""
        # 等级信息
        info += f"{CHAR_INFO_KEY_LEVEL} {self.level}\n"
        # 属性值信息
        info += f"{CHAR_INFO_KEY_ABILITY} {'/'.join([str(val) for val in self.ability])}\n"

        # 熟练信息
        prof_info = [(scale, check_item_list[index]) for index, scale in enumerate(self.check_prof) if scale > 0]
        prof_list = []
        for scale, prof_name in prof_info:
            if scale == 1:
                prof_list.append(prof_name)
            else:
                prof_list.append(f"{scale}*{prof_name}")
        if len(prof_list) > 0:
            info += f"{CHAR_INFO_KEY_PROF} {'/'.join(prof_list)}\n"

        # 额外加值信息
        ext_info = [(ext_item_list[index], ext_str) for index, ext_str in enumerate(self.check_ext) if ext_str]
        adv_dict = dict([(ext_item_list[index], adv_flag) for index, adv_flag in enumerate(self.check_adv) if adv_flag != 0])
        ext_list = []
        for check_name, ext_str in ext_info:
            ext_str_prefix = ""
            if check_name in adv_dict:
                if adv_dict[check_name] > 0:
                    ext_str_prefix = "优势"
                else:
                    ext_str_prefix = "劣势"
            ext_list.append(f"{check_name}:{ext_str_prefix}{ext_str}")
        if len(ext_list) > 0:
            info += f"{CHAR_INFO_KEY_EXT} {'/'.join(ext_list)}\n"

        return info.strip()
