from unittest.mock import MagicMock

import pytest

from tests.support.dashboard.playwright import launch_browser


def test_launch_browser_requires_the_playwright_managed_chromium() -> None:
    chromium = MagicMock()
    chromium.launch.side_effect = RuntimeError("managed Chromium is missing")

    with pytest.raises(RuntimeError, match="managed Chromium is missing"):
        launch_browser(chromium)

    chromium.launch.assert_called_once_with(
        headless=True,
        args=["--no-sandbox", "--disable-setuid-sandbox"],
    )
