"""契约测试：CommandContext / CommandContextResolver

覆盖范围：
  1. CommandContext 属性推导（is_group / is_private / config_key / 透传）
  2. per-invocation 读缓存（group_config 只查一次 DB，get_config_value 后备）
  3. CommandContextResolver.resolve() 工厂行为
"""
from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest

from plugins.DicePP.core.command.context import CommandContext, CommandContextResolver


# ── 1. 属性推导 ──────────────────────────────────────────────────────────────────


class TestCommandContextProperties:
    """CommandContext 实例化时同步计算的属性。"""

    def test_is_group_true_when_group_id_present(self):
        """group_id 非空 → is_group 为 True"""
        meta = MagicMock()
        meta.group_id = "123456"
        ctx = CommandContext(bot=MagicMock(), meta=meta)

        assert ctx.is_group is True
        assert ctx.is_private is False

    def test_is_private_true_when_group_id_empty(self):
        """group_id 为空字符串 → is_private 为 True"""
        meta = MagicMock()
        meta.group_id = ""
        ctx = CommandContext(bot=MagicMock(), meta=meta)

        assert ctx.is_private is True
        assert ctx.is_group is False

    def test_config_key_for_group_message(self):
        """群消息 → config_key 等于 group_id"""
        meta = MagicMock()
        meta.group_id = "group_a"
        meta.user_id = "u001"
        ctx = CommandContext(bot=MagicMock(), meta=meta)

        assert ctx.config_key == "group_a"

    def test_config_key_for_private_message(self):
        """私聊 → config_key 为 __user__<user_id>"""
        meta = MagicMock()
        meta.group_id = ""
        meta.user_id = "u001"
        ctx = CommandContext(bot=MagicMock(), meta=meta)

        assert ctx.config_key == "__user__u001"

    def test_properties_forwarded_from_meta(self):
        """user_id / group_id / nickname / permission 正确透传"""
        meta = MagicMock()
        meta.user_id = "u007"
        meta.group_id = "g042"
        meta.nickname = "Agent"
        meta.permission = 3
        ctx = CommandContext(bot=MagicMock(), meta=meta)

        assert ctx.user_id == "u007"
        assert ctx.group_id == "g042"
        assert ctx.nickname == "Agent"
        assert ctx.permission == 3


# ── 2. per-invocation 读缓存 ─────────────────────────────────────────────────────


class TestCommandContextCache:
    """同一 CommandContext 实例上的读缓存行为。"""

    @pytest.mark.asyncio
    async def test_group_config_caches_db_result(self):
        """同一实例多次调用 group_config() 只查一次 DB"""
        meta = MagicMock()
        meta.group_id = "g100"
        meta.user_id = "u100"
        expected = MagicMock()

        bot = MagicMock()
        bot.db.group_config.get = AsyncMock(return_value=expected)

        ctx = CommandContext(bot=bot, meta=meta)

        r1 = await ctx.group_config()
        r2 = await ctx.group_config()

        assert r1 is expected
        assert r2 is expected
        bot.db.group_config.get.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_config_value_returns_default_when_no_data(self):
        """get_config_value 在配置不可用时返回 default"""
        meta = MagicMock()
        meta.group_id = "g200"
        meta.user_id = "u200"

        bot = MagicMock()
        # group_config_data 内部调用 group_config，后者返回 None → data 为空字典
        bot.db.group_config.get = AsyncMock(return_value=None)

        ctx = CommandContext(bot=bot, meta=meta)
        result = await ctx.get_config_value("nonexistent", "fallback")

        assert result == "fallback"

    @pytest.mark.asyncio
    async def test_get_config_value_returns_value_when_field_exists(self):
        """get_config_value 正确返回已有字段值"""
        meta = MagicMock()
        meta.group_id = "g300"
        meta.user_id = "u300"

        bot = MagicMock()
        cfg = MagicMock()
        cfg.data = {"enabled": True, "limit": 5}
        bot.db.group_config.get = AsyncMock(return_value=cfg)

        ctx = CommandContext(bot=bot, meta=meta)

        assert await ctx.get_config_value("enabled") is True
        assert await ctx.get_config_value("limit") == 5


# ── 3. CommandContextResolver.resolve() ──────────────────────────────────────────


class TestCommandContextResolver:
    """CommandContextResolver 工厂行为。"""

    @pytest.mark.asyncio
    async def test_resolve_returns_command_context_instance(self):
        """resolve 返回 CommandContext 实例"""
        meta = MagicMock()
        meta.group_id = "g999"
        meta.user_id = "u999"
        bot = MagicMock()

        ctx = await CommandContextResolver.resolve(bot, meta)

        assert isinstance(ctx, CommandContext)

    @pytest.mark.asyncio
    async def test_resolve_sets_group_id(self):
        """resolve 返回的上下文正确携带 group_id"""
        meta = MagicMock()
        meta.group_id = "resolve_group"
        meta.user_id = "resolve_user"
        bot = MagicMock()

        ctx = await CommandContextResolver.resolve(bot, meta)

        assert ctx.group_id == "resolve_group"

    @pytest.mark.asyncio
    async def test_resolve_sets_private_context_when_no_group_id(self):
        """resolve 在无 group_id 时返回私聊上下文"""
        meta = MagicMock()
        meta.group_id = ""
        meta.user_id = "resolve_private_user"
        bot = MagicMock()

        ctx = await CommandContextResolver.resolve(bot, meta)

        assert ctx.is_private is True
        assert ctx.config_key == "__user__resolve_private_user"
