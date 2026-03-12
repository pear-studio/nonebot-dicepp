"""
角色数据模型 (Pydantic)

设计原则:
- 此文件只包含纯数据字段和不依赖外部模块的方法
- 依赖 roll 模块的复杂业务逻辑放在 module/character/*/services.py

DND5E 角色相关常量定义在 module/character/dnd5e/constants.py
"""
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


# ============================================================
# HP 信息
# ============================================================

CHAR_INFO_KEY_HP = "$生命值$"
CHAR_INFO_KEY_HP_DICE = "$生命骰$"


class HPInfo(BaseModel):
    """HP信息"""
    is_init: bool = False
    is_alive: bool = True
    hp_cur: int = 0   # 当前生命值
    hp_max: int = 0   # 最大生命值
    hp_temp: int = 0  # 临时生命值
    hp_dice_type: int = 0   # 生命骰面数
    hp_dice_num: int = 0    # 生命骰数量
    hp_dice_max: int = 0    # 生命骰最大数量

    def initialize(
        self,
        hp_cur: int,
        hp_max: int = 0,
        hp_temp: int = 0,
        hp_dice_type: int = 0,
        hp_dice_num: int = 0,
        hp_dice_max: int = 0
    ) -> None:
        """初始化 HP 信息"""
        assert hp_max >= hp_cur >= 0, f"无效的生命值信息: {hp_cur}/{hp_max}"
        assert hp_temp >= 0, f"无效的临时生命值信息: {hp_temp}"
        assert 100 >= hp_dice_type >= 0 and 1000 >= hp_dice_max >= 0, \
            f"无效的生命骰信息: {hp_dice_max}颗{hp_dice_type}面骰"

        self.is_init = True
        self.is_alive = True
        self.hp_cur = hp_cur
        self.hp_max = hp_max
        self.hp_temp = hp_temp
        self.hp_dice_type = hp_dice_type
        self.hp_dice_num = hp_dice_num
        self.hp_dice_max = hp_dice_max

    def is_record_normal(self) -> bool:
        """当前是否正常记录生命值 (拥有hp, 而不是单纯记录受损hp)"""
        return self.hp_cur > 0 or (self.hp_cur == 0 and not self.is_alive)

    def is_record_damage(self) -> bool:
        """当前是否是记录受损生命值的情况"""
        return not self.is_record_normal()

    def take_damage(self, value: int) -> None:
        """受到伤害"""
        # 临时生命值先吸收
        if self.hp_temp > 0:
            if self.hp_temp >= value:
                self.hp_temp -= value
                return
            else:
                value -= self.hp_temp
                self.hp_temp = 0
        # 生命值
        if self.is_alive:
            if self.hp_cur > 0:
                if self.hp_cur > value:
                    self.hp_cur -= value
                else:
                    self.hp_cur = 0
                    self.is_alive = False
            else:  # hp_cur <= 0 且 is_alive==True 说明当前记录的是受损生命值
                self.hp_cur -= value

    def heal(self, value: int) -> None:
        """治疗"""
        if self.is_record_normal():
            if self.hp_max == 0:  # 没有设置生命值上限
                self.hp_cur += value
            else:
                self.hp_cur = min(self.hp_max, self.hp_cur + value)
        else:  # 记录受损生命值的情况
            self.hp_cur = min(0, self.hp_cur + value)
        self.is_alive = True

    def get_info(self) -> str:
        """获取 HP 信息字符串"""
        hp_temp_info = f" ({self.hp_temp})" if self.hp_temp != 0 else ""
        if self.is_record_normal():
            hp_max_info = f"/{self.hp_max}" if self.hp_max != 0 else ""
            hp_info = f"HP:{self.hp_cur}{hp_max_info}{hp_temp_info}"
            if not self.is_alive:
                hp_info += " 昏迷"
        else:
            hp_info = f"损失HP:{-self.hp_cur}{hp_temp_info}"
        return hp_info

    def long_rest(self) -> str:
        """长休 - 恢复生命值和生命骰"""
        info = ""
        if self.hp_max != 0:
            info = f"生命值回复至上限({self.hp_max})"
            self.hp_cur = self.hp_max
        if self.hp_temp != 0:
            info += f" {self.hp_temp}点临时生命值失效"
            self.hp_temp = 0
        if self.hp_dice_max != 0 and self.hp_dice_type != 0:
            prev_hp_dice_num = self.hp_dice_num
            self.hp_dice_num = int(max(1, min(
                self.hp_dice_max,
                self.hp_dice_num + self.hp_dice_max // 2
            )))
            info += f"\n回复{self.hp_dice_num - prev_hp_dice_num}个生命骰, "
            info += f"当前拥有{self.hp_dice_num}/{self.hp_dice_max}个D{self.hp_dice_type}生命骰"
        return info.strip()

    def get_char_info(self) -> str:
        """
        返回可用来组合成初始化角色卡的字符串
        例子:
        $生命值$ 5/10 (4)
        $生命骰$ 4/10 D6
        """
        info = f"{CHAR_INFO_KEY_HP} {self.hp_cur}"
        if self.hp_max > 0:
            info += f"/{self.hp_max}"
        if self.hp_temp > 0:
            info += f" ({self.hp_temp})"
        if self.hp_dice_type > 0:
            info += f"\n{CHAR_INFO_KEY_HP_DICE} {self.hp_dice_num}/{self.hp_dice_max} D{self.hp_dice_type}"
        return info


