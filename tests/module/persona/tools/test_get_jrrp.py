"""
get_jrrp tool executor 单元测试

使用 time injection 隔离时间依赖，验证 seed-fixed 随机数、数值范围等。
"""
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch


class TestGetJrrpTool:
    """get_jrrp 工具 executor 测试"""

    FIXED_NOW = datetime(2024, 6, 15, 12, 0, 0)

    @pytest.fixture
    def tool_ctx(self):
        """创建最小 ToolContext"""
        from plugins.DicePP.module.persona.tools.context import ToolContext
        return ToolContext(
            user_id="test_user_123",
            group_id="",
            timezone="Asia/Shanghai",
        )

    @pytest.fixture
    def freeze_time(self, monkeypatch):
        """固定 wall_now 返回时间"""
        fixed = self.FIXED_NOW
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.tools.get_jrrp.wall_now",
            lambda timezone_name=None: fixed,
        )
        return fixed

    async def _exec(self, args: dict, ctx) -> str:
        from plugins.DicePP.module.persona.tools.get_jrrp import get_jrrp_executor
        return await get_jrrp_executor(args, ctx)

    # ── 基础功能 ────────────────────────────────────────────────────────

    async def test_private_chat_default_user(self, tool_ctx, freeze_time):
        """私聊未指定 user_id 时默认使用当前用户"""
        result = await self._exec({}, tool_ctx)
        assert "今日运势:" in result
        assert "/100" in result
        assert "昨日" in result

    async def test_explicit_user_id(self, tool_ctx, freeze_time):
        """指定 user_id 时使用指定用户"""
        result = await self._exec({"user_id": "target_user_456"}, tool_ctx)
        assert "今日运势:" in result
        assert "/100" in result

    async def test_group_chat_empty_user_id(self):
        """群聊未指定 user_id 时返回提示"""
        from plugins.DicePP.module.persona.tools.context import ToolContext
        ctx = ToolContext(
            user_id="user_123",
            group_id="group_456",
            timezone="Asia/Shanghai",
        )
        result = await self._exec({}, ctx)
        assert "请输入有效的用户 ID" in result

    async def test_empty_string_user_id(self, tool_ctx, freeze_time):
        """空字符串 user_id 在私聊时默认当前用户"""
        result = await self._exec({"user_id": ""}, tool_ctx)
        assert "今日运势:" in result

    async def test_no_username_in_output(self, tool_ctx, freeze_time):
        """返回纯数值格式，不含用户名"""
        result = await self._exec({"user_id": "some_user"}, tool_ctx)
        assert "的今日" not in result

    # ── 数值范围与格式 ──────────────────────────────────────────────────

    async def test_numeric_range(self, tool_ctx, freeze_time):
        """返回的运势值在 0-100 范围内"""
        import re
        result = await self._exec({"user_id": "range_user"}, tool_ctx)
        match = re.search(r"(\d+)/100", result)
        assert match is not None
        value = int(match.group(1))
        assert 0 <= value <= 100

    async def test_both_today_and_yesterday_values(self, tool_ctx, freeze_time):
        """同时包含今日和昨日数值"""
        import re
        result = await self._exec({"user_id": "both_user"}, tool_ctx)
        values = re.findall(r"(\d+)/100", result)
        assert len(values) == 2  # 今日 + 昨日

    # ── seed-fixed 确定性验证 ──────────────────────────────────────────

    async def test_seed_fixed_deterministic(self, tool_ctx, freeze_time):
        """相同 user_id + 相同日期返回相同运势值"""
        r1 = await self._exec({"user_id": "det_user"}, tool_ctx)
        r2 = await self._exec({"user_id": "det_user"}, tool_ctx)
        assert r1 == r2

    async def test_seed_fixed_different_user_different(self, tool_ctx, freeze_time):
        """不同用户在同一天返回不同运势值"""
        r1 = await self._exec({"user_id": "user_a"}, tool_ctx)
        r2 = await self._exec({"user_id": "user_b"}, tool_ctx)
        assert r1 != r2

    async def test_seed_fixed_known_value(self, tool_ctx, monkeypatch):
        """固定时间和用户可预计算期望值"""
        import random
        from plugins.DicePP.utils.time import datetime_to_str_day

        fixed_now = datetime(2024, 3, 1, 8, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.tools.get_jrrp.wall_now",
            lambda timezone_name=None: fixed_now,
        )

        user_id = "known_user"
        # 用同样的随机算法预计算
        today_str = datetime_to_str_day(fixed_now)
        yesterday_str = datetime_to_str_day(fixed_now - timedelta(days=1))
        expected_jrrp = random.Random(today_str + user_id).randint(1, 100)
        expected_zrrp = random.Random(yesterday_str + user_id).randint(1, 100)

        result = await self._exec({"user_id": user_id}, tool_ctx)
        assert f"{expected_jrrp}/100" in result
        assert f"{expected_zrrp}/100" in result

    # ── ToolDef 格式 ────────────────────────────────────────────────────

    def test_tool_def_format(self):
        """ToolDef 符合 OpenAI function calling 格式"""
        from plugins.DicePP.module.persona.tools.get_jrrp import GET_JRRP_TOOL
        d = GET_JRRP_TOOL.to_openai_format()
        assert d["type"] == "function"
        assert d["function"]["name"] == "get_jrrp"
        assert "description" in d["function"]

        params = d["function"]["parameters"]
        assert params["type"] == "object"
        assert "user_id" in params["properties"]
        user_id_schema = params["properties"]["user_id"]
        assert user_id_schema["type"] == "string"
        assert "description" in user_id_schema

        # user_id 应为可选（不在 required 中）
        assert "user_id" not in params.get("required", [])
