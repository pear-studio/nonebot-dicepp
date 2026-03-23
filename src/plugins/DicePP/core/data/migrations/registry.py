from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List

from .base import Migration


class MigrationRegistryError(ValueError):
    pass


@dataclass(slots=True)
class MigrationRegistry:
    _migrations: List[Migration]

    def __init__(self) -> None:
        self._migrations = []

    def register(self, migration: Migration) -> None:
        self._migrations.append(migration)

    def validate(self) -> None:
        versions = [m.version for m in self._migrations]
        if not versions:
            raise MigrationRegistryError("Migration registry is empty.")

        if any(version <= 0 for version in versions):
            raise MigrationRegistryError("Migration version must be greater than 0.")

        seen = set()
        duplicates = []
        for version in versions:
            if version in seen:
                duplicates.append(version)
            seen.add(version)
        if duplicates:
            duplicate_text = ", ".join(str(version) for version in sorted(set(duplicates)))
            raise MigrationRegistryError(f"Duplicate migration versions: {duplicate_text}")

        sorted_versions = sorted(versions)
        if versions != sorted_versions:
            raise MigrationRegistryError(
                f"Migration versions must be strictly increasing. Current order: {versions}"
            )

    def all(self) -> List[Migration]:
        self.validate()
        return list(self._migrations)

    def latest_version(self) -> int:
        self.validate()
        return self._migrations[-1].version

    def pending_after(self, current_version: int) -> List[Migration]:
        self.validate()
        return [migration for migration in self._migrations if migration.version > current_version]


def build_registry(migrations: Iterable[Migration]) -> MigrationRegistry:
    registry = MigrationRegistry()
    for migration in migrations:
        registry.register(migration)
    registry.validate()
    return registry
