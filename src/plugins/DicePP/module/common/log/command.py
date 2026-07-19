from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .types import LogExportFormat, LogExportView


class LogCommandAction(str, Enum):
    HELP = "help"
    ON = "on"
    OFF = "off"
    LIST = "list"
    DELETE = "del"
    EXPORT = "export"
    WEB = "web"
    LINK = "link"
    MIGRATION = "migration"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class ParsedLogCommand:
    action: LogCommandAction
    name: str = ""
    view: LogExportView = LogExportView.CURATED
    formats: tuple[LogExportFormat, ...] = ()
    feedback: str | None = None
    legacy_action: str | None = None


_STANDARD_FORMATS = (LogExportFormat.TXT, LogExportFormat.DOCX)
_FORMAT_TOKENS = {
    "txt": LogExportFormat.TXT,
    "docx": LogExportFormat.DOCX,
    "html": LogExportFormat.HTML,
}
_VIEW_TOKENS = {"all", "完整"}
_EXPORT_MODE_TOKENS = {*_FORMAT_TOKENS, "web", "link"}
_RESERVED_EXPORT_TOKENS = _EXPORT_MODE_TOKENS | _VIEW_TOKENS


def parse_log_command(message: str) -> ParsedLogCommand | None:
    """Parse the user-visible Log grammar without reading state or doing I/O."""
    tokens = message.strip().split()
    if not tokens:
        return None

    command = tokens[0].casefold()
    if command == ".stat":
        if len(tokens) >= 2 and tokens[1].casefold() in {"log", "日志"}:
            return _migration(
                "stat",
                "日志统计功能暂未提供；本次未执行旧统计命令。",
            )
        return None
    if command != ".log":
        return None
    if len(tokens) == 1:
        return ParsedLogCommand(LogCommandAction.HELP)

    action = tokens[1].casefold()
    arguments = tokens[2:]
    if action == "on":
        return ParsedLogCommand(LogCommandAction.ON, name=" ".join(arguments))
    if action == "off":
        return _without_arguments(LogCommandAction.OFF, arguments)
    if action == "list":
        return _without_arguments(LogCommandAction.LIST, arguments)
    if action == "del":
        if not arguments:
            return _invalid("请提供日志名称，例如：.log del 团A")
        return ParsedLogCommand(LogCommandAction.DELETE, name=" ".join(arguments))
    if action == "export":
        return _parse_export(arguments)

    migration = _parse_legacy(action, arguments)
    if migration is not None:
        return migration
    return _invalid(f"未知日志操作“{tokens[1]}”，请输入 .log 查看帮助。")


def _parse_export(arguments: list[str]) -> ParsedLogCommand:
    if not arguments:
        return _invalid("请提供日志名称，例如：.log export 团A")

    remaining = list(arguments)
    view = LogExportView.CURATED
    mode: str | None = None

    # A lone reserved word is a valid log name. Options are peeled only when
    # at least one token remains as the name.
    if len(remaining) >= 2 and remaining[-1].casefold() in _VIEW_TOKENS:
        view = LogExportView.COMPLETE
        remaining.pop()
        if len(remaining) >= 2 and remaining[-1].casefold() in _EXPORT_MODE_TOKENS:
            mode = remaining.pop().casefold()
    elif len(remaining) >= 2 and remaining[-1].casefold() in _EXPORT_MODE_TOKENS:
        mode = remaining.pop().casefold()

    if not remaining:
        return _invalid("请提供日志名称，例如：.log export 团A")
    if len(remaining) >= 2 and remaining[-1].casefold() in _RESERVED_EXPORT_TOKENS:
        return _invalid(
            "导出选项顺序无效；请使用 .log export <名称> <类型> [all|完整]。"
        )

    name = " ".join(remaining)
    if mode == "link":
        if view is LogExportView.COMPLETE:
            return _invalid("link 只查询现有链接，不接受 all 或完整选项。")
        return ParsedLogCommand(LogCommandAction.LINK, name=name)
    if mode == "web":
        return ParsedLogCommand(LogCommandAction.WEB, name=name, view=view)
    formats = _STANDARD_FORMATS if mode is None else (_FORMAT_TOKENS[mode],)
    return ParsedLogCommand(
        LogCommandAction.EXPORT,
        name=name,
        view=view,
        formats=formats,
    )


def _without_arguments(
    action: LogCommandAction, arguments: list[str]
) -> ParsedLogCommand:
    if arguments:
        return _invalid(f".log {action.value} 不接受额外参数。")
    return ParsedLogCommand(action)


def _parse_legacy(
    action: str, arguments: list[str]
) -> ParsedLogCommand | None:
    name = " ".join(arguments)
    if action == "new":
        replacement = f".log on {name}" if name else ".log on <名称>"
        return _migration(
            action,
            f".log new 已移除，请使用 {replacement}；本次未创建日志。",
        )
    if action in {"end", "halt", "stop"}:
        return _migration(
            action,
            f".log {action} 已移除，请使用 .log off；本次未停止或导出日志。",
        )
    if action == "get":
        replacement = (
            f".log export {name} link" if name else ".log export <名称> link"
        )
        return _migration(
            action,
            f".log get 已移除，请使用 {replacement}；本次未上传或查询。",
        )
    if action == "publish":
        replacement = (
            f".log export {name} web" if name else ".log export <名称> web"
        )
        return _migration(
            action,
            f".log publish 已移除，请使用 {replacement}；本次未发布。",
        )
    if action == "set":
        return _migration(
            action,
            "新版日志始终保存完整数据；导出时使用默认跑团正文或 all（完整）视图。本次未修改设置。",
        )
    if action == "stat":
        return _migration(
            action,
            "日志统计功能暂未提供；本次未执行旧统计命令。",
        )
    return None


def _migration(legacy_action: str, feedback: str) -> ParsedLogCommand:
    return ParsedLogCommand(
        LogCommandAction.MIGRATION,
        feedback=feedback,
        legacy_action=legacy_action,
    )


def _invalid(feedback: str) -> ParsedLogCommand:
    return ParsedLogCommand(LogCommandAction.INVALID, feedback=feedback)


__all__ = ["LogCommandAction", "ParsedLogCommand", "parse_log_command"]
