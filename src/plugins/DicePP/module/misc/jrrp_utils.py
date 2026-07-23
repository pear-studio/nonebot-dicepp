"""
JRRP 共享计算函数和文本渲染

提供 compute_jrrp 函数，供 JrrpCommand、PersonaCommand、get_jrrp 工具共用。
同时提供独立的文本渲染函数。

format_jrrp_* 函数是输出字符串的单事实来源。
未来如需 i18n 支持，需参数化这些函数以支持语言切换。
"""
import random
import datetime
from typing import NamedTuple, Literal

from plugins.DicePP.utils.time import datetime_to_str_day


class JrrpResult(NamedTuple):
    """JRRP 计算结果"""
    jrrp: int              # 今日值 (1-100)
    zrrp: int              # 昨日值 (1-100)
    delta: int             # 绝对差值
    delta_percent: float   # 百分比变化
    direction: Literal['up', 'down', 'same']  # 方向
    is_min: bool           # 是否为 1
    is_max: bool           # 是否为 100


def compute_jrrp(user_id: str, date: datetime.datetime) -> JrrpResult:
    """计算指定用户在某日的 JRRP 值

    种子字符串使用 ``datetime_to_str_day(date) + str(user_id)``，
    与现有 JrrpCommand 一致。

    Args:
        user_id: 用户 ID（任意非空字符串）
        date: 目标日期

    Returns:
        JrrpResult NamedTuple
    """
    # 昨日 seed：昨日日期 + user_id
    yesterday = date - datetime.timedelta(days=1)
    zrrp_rng = random.Random(datetime_to_str_day(yesterday) + str(user_id))
    zrrp: int = zrrp_rng.randint(1, 100)

    # 今日 seed：今日日期 + user_id
    jrrp_rng = random.Random(datetime_to_str_day(date) + str(user_id))
    jrrp: int = jrrp_rng.randint(1, 100)

    # 注意：使用独立的 Random 实例而非全局 random.seed()，
    # 避免影响模块级 random 的其他调用方（如 sleep_messages、refuse_messages 的 random.choice）。

    # 计算 delta 和 delta_percent
    # zrrp 由 randint(1, 100) 保证 ≥ 1，无需零值保护
    delta = jrrp - zrrp
    delta_percent = round(abs(jrrp - zrrp) / zrrp * 100, 2)

    if jrrp > zrrp:
        direction = 'up'
    elif jrrp < zrrp:
        direction = 'down'
    else:
        direction = 'same'

    return JrrpResult(
        jrrp=jrrp,
        zrrp=zrrp,
        delta=delta,
        delta_percent=delta_percent,
        direction=direction,
        is_min=(jrrp == 1),
        is_max=(jrrp == 100),
    )


def format_jrrp_info_line(name: str, jrrp: int) -> str:
    """生成纯数值行（含极端值时的"大凶"/"大吉"标签）

    Args:
        name: 用户显示名
        jrrp: 今日人品值

    Returns:
        形如 ``"{name}的今日人品是:{jrrp}"`` 的字符串；
        jrrp=1 时使用"大凶"标签，jrrp=100 时使用"大吉"标签
    """
    if jrrp == 1:
        return f"{name}的今日人品是:...你确定要听么..是大凶的{jrrp}哦..."
    elif jrrp == 100:
        return f"{name}的今日人品是:...这是！这是大吉的{jrrp}哦！"
    else:
        return f"{name}的今日人品是:{jrrp}"


def format_jrrp_trend_line(zrrp: int, jrrp: int, delta_percent: float, direction: Literal['up', 'down', 'same']) -> str:
    """生成趋势行

    Args:
        zrrp: 昨日人品值
        jrrp: 今日人品值
        delta_percent: 百分比变化
        direction: 方向 ('up', 'down', 'same')

    Returns:
        趋势文本，如 ``"人品比昨天上升了 X%"`` / ``"人品比昨天下降了 X%"`` / ``"人品与昨天相同"``
    """
    if direction == 'up':
        return f"\n人品比昨天上升了{delta_percent}%！"
    elif direction == 'down':
        return f"\n人品比昨天下降了{delta_percent}%呢..."
    else:
        return "\n人品与昨天相同呢。"


# 注：此函数输出格式与 command.py _handle_jrrp 中 change_text 内联计算保持同步。
# 修改任一处时需同步更新另一处。
def format_compact_trend(delta_percent: float, direction: Literal['up', 'down', 'same']) -> str:
    """生成紧凑趋势文本（无换行前缀），供 command.py 和 get_jrrp.py 复用。

    Returns:
        ``"上涨 X%"`` / ``"下跌 X%"`` / ``"与昨日相同"``
    """
    if direction == 'up':
        return f"上涨 {delta_percent}%"
    elif direction == 'down':
        return f"下跌 {delta_percent}%"
    else:
        return "与昨日相同"


def format_jrrp_text(name: str, jrrp: int, zrrp: int, delta_percent: float, direction: str) -> str:
    """便捷组合函数：生成完整两行文本

    供 JrrpCommand.process_msg 和 PersonaCommand._handle_jrrp 回退路径使用。

    Returns:
        ``format_jrrp_info_line`` + ``format_jrrp_trend_line`` 拼接结果
    """
    info_line = format_jrrp_info_line(name, jrrp)
    trend_line = format_jrrp_trend_line(zrrp, jrrp, delta_percent, direction)
    return info_line + trend_line
