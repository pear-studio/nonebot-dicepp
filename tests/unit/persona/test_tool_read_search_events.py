"""read_events / search_events 工具集成测试"""
import pytest
from datetime import date as date_type

from plugins.DicePP.module.persona.tools.read_events import (
    READ_EVENTS_TOOL, make_read_events_executor,
)
from plugins.DicePP.module.persona.tools.search_events import (
    SEARCH_EVENTS_TOOL, make_search_events_executor,
)
from plugins.DicePP.module.persona.tools.context import ToolContext


def _make_ctx(temp_db):
    return ToolContext(user_id="u1", store=temp_db)


class TestReadEventsTool:
    """read_events 工具测试"""

    @pytest.mark.asyncio
    async def test_tool_def_name(self):
        assert READ_EVENTS_TOOL.name == "read_events"

    @pytest.mark.asyncio
    async def test_returns_events_for_today_by_default(self, temp_db):
        today = date_type.today().strftime("%Y-%m-%d")
        await temp_db.add_daily_event(today, "system", "事件A", reaction="反应A")
        await temp_db.add_daily_event(today, "system", "事件B")

        executor = make_read_events_executor()
        result = await executor({}, _make_ctx(temp_db))

        assert "事件A" in result
        assert "事件B" in result
        assert "反应A" in result
        assert today in result

    @pytest.mark.asyncio
    async def test_returns_events_for_specific_date(self, temp_db):
        await temp_db.add_daily_event("2026-01-15", "system", "特定事件")

        executor = make_read_events_executor()
        result = await executor({"date": "2026-01-15"}, _make_ctx(temp_db))

        assert "特定事件" in result
        assert "2026-01-15" in result

    @pytest.mark.asyncio
    async def test_no_events_message(self, temp_db):
        executor = make_read_events_executor()
        result = await executor({"date": "2020-01-01"}, _make_ctx(temp_db))
        assert "暂无事件记录" in result

    @pytest.mark.asyncio
    async def test_shows_deltas_and_share_desire(self, temp_db):
        today = date_type.today().strftime("%Y-%m-%d")
        await temp_db.add_daily_event(
            today, "system", "事件A",
            energy_delta=5, mood_delta=-3, health_delta=0,
            share_desire=0.75,
        )

        executor = make_read_events_executor()
        result = await executor({}, _make_ctx(temp_db))

        assert "+5" in result
        assert "-3" in result
        assert "75%" in result

    @pytest.mark.asyncio
    async def test_no_store_returns_unavailable(self, temp_db):
        ctx = ToolContext(user_id="u1", store=None)
        executor = make_read_events_executor()
        result = await executor({}, ctx)
        assert "不可用" in result


class TestSearchEventsTool:
    """search_events 工具测试"""

    @pytest.mark.asyncio
    async def test_tool_def_name(self):
        assert SEARCH_EVENTS_TOOL.name == "search_events"

    @pytest.mark.asyncio
    async def test_search_by_keyword_finds_match(self, temp_db):
        today = date_type.today().strftime("%Y-%m-%d")
        await temp_db.add_daily_event(
            today, "system", "在酒馆喝酒", reaction="很开心",
        )
        await temp_db.add_daily_event(today, "system", "在森林散步")

        executor = make_search_events_executor()
        result = await executor({"keyword": "酒馆"}, _make_ctx(temp_db))

        assert "在酒馆喝酒" in result
        assert "很开心" in result
        assert "在森林散步" not in result

    @pytest.mark.asyncio
    async def test_search_no_results(self, temp_db):
        today = date_type.today().strftime("%Y-%m-%d")
        await temp_db.add_daily_event(today, "system", "在酒馆喝酒")

        executor = make_search_events_executor()
        result = await executor({"keyword": "不存在的词"}, _make_ctx(temp_db))

        assert "未找到" in result

    @pytest.mark.asyncio
    async def test_empty_keyword_returns_prompt(self, temp_db):
        executor = make_search_events_executor()
        result = await executor({"keyword": ""}, _make_ctx(temp_db))
        assert "请提供" in result

        result = await executor({"keyword": "   "}, _make_ctx(temp_db))
        assert "请提供" in result

    @pytest.mark.asyncio
    async def test_respects_days_and_limit_params(self, temp_db):
        await temp_db.add_daily_event("2026-06-01", "system", "事件A 关键词")
        await temp_db.add_daily_event("2026-06-02", "system", "事件B 关键词")

        executor = make_search_events_executor()
        result = await executor(
            {"keyword": "关键词", "days": 365, "limit": 1},
            _make_ctx(temp_db),
        )
        assert "搜索结果" in result

    @pytest.mark.asyncio
    async def test_no_store_returns_unavailable(self, temp_db):
        ctx = ToolContext(user_id="u1", store=None)
        executor = make_search_events_executor()
        result = await executor({"keyword": "test"}, ctx)
        assert "不可用" in result