# ============================================================
# 属性信息
# ============================================================

# 属性列表
ABILITY_LIST = ["力量", "敏捷", "体质", "智力", "感知", "魅力"]
ABILITY_NUM = len(ABILITY_LIST)

# 技能列表
SKILL_LIST = [
    "运动",
    "体操", "巧手", "隐匿", "先攻",
    "奥秘", "历史", "调查", "自然", "宗教",
    "驯兽", "洞悉", "医药", "察觉", "求生",
    "欺瞒", "威吓", "表演", "游说",
]
SKILL_NUM = len(SKILL_LIST)

# 技能对应的属性
SKILL_PARENT_DICT = {
    "运动": "力量",
    "体操": "敏捷", "巧手": "敏捷", "隐匿": "敏捷", "先攻": "敏捷",
    "奥秘": "智力", "历史": "智力", "调查": "智力", "自然": "智力", "宗教": "智力",
    "驯兽": "感知", "洞悉": "感知", "医药": "感知", "察觉": "感知", "求生": "感知",
    "欺瞒": "魅力", "威吓": "魅力", "表演": "魅力", "游说": "魅力",
}

# 技能同义词
SKILL_SYNONYM_DICT = {
    "特技": "体操", "妙手": "巧手", "潜行": "隐匿", "隐蔽": "隐匿", "隐秘": "隐匿", "躲藏": "隐匿",
    "驯养": "驯兽", "驯服": "驯兽", "医疗": "医药", "医术": "医药", "观察": "察觉", "生存": "求生",
    "欺骗": "欺瞒", "欺诈": "欺瞒", "哄骗": "欺瞒", "唬骗": "欺瞒", "威胁": "威吓", "说服": "游说"
}

# 豁免列表
SAVING_LIST = ["力量豁免", "敏捷豁免", "体质豁免", "智力豁免", "感知豁免", "魅力豁免"]
SAVING_NUM = len(SAVING_LIST)

SAVING_PARENT_DICT = {
    "力量豁免": "力量", "敏捷豁免": "敏捷", "体质豁免": "体质",
    "智力豁免": "智力", "感知豁免": "感知", "魅力豁免": "魅力",
}

# 攻击列表
ATTACK_LIST = ["力量攻击", "敏捷攻击", "体质攻击", "智力攻击", "感知攻击", "魅力攻击"]
ATTACK_NUM = len(ATTACK_LIST)

ATTACK_PARENT_DICT = {
    "力量攻击": "力量", "敏捷攻击": "敏捷", "体质攻击": "体质",
    "智力攻击": "智力", "感知攻击": "感知", "魅力攻击": "魅力",
}

# 可检定条目
CHECK_ITEM_LIST = ABILITY_LIST + SKILL_LIST + SAVING_LIST + ATTACK_LIST
CHECK_ITEM_NUM = ABILITY_NUM * 3 + SKILL_NUM
CHECK_ITEM_INDEX_DICT = dict((k, i) for i, k in enumerate(CHECK_ITEM_LIST))

# 可额外加值条目
SAVING_ALL_KEY = "豁免"
ATTACK_ALL_KEY = "攻击"
EXT_ITEM_LIST = CHECK_ITEM_LIST + [SAVING_ALL_KEY, ATTACK_ALL_KEY]
EXT_ITEM_NUM = CHECK_ITEM_NUM + 2
EXT_ITEM_INDEX_DICT = dict((k, i) for i, k in enumerate(EXT_ITEM_LIST))

