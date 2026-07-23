from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from plugins.DicePP.core.command import (
    BotCommandBase,
    BotSendMsgCommand,
    UserCommandBase,
    custom_user_command,
)
from plugins.DicePP.core.command.const import (
    DPP_COMMAND_CLUSTER_DEFAULT,
    DPP_COMMAND_FLAG_DEFAULT,
)
from plugins.DicePP.core.communication import GroupMessagePort, MessageMetaData
from plugins.DicePP.core.message_types import MessageType

from .errors import LogDomainError, LogErrorCode
from .publisher import PublicationStatus
from .runtime import LogPublicationUnavailableError
from .types import (
    LogDeliveryStatus,
    LogExportFormat,
    LogExportView,
    LogGenerationStatus,
    LogOffAction,
    LogOnAction,
)


DC_LOG_SESSION = "log_session"

LOG_USAGE = """日志指令：
.log on [名称]              开始、继续或切换日志
.log off                    停止当前日志并导出
.log list                   查看本群日志
.log export <名称> [选项]   重新导出日志
.log del <名称>             删除日志

导出选项：
默认：跑团正文 TXT + DOCX
txt / docx：只导出指定格式
html：暂未支持的单文件 HTML
all / 完整：导出全部记录
web [all]：显式发布网页
link：查看最近一次成功发布链接"""


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


@custom_user_command(
    readable_name="跑团日志指令",
    # Must run before broad prefix commands such as QueryCommand's `.s` alias,
    # so the exact legacy `.stat log` migration entry cannot be swallowed.
    priority=1,
    flag=DPP_COMMAND_FLAG_DEFAULT,
    cluster=DPP_COMMAND_CLUSTER_DEFAULT,
    group_only=True,
    permission_require=0,
)
class LogCommand(UserCommandBase):
    def can_process_msg(
        self,
        msg_str: str,
        meta: MessageMetaData,
    ) -> tuple[bool, bool, Any]:
        parsed = parse_log_command(msg_str)
        return (parsed is not None, False, parsed)

    async def process_msg(
        self,
        msg_str: str,
        meta: MessageMetaData,
        hint: Any,
    ) -> list[BotCommandBase]:
        parsed = hint if isinstance(hint, ParsedLogCommand) else parse_log_command(msg_str)
        if parsed is None or not meta.group_id:
            return []
        if parsed.action is LogCommandAction.HELP:
            return self._reply(meta.group_id, LOG_USAGE)
        if parsed.action in {LogCommandAction.INVALID, LogCommandAction.MIGRATION}:
            return self._reply(meta.group_id, parsed.feedback or LOG_USAGE)

        runtime = getattr(self.bot, "log_runtime", None)
        if runtime is None:
            return self._reply(meta.group_id, "日志服务尚未初始化，请稍后重试。")

        try:
            feedback = await self._dispatch(parsed, meta, runtime)
        except LogDomainError as exc:
            feedback = _format_domain_error(exc)
        except LogPublicationUnavailableError as exc:
            feedback = f"网页日志功能不可用：{exc}。本次未访问网络。"
        return self._reply(meta.group_id, feedback)

    async def _dispatch(self, parsed, meta, runtime) -> str:
        group_id = meta.group_id
        requested_by = str(meta.user_id or "")
        if parsed.action is LogCommandAction.ON:
            result = await runtime.service.turn_on(
                group_id,
                parsed.name or None,
                requested_by=requested_by,
            )
            if result.action is LogOnAction.SWITCHED:
                assert result.previous_session is not None
                feedback = (
                    f"已切换到日志《{result.session.name}》并开始记录；"
                    f"旧日志《{result.previous_session.name}》已停止。"
                )
            else:
                messages = {
                    LogOnAction.CREATED: f"已新建日志《{result.session.name}》并开始记录。",
                    LogOnAction.RESUMED: f"已继续记录日志《{result.session.name}》。",
                    LogOnAction.ALREADY_RECORDING: f"日志《{result.session.name}》正在记录中。",
                }
                feedback = messages[result.action]
            if result.export_request is not None:
                feedback += "\n" + await _run_export(runtime, result.export_request)
            return feedback

        if parsed.action is LogCommandAction.OFF:
            result = await runtime.service.turn_off(
                group_id,
                requested_by=requested_by,
            )
            if result.action is LogOffAction.ALREADY_OFF:
                return f"日志《{result.session.name}》当前未在记录，不会重复导出。"
            feedback = f"已停止记录日志《{result.session.name}》。"
            if result.export_request is not None:
                feedback += "\n" + await _run_export(runtime, result.export_request)
            return feedback

        if parsed.action is LogCommandAction.LIST:
            return _format_log_list(await runtime.service.list_logs(group_id))

        if parsed.action is LogCommandAction.DELETE:
            result = await runtime.service.delete_log(group_id, parsed.name)
            lines = [f"已删除日志《{result.session.name}》。"]
            if result.current_cleared:
                lines.append("当前日志选择已清空。")
            if result.had_export_history or result.had_publication_history:
                lines.append("群文件或网页等外部副本不会自动删除。")
            return "\n".join(lines)

        request = await runtime.service.prepare_export(
            group_id,
            parsed.name,
            requested_by=requested_by,
            view=parsed.view,
            formats=parsed.formats,
        )
        if parsed.action is LogCommandAction.EXPORT:
            return await _run_export(runtime, request)
        if parsed.action is LogCommandAction.WEB:
            privacy = (
                "提醒：完整视图包含场外发言和日志管理指令。\n"
                if parsed.view is LogExportView.COMPLETE
                else ""
            )
            publication = await runtime.publish(request)
            if publication.status is PublicationStatus.SUCCESS:
                feedback = f"网页发布成功：{publication.url}"
                if publication.audit_error:
                    feedback += f"\n发布成功，但状态记录失败：{publication.audit_error}"
                return privacy + feedback
            return privacy + f"网页发布失败：{publication.error or '未知错误'}。本地日志不受影响。"
        if parsed.action is LogCommandAction.LINK:
            publication = await runtime.latest_link(request.log_id)
            if publication is None:
                return f"日志《{request.log_name}》还没有成功发布的网页链接。"
            return f"日志《{request.log_name}》最近发布链接：{publication.url}"
        return LOG_USAGE

    def _reply(self, group_id: str, feedback: str) -> list[BotCommandBase]:
        command = BotSendMsgCommand(
            self.bot.account,
            feedback,
            [GroupMessagePort(group_id)],
        )
        command.message_type = MessageType.LOG_CONTROL
        return [command]

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        return LOG_USAGE if keyword.casefold() in {"log", "日志"} else ""

    def get_description(self) -> str:
        return ".log 日志管理"


