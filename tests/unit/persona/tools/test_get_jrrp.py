"""get_jrrp 工具测试。"""

from datetime import datetime, timedelta
import random
import re

import pytest

from plugins.DicePP.module.persona.agent.runtime_types import ToolExecutionContext
from plugins.DicePP.module.persona.tools.get_jrrp import (
    GET_JRRP_TOOL,
    build_get_jrrp_tool,
)




FIXED_NOW = datetime(2024, 6, 15, 12, 0, 0)


def _ctx() -> ToolExecutionContext:
    return ToolExecutionContext(
        run_id="r1",
        tool_call_id="tc1",
        call_index=0,
        same_name_index=0,
    )


async def _execute(user_id_default: str, monkeypatch, **kwargs) -> str:
    monkeypatch.setattr(
        "plugins.DicePP.module.persona.tools.get_jrrp.wall_now",
        lambda timezone_name=None: FIXED_NOW,
    )
    tool = build_get_jrrp_tool(user_id_default=user_id_default)
    result = await tool.handler(tool.args_schema(**kwargs), _ctx())
    return result.observation


async def test_private_chat_default_user(monkeypatch):
    result = await _execute("test_user_123", monkeypatch)

    assert "今日运势:" in result
    assert "/100" in result
    assert "昨日" in result


async def test_explicit_user_id(monkeypatch):
    result = await _execute("test_user_123", monkeypatch, user_id="target_user_456")

    assert "今日运势:" in result
    assert "/100" in result


async def test_group_chat_empty_user_id(monkeypatch):
    result = await _execute("", monkeypatch)

    assert "请输入有效的玩家 QQ ID" in result


async def test_empty_string_user_id_falls_back_to_default(monkeypatch):
    result = await _execute("test_user_123", monkeypatch, user_id="")

    assert "今日运势:" in result


async def test_no_username_in_output(monkeypatch):
    result = await _execute("test_user_123", monkeypatch, user_id="some_user")

    assert "的今日" not in result


async def test_numeric_range(monkeypatch):
    result = await _execute("test_user_123", monkeypatch, user_id="range_user")
    match = re.search(r"(\d+)/100", result)

    assert match is not None
    assert 0 <= int(match.group(1)) <= 100


async def test_both_today_and_yesterday_values(monkeypatch):
    result = await _execute("test_user_123", monkeypatch, user_id="both_user")

    assert len(re.findall(r"(\d+)/100", result)) == 2


async def test_seed_fixed_deterministic(monkeypatch):
    r1 = await _execute("test_user_123", monkeypatch, user_id="det_user")
    r2 = await _execute("test_user_123", monkeypatch, user_id="det_user")

    assert r1 == r2


async def test_seed_fixed_different_user_different(monkeypatch):
    r1 = await _execute("test_user_123", monkeypatch, user_id="user_a")
    r2 = await _execute("test_user_123", monkeypatch, user_id="user_b")

    assert r1 != r2


async def test_seed_fixed_known_value(monkeypatch):
    from plugins.DicePP.utils.time import datetime_to_str_day

    fixed_now = datetime(2024, 3, 1, 8, 0, 0)
    monkeypatch.setattr(
        "plugins.DicePP.module.persona.tools.get_jrrp.wall_now",
        lambda timezone_name=None: fixed_now,
    )

    user_id = "known_user"
    today_str = datetime_to_str_day(fixed_now)
    yesterday_str = datetime_to_str_day(fixed_now - timedelta(days=1))
    expected_jrrp = random.Random(today_str + user_id).randint(1, 100)
    expected_zrrp = random.Random(yesterday_str + user_id).randint(1, 100)

    tool = build_get_jrrp_tool(user_id_default="test_user_123")
    result = await tool.handler(tool.args_schema(user_id=user_id), _ctx())

    assert f"{expected_jrrp}/100" in result.observation
    assert f"{expected_zrrp}/100" in result.observation


def test_tool_spec_format():
    assert GET_JRRP_TOOL.name == "get_jrrp"
    assert GET_JRRP_TOOL.description
    properties = GET_JRRP_TOOL.args_schema.model_json_schema()["properties"]
    assert "user_id" in properties
    assert "user_id" not in GET_JRRP_TOOL.args_schema.model_json_schema().get("required", [])
