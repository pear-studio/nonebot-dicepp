from __future__ import annotations

from pathlib import Path

from ..projection import LogProjection, ProjectedReply
from ..types import LogExportFormat
from .base import AtomicFileExporter


class TextLogExporter(AtomicFileExporter):
    format = LogExportFormat.TXT
    suffix = ".txt"

    def _write_sync(self, projection: LogProjection, path: Path) -> None:
        path.write_text(render_text(projection), encoding="utf-8", newline="\n")


def render_text(projection: LogProjection) -> str:
    lines = [
        f"日志：{projection.log_name}",
        f"日志 ID：{projection.log_id}",
        f"群 ID：{projection.group_id}",
        f"创建时间：{projection.created_at:%Y-%m-%d %H:%M:%S}",
        f"导出视图：{projection.view.value}",
        f"记录快照：{projection.record_upper_id}",
        "",
    ]
    for message in projection.messages:
        lines.append(
            f"{message.nickname} ({message.user_id})  "
            f"{message.time:%Y-%m-%d %H:%M:%S}"
        )
        if message.reply is not None:
            lines.extend(_render_reply(message.reply))
        lines.append(message.readable_text)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_reply(reply: ProjectedReply) -> list[str]:
    if reply.author is None:
        return [f"> [回复消息：{reply.message_id}]"]
    return [
        f"> {reply.author}（消息 {reply.message_id}）",
        *(f"> {line}" for line in reply.excerpt),
    ]
