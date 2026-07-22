import pytest

from tests.support.xdist import calculate_xdist_worker_count


@pytest.mark.unit
@pytest.mark.parametrize(
    ("available_cpu_count", "expected"),
    [
        (None, 1),
        (1, 1),
        (2, 1),
        (3, 2),
        (4, 3),
        (8, 4),
    ],
)
def test_worker_count_leaves_capacity_and_caps_parallelism(
    available_cpu_count,
    expected,
):
    assert calculate_xdist_worker_count(available_cpu_count) == expected


def test_worker_count_respects_explicit_xdist_override():
    assert calculate_xdist_worker_count(2, "7") == 7
