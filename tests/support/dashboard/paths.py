"""Repository path discovery for Dashboard tests."""

from pathlib import Path


def repo_root(start: Path | None = None) -> Path:
    """Find the repository root by stable sentinel files, independent of test depth."""
    candidates = (
        [start.resolve()]
        if start
        else [Path.cwd().resolve(), Path(__file__).resolve()]
    )
    for candidate in candidates:
        for directory in (candidate, *candidate.parents):
            if (directory / "pyproject.toml").is_file() and (
                directory / "dashboard"
            ).is_dir():
                return directory
    raise RuntimeError(f"Could not find DicePP repository root from {candidates!r}")
