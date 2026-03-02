import abc
import operator
from typing import Dict, Type, Union, Iterable, Tuple, Any

from .formula import *
from .roll_config import *
from .roll_utils import RollDiceError, roll_a_dice
from .result import RollResult


class RollExpModifier(metaclass=abc.ABCMeta):
    """
    用于表示掷骰表达式修饰符
    """
    @abc.abstractmethod
    def __init__(self, args: str):
        # 确保init签名一致
        pass

    @abc.abstractmethod
    def expectation(self, roll_res: RollResult) -> RollResult:
        """
        直接计算期望数值而不进行处理，期望数值会先算一边，然后如果修饰器不用则用修饰器决定
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def modify(self, roll_res: RollResult) -> RollResult:
        """
        修改并返回掷骰数据, 返回的变量应该基于初始变量
        """
        raise NotImplementedError()


ROLL_MODIFIERS_DICT: Dict[str, Type[RollExpModifier]] = {}


def roll_modifier(regexp: Union[str, Iterable[str]]):
    """
    类修饰器, 将自定义掷骰表达式修饰符注册到列表中
    Args:
        regexp: 一个或多个正则表达式, 用来唯一地匹配该修饰符, 若有多个符合则较早定义的优先
    """
    def inner(cls: Type[RollExpModifier]):
        """
        Args:
            cls: 修饰的类必须继承自RollExpModifier
        Returns:
            cls: 返回修饰后的cls
        """
        assert issubclass(cls, RollExpModifier)
        if type(regexp) is str:
            symbols = [regexp]
        else:
            symbols = regexp
        for s in symbols:
            assert " " not in s and s.isupper()
            assert s not in ROLL_MODIFIERS_DICT.keys()
            ROLL_MODIFIERS_DICT[s] = cls
        return cls
    return inner

# 注意定义的顺序也即是执行优先级, 越早定义则优先级越高
# 同一种修饰符的匹配优先级为从左到右


@roll_modifier("(R|X|XO)(<|>|=|<=|>=|==)?[1-9][0-9]*")
class REModReroll(RollExpModifier):
    """
    修饰符R:某一个骰子满足条件则重骰
    修饰符X:额外再投一颗
    修饰符XO:额外投掷一颗（仅一颗）
    """
    def __init__(self, args: str):
        super().__init__(args)
        # 模式
        if args[1] == "O":
            mod, args = args[:2], args[2:]
        else:
            mod, args = args[0], args[1:]

        # 判定条件
        self.mod: str = mod
        self.comp: str
        self.rhs: int
        self.op, self.comp, self.rhs = condition_split(args)

        if self.rhs < DICE_CONSTANT_MIN or self.rhs > DICE_CONSTANT_MAX:
            raise RollDiceError(f"常量大小必须在{DICE_CONSTANT_MIN}至{DICE_CONSTANT_MAX}之间")
    
    def expectation(self, roll_res: RollResult) -> RollResult:
        dice_type = max(roll_res.type,1)
        ce = condition_expectation(1,dice_type,self.op,self.rhs) # 价值率
        cr = condition_range(1,dice_type,self.op,self.rhs) # 价值范围
        delta_exp = float(1 + dice_type)/2
        roll_res.exp += self.mod + self.comp + str(self.rhs)
        if self.mod == "R":
            # 重骰对期望的影响 = （重骰期望 - 重骰前期望）* 重骰概率
            delta_exp -= float(cr[0] + cr[1])/2
            delta_exp *= ce
        elif self.mod == "XO":
            # 额外骰一次对期望的影响 = 重骰期望 * 重骰概率
            delta_exp *= ce
        else:  # "X"
            # 额外骰无数次对期望的影响 = (重骰期望) * (重骰概率 + 重骰概率^2 + ......
            # 理论上ce=1/10时的极限为1/9，因此重新计算一个分子/(分母-分子)就好（偷懒！）
            # 完全重骰概率 = 触发面数 / ( 骰子面数 - 触发面数 )
            trig = float(cr[1] - cr[0] + 1)
            if dice_type > trig:
                ce = trig / (dice_type - trig)
                delta_exp *= ce
            else:
                # 期望为无限
                roll_res.val_list = [0 for val in roll_res.val_list]
                roll_res.info = "∞"
                return roll_res
        roll_res.val_list = [val + delta_exp for val in roll_res.val_list]
        roll_res.info = str(round(sum(roll_res.val_list),2))
        return roll_res

    def modify(self, roll_res: RollResult) -> RollResult:
        """
        输入的roll_res必须由形如XDY的表达式产生, 如果是常量表达式或复合表达式将会抛出一个异常
        """
        if roll_res.dice_num == 0:  # 只处理含有骰子的表达式
            raise RollDiceError(f"重骰对象只能为形如XDY的表达式, 如2D20R1, 此处为{roll_res.exp}")

        # 如果一个条件都不满足直接原样返回
        if all((not self.op(val, self.rhs) for val in roll_res.val_list)):
            roll_res.exp += self.mod + self.comp + str(self.rhs)
            return roll_res
        new_val_list = roll_res.val_list.copy()
        new_info_list = [f"[{str(val)}]" for val in roll_res.val_list]

        for index, val in enumerate(roll_res.val_list):
            if self.op(val, self.rhs):
                if self.mod == "R":
                    add_dice = roll_a_dice(roll_res.type)
                    new_info_list[index] = f"[{roll_res.val_list[index]}̶→{add_dice}]"
                    new_val_list[index] = add_dice
                elif self.mod == "XO":
                    add_dice = roll_a_dice(roll_res.type)
                    new_info_list[index] += f"‹{add_dice}›"
                    new_val_list.append(add_dice)
                else:  # "X"
                    repeat_time = 0
                    add_dice = val
                    dice_type = max(roll_res.type,1)
                    cr = condition_range(1,dice_type,self.op,self.rhs) # 价值范围
                    if dice_type > (cr[1] - cr[0] + 1):
                        # 无限处理重骰
                        while self.op(add_dice, self.rhs):
                            repeat_time += 1
                            add_dice = roll_a_dice(roll_res.type)
                            new_val_list.append(add_dice)
                            new_info_list[index] = new_info_list[index][:-1] + f"|{add_dice}]"
                            if repeat_time > EXPLODE_LIMIT:
                                raise RollDiceError(f"爆炸次数过多")
                    else:
                        raise RollDiceError(f"掷骰结果出现无限大,该范围{cr[0]}~{cr[1]}不可用")
        roll_res.val_list = new_val_list
        roll_res.info = "".join(new_info_list)
        roll_res.exp += self.mod + self.comp + str(self.rhs)
        #roll_res.d20_state = 0
        return roll_res


@roll_modifier("CS(<|>|=|>=|<=|==)?[1-9][0-9]*")
class REModCountSuccess(RollExpModifier):
    """
    修饰符CS:计算当前结果中符合条件的骰值的数量
    """

    def __init__(self, args: str):
        super().__init__(args)

        args = args[2:]
        # 判定条件
        self.comp: str
        self.rhs: int
        self.op, self.comp, self.rhs = condition_split(args)
        if self.rhs < DICE_CONSTANT_MIN or self.rhs > DICE_CONSTANT_MAX:
            raise RollDiceError(f"常量大小必须在{DICE_CONSTANT_MIN}至{DICE_CONSTANT_MAX}之间")

    def expectation(self, roll_res: RollResult) -> RollResult:
        raise RollDiceError(f"暂时不支持处理这种修饰的快速期望")

    def modify(self, roll_res: RollResult) -> RollResult:
        # result = sum([self.op(val, self.rhs) for val in roll_res.val_list])
        result = ""
        successes = 0
        failures = 0
        for val in roll_res.val_list:
            if self.op(val, self.rhs):
                successes += 1
            else:
                failures += 1
        
        if (successes + failures) == 1:
            if successes == 1:
                result = "成功"
            else: #if failures == 1:
                result = "失败"
        else:
            if successes:
                result += str(successes)+"次成功"
            if failures:
                result += str(failures)+"次失败"
        roll_res.info = roll_res.get_styled_dice_info() + " " + self.comp + " " + str(self.rhs) + f"({result})"
        #roll_res.val_list = [result]
        roll_res.exp = f"{roll_res.exp}CS{self.comp}{self.rhs}"
        return roll_res

@roll_modifier("F")
class REModFloat(RollExpModifier):
    """
    修饰符F:将结果变化为Float类型
    """

    def __init__(self, args: str):
        super().__init__(args)

    def expectation(self, roll_res: RollResult) -> RollResult:
        roll_res.info = str(round(sum(roll_res.val_list),2))
        return self.modify(roll_res)

    def modify(self, roll_res: RollResult) -> RollResult:
        roll_res.float_state = True
        roll_res.exp = f"{roll_res.exp}F"
        return roll_res

@roll_modifier("M[1-9][0-9]?")
class REModMinimum(RollExpModifier):
    """
    修饰符M:骰子保底出目
    """
    def __init__(self, args: str):
        super().__init__(args)
        self.num = int(args[1:])

    def expectation(self, roll_res: RollResult) -> RollResult:
        dice_type = max(roll_res.type,1)
        ce = condition_expectation(1,dice_type,operator.le,self.num) # 价值率
        cr = condition_range(1,dice_type,operator.le,self.num) # 价值范围
        roll_res.exp += "M" + str(self.num)
        # 保底对期望的影响 = （保底值 - 保底前期望）* 重骰概率
        delta_exp = ( self.num - float(cr[0] + cr[1])/2 ) * ce
        roll_res.val_list = [val + delta_exp for val in roll_res.val_list]
        roll_res.info = str(round(sum(roll_res.val_list),2))
        return roll_res

    def modify(self, roll_res: RollResult) -> RollResult:
        pt: int = max(1,min(self.num,roll_res.type))
        for index in range(len(roll_res.val_list)):
            if roll_res.val_list[index] < pt:
                roll_res.info = roll_res.info.replace(f"[{roll_res.val_list[index]}]",f"[{roll_res.val_list[index]}→{pt}]")
                roll_res.val_list[index] = pt
        roll_res.exp = f"{roll_res.exp}M{self.num}"
        # 因为修改了骰子情况，所以重新算一遍大成功与大失败
        if roll_res.type == 20:
            roll_res.d20_num = self.num
            roll_res.success_or_fail(20,1)
        elif roll_res.type == 100:
            roll_res.d20_num = self.num
            roll_res.success_or_fail(1,100)
        return roll_res

@roll_modifier("P[1-9][0-9]?")
class REModPortent(RollExpModifier):
    """
    修饰符P:强制修改骰子出目
    """
    def __init__(self, args: str):
        super().__init__(args)
        self.num = int(args[1:])

    def expectation(self, roll_res: RollResult) -> RollResult:
        roll_res.info = str(round(sum(roll_res.val_list),2))
        return self.modify(roll_res)

    def modify(self, roll_res: RollResult) -> RollResult:
        pt: int = max(1,min(self.num,roll_res.type))
        for index in range(len(roll_res.val_list)):
            roll_res.info = roll_res.info.replace(f"[{roll_res.val_list[index]}]",f"[={pt}]")
            roll_res.val_list[index] = pt
        roll_res.exp = f"{roll_res.exp}P{self.num}"
        # 因为修改了骰子情况，所以重新算一遍大成功与大失败
        if roll_res.type == 20:
            roll_res.d20_num = self.num
            roll_res.success_or_fail(20,1)
        elif roll_res.type == 100:
            roll_res.d100_num = self.num
            roll_res.success_or_fail(1,100)
        return roll_res

@roll_modifier("K[HL]?[1-9][0-9]?")
class REModMinMax(RollExpModifier):
    """
    修饰符KL:取表达式中x个最大值
    修饰符KH:取表达式中最小值
    """
    def __init__(self, args: str):
        super().__init__(args)
        self.exp_str: str = "K"
        self.formula: str = "MAX"
        if args[1] == "H": # 取最大值
            self.exp_str = "KH"
            val_str = args[2:]
            self.formula = "MAX"
        elif args[1] == "L": # 取最小值
            self.exp_str = "KL"
            val_str = args[2:]
            self.formula = "MIN"
        else: # 默认为取最大值
            val_str = args[1:]
        self.num = int(val_str)

    def expectation(self, roll_res: RollResult) -> RollResult:
        roll_res.exp = f"{roll_res.exp}{self.exp_str}{self.num}"
        """
        # 暂时没有更好方法，先限定只能2骰取1
        if roll_res.dice_num != 2 or self.num != 1:
            raise RollDiceError(f"暂时不支持处理2骰取1以外的期望")
        # 抛弃原本的期望，重新计算期望
        dice_type = max(roll_res.type,1)
        poss_result_sum: int = 0 # 全部可能性的结果之和
        poss_num: int = pow(dice_type,2) # 全部可能性的结果之和
        if self.formula == "MAX":
            # 取最大值，每个骰值X加起来等同于X(2X-1)
            for index in range(1,dice_type+1):
                poss_result_sum += (index * 2 - 1) * index
        elif self.formula == "MIN":
            # 取最小值，每个骰值X出现的数量等同于X(2Y-2X+2-1)
            for index in range(1,dice_type+1):
                poss_result_sum += (dice_type * 2 - index * 2 + 1) * index
        roll_res.val_list = [float(poss_result_sum) / poss_num]
        roll_res.info = str(round(sum(roll_res.val_list),2))
        """
        if self.formula == "MAX":
            roll_res.val_list = [self.xdykz_exp(roll_res.dice_num,roll_res.type,self.num)]
        elif self.formula == "MIN":
            roll_res.val_list = [self.xdykz_exp(roll_res.dice_num,roll_res.type,self.num,True)]
        roll_res.info = str(round(sum(roll_res.val_list),2))
        return roll_res
    
    def xdykz_exp(self,x:int,y:int,z:int,anti:bool=False):
        base_var = (y+1)/2*x # 基础值，即为不在乎Z情况下期望
        if x == z: # N骰取和
            result = base_var
        elif z == 1: # N骰取其一
            total_r = y ** x # 可能的结果数量
            total_var = 0 # 总和值
            pow_var = [pow(i,x) for i in range(0,y+1)] # 乘算值
            if anti: # 取最小值
                total_var = sum([
                    i*(pow_var[y-i+1]-pow_var[y-i])
                    for i in range(1,y+1)
                ])
            else: # 取最大值
                total_var = sum([
                    i*(pow_var[i]-pow_var[i-1])
                    for i in range(1,y+1)
                ])
            result = total_var / total_r
        elif z == x-1: # N骰踢1，倒桩即可
            result = base_var - self.xdykz_exp(x,y,1,not anti)
        else: # N骰踢M，最麻烦的情况，没办法，摆烂！
            raise RollDiceError(f"算不出来——摆烂啦！")
        return result

    def modify(self, roll_res: RollResult) -> RollResult:
        """
        注意val的顺序会被重新排序, 但info里的内容不会
        """
        new_info = self.formula
        if self.num != 1:
            new_info += str(self.num)

        new_info += "{" + roll_res.get_styled_dice_info() + "}"

        # 只有在列表足够大的情况下才会处理骰子列表，以免报错
        if len(roll_res.val_list) > self.num:
            if self.formula == "MAX":
                roll_res.val_list = sorted(roll_res.val_list)[-self.num:]
            else: # if self.formula == "MIN":
                roll_res.val_list = sorted(roll_res.val_list)[:self.num]      
        roll_res.exp = f"{roll_res.exp}{self.exp_str}{self.num}"
        roll_res.info = new_info
        # 因为修改了骰子情况，所以重新算一遍大成功与大失败
        if roll_res.type == 20:
            roll_res.d20_num = self.num
            roll_res.success_or_fail(20,1)
        elif roll_res.type == 100:
            roll_res.d20_num = self.num
            roll_res.success_or_fail(1,100)

        return roll_res