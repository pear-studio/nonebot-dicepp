import os
import sys
import re
import contextvars
import traceback
from pathlib import Path
from typing import List

from loguru import logger

from utils.stdio import configure_redirected_stdio_utf8

configure_redirected_stdio_utf8()

LOG_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{line}</cyan> | "
    "{extra[request_id]} | "
    "<level>{message}</level>"
)

# 文件日志格式（无颜色代码）
FILE_LOG_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | "
    "{level: <8} | "
    "{name}:{line} | "
    "{extra[request_id]} | "
    "{message}"
)


def _safe_format(format_template: str):
    """Return a Loguru formatter that tolerates third-party logger.configure().

    NoneBot calls logger.configure() during startup and replaces the global
    patcher/extra defaults. Keep request_id safety at the handler level so
    framework logs emitted after that reconfiguration still format correctly.
    """

    def formatter(record: dict) -> str:
        _patch_extra(record)
        return format_template + "\n{exception}"

    return formatter


def _get_logs_dir() -> str:
    """获取持久化日志目录路径（data/logs/）。

    通过 DICEPP_PROJECT_ROOT 环境变量定位项目根（Docker/打包环境均已设置），
    确保日志文件写入挂载的 volume，容器重建后不丢失。
    """
    project_root = os.getenv("DICEPP_PROJECT_ROOT")
    if project_root:
        return os.path.join(project_root, "data", "logs")
    # 兜底：从当前文件位置向上推算项目根
    # utils/logger.py → utils/ → DicePP/ → plugins/ → src/ → project_root/
    project_root = str(Path(__file__).resolve().parents[4])
    return os.path.join(project_root, "data", "logs")


def _patch_extra(record: dict) -> None:
    """为每条 log record 补全缺失的 extra 字段。

    NoneBot 框架自身 emit 的日志不携带 request_id，直接使用
    {extra[request_id]} 格式化会导致 KeyError。此 patcher 确保
    所有 handler 在格式化前都能拿到安全的默认值。
    """
    record["extra"].setdefault("request_id", "--------")


def _stderr_colorize() -> bool:
    isatty = getattr(sys.stderr, "isatty", None)
    if isatty is None:
        return False
    try:
        return bool(isatty())
    except Exception:
        return False


_STDERR_HANDLER_ID: int | None = None


def _add_stderr_handler(level: str) -> int:
    return logger.add(
        sys.stderr,
        format=_safe_format(LOG_FORMAT),
        level=level.upper(),
        colorize=_stderr_colorize(),
    )


def _add_file_handlers() -> None:
    logs_dir = _get_logs_dir()
    os.makedirs(logs_dir, exist_ok=True)
    # 全级别持久化日志文件（按天轮转，保留 30 天，压缩归档）
    # 写入 data/logs/ 目录（挂载的 volume），容器重建后日志不丢失
    logger.add(
        os.path.join(logs_dir, "dicepp.log"),
        rotation="00:00",
        retention="30 days",
        compression="gz",
        diagnose=False,
        level="DEBUG",
        format=_safe_format(FILE_LOG_FORMAT),
        encoding="utf-8",
        delay=True,
    )

    # error 级别独立日志文件（10MB 轮转，方便快速定位错误）
    logger.add(
        os.path.join(logs_dir, "error.log"),
        rotation="10 MB",
        diagnose=False,
        level="ERROR",
        format=_safe_format(FILE_LOG_FORMAT),
        encoding="utf-8",
        delay=True,
    )


def restore_runtime_logging(
    level: str = "DEBUG",
    *,
    file_logging: bool = True,
) -> None:
    """Restore DicePP handlers after framework logging reconfiguration.

    In the shell scenario, BotRunner calls this repeatedly (activate/restore),
    so _STDERR_HANDLER_ID is rebuilt on every call — callers must not cache
    that value across calls.
    """
    global _STDERR_HANDLER_ID
    configure_redirected_stdio_utf8()
    logger.remove()
    _STDERR_HANDLER_ID = _add_stderr_handler(level)
    if file_logging:
        _add_file_handlers()
    # 配置默认 extra + 全局 patcher，确保所有 handler 都能安全格式化
    logger.configure(extra={"request_id": "--------"}, patcher=_patch_extra)


restore_runtime_logging()


def configure_log_level(level: str) -> None:
    """运行时调整 stderr handler 日志级别（启动后从 config 读取）。"""
    global _STDERR_HANDLER_ID
    try:
        if _STDERR_HANDLER_ID is not None:
            logger.remove(_STDERR_HANDLER_ID)
    except ValueError:
        pass
    configure_redirected_stdio_utf8()
    _STDERR_HANDLER_ID = _add_stderr_handler(level)


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
