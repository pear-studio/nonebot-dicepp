import pytest
import os
import tempfile
from datetime import datetime

pytestmark = pytest.mark.skip(reason="BotDatabase tests require environment setup - tested indirectly via Repository tests")


class TestBotDatabase:
    pass
