"""Platform-neutral paths for one DicePP instance."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True, slots=True)
class InstanceLayout:
    """Resolve every mutable path belonging to one DicePP instance.

    ``data_root`` remains separately overridable for compatibility with the
    existing Dashboard ``DICEPP_DATA_DIR`` deployment option. New deployments
    should normally keep it at ``root / "data"``.
    """

    root: Path
    data_root: Path

    @classmethod
    def from_root(
        cls,
        root: str | os.PathLike[str],
        *,
        data_root: str | os.PathLike[str] | None = None,
    ) -> "InstanceLayout":
        resolved_root = Path(root).expanduser().resolve()
        resolved_data = (
            Path(data_root).expanduser().resolve()
            if data_root is not None
            else resolved_root / "data"
        )
        return cls(root=resolved_root, data_root=resolved_data)

    @classmethod
    def from_env(
        cls,
        default_root: str | os.PathLike[str],
        *,
        environ: Mapping[str, str] | None = None,
    ) -> "InstanceLayout":
        env = os.environ if environ is None else environ
        root = env.get("DICEPP_PROJECT_ROOT", str(default_root))
        return cls.from_root(root, data_root=env.get("DICEPP_DATA_DIR"))

    @classmethod
    def from_legacy_paths(cls, paths: object) -> "InstanceLayout":
        """Adapt the existing class-style Paths facades during migration."""
        root = Path(getattr(paths, "PROJECT_ROOT"))
        data_root = getattr(paths, "DATA_ROOT", None)
        if data_root is None:
            data_root = getattr(paths, "DATA_DIR", None)
        if data_root is None:
            data_root = Path(getattr(paths, "DATA_BOTS_DIR")).parent
        return cls.from_root(root, data_root=data_root)

    @property
    def config_dir(self) -> Path:
        return self.root / "config"

    @property
    def config_global(self) -> Path:
        return self.config_dir / "global.json"

    @property
    def config_user(self) -> Path:
        return self.config_dir / "user.json"

    @property
    def config_bots_dir(self) -> Path:
        return self.config_dir / "bots"

    @property
    def data_bots_dir(self) -> Path:
        return self.data_root / "bots"

    @property
    def local_images_dir(self) -> Path:
        return self.data_root / "local_images"

    @property
    def content_dir(self) -> Path:
        return self.root / "content"

    @property
    def content_characters_dir(self) -> Path:
        return self.content_dir / "characters"

    @property
    def content_queries_dir(self) -> Path:
        return self.content_dir / "queries"

    @property
    def content_decks_dir(self) -> Path:
        return self.content_dir / "decks"

    @property
    def content_random_dir(self) -> Path:
        return self.content_dir / "random"

    @property
    def content_excel_dir(self) -> Path:
        return self.content_dir / "excel"

    @property
    def dashboard_data_dir(self) -> Path:
        return self.root / "dashboard" / "data"

    @property
    def dashboard_db(self) -> Path:
        return self.dashboard_data_dir / "dashboard.db"

    @property
    def logs_dir(self) -> Path:
        return self.data_root / "logs"

    @property
    def runtime_log(self) -> Path:
        return self.logs_dir / "dicepp-runtime.log"

    @property
    def backups_dir(self) -> Path:
        return self.data_root / "backups"

    @property
    def manager_dir(self) -> Path:
        """Return the Manager-owned instance boundary.

        Manager state is deliberately outside ``dashboard/data`` so the
        lifecycle authority can survive Dashboard replacement or failure.
        """
        return self.root / "manager"

    @property
    def manager_state_dir(self) -> Path:
        return self.manager_dir / "state"

    @property
    def manager_db(self) -> Path:
        return self.manager_state_dir / "manager.db"

    @property
    def manager_token(self) -> Path:
        return self.manager_state_dir / "api-token"

    @property
    def manager_control_dir(self) -> Path:
        """Return the Bot↔Manager credential boundary.

        Dashboard deliberately does not mount this directory.  It is kept
        separate from ``manager/state`` because Dashboard needs the latter's
        HTTP API token but must never be able to read Bot control credentials.
        """
        return self.manager_dir / "control"

    @property
    def manager_control_token(self) -> Path:
        return self.manager_control_dir / "control-token"

    @property
    def manager_packages_dir(self) -> Path:
        return self.manager_dir / "packages"

    @property
    def manager_backups_dir(self) -> Path:
        return self.manager_dir / "backups"

    @property
    def manager_recovery_dir(self) -> Path:
        """Return short-lived Windows program recovery material storage."""
        return self.manager_dir / "recovery"

    def area_root(self, area: str) -> Path:
        roots = {
            "config": self.config_dir,
            "data": self.data_root,
            "content": self.content_dir,
        }
        try:
            return roots[area]
        except KeyError as exc:
            raise ValueError(f"Unsupported instance data area: {area!r}") from exc

    def bot_data_dir(self, bot_id: str) -> Path:
        return self.data_bots_dir / _safe_segment(bot_id, "bot_id")

    def bot_config_path(self, bot_id: str) -> Path:
        return self.config_bots_dir / f"{_safe_segment(bot_id, 'bot_id')}.json"


def _safe_segment(value: str, label: str) -> str:
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError(f"{label} must be one path segment")
    return value
