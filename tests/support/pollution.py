"""Detect writes from tests into the checkout's real config and data trees."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path


@dataclass(frozen=True)
class RepositorySnapshot:
    files: dict[str, str]


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def capture_repository_snapshot(root: Path) -> RepositorySnapshot:
    """Hash every file under config/ and data/ without retaining file contents."""
    files: dict[str, str] = {}
    for directory_name in ("config", "data"):
        directory = root / directory_name
        if not directory.exists():
            continue
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                files[str(path.relative_to(root))] = _hash_file(path)
    return RepositorySnapshot(files=files)


def _describe_changes(
    baseline: RepositorySnapshot,
    current: RepositorySnapshot,
) -> list[str]:
    before = baseline.files
    after = current.files
    changes: list[str] = []
    for label, paths in (
        ("added", sorted(set(after) - set(before))),
        ("removed", sorted(set(before) - set(after))),
        (
            "modified",
            sorted(
                path
                for path in set(before) & set(after)
                if before[path] != after[path]
            ),
        ),
    ):
        if paths:
            changes.append(
                f"repository {label} files:\n"
                + "\n".join(f"  - {path}" for path in paths[:20])
            )
    return changes


def assert_repository_unchanged(
    root: Path,
    baseline: RepositorySnapshot,
) -> None:
    """Raise when a test changed the checkout's real config/ or data/ trees."""
    changes = _describe_changes(baseline, capture_repository_snapshot(root))
    if changes:
        raise AssertionError(
            "Test pollution detected in the real repository.\n"
            "Ordinary tests must write through DICEPP_PROJECT_ROOT into the pytest temp app dir.\n\n"
            + "\n\n".join(changes)
        )
