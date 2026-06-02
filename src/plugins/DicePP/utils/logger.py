import sys
import traceback
import re
import contextvars
from typing import List, Optional


def dice_log(*args, **kwargs):
    """
    记录Log信息
    """
    kwargs.pop("file", None)
    print("logger: ", *args, file=sys.stderr, **kwargs)


def get_exception_info() -> List[str]:
    """返回当前简洁的堆栈信息, 越后面的字符串代表越深的堆栈, 最后一个字符串代表错误类型. 如果当前无错误堆栈, 输出空数组"""
    et, ev, tb = sys.exc_info()
    msg = traceback.format_exception(et, ev, tb)
    for i, m in enumerate(msg):
        msg[i] = re.sub(r'File ".*DicePP(.*)"', lambda match: str(match.groups()[-1])[1:], m).strip()
        msg[i] = re.sub(r", in.*\s{2,}", lambda match: str(match.group()).strip()+": ", m)
    return msg[1:]


# 单次 chat 请求的 trace_id 上下文（最小实现，不抽公共 wrapper）
# 在 process_msg 入口 _request_id_var.set()，finally 块 _request_id_var.reset()。
# asyncio task 之间通过 contextvars 自动传播，无需手动传参。
# 调用方直接使用 logger.bind(request_id=_request_id_var.get()).info(...) 注入结构化字段。
_request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "chat_request_id", default="--------",
)
