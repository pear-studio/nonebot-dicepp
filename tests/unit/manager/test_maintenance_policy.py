import pytest

from dicepp_manager.maintenance_policy import is_terminal_rollback_failure


@pytest.mark.parametrize(
    ("journal", "expected"),
    [
        ({"status": "running", "detail": {"commit_point": "program_switch_started"}}, False),
        ({"status": "rollback_failed", "detail": {}}, False),
        ({"status": "rollback_failed", "detail": {"commit_point": "not_started"}}, False),
        (
            {
                "status": "rollback_failed",
                "detail": {"commit_point": "program_switch_started"},
            },
            True,
        ),
    ],
)
def test_terminal_rollback_requires_failed_destructive_rollback(
    journal: dict,
    expected: bool,
) -> None:
    assert is_terminal_rollback_failure(journal) is expected


def test_authoritative_program_rollback_keeps_manager_recovery_retryable() -> None:
    journal = {
        "status": "rollback_failed",
        "detail": {"commit_point": "program_switch_started"},
    }

    assert not is_terminal_rollback_failure(
        journal,
        authoritative_rollback={"status": "program_rolled_back"},
    )
