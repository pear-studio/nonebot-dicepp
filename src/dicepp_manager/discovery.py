"""Discover logical accounts and map them to real RuntimeUnits."""

from __future__ import annotations

import json
from pathlib import Path

from dicepp_data import InstanceLayout

from .models import RuntimeUnit, validate_runtime_unit_id


class RuntimeUnitDiscovery:
    def __init__(self, layout: InstanceLayout, *, runtime_unit_id: str, adapter: str) -> None:
        self._layout = layout
        self._runtime_unit_id = validate_runtime_unit_id(runtime_unit_id)
        self._adapter = adapter

    def list_units(self) -> list[RuntimeUnit]:
        return [
            RuntimeUnit(
                runtime_unit_id=self._runtime_unit_id,
                bot_ids=tuple(self._bot_ids()),
                shared_process=True,
                adapter=self._adapter,
            )
        ]

    def _bot_ids(self) -> list[str]:
        directory = self._layout.config_bots_dir
        if not directory.is_dir():
            return []
        result: list[str] = []
        for path in sorted(directory.glob("*.json")):
            if path.name.startswith("_") or not _enabled_config(path):
                continue
            result.append(path.stem)
        return result


def _enabled_config(path: Path) -> bool:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return True
    return not isinstance(value, dict) or value.get("enabled", True) is not False
