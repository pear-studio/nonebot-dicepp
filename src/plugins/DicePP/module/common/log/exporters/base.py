from __future__ import annotations

import asyncio
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Protocol

from ..projection import LogProjection
from ..types import LogExportFormat


_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
_INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass(frozen=True, slots=True)
class ExportTarget:
    request_id: str
    format: LogExportFormat
    final_path: Path
    group_file_name: str
    db_local_path: str


@dataclass(frozen=True, slots=True)
class GeneratedArtifact:
    format: LogExportFormat
    path: Path
    group_file_name: str
    db_local_path: str
    size: int


class LogExporter(Protocol):
    format: ClassVar[LogExportFormat]
    suffix: ClassVar[str]

    async def generate(
        self, projection: LogProjection, target: ExportTarget
    ) -> GeneratedArtifact: ...


class AtomicFileExporter:
    format: ClassVar[LogExportFormat]
    suffix: ClassVar[str]

    async def generate(
        self, projection: LogProjection, target: ExportTarget
    ) -> GeneratedArtifact:
        if target.format is not self.format:
            raise ValueError(
                f"Exporter {self.format.value} cannot generate {target.format.value}"
            )
        await asyncio.to_thread(self._generate_sync, projection, target)
        return GeneratedArtifact(
            format=self.format,
            path=target.final_path,
            group_file_name=target.group_file_name,
            db_local_path=target.db_local_path,
            size=target.final_path.stat().st_size,
        )

    def _generate_sync(self, projection: LogProjection, target: ExportTarget) -> None:
        temp_path: Path | None = None
        try:
            descriptor, raw_path = tempfile.mkstemp(
                dir=target.final_path.parent,
                prefix=f".{target.final_path.name}.",
                suffix=".tmp",
            )
            os.close(descriptor)
            temp_path = Path(raw_path)
            self._write_sync(projection, temp_path)
            os.replace(temp_path, target.final_path)
            temp_path = None
        except BaseException:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
            target.final_path.unlink(missing_ok=True)
            raise

    def _write_sync(self, projection: LogProjection, path: Path) -> None:
        raise NotImplementedError


def build_filename_base(
    *, log_name: str, group_id: str, log_id: str, request_id: str, timestamp: str
) -> str:
    return "_".join(
        (
            sanitize_filename_component(log_name, maximum=80),
            f"群{sanitize_filename_component(group_id, maximum=40)}",
            sanitize_filename_component(log_id, maximum=8),
            sanitize_filename_component(request_id, maximum=8),
            sanitize_filename_component(timestamp, maximum=32),
        )
    )


def sanitize_filename_component(value: str, *, maximum: int = 80) -> str:
    cleaned = _INVALID_FILENAME.sub("_", value).strip().rstrip(". ")
    cleaned = re.sub(r"_+", "_", cleaned)
    if not cleaned:
        cleaned = "log"
    if cleaned.split(".", 1)[0].upper() in _WINDOWS_RESERVED:
        cleaned = f"_{cleaned}"
    cleaned = cleaned[:maximum].rstrip(". ")
    return cleaned or "log"


def reserve_export_target(
    *,
    output_root: Path,
    bot_data_root: Path,
    filename_base: str,
    request_id: str,
    format: LogExportFormat,
    suffix: str,
) -> ExportTarget:
    root = output_root.resolve()
    data_root = bot_data_root.resolve()
    _relative_to(root, data_root)
    root.mkdir(parents=True, exist_ok=True)

    for counter in range(1, 10_000):
        collision_suffix = "" if counter == 1 else f"_{counter}"
        filename = f"{filename_base}{collision_suffix}{suffix}"
        final_path = (root / filename).resolve()
        _relative_to(final_path, root)
        try:
            descriptor = os.open(
                final_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError:
            continue
        os.close(descriptor)
        return ExportTarget(
            request_id=request_id,
            format=format,
            final_path=final_path,
            group_file_name=filename,
            db_local_path=_relative_to(final_path, data_root).as_posix(),
        )
    raise FileExistsError("Could not reserve a unique log export filename")


def remove_owned_artifact(target: ExportTarget) -> None:
    target.final_path.unlink(missing_ok=True)


def _relative_to(path: Path, root: Path) -> Path:
    try:
        return path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Export path escapes its configured root: {path}") from exc
