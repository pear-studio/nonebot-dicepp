from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import aiosqlite
import pytest
import pytest_asyncio

from core.command import (
    BotCommandDispatchResult,
    BotSendFileCommand,
    BotSendMsgCommand,
    FileDeliveryOutcome,
    FileDeliveryResult,
)
from core.communication import MessageMetaData, MessageSender, PostSendEvent
from core.data import LogRepository
from core.data.models import LogRecord
from core.data.schema import ensure_bot_log_schema
from core.message_types import MessageType
from module.common.log.command import LogCommand
from module.common.log.command import _run_export
from module.common.log.export_service import ArtifactResult, ExportBatchResult
from module.common.log.publisher import ProviderPublishResult
from module.common.log.runtime import LogRuntime
from module.common.log.types import (
    ExportRequest,
    LogDeliveryStatus,
    LogExportFormat,
    LogExportReason,
    LogExportView,
    LogGenerationStatus,
)


NOW = datetime(2026, 7, 20, 19, 0, 0)


class _LocHelper:
    @staticmethod
    def format_loc_text(key, **kwargs):
        return key.format(**kwargs) if kwargs else key


class _CaptureProxy:
    def __init__(self) -> None:
        self.commands = []

    async def process_bot_command(self, command):
        self.commands.append(command)
        deliveries = ()
        if isinstance(command, BotSendFileCommand):
            deliveries = tuple(
                FileDeliveryResult(
                    target=target,
                    outcome=FileDeliveryOutcome.FOLDER_SUCCESS,
                    requested_folder="跑团log",
                )
                for target in command.targets
            )
        return BotCommandDispatchResult(command, deliveries)


class _FakeProvider:
    name = "fake_web"

    def __init__(self) -> None:
        self.projections = []

    async def publish(self, projection, *, request_id, requested_by):
        self.projections.append(projection)
        return ProviderPublishResult(f"https://logs.test/{request_id}")


class _FakeBot:
    def __init__(self, data_path: Path, provider: _FakeProvider) -> None:
        self.account = "bot-42"
        self.data_path = str(data_path)
        self.proxy = _CaptureProxy()
        self.loc_helper = _LocHelper()
        self.config = SimpleNamespace(
            log=SimpleNamespace(
                web=SimpleNamespace(
                    provider="dice_log_v105",
                    endpoint="",
                    token="",
                    timeout_seconds=15.0,
                )
            )
        )
        self.platform_hooks = []
        self.post_send_hooks = []
        self.recall_hooks = []
        self.log_runtime = None
        self._provider = provider

    @staticmethod
    def _register(collection, hook):
        collection.append(hook)
        return lambda: collection.remove(hook) if hook in collection else None

    def add_platform_message_hook(self, hook):
        return self._register(self.platform_hooks, hook)

    def add_post_send_hook(self, hook):
        return self._register(self.post_send_hooks, hook)

    def add_message_recall_hook(self, hook):
        return self._register(self.recall_hooks, hook)


@pytest_asyncio.fixture
async def command_parts(tmp_path: Path):
    db_path = tmp_path / "log.db"
    ensure_bot_log_schema(db_path)
    db = await aiosqlite.connect(db_path)
    await db.execute("PRAGMA foreign_keys=ON;")
    repository = LogRepository(db)
    provider = _FakeProvider()
    bot = _FakeBot(tmp_path / "bot", provider)
    runtime = LogRuntime(
        bot,
        repository,
        clock=lambda: NOW,
        publication_provider=provider,
    )
    bot.log_runtime = runtime
    command = LogCommand(bot)
    try:
        yield bot, repository, provider, runtime, command
    finally:
        runtime.close()
        await db.close()


async def _send_command(
    command: LogCommand,
    message: str,
    *,
    group_id: str = "g1",
) -> BotSendMsgCommand:
    meta = MessageMetaData(
        message,
        message,
        MessageSender("user-1", "调查员"),
        group_id=group_id,
    )
    should_process, should_pass, hint = command.can_process_msg(message, meta)
    assert should_process is True
    assert should_pass is False
    replies = await command.process_msg(message, meta, hint)
    assert len(replies) == 1
    assert isinstance(replies[0], BotSendMsgCommand)
    return replies[0]


async def _send(command: LogCommand, message: str, *, group_id: str = "g1") -> str:
    return (await _send_command(command, message, group_id=group_id)).msg


async def _add_record(repository, log_id: str, content: str) -> None:
    await repository.add_record(
        LogRecord(
            log_id=log_id,
            time=NOW,
            user_id="user-1",
            nickname="调查员",
            source="user",
            message_type="ambient",
            plain_content=content,
            raw_content=content,
            message_id=f"message-{content}",
        )
    )


