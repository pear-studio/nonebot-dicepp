"""Read-only archive management for the standalone Manager."""

from __future__ import annotations

from pathlib import Path

from dicepp_data import InstanceLayout

from .archive import (
    ArchiveError,
    delete_archive,
    export_archive_path,
    import_archive,
    list_archives,
    read_archive_detail,
    verify_archive,
)
from .service import ManagerService


class ArchiveCoordinator:
    """Expose archive inventory and transfer operations only.

    Creating or restoring an archive changes the live data instance and is no
    longer a Manager responsibility. Those operations will be reintroduced by
    the Dashboard-owned data module once it can verify that the Bot is stopped.
    """

    def __init__(self, *, layout: InstanceLayout, service: ManagerService) -> None:
        self.layout = layout
        self.service = service
        self.store = service.store

    def list(self) -> list[dict]:
        return list_archives(layout=self.layout)

    def detail(self, filename: str) -> tuple[dict, dict]:
        return read_archive_detail(filename, layout=self.layout)

    def verify(self, filename: str) -> dict:
        return verify_archive(filename, layout=self.layout)

    def delete(self, filename: str) -> dict:
        if filename in self.store.protected_archive_names():
            raise ArchiveError("Archive is protected by an active or failed transaction")
        return delete_archive(filename, layout=self.layout)

    def export_path(self, filename: str) -> Path:
        return export_archive_path(filename, layout=self.layout)

    def import_stream(self, filename: str, source) -> dict:
        return import_archive(filename, source, layout=self.layout)
