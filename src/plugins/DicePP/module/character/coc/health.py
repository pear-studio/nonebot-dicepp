from typing import Literal, Optional
import json

from core.data import JsonObject, custom_json_object
from module.roll import exec_roll_exp, RollDiceError, RollResult

CHAR_INFO_KEY_HP = "$生命值$"
CHAR_INFO_KEY_HP_DICE = "$生命骰$"


@custom_json_object
class HPInfo(JsonObject):
    """
    HP信息
    """

    def serialize(self) -> str:
        json_dict = self.__dict__
        return json.dumps(json_dict)

    def deserialize(self, json_str: str) -> None:
        json_dict: dict = json.loads(json_str)
        for key, value in json_dict.items():
            if key in self.__dict__:
                self.__setattr__(key, value)

    def __init__(self):
        self.is_init = False
        self.is_alive = True
        self.hp_cur = 0  # 当前生命值
        self.hp_max = 0  # 最大生命值
        self.hp_temp = 0  # 临时生命值
        self.hp_dice_type = 0  # 生命骰面数
        self.hp_dice_num = 0  # 生命骰数量
        self.hp_dice_max = 0  # 生命骰最大数量

    def initialize(self, hp_cur: int, hp_max: int = 0, hp_temp: int = 0, hp_dice_type: int = 0, hp_dice_num: int = 0, hp_dice_max: int = 0):
        assert hp_max >= hp_cur >= 0, f"无效的生命值信息: {hp_cur}/{hp_max}"
        assert hp_temp >= 0, f"无效的临时生命值信息: {hp_temp}"
        assert 100 >= hp_dice_type >= 0 and 1000 >= hp_dice_max >= 0, f"无效的生命骰信息: {hp_dice_max}颗{hp_dice_type}面骰"

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

    def take_damage(self, value: int):
        # 临时生命值
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
            else:  # self.hp_cur <= 0 hp_cur如果小于等于0且is_alive==True说明当前记录的是受损生命值
                self.hp_cur -= value

    def heal(self, value: int):
        if self.is_record_normal():
            if self.hp_max == 0:  # 没有设置生命值上限
                self.hp_cur += value
            else:
                self.hp_cur = min(self.hp_max, self.hp_cur + value)
        else:  # 记录受损生命值的情况
            self.hp_cur = min(0, self.hp_cur + value)
        self.is_alive = True

    def get_info(self) -> str:
        hp_info: str
        hp_temp_info = f" ({self.hp_temp})" if self.hp_temp != 0 else ""
        if self.is_record_normal():
            hp_max_info = f"/{self.hp_max}" if self.hp_max != 0 else ""
            hp_info = f"HP:{self.hp_cur}{hp_max_info}{hp_temp_info}"
            if not self.is_alive:
                hp_info += " 昏迷"
        else:
            hp_info = f"损失HP:{-self.hp_cur}{hp_temp_info}"
        return hp_info

    def use_hp_dice(self, num: int, con_mod: int) -> str:
        """
        使用生命骰, 并返回修改结果描述, 如
        使用2颗D4生命骰, 体质调整值为1, 回复(4+1)+(2+1)=8点生命值
        HP: 2/4 -> 4/4
        """
        if not self.is_init or self.hp_dice_type <= 0:
            return "尚未设置生命骰"
        if num <= 0 or num > 1000:
            return f"无效的生命骰数量({num})"
        if self.hp_dice_num < num:
            return f"生命骰数量不足, 还有{self.hp_dice_num}颗生命骰"
        roll_exp = f"1D{self.hp_dice_type}+{con_mod}"

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

        mod_info = f"使用{num}颗D{self.hp_dice_type}生命骰, 体质调整值为{con_mod}, 回复{roll_result_str}点生命值\n"
        hp_info_str_prev = self.get_info()
        self.hp_dice_num -= num
        self.heal(roll_result_val)
        mod_info += f"{hp_info_str_prev} -> {self.get_info()}"
        return mod_info

    def long_rest(self):
        info = ""
        if self.hp_max != 0:
            info = f"生命值回复至上限({self.hp_max})"
            self.hp_cur = self.hp_max
        if self.hp_temp != 0:
            info += f" {self.hp_temp}点临时生命值失效"
            self.hp_temp = 0
        if self.hp_dice_max != 0 and self.hp_dice_type != 0:
            prev_hp_dice_num = self.hp_dice_num
            self.hp_dice_num = int(max(1, min(self.hp_dice_max, self.hp_dice_num + self.hp_dice_max // 2)))
            info += f"\n回复{self.hp_dice_num-prev_hp_dice_num}个生命骰, 当前拥有{self.hp_dice_num}/{self.hp_dice_max}个D{self.hp_dice_type}生命骰"
        return info.strip()

    def process_roll_result(self, cmd_type: Literal["=", "+", "-"],
                            hp_cur_mod_result: Optional[RollResult] = None,
                            hp_max_mod_result: Optional[RollResult] = None,
                            hp_temp_mod_result: Optional[RollResult] = None,
                            short_feedback=False) -> str:
        """
        根据输入进行修改, 返回修改结果描述, 如:
        当前HP减少 2*4 = 8
        HP: 10 -> HP: 2
        """
        mod_info = ""
        if cmd_type == "=":  # 设置生命值
            self.is_init = True
            if hp_cur_mod_result:
                if self.is_record_normal():
                    self.is_alive = hp_cur_mod_result.get_val() > 0
                self.hp_cur = hp_cur_mod_result.get_val()
                mod_info = f"HP={hp_cur_mod_result.get_result()}"
            if hp_max_mod_result:
                self.hp_max = hp_max_mod_result.get_val()
                if self.hp_cur > self.hp_max:
                    self.take_damage(self.hp_cur - self.hp_max)
                mod_info = f"HP={hp_cur_mod_result.get_result()}/{hp_max_mod_result.get_result()}"
            if hp_temp_mod_result:
                self.hp_temp = hp_temp_mod_result.get_val()
                if "HP=" in mod_info:
                    mod_info += f" ({hp_temp_mod_result.get_result()})"
                else:
                    mod_info = f"临时HP={hp_temp_mod_result.get_result()}"
            mod_info += f"\n当前{self.get_info()}"
        elif cmd_type == "+":  # 增加生命值
            hp_info_str_prev = self.get_info()
            if hp_max_mod_result:  # 先结算生命值上限
                self.hp_max += hp_max_mod_result.get_val()
                mod_info += f"最大HP增加{hp_max_mod_result.get_result()}, "
            if hp_cur_mod_result:
                self.heal(hp_cur_mod_result.get_val())
                mod_info += f"当前HP增加{hp_cur_mod_result.get_result()}"
            if hp_temp_mod_result:
                self.hp_temp += hp_temp_mod_result.get_val()
                if mod_info:
                    mod_info += ", "
                mod_info += f"临时HP增加{hp_temp_mod_result.get_result()}"
            mod_info += f"\n{hp_info_str_prev} -> {self.get_info()}"
        else:  # cmd_type == "-"  扣除生命值
            hp_info_str_prev = self.get_info()
            if hp_temp_mod_result:  # 先结算临时生命值
                self.hp_temp = max(0, self.hp_temp - hp_temp_mod_result.get_val())
                mod_info += f"临时HP减少{hp_temp_mod_result.get_result()}"
            if hp_max_mod_result:  # 先结算生命值上限
                self.hp_max -= hp_max_mod_result.get_val()
                if mod_info:
                    mod_info += ", "
                mod_info += f"最大HP减少{hp_max_mod_result.get_result()}"
                if self.hp_cur > self.hp_max:
                    self.take_damage(self.hp_cur - self.hp_max)
            if hp_cur_mod_result:
                self.take_damage(hp_cur_mod_result.get_val())
                if mod_info:
                    mod_info += ", "
                mod_info += f"当前HP减少{hp_cur_mod_result.get_result()}"
            mod_info += f"\n{hp_info_str_prev} -> {self.get_info()}"

        mod_info = mod_info.strip()
        if short_feedback:
            mod_info = mod_info.replace("\n", "; ")
        return mod_info

    def get_char_info(self) -> str:
        """
        返回可用来组合成初始化角色卡的字符串
        例子:
        $生命值$ 5/10 (4)
        $生命骰$ 4/10 D6
        例子2:
        $生命值$ 8
        """
        info: str
        # 生命值信息
        info = f"{CHAR_INFO_KEY_HP} {self.hp_cur}"
        if self.hp_max > 0:
            info += f"/{self.hp_max}"
        if self.hp_temp > 0:
            info += f" ({self.hp_temp})"
        # 生命骰信息
        if self.hp_dice_type > 0:
            info += f"\n{CHAR_INFO_KEY_HP_DICE} {self.hp_dice_num}/{self.hp_dice_max} D{self.hp_dice_type}"
        return info
