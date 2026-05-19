import pytest
from pathlib import Path


def pytest_collection_modifyitems(items):
    """Apply integration marker to all tests under tests/integration/."""
    integration_dir = Path(__file__).parent.resolve()
    for item in items:
        item_path = getattr(item, "fspath", None)
        if item_path is not None:
            try:
                Path(item_path).resolve().relative_to(integration_dir)
                item.add_marker(pytest.mark.integration)
            except ValueError:
                pass
