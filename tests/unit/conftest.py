import pytest
from pathlib import Path


def pytest_collection_modifyitems(items):
    """Apply unit marker to all tests under tests/unit/."""
    unit_dir = Path(__file__).parent.resolve()
    for item in items:
        item_path = getattr(item, "fspath", None)
        if item_path is not None:
            try:
                Path(item_path).resolve().relative_to(unit_dir)
                item.add_marker(pytest.mark.unit)
            except ValueError:
                pass