@pytest.mark.asyncio
async def test_on_off_delivers_standard_files_and_repeated_off_is_idempotent(
    command_parts,
) -> None:
    bot, repository, _, _, command = command_parts

    assert "已新建日志《团A》并开始记录" in await _send(command, ".log on 团A")
    active = await repository.get_recording_session("g1")
    await _add_record(repository, active.id, "进入古宅")
    stopped_reply = await _send(command, ".log off")

    assert "已停止记录日志《团A》" in stopped_reply
    assert "TXT：已生成并发送到群文件" in stopped_reply
    assert "DOCX：已生成并发送到群文件" in stopped_reply
    file_commands = [c for c in bot.proxy.commands if isinstance(c, BotSendFileCommand)]
    assert len(file_commands) == 2
    assert all(c.display_name.startswith("跑团log/") for c in file_commands)
    assert len(await repository.list_exports(active.id)) == 2

    repeated = await _send(command, ".log off")
    assert "不会重复导出" in repeated
    assert len([c for c in bot.proxy.commands if isinstance(c, BotSendFileCommand)]) == 2
    assert len(await repository.list_exports(active.id)) == 2

    assert "已继续记录日志《团A》" in await _send(command, ".log on")


@pytest.mark.asyncio
async def test_switch_list_and_delete_preserve_lifecycle_truth(command_parts) -> None:
    _, repository, _, _, command = command_parts
    await _send(command, ".log on 团A")
    await _send(command, ".log off")
    await _send(command, ".log on 团B")

    switched = await _send(command, ".log on 团A")
    assert "已切换到日志《团A》" in switched
    assert "旧日志《团B》已停止" in switched
    assert (await repository.get_recording_session("g1")).name == "团A"

    listed = await _send(command, ".log list")
    assert "《团A》 [当前 / 记录中]" in listed
    assert "《团B》 [已停止]" in listed
    assert "消息：0 条" in listed
    assert "最近导出" in listed

    cannot_delete = await _send(command, ".log del 团A")
    assert "正在记录，请先使用 .log off" in cannot_delete
    await _send(command, ".log off")
    deleted = await _send(command, ".log del 团A")
    assert "已删除日志《团A》" in deleted
    assert "外部副本不会自动删除" in deleted


@pytest.mark.asyncio
async def test_explicit_export_html_web_and_link_have_separate_side_effects(
    command_parts,
) -> None:
    bot, repository, provider, _, command = command_parts
    await _send(command, ".log on 团A")
    active = await repository.get_recording_session("g1")
    await _add_record(repository, active.id, "正文")

    txt = await _send(command, ".log export 团A txt")
    assert "TXT：已生成并发送到群文件" in txt
    assert (await repository.get_session(active.id)).recording is True

    html = await _send(command, ".log export 团A html")
    assert "HTML：暂未支持，未生成文件" in html
    assert not list(Path(bot.data_path, "logs").glob("*.html"))

    web = await _send(command, ".log export 团A web all")
    assert "完整视图包含场外发言和日志管理指令" in web
    assert "网页发布成功：https://logs.test/" in web
    assert len(provider.projections) == 1

    bot.config.log.web.provider = "unsupported"
    bot.log_runtime = LogRuntime(bot, repository, clock=lambda: NOW)
    unavailable_command = LogCommand(bot)
    link = await _send(unavailable_command, ".log export 团A link")
    assert "最近发布链接：https://logs.test/" in link
    assert len(provider.projections) == 1


@pytest.mark.asyncio
async def test_log_control_input_and_reply_are_excluded_from_curated_txt(
    command_parts,
) -> None:
    bot, repository, _, runtime, command = command_parts
    await _send(command, ".log on 团A")
    active = await repository.get_recording_session("g1")

    migration_meta = MessageMetaData(
        ".stat log",
        ".stat log",
        MessageSender("user-1", "调查员"),
        group_id="g1",
    )
    migration_meta.message_id = "migration-user"
    await runtime.recorder.record_user_message(migration_meta)
    migration_reply = await _send_command(command, ".stat log")
    assert migration_reply.message_type is MessageType.LOG_CONTROL
    await runtime.recorder.record_bot_message(
        PostSendEvent(
            group_id="g1",
            user_id=bot.account,
            role="assistant",
            message_type=migration_reply.message_type.value,
            content=migration_reply.msg,
            display_name="我",
            platform_message_id="migration-bot",
            history_stream_id=None,
        )
    )
    await _add_record(repository, active.id, "进入古宅")

    await _send(command, ".log export 团A txt")

    records = await repository.get_records(active.id)
    assert [record.message_type for record in records] == [
        "log_control",
        "log_control",
        "ambient",
    ]
    txt_files = list(Path(bot.data_path, "logs").glob("*.txt"))
    assert len(txt_files) == 1
    exported = txt_files[0].read_text(encoding="utf-8")
    assert "进入古宅" in exported
    assert ".stat log" not in exported
    assert "统计功能暂未提供" not in exported


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("setup", "operation"),
    [
        ("none", ".log on 团A"),
        ("active", ".log list"),
        ("active", ".log export 团A txt"),
        ("active", ".log export 团A docx"),
        ("active", ".log export 团A html"),
        ("active", ".log export 团A all"),
        ("active", ".log export 团A link"),
        ("active", ".log off"),
        ("stopped", ".log del 团A"),
    ],
)
async def test_local_log_commands_never_call_web_provider(
    command_parts,
    setup: str,
    operation: str,
) -> None:
    _, _, provider, _, command = command_parts
    if setup in {"active", "stopped"}:
        await _send(command, ".log on 团A")
    if setup == "stopped":
        await _send(command, ".log off")
    provider.projections.clear()

    await _send(command, operation)

    assert provider.projections == []