async def _run_export(runtime, request) -> str:
    try:
        batch = await runtime.generate_and_deliver(request)
    except Exception as exc:
        return f"导出失败：{type(exc).__name__}: {exc}。日志状态不受影响。"
    lines = ["导出结果："]
    for result in batch.artifacts:
        label = result.format.value.upper()
        if result.generation_status is LogGenerationStatus.FAILED:
            if result.format is LogExportFormat.HTML:
                lines.append("- HTML：暂未支持，未生成文件。")
            else:
                lines.append(f"- {label}：生成失败（{result.generation_error or '未知错误'}）。")
        elif result.delivery_status is LogDeliveryStatus.SUCCESS:
            if result.audit_error:
                lines.append(
                    f"- {label}：已生成并发送到群文件，但状态记录失败"
                    f"（{result.audit_error}）；文件无需重传。"
                )
            else:
                lines.append(f"- {label}：已生成并发送到群文件。")
        elif result.delivery_status is LogDeliveryStatus.FAILED:
            lines.append(
                f"- {label}：已生成，但群文件发送失败；本地文件已保留，可重新导出。"
            )
        else:
            lines.append(f"- {label}：已生成，但当前尚未发送到群文件。")
    return "\n".join(lines)


def _format_log_list(items) -> str:
    if not items:
        return "本群还没有日志，请使用 .log on <名称> 创建。"
    lines = ["本群日志："]
    for item in items:
        state = []
        if item.is_current:
            state.append("当前")
        state.append("记录中" if item.recording else "已停止")
        lines.append(f"- 《{item.name}》 [{' / '.join(state)}]")
        lines.append(
            f"  创建：{_format_time(item.created_at)}；最后消息：{_format_time(item.last_message_at)}；"
            f"消息：{item.record_count} 条；最近导出：{_format_time(item.last_export_at)}"
        )
    return "\n".join(lines)


def _format_time(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value is not None else "无"


def _format_domain_error(exc: LogDomainError) -> str:
    context = exc.context
    messages = {
        LogErrorCode.CURRENT_LOG_REQUIRED: "当前没有日志，请使用 .log on <名称> 创建。",
        LogErrorCode.LOG_NOT_FOUND: f"未找到日志《{context.get('name', '')}》。",
        LogErrorCode.ACTIVE_LOG_NAME_UNKNOWN: (
            f"日志《{context.get('active_name', '')}》正在记录，未找到《{context.get('name', '')}》；"
            "为避免误切换，本次未创建新日志。请先使用 .log off。"
        ),
        LogErrorCode.LOG_IS_RECORDING: f"日志《{context.get('name', '')}》正在记录，请先使用 .log off。",
        LogErrorCode.INVALID_NAME: "日志名称不能为空。",
    }
    return messages.get(exc.code, "日志操作失败，请稍后重试。")


__all__ = [
    "DC_LOG_SESSION",
    "LOG_USAGE",
    "LogCommand",
    "LogCommandAction",
    "ParsedLogCommand",
    "parse_log_command",
]
