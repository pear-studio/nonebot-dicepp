"""
get_jrrp tool executor 单元测试
"""
import pytest
from unittest.mock import patch, MagicMock


class TestGetJrrpTool:
    """get_jrrp 工具 executor 测试"""

    @pytest.fixture
    def tool_ctx(self):
        """创建最小 ToolContext"""
        from plugins.DicePP.module.persona.tools.context import ToolContext
        return ToolContext(
            user_id="test_user_123",
            group_id="",
            timezone="Asia/Shanghai",
        )

    async def test_private_chat_default_user(self, tool_ctx):
        """私聊未指定 user_id 时默认使用当前用户"""
        from plugins.DicePP.module.persona.tools.get_jrrp import get_jrrp_executor
        result = await get_jrrp_executor({}, tool_ctx)
        assert "今日运势:" in result
        assert "/100" in result
        assert "昨日" in result

    async def test_explicit_user_id(self, tool_ctx):
        """指定 user_id 时使用指定用户"""
        from plugins.DicePP.module.persona.tools.get_jrrp import get_jrrp_executor
        result = await get_jrrp_executor({"user_id": "target_user_456"}, tool_ctx)
        assert "今日运势:" in result
        assert "/100" in result

    async def test_group_chat_empty_user_id(self):
        """群聊未指定 user_id 时返回提示"""
        from plugins.DicePP.module.persona.tools.context import ToolContext
        from plugins.DicePP.module.persona.tools.get_jrrp import get_jrrp_executor
        ctx = ToolContext(
            user_id="user_123",
            group_id="group_456",
            timezone="Asia/Shanghai",
        )
        result = await get_jrrp_executor({}, ctx)
        assert "请输入有效的用户 ID" in result

    async def test_empty_string_user_id(self, tool_ctx):
        """空字符串 user_id 在私聊时默认当前用户"""
        from plugins.DicePP.module.persona.tools.get_jrrp import get_jrrp_executor
        result = await get_jrrp_executor({"user_id": ""}, tool_ctx)
        assert "今日运势:" in result

    async def test_no_username_in_output(self, tool_ctx):
        """返回纯数值格式，不含用户名"""
        from plugins.DicePP.module.persona.tools.get_jrrp import get_jrrp_executor
        result = await get_jrrp_executor({"user_id": "some_user"}, tool_ctx)
        # 不应包含用户名前缀如 "pear的今日..."
        assert "的今日" not in result

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
        # user_id 应为可选（不在 required 中）
        assert "user_id" not in params.get("required", [])
