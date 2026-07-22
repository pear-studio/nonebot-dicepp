from __future__ import annotations

import os


MAX_XDIST_WORKERS = 4


def calculate_xdist_worker_count(
    available_cpu_count: int | None,
    explicit_override: str | None = None,
) -> int:
    """Choose bounded pytest parallelism, leaving one CPU available to the OS."""
    if explicit_override:
        try:
            return int(explicit_override)
        except ValueError:
            pass

    cpu_count = max(available_cpu_count or 1, 1)
    return min(MAX_XDIST_WORKERS, max(cpu_count - 1, 1))


def detect_available_cpu_count() -> int | None:
    """Return CPUs available to this process when the runtime exposes that value."""
    process_cpu_count = getattr(os, "process_cpu_count", None)
    if process_cpu_count is not None:
        count = process_cpu_count()
        if count is not None:
            return count

    sched_getaffinity = getattr(os, "sched_getaffinity", None)
    if sched_getaffinity is not None:
        try:
            return len(sched_getaffinity(0))
        except OSError:
            pass

    return os.cpu_count()
