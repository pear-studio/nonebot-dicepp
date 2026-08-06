"""Pure policy predicates shared by durable maintenance coordinators."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def is_terminal_rollback_failure(
    journal: Mapping[str, Any],
) -> bool:
    """Return whether a destructive rollback failure must not be replayed.

    Old journals without a commit point remain retryable.
    """
    detail = journal.get("detail")
    commit_point = detail.get("commit_point") if isinstance(detail, Mapping) else None
    return (
        journal.get("status") == "rollback_failed"
        and commit_point not in (None, "not_started")
    )
