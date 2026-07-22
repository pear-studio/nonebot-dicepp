"""Fixture registration for PersonaCommand unit tests."""

from __future__ import annotations

import pytest

from tests.support.persona_command import (
    default_persona_config,
    get_sent_content,
    make_cmd,
    make_group_meta,
    make_mock_bot,
    make_private_meta,
)


@pytest.fixture(autouse=True)
def _inject_persona_helpers(request):
    instance = getattr(request, "instance", None)
    if instance is None:
        return
    instance.make_group_meta = make_group_meta
    instance.make_private_meta = make_private_meta
    instance.default_persona_config = default_persona_config
    instance.make_mock_bot = make_mock_bot
    instance.make_cmd = make_cmd
    instance.get_sent_content = get_sent_content
