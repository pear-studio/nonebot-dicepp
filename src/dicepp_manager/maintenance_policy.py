"""Pure policy predicates shared by durable maintenance coordinators."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def is_terminal_rollback_failure(
    journal: Mapping[str, Any],
    *,
    authoritative_rollback: Mapping[str, Any] | None = None,
) -> bool:
    """Return whether a destructive rollback failure must not be replayed.

    Old journals without a commit point remain retryable.  During a Windows
    hand-off, an authoritative UpdateGuard ``program_rolled_back`` marker also
    keeps Manager-side data recovery retryable because program rollback is no
    longer destructive on the next attempt.
    """
    detail = journal.get("detail")
    commit_point = detail.get("commit_point") if isinstance(detail, Mapping) else None
    authoritative_status = (
        authoritative_rollback.get("status")
        if isinstance(authoritative_rollback, Mapping)
        else None
    )
    return (
        journal.get("status") == "rollback_failed"
        and commit_point not in (None, "not_started")
        and authoritative_status != "program_rolled_back"
    )
