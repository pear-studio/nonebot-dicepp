from __future__ import annotations

import pytest

from module.common.log.command import (
    LogCommandAction,
    parse_log_command,
)
from module.common.log.types import LogExportFormat, LogExportView



@pytest.mark.parametrize(
    ("message", "action", "name"),
    [
        (".log", LogCommandAction.HELP, ""),
        (".LOG on", LogCommandAction.ON, ""),
        (".log on  雾都 夜话 ", LogCommandAction.ON, "雾都 夜话"),
        (".log off", LogCommandAction.OFF, ""),
        (".log list", LogCommandAction.LIST, ""),
        (".log del 雾都 夜话", LogCommandAction.DELETE, "雾都 夜话"),
    ],
)
def test_lifecycle_grammar(message: str, action: LogCommandAction, name: str) -> None:
    parsed = parse_log_command(message)

    assert parsed is not None
    assert parsed.action is action
    assert parsed.name == name


@pytest.mark.parametrize(
    ("message", "action", "name", "view", "formats"),
    [
        (
            ".log export 团A",
            LogCommandAction.EXPORT,
            "团A",
            LogExportView.CURATED,
            (LogExportFormat.TXT, LogExportFormat.DOCX),
        ),
        (
            ".log export 雾都 夜话 txt",
            LogCommandAction.EXPORT,
            "雾都 夜话",
            LogExportView.CURATED,
            (LogExportFormat.TXT,),
        ),
        (
            ".log export 团A DOCX 完整",
            LogCommandAction.EXPORT,
            "团A",
            LogExportView.COMPLETE,
            (LogExportFormat.DOCX,),
        ),
        (
            ".log export 团A html all",
            LogCommandAction.EXPORT,
            "团A",
            LogExportView.COMPLETE,
            (LogExportFormat.HTML,),
        ),
        (
            ".log export 团A all",
            LogCommandAction.EXPORT,
            "团A",
            LogExportView.COMPLETE,
            (LogExportFormat.TXT, LogExportFormat.DOCX),
        ),
        (
            ".log export 团A web",
            LogCommandAction.WEB,
            "团A",
            LogExportView.CURATED,
            (),
        ),
        (
            ".log export 雾都 夜话 web 完整",
            LogCommandAction.WEB,
            "雾都 夜话",
            LogExportView.COMPLETE,
            (),
        ),
        (
            ".log export 团A link",
            LogCommandAction.LINK,
            "团A",
            LogExportView.CURATED,
            (),
        ),
    ],
)
def test_export_grammar(
    message: str,
    action: LogCommandAction,
    name: str,
    view: LogExportView,
    formats: tuple[LogExportFormat, ...],
) -> None:
    parsed = parse_log_command(message)

    assert parsed is not None
    assert parsed.action is action
    assert parsed.name == name
    assert parsed.view is view
    assert parsed.formats == formats


def test_lone_reserved_export_word_is_a_name_not_an_option() -> None:
    parsed = parse_log_command(".log export all")

    assert parsed is not None
    assert parsed.action is LogCommandAction.EXPORT
    assert parsed.name == "all"
    assert parsed.view is LogExportView.CURATED
    assert parsed.formats == (LogExportFormat.TXT, LogExportFormat.DOCX)


@pytest.mark.parametrize(
    "message",
    [
        ".log off now",
        ".log list 团A",
        ".log del",
        ".log export",
        ".log export 团A all txt",
        ".log export 团A txt docx",
        ".log export 团A link all",
        ".log export 团A link 完整",
    ],
)
def test_invalid_or_ambiguous_grammar_is_rejected(message: str) -> None:
    parsed = parse_log_command(message)

    assert parsed is not None
    assert parsed.action is LogCommandAction.INVALID
    assert parsed.feedback


@pytest.mark.parametrize(
    ("message", "legacy_action", "replacement", "non_action"),
    [
        (".log new 团A", "new", ".log on 团A", "未创建"),
        (".log end", "end", ".log off", "未停止或导出"),
        (".log halt", "halt", ".log off", "未停止或导出"),
        (".log get 团A", "get", ".log export 团A link", "未上传或查询"),
        (".log publish 团A", "publish", ".log export 团A web", "未发布"),
        (".log set outside", "set", "all", "未修改设置"),
        (".log stat 团A", "stat", "暂未提供", "未执行"),
        (".stat log 团A", "stat", "暂未提供", "未执行"),
        (".stat 日志", "stat", "暂未提供", "未执行"),
    ],
)
def test_legacy_commands_only_return_migration_feedback(
    message: str,
    legacy_action: str,
    replacement: str,
    non_action: str,
) -> None:
    parsed = parse_log_command(message)

    assert parsed is not None
    assert parsed.action is LogCommandAction.MIGRATION
    assert parsed.legacy_action == legacy_action
    assert replacement in (parsed.feedback or "")
    assert non_action in (parsed.feedback or "")
    assert parsed.name == ""
    assert parsed.formats == ()


@pytest.mark.parametrize("message", ["", "hello", ".logger on 团A", ".stat hp"])
def test_unrelated_messages_are_not_claimed(message: str) -> None:
    assert parse_log_command(message) is None