@pytest.mark.asyncio
async def test_off_export_failure_does_not_rollback_and_repeated_off_does_not_retry(
    command_parts,
    monkeypatch,
) -> None:
    _, repository, _, runtime, command = command_parts
    await _send(command, ".log on 团A")
    calls = 0

    async def _fail_export(_request):
        nonlocal calls
        calls += 1
        raise RuntimeError("disk unavailable")

    monkeypatch.setattr(runtime, "generate_and_deliver", _fail_export)
    stopped = await _send(command, ".log off")
    repeated = await _send(command, ".log off")

    session = (await repository.list_sessions("g1"))[0]
    assert session.recording is False
    assert "导出失败：RuntimeError: disk unavailable" in stopped
    assert "日志状态不受影响" in stopped
    assert "不会重复导出" in repeated
    assert calls == 1


@pytest.mark.asyncio
async def test_switch_export_failure_keeps_new_log_active(
    command_parts,
    monkeypatch,
) -> None:
    _, repository, _, runtime, command = command_parts
    await _send(command, ".log on 团A")
    await _send(command, ".log off")
    await _send(command, ".log on 团B")
    await _send(command, ".log off")
    await _send(command, ".log on 团A")

    async def _fail_export(_request):
        raise RuntimeError("renderer unavailable")

    monkeypatch.setattr(runtime, "generate_and_deliver", _fail_export)
    reply = await _send(command, ".log on 团B")

    sessions = {item.name: item for item in await repository.list_sessions("g1")}
    assert sessions["团A"].recording is False
    assert sessions["团B"].recording is True
    assert (await repository.get_recording_session("g1")).name == "团B"
    assert "已切换到日志《团B》" in reply
    assert "导出失败：RuntimeError: renderer unavailable" in reply
    assert "日志状态不受影响" in reply


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (".log new 团A", ".log on 团A"),
        (".log end", ".log off"),
        (".log halt", ".log off"),
        (".log get 团A", ".log export 团A link"),
        (".log publish 团A", ".log export 团A web"),
        (".log set outside", "始终保存完整数据"),
        (".log stat", "统计功能暂未提供"),
        (".stat log", "统计功能暂未提供"),
    ],
)
async def test_legacy_entries_only_reply_with_migration_guidance(
    command_parts, message, expected
) -> None:
    bot, repository, provider, _, command = command_parts

    reply = await _send(command, message)

    assert expected in reply
    assert await repository.list_sessions("g1") == []
    assert bot.proxy.commands == []
    assert provider.projections == []


def test_private_log_command_is_claimed_for_the_shared_group_only_notice(
    command_parts,
) -> None:
    _, _, _, _, command = command_parts
    meta = MessageMetaData(
        ".log list",
        ".log list",
        MessageSender("user-1", "调查员"),
        group_id="",
    )

    should_process, _, _ = command.can_process_msg(".log list", meta)

    assert should_process is True
    assert command.group_only is True
    assert command.permission_require == 0


def test_registry_orders_log_command_before_broad_query_alias() -> None:
    from core.command.user_cmd import DEFAULT_REGISTRY

    names = [command.__name__ for command in DEFAULT_REGISTRY.get_sorted_commands()]

    assert names.count("LogCommand") == 1
    assert names.index("LogCommand") < names.index("QueryCommand")


@pytest.mark.asyncio
async def test_export_summary_preserves_success_when_audit_update_fails() -> None:
    request = ExportRequest(
        request_id="request-audit",
        reason=LogExportReason.MANUAL,
        log_id="log-audit",
        group_id="g1",
        log_name="团A",
        view=LogExportView.CURATED,
        formats=(LogExportFormat.TXT,),
        record_upper_id=1,
        requested_at=NOW,
        requested_by="user-1",
    )
    batch = ExportBatchResult(
        request,
        (
            ArtifactResult(
                format=LogExportFormat.TXT,
                export_id=1,
                generation_status=LogGenerationStatus.SUCCESS,
                delivery_status=LogDeliveryStatus.SUCCESS,
                audit_error="RuntimeError: delivery audit unavailable",
            ),
        ),
    )
    runtime = SimpleNamespace(generate_and_deliver=lambda _: None)

    async def _generate_and_deliver(_):
        return batch

    runtime.generate_and_deliver = _generate_and_deliver
    summary = await _run_export(runtime, request)

    assert "TXT：已生成并发送到群文件" in summary
    assert "状态记录失败（RuntimeError: delivery audit unavailable）" in summary
    assert "文件无需重传" in summary
