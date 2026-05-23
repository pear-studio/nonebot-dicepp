"""search_persona smoke 测试 — 覆盖 source=all/profile/diary/messages 基本通路"""
import pytest
from unittest.mock import AsyncMock, MagicMock

pytestmark = pytest.mark.integration


def _make_ctx(**kwargs):
    """构造最小 ToolContext mock"""
    from module.persona.tools.context import ToolContext

    ctx = MagicMock(spec=ToolContext)
    ctx.user_id = kwargs.get("user_id", "u1")
    ctx.group_id = kwargs.get("group_id", "g1")
    ctx.store = kwargs.get("store", MagicMock())
    ctx.store.search_memory = AsyncMock(return_value="")
    ctx.store.search_messages = AsyncMock(return_value=[])
    return ctx


@pytest.mark.asyncio
async def test_source_all_merges_results():
    """source=all 三路搜索合并"""
    from module.persona.tools.search_persona import make_search_persona_executor

    ctx = _make_ctx()
    ctx.store.search_memory = AsyncMock(side_effect=[
        "【用户档案】\nname: 小明",     # profile
        "【相关日记】\n[2026-05-21] 旅行",  # diary
    ])
    ctx.store.search_messages = AsyncMock(return_value=[])

    executor = make_search_persona_executor(search_max_chars=180)
    result = await executor({"keyword": "旅行", "source": "all"}, ctx)

    assert "【用户档案】" in result
    assert "【相关日记】" in result


@pytest.mark.asyncio
async def test_source_profile():
    """source=profile 仅搜档案"""
    from module.persona.tools.search_persona import make_search_persona_executor

    ctx = _make_ctx()
    ctx.store.search_memory = AsyncMock(return_value="【用户档案】\nname: 小明")

    executor = make_search_persona_executor(search_max_chars=180)
    result = await executor({"keyword": "name", "source": "profile"}, ctx)

    assert "小明" in result
    ctx.store.search_messages.assert_not_called()


@pytest.mark.asyncio
async def test_source_diary():
    """source=diary 仅搜日记"""
    from module.persona.tools.search_persona import make_search_persona_executor

    ctx = _make_ctx()
    ctx.store.search_memory = AsyncMock(return_value="【相关日记】\n[2026-05-21] 内容")

    executor = make_search_persona_executor(search_max_chars=180)
    result = await executor({"keyword": "内容", "source": "diary", "days": 14}, ctx)

    assert "2026-05-21" in result


@pytest.mark.asyncio
async def test_source_messages():
    """source=messages 仅搜消息"""
    from module.persona.tools.search_persona import make_search_persona_executor
    from datetime import datetime

    class _FakeMsg:
        def __init__(self, user_id, role, content, display_name):
            self.user_id = user_id
            self.role = role
            self.content = content
            self.display_name = display_name
            self.created_at = datetime(2026, 5, 21, 15, 0, 0)

    msgs = [_FakeMsg("u1", "user", "奈雪的茶好好喝", "小王")]
    ctx = _make_ctx()
    ctx.store.search_messages = AsyncMock(return_value=msgs)

    executor = make_search_persona_executor(search_max_chars=180)
    result = await executor({"keyword": "奈雪", "source": "messages"}, ctx)

    assert "群聊记录" in result
    assert "奈雪的茶好好喝" in result


@pytest.mark.asyncio
async def test_empty_keyword_skips_profile():
    """空 keyword 时跳过 profile，但仍搜 diary 和 messages"""
    from module.persona.tools.search_persona import make_search_persona_executor

    ctx = _make_ctx()
    ctx.store.search_memory = AsyncMock(return_value="【相关日记】\n[2026-05-21] 今天天气好")

    executor = make_search_persona_executor(search_max_chars=180)
    result = await executor({"source": "all"}, ctx)

    # profile should not be called with keyword="" (被空 keyword 检查跳过)
    # diary still searched
    assert "今天天气好" in result


@pytest.mark.asyncio
async def test_private_chat_messages_returns_hint():
    """私聊场景 messages 分支返回提示而非静默空返回"""
    from module.persona.tools.search_persona import make_search_persona_executor

    ctx = _make_ctx(group_id="")

    executor = make_search_persona_executor(search_max_chars=180)
    result = await executor({"source": "messages"}, ctx)

    assert "私聊场景不支持搜索聊天记录" in result


@pytest.mark.asyncio
async def test_no_results_returns_fallback():
    """无结果时返回 fallback 文本"""
    from module.persona.tools.search_persona import make_search_persona_executor

    ctx = _make_ctx()
    ctx.store.search_memory = AsyncMock(return_value="")
    ctx.store.search_messages = AsyncMock(return_value=[])

    executor = make_search_persona_executor(search_max_chars=180)
    result = await executor({"keyword": "zzz_nonexistent", "source": "all"}, ctx)

    assert result == "未找到相关记录"


@pytest.mark.asyncio
async def test_days_clamped_to_positive():
    """days 负值/零值被钳位为 1"""
    from module.persona.tools.search_persona import make_search_persona_executor

    ctx = _make_ctx()
    ctx.store.search_memory = AsyncMock(return_value="")

    executor = make_search_persona_executor(search_max_chars=180)
    await executor({"source": "diary", "days": -1}, ctx)

    # search_memory 被调用时 days 应为 1（被钳位）
    call_kwargs = ctx.store.search_memory.call_args.kwargs
    assert call_kwargs["days"] == 1


@pytest.mark.asyncio
async def test_user_id_passed_to_search_messages():
    """user_id 参数传递到 store.search_messages()"""
    from module.persona.tools.search_persona import make_search_persona_executor

    ctx = _make_ctx()
    ctx.store.search_messages = AsyncMock(return_value=[])

    executor = make_search_persona_executor(search_max_chars=180)
    await executor({"source": "messages", "user_id": "target_user"}, ctx)

    call_kwargs = ctx.store.search_messages.call_args.kwargs
    assert call_kwargs["user_id"] == "target_user"
