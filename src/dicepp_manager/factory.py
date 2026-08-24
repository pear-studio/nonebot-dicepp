"""Composition root for the standalone Manager process."""

from __future__ import annotations

from pathlib import Path

from dicepp_control.control_token import ensure_token

from .config import ManagerSettings
from .control import ControlChannelService
from .owner import ManagerOwnerLock
from .service import ManagerService
from .store import ManagerOperationStore


def create_manager_service(settings: ManagerSettings) -> ManagerService:
    for directory in (
        settings.layout.manager_state_dir,
        settings.layout.manager_control_dir,
        settings.layout.manager_backups_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    owner = ManagerOwnerLock(settings.layout.manager_state_dir)
    owner.acquire()
    try:
        store = ManagerOperationStore(settings.layout.manager_db)
        store.recover_incomplete_operations()
        service = ManagerService(
            store=store,
            state_dir=settings.layout.manager_state_dir,
            owner_lock=owner,
        )
        ensure_token(settings.layout.root)
        service.control_service = ControlChannelService(
            project_root=settings.layout.root,
            known_bot_ids=lambda: _configured_bot_ids(settings.layout),
            heartbeat_timeout=settings.control_heartbeat_timeout,
            reload_timeout=settings.control_reload_timeout,
        )
        return service
    except BaseException:
        owner.release()
        raise


def _configured_bot_ids(layout) -> set[str]:
    directory = layout.config_bots_dir
    if not directory.is_dir():
        return set()
    return {
        path.stem
        for path in directory.glob("*.json")
        if path.is_file() and path.stem != "_template"
    }


__all__ = ["create_manager_service"]
