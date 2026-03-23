from __future__ import annotations

from .base import Migration, MigrationContext
from .operations import run_temp_replay_check
from .registry import MigrationRegistry, MigrationRegistryError, build_registry
from .runner import MigrationExecutionError, MigrationRunResult, MigrationRunner
from .v1_baseline import BaselineMigrationV1


def default_registry() -> MigrationRegistry:
    return build_registry(
        [
            BaselineMigrationV1(),
        ]
    )


__all__ = [
    "Migration",
    "MigrationContext",
    "MigrationRegistry",
    "MigrationRegistryError",
    "MigrationExecutionError",
    "MigrationRunResult",
    "MigrationRunner",
    "default_registry",
    "run_temp_replay_check",
]
