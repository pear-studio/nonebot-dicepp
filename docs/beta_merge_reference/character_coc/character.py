from typing import List, Dict
from collections import defaultdict
import json

from core.data import JsonObject, custom_json_object

from module.character.dnd5e import HPInfo, AbilityInfo, SpellInfo, MoneyInfo
from module.character.dnd5e import check_item_index_dict, ext_item_index_dict

from module.character.dnd5e import CHAR_INFO_KEY_HP, CHAR_INFO_KEY_HP_DICE
from module.character.dnd5e import CHAR_INFO_KEY_LEVEL, CHAR_INFO_KEY_PROF, CHAR_INFO_KEY_ABILITY, CHAR_INFO_KEY_EXT

CHAR_INFO_KEY_NAME = "$姓名$"

CHAR_INFO_KEY_LIST = [
    CHAR_INFO_KEY_NAME,  # 姓名
    CHAR_INFO_KEY_LEVEL,  # 等级
    CHAR_INFO_KEY_HP, CHAR_INFO_KEY_HP_DICE,  # 生命值
    CHAR_INFO_KEY_ABILITY,  # 属性
    CHAR_INFO_KEY_PROF,  # 熟练
    CHAR_INFO_KEY_EXT,  # 额外加值
]


def read_data_from_str_to_dict(input_str: str, output_dict: Dict[str, str]):
    """
    使用类似角色卡描述的字符串填充输出字典, 本质只是分割字符串
    默认所有关键字都被$符号包裹, 且$符号不会出现在普通信息中
    """
    info_list = input_str.split("$")
    index = 0
    while index < len(info_list)-1:
        key = f"${info_list[index]}$"
        if key in CHAR_INFO_KEY_LIST:
            content = info_list[index+1].strip()
            if content and content not in CHAR_INFO_KEY_LIST:
                output_dict[key] = content
                index += 1
        index += 1


@custom_json_object
class DNDCharInfo(JsonObject):
    """
    DND5E 角色信息
    """

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
                    # self.__setattr__(key, value_init)
                else:
                    self.__setattr__(key, value)

    def __init__(self):
        self.is_init = False
        self.name: str = ""
        self.hp_info: HPInfo = HPInfo()
        self.ability_info: AbilityInfo = AbilityInfo()
        self.spell_info: SpellInfo = SpellInfo()
        self.money_info: MoneyInfo = MoneyInfo()

    def initialize(self, input_str: str):
        """
        通过用户输入初始化属性, 任意内容初始化失败则抛出AssertionError, 并且不会产生实际影响
        角色卡示例:
        $姓名$ 伊丽莎白
        $等级$ 5
        $生命值$ 5/10(4)
        $生命骰$ 4/10 D6
        $属性$ 10/11/12/15/12
        $熟练$ 力量/2*隐匿/奥秘
        $额外加值$ 运动:优势/隐匿:优势+2/游说:-2
        """
        hp_info: HPInfo = HPInfo()
        ability_info: AbilityInfo = AbilityInfo()
        spell_info: SpellInfo = SpellInfo()
        money_info: MoneyInfo = MoneyInfo()

        char_info_dict: Dict[str, str] = defaultdict(str)
        read_data_from_str_to_dict(input_str, char_info_dict)

        # 记录姓名
        name_str = char_info_dict[CHAR_INFO_KEY_NAME]

        # 处理生命值相关信息
        hp_info_str = char_info_dict[CHAR_INFO_KEY_HP]
        if hp_info_str:
            # 处理生命值信息
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
                if hp_dice_info_str.find("d") == -1:
                    raise AssertionError(f"生命骰信息不完整:{hp_dice_info_str}, 必须指定生命骰大小")
                hp_dice_max_str, hp_dice_type_str = hp_dice_info_str.split("d", maxsplit=1)
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
                assert ext_item_str.find(":") > 0 and not ext_item_str.endswith(":"), "必须使用冒号(:)分割额外加值条目和内容"
                key_str, content_str = ext_item_str.split(":", maxsplit=1)
                ext_dict[key_str] = content_str

        ability_info.initialize(level_str, ability_info_list, prof_list, ext_dict)

        # 应用初始化
        self.is_init = True
        self.name = name_str
        self.hp_info = hp_info
        self.ability_info = ability_info
        self.spell_info = spell_info
        self.money_info = money_info

    def get_char_info(self) -> str:
        """返回完整角色卡描述"""
        # 重组顺序
        char_info_dict: Dict[str, str] = {}
        if self.name:
            char_info_dict[CHAR_INFO_KEY_NAME] = self.name
        read_data_from_str_to_dict(self.hp_info.get_char_info(), char_info_dict)
        read_data_from_str_to_dict(self.ability_info.get_char_info(), char_info_dict)
        char_info = ""
        for key in CHAR_INFO_KEY_LIST:
            if key in char_info_dict:
                char_info += f"{key} {char_info_dict[key]}\n"
        return char_info.strip()

    def use_hp_dice(self, num: int) -> str:
        con_mod: int = self.ability_info.get_modifier(check_item_index_dict["体质"])
        return self.name + self.hp_info.use_hp_dice(num, con_mod)

    def long_rest(self) -> str:
        result: str = f"{self.name}进行了一次长休\n"
        result += self.hp_info.long_rest()
        return result.strip()


def gen_template_char() -> DNDCharInfo:
    char_info = DNDCharInfo()
    char_info.name = "张三"

    char_info.hp_info.hp_cur = 20
    char_info.hp_info.hp_max = 30
    char_info.hp_info.hp_temp = 5
    char_info.hp_info.hp_dice_type = 8
    char_info.hp_info.hp_dice_num = 3
    char_info.hp_info.hp_dice_max = 4

    char_info.ability_info.level = 4
    char_info.ability_info.ability = [10, 15, 12, 13, 8, 11]
    char_info.ability_info.check_prof[check_item_index_dict["敏捷攻击"]] = 1
    char_info.ability_info.check_prof[check_item_index_dict["敏捷豁免"]] = 1
    char_info.ability_info.check_prof[check_item_index_dict["体操"]] = 1
    char_info.ability_info.check_prof[check_item_index_dict["隐匿"]] = 2

    char_info.ability_info.check_adv[ext_item_index_dict["隐匿"]] = 1

    char_info.ability_info.check_ext[ext_item_index_dict["豁免"]] = "+2"
    char_info.ability_info.check_ext[ext_item_index_dict["敏捷攻击"]] = "+1d4"
    return char_info
