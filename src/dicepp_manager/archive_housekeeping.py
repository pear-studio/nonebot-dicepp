"""Public archive housekeeping boundary shared by maintenance workflows."""

from __future__ import annotations

from dicepp_data import InstanceLayout

from .archive import enforce_system_retention
from .store import ManagerOperationStore


class ArchiveHousekeeping:
    def __init__(
        self,
        *,
        layout: InstanceLayout,
        store: ManagerOperationStore,
    ) -> None:
        self.layout = layout
        self.store = store

    def cleanup_inprogress(self) -> None:
        directory = self.layout.manager_backups_dir
        if not directory.exists():
            return
        for path in directory.iterdir():
            if path.is_file() and path.name.endswith((".inprogress", ".importing")):
                try:
                    path.unlink()
                except OSError:
                    continue

    def apply_retention(self) -> list[str]:
        return enforce_system_retention(
            layout=self.layout,
            protected=self.store.protected_archive_names(),
        )
