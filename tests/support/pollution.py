"""Detect writes from tests into the checkout's real config and data trees."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path


_RETIRED_RUNTIME_PATHS = (Path("src/plugins/DicePP/Data"),)


@dataclass(frozen=True)
class RepositorySnapshot:
    files: dict[str, str]


@dataclass(frozen=True)
class RetiredRuntimeState:
    entries: tuple[tuple[str, int, int, int], ...]


def capture_retired_runtime_state(root: Path) -> RetiredRuntimeState | None:
    """Capture cheap metadata for the small retired plugin-local data tree."""
    retired_root = root / _RETIRED_RUNTIME_PATHS[0]
    if not retired_root.exists():
        return None
    entries: list[tuple[str, int, int, int]] = []
    for directory, child_dirs, filenames in os.walk(
        retired_root, followlinks=False
    ):
        directory_path = Path(directory)
        names = [*child_dirs, *filenames]
        for name in names:
            path = directory_path / name
            stat = path.lstat()
            entries.append(
                (
                    str(path.relative_to(retired_root)),
                    stat.st_mode,
                    stat.st_size,
                    stat.st_mtime_ns,
                )
            )
        child_dirs[:] = [
            name for name in child_dirs if not (directory_path / name).is_symlink()
        ]
    return RetiredRuntimeState(entries=tuple(sorted(entries)))


def assert_retired_runtime_unchanged(
    root: Path,
    baseline: RetiredRuntimeState | None,
    *,
    nodeid: str,
) -> None:
    current = capture_retired_runtime_state(root)
    if current != baseline:
        raise AssertionError(
            "Test created or modified retired runtime path "
            f"src/plugins/DicePP/Data: {nodeid}"
        )


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
    for relative_path in _RETIRED_RUNTIME_PATHS:
        if (root / relative_path).exists():
            changes.append(
                "retired runtime path exists in the repository: "
                f"{relative_path}"
            )
    if changes:
        raise AssertionError(
            "Test pollution detected in the real repository.\n"
            "Ordinary tests must write through DICEPP_PROJECT_ROOT into the pytest temp app dir.\n\n"
            + "\n\n".join(changes)
        )
