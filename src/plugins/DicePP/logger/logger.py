import sys
import traceback
import re
from typing import List


def dice_log(*args, **kwargs):
    """
    记录Log信息
    """
    if kwargs:
        print("logger: ", *args, kwargs)
    else:
        print("logger: ", *args)


def get_exception_info() -> List[str]:
    """返回当前简洁的堆栈信息, 越后面的字符串代表越深的堆栈, 最后一个字符串代表错误类型. 如果当前无错误堆栈, 输出空数组"""
    et, ev, tb = sys.exc_info()
    msg = traceback.format_exception(et, ev, tb)
    for i, m in enumerate(msg):
        msg[i] = re.sub(r'File ".*DicePP(.*)"', lambda match: str(match.groups()[-1])[1:], m).strip()
        msg[i] = re.sub(r", in.*\s{2,}", lambda match: str(match.group()).strip()+": ", msg[i])
    return msg[1:]
