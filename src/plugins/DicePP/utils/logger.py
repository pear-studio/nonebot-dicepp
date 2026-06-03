import sys
import re
import contextvars
import traceback
from typing import List

from loguru import logger

LOG_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{line}</cyan> | "
    "{extra[request_id]:--------} | "
    "<level>{message}</level>"
)

# 移除默认 handler
logger.remove()

# stderr handler（默认 DEBUG 级别，运行时可通过 configure_log_level 调整）
_STDERR_HANDLER_ID = logger.add(
    sys.stderr, format=LOG_FORMAT, level="DEBUG", colorize=True,
)

# error.log 文件 handler（延迟创建，10MB 轮转）
logger.add(
    "error.log", rotation="10 MB", diagnose=False, level="ERROR",
    format=LOG_FORMAT, delay=True,
)

# 配置默认的 extra 值，避免未设置 request_id 时报 KeyError
logger.configure(extra={"request_id": "--------"})


def configure_log_level(level: str) -> None:
    """运行时调整 stderr handler 日志级别（启动后从 config 读取）。"""
    global _STDERR_HANDLER_ID
    try:
        logger.remove(_STDERR_HANDLER_ID)
    except ValueError:
        pass
    _STDERR_HANDLER_ID = logger.add(
        sys.stderr, format=LOG_FORMAT, level=level.upper(), colorize=True,
    )


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
# 上层通过 logger.contextualize(request_id=...) 注入结构化字段，子调用直接用 logger.xxx()。
_request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "chat_request_id", default="--------",
)