# 角色卡关键字
CHAR_INFO_KEY_LEVEL = "$等级$"
CHAR_INFO_KEY_ABILITY = "$属性$"
CHAR_INFO_KEY_PROF = "$熟练$"
CHAR_INFO_KEY_EXT = "$额外加值$"
CHAR_INFO_KEY_NAME = "$姓名$"


class AbilityInfo(BaseModel):
    """属性信息"""
    is_init: bool = False
    version: int = 1  # 版本号
    level: int = 0    # 等级
    ability: List[int] = Field(default_factory=lambda: [0] * ABILITY_NUM)
    check_prof: List[int] = Field(default_factory=lambda: [0] * CHECK_ITEM_NUM)
    check_ext: List[str] = Field(default_factory=lambda: [""] * EXT_ITEM_NUM)
    check_adv: List[int] = Field(default_factory=lambda: [0] * EXT_ITEM_NUM)

    def get_prof_bonus(self) -> int:
        """获取熟练加值"""
        return 2 + (self.level - 1) // 4

    def get_modifier(self, ability_index: int) -> int:
        """获取属性调整值"""
        ability_score = self.ability[ability_index]
        return (ability_score - 10) // 2

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
        prof_info = [
            (scale, CHECK_ITEM_LIST[index])
            for index, scale in enumerate(self.check_prof) if scale > 0
        ]
        prof_list = []
        for scale, prof_name in prof_info:
            if scale == 1:
                prof_list.append(prof_name)
            else:
                prof_list.append(f"{scale}*{prof_name}")
        if prof_list:
            info += f"{CHAR_INFO_KEY_PROF} {'/'.join(prof_list)}\n"

        # 额外加值信息
        ext_info = [
            (EXT_ITEM_LIST[index], ext_str)
            for index, ext_str in enumerate(self.check_ext) if ext_str
        ]
        adv_dict = dict([
            (EXT_ITEM_LIST[index], adv_flag)
            for index, adv_flag in enumerate(self.check_adv) if adv_flag != 0
        ])
        ext_list = []
        for check_name, ext_str in ext_info:
            ext_str_prefix = ""
            if check_name in adv_dict:
                ext_str_prefix = "优势" if adv_dict[check_name] > 0 else "劣势"
            ext_list.append(f"{check_name}:{ext_str_prefix}{ext_str}")
        if ext_list:
            info += f"{CHAR_INFO_KEY_EXT} {'/'.join(ext_list)}\n"

        return info.strip()


class SpellInfo(BaseModel):
    """法术信息 (暂未完整实现)"""
    is_init: bool = False


class MoneyInfo(BaseModel):
    """金钱信息 (暂未完整实现)"""
    is_init: bool = False


# ============================================================
# 角色模型
# ============================================================

class DNDCharacter(BaseModel):
    """DND5E 角色卡"""
    group_id: str
    user_id: str
    name: str = ""
    hp_info: HPInfo = Field(default_factory=HPInfo)
    ability_info: AbilityInfo = Field(default_factory=AbilityInfo)
    spell_info: SpellInfo = Field(default_factory=SpellInfo)
    money_info: MoneyInfo = Field(default_factory=MoneyInfo)
    is_init: bool = False

    def get_char_info(self) -> str:
        """返回完整角色卡描述"""
        char_info = ""
        if self.name:
            char_info += f"{CHAR_INFO_KEY_NAME} {self.name}\n"
        char_info += self.hp_info.get_char_info() + "\n"
        char_info += self.ability_info.get_char_info()
        return char_info.strip()

    def long_rest(self) -> str:
        """长休"""
        result = f"{self.name}进行了一次长休\n"
        result += self.hp_info.long_rest()
        return result.strip()


class COCCharacter(BaseModel):
    """COC 角色卡"""
    group_id: str
    user_id: str
    name: str = ""
    hp_info: HPInfo = Field(default_factory=HPInfo)
    ability_info: AbilityInfo = Field(default_factory=AbilityInfo)
    spell_info: SpellInfo = Field(default_factory=SpellInfo)
    money_info: MoneyInfo = Field(default_factory=MoneyInfo)
    is_init: bool = False