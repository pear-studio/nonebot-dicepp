"""Persona unit-test fixture registration."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def reset_clock_after_test():
    yield
    from plugins.DicePP.utils.time import WallClock, set_clock

    set_clock(WallClock())
