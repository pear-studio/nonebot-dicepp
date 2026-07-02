"""Story Deck 工具通过 ToolExecutor 调度的功能测试

验证 story_deck 的 5 个工具在 ToolExecutor(**parsed.model_dump()) 展开为
关键字参数时正常工作 —— 不依赖具体 store 实现，只测调度层的契约。
"""
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock, patch, PropertyMock

import pytest
from pydantic import BaseModel

from plugins.DicePP.module.persona.agent.tool_executor import ToolSpec, ToolRegistry, ToolExecutor
from plugins.DicePP.module.persona.agent.actions import EffectKind
from plugins.DicePP.module.persona.agent.event_bus import AgentEventBus, EventStore
from plugins.DicePP.module.persona.agent.state import AgentRunState
from plugins.DicePP.module.persona.tools.story_deck import (
    SearchStoryDeckArgs,
    ListStoryDeckArgs,
    ReadPastEventArgs,
    EditStoryDeckArgs,
    EditFrontsArgs,
    make_search_story_deck_executor,
    make_list_story_deck_executor,
    make_read_past_events_executor,
    make_edit_story_deck_executor,
    make_edit_fronts_executor,
)


# ── helpers ───────────────────────────────────────────────────────


def _make_state(**kwargs) -> AgentRunState:
    defaults = dict(run_id="r1", turn_id="t1", user_id="u1", group_id="g1", mode="chat")
    defaults.update(kwargs)
    return AgentRunState(**defaults)


def _tc(name: str, args: dict, tc_id: str = "tc_1") -> dict:
    """快捷构造 tool call dict"""
    return {"id": tc_id, "name": name, "arguments": json.dumps(args)}


# ── StoryDeckEntry stub ──────────────────────────────────────────


class _FakeEntry:
    """模拟 StoryDeckEntry，供 search / list 的格式化辅助函数消费"""

    def __init__(self, key: str, type: str, content: str = ""):
        self.key = key
        self.type = type
        self.content = content


# ── search_story_deck ─────────────────────────────────────────────


class TestSearchStoryDeck:
    @pytest.fixture
    def store(self):
        from unittest.mock import MagicMock

        s = MagicMock()
        s.search_story_deck = AsyncMock(return_value=[])
        s.get_story_deck_entry = AsyncMock(return_value=None)
        s.get_linked_entries = AsyncMock(return_value=[])
        return s

    @pytest.fixture
    def executor(self, store):
        event_bus = AgentEventBus(event_store=Mock(spec=EventStore))
        reg = ToolRegistry()
        reg.register(ToolSpec(
            name="search_story_deck",
            description="搜索叙事条目库",
            args_schema=SearchStoryDeckArgs,
            effect=EffectKind.PURE,
            executor=make_search_story_deck_executor(store),
        ))
        return ToolExecutor(registry=reg, event_bus=event_bus)

    @pytest.mark.asyncio
    async def test_basic_search(self, executor, store):
        """正常搜索：返回搜索结果并格式化，store 被正确调用"""
        store.search_story_deck.return_value = [
            _FakeEntry("七七", "entity", "一位来自璃月的少女"),
        ]

        results = await executor.execute_many(
            [_tc("search_story_deck", {"query": "七七"})], _make_state()
        )

        assert results[0]["status"] == "success"
        assert "七七" in results[0]["content"]
        store.search_story_deck.assert_called_once_with("七七")

    @pytest.mark.asyncio
    async def test_empty_query(self, executor, store):
        """空查询：返回提示，不调用 store"""
        results = await executor.execute_many(
            [_tc("search_story_deck", {"query": "  "})], _make_state()
        )

        assert results[0]["status"] == "success"
        assert "请提供搜索关键词" in results[0]["content"]
        store.search_story_deck.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_results(self, executor, store):
        """无结果：返回提示"""
        store.search_story_deck.return_value = []

        results = await executor.execute_many(
            [_tc("search_story_deck", {"query": "不存在"})], _make_state()
        )

        assert results[0]["status"] == "success"
        assert "未找到" in results[0]["content"]

    @pytest.mark.asyncio
    async def test_exact_match_with_linked_entries(self, executor, store):
        """精确命中 key：附带关联条目"""
        store.search_story_deck.return_value = [
            _FakeEntry("七七", "entity", "璃月少女"),
        ]
        store.get_story_deck_entry.return_value = _FakeEntry("七七", "entity", "...")
        store.get_linked_entries.return_value = [
            _FakeEntry("白术", "entity", "不卜庐大夫"),
        ]

        results = await executor.execute_many(
            [_tc("search_story_deck", {"query": "七七"})], _make_state()
        )

        assert results[0]["status"] == "success"
        assert "关联条目" in results[0]["content"]
        assert "白术" in results[0]["content"]


# ── list_story_deck ───────────────────────────────────────────────


class TestListStoryDeck:
    @pytest.fixture
    def store(self):
        from unittest.mock import MagicMock

        s = MagicMock()
        s.list_story_deck_entries = AsyncMock(return_value=[])
        s.get_story_deck_count = AsyncMock(return_value=0)
        return s

    @pytest.fixture
    def executor(self, store):
        event_bus = AgentEventBus(event_store=Mock(spec=EventStore))
        reg = ToolRegistry()
        reg.register(ToolSpec(
            name="list_story_deck",
            description="分页列出叙事条目",
            args_schema=ListStoryDeckArgs,
            effect=EffectKind.PURE,
            executor=make_list_story_deck_executor(store),
        ))
        return ToolExecutor(registry=reg, event_bus=event_bus)

    @pytest.mark.asyncio
    async def test_list_with_defaults(self, executor, store):
        """默认参数：type=None, limit=50, offset=0"""
        store.list_story_deck_entries.return_value = [
            _FakeEntry("k1", "entity", "c1"),
        ]
        store.get_story_deck_count.return_value = 1

        results = await executor.execute_many(
            [_tc("list_story_deck", {})], _make_state()
        )

        assert results[0]["status"] == "success"
        store.list_story_deck_entries.assert_called_once_with(type=None, limit=50, offset=0)
        assert "总计 1 条" in results[0]["content"]

    @pytest.mark.asyncio
    async def test_list_with_type_filter(self, executor, store):
        """按 type 过滤"""
        store.list_story_deck_entries.return_value = []
        store.get_story_deck_count.return_value = 0

        results = await executor.execute_many(
            [_tc("list_story_deck", {"type": "entity", "limit": 10, "offset": 0})],
            _make_state(),
        )

        assert results[0]["status"] == "success"
        store.list_story_deck_entries.assert_called_once_with(type="entity", limit=10, offset=0)
        assert "过滤: entity" in results[0]["content"]


# ── read_past_events ──────────────────────────────────────────────


class TestReadPastEvents:
    @pytest.fixture
    def store(self):
        from unittest.mock import MagicMock

        s = MagicMock()
        s.get_events_range = AsyncMock(return_value=[])
        return s

    @pytest.fixture
    def executor(self, store):
        event_bus = AgentEventBus(event_store=Mock(spec=EventStore))
        reg = ToolRegistry()
        reg.register(ToolSpec(
            name="read_past_events",
            description="查询最近 N 天事件",
            args_schema=ReadPastEventArgs,
            effect=EffectKind.PURE,
            executor=make_read_past_events_executor(store),
        ))
        return ToolExecutor(registry=reg, event_bus=event_bus)

    @pytest.mark.asyncio
    async def test_read_with_days(self, executor, store):
        """read_past_events 调度: 验证 keyword args 正确传递并调用 store"""
        fixed_now = datetime(2026, 7, 2, 12, 0, 0, tzinfo=timezone.utc)
        fake_clock = Mock()
        type(fake_clock).now = Mock(return_value=fixed_now)

        store.get_events_range.return_value = []

        with patch("utils.time.get_clock", return_value=fake_clock):
            results = await executor.execute_many(
                [_tc("read_past_events", {"days": 7, "limit": 10, "offset": 0})],
                _make_state(),
            )

        assert results[0]["status"] == "success"
        # 验证 store.get_events_range 被调用，且日期范围正确
        store.get_events_range.assert_called_once()
        start_date, end_date = store.get_events_range.call_args[0]
        assert start_date == "2026-06-25"  # 7 天前
        assert end_date == "2026-07-01"    # 昨天


# ── edit_story_deck ───────────────────────────────────────────────


class TestEditStoryDeck:
    @pytest.fixture
    def store(self):
        from unittest.mock import MagicMock

        s = MagicMock()
        s.get_story_deck_entry = AsyncMock(return_value=None)
        s.upsert_story_deck_entry = AsyncMock(return_value=(True, None))
        s.delete_story_deck_entry = AsyncMock(return_value=(True, None, []))
        return s

    @pytest.fixture
    def executor(self, store):
        event_bus = AgentEventBus(event_store=Mock(spec=EventStore))
        reg = ToolRegistry()
        reg.register(ToolSpec(
            name="edit_story_deck",
            description="批量编辑叙事条目库",
            args_schema=EditStoryDeckArgs,
            effect=EffectKind.STATE_WRITE,
            executor=make_edit_story_deck_executor(store, max_entries=100),
        ))
        return ToolExecutor(registry=reg, event_bus=event_bus)

    @pytest.mark.asyncio
    async def test_create_entry(self, executor, store):
        """创建条目：调用 upsert，返回 applied"""
        results = await executor.execute_many(
            [_tc("edit_story_deck", {
                "changes": [
                    {"action": "create", "key": "新角色", "type": "entity", "content": "一位旅人"},
                ]
            })],
            _make_state(),
        )

        assert results[0]["status"] == "success"
        assert "已应用 1 条" in results[0]["content"]
        assert "create" in results[0]["content"]
        store.upsert_story_deck_entry.assert_called_once_with(
            key="新角色", type="entity", content="一位旅人", max_entries=100
        )

    @pytest.mark.asyncio
    async def test_update_entry(self, executor, store):
        """更新条目：先查存在，再 upsert"""
        store.get_story_deck_entry.return_value = _FakeEntry("旧角色", "entity", "旧内容")

        results = await executor.execute_many(
            [_tc("edit_story_deck", {
                "changes": [
                    {"action": "update", "key": "旧角色", "content": "更新后的内容"},
                ]
            })],
            _make_state(),
        )

        assert results[0]["status"] == "success"
        assert "已应用 1 条" in results[0]["content"]
        store.upsert_story_deck_entry.assert_called_once_with(
            key="旧角色", type="entity", content="更新后的内容", max_entries=100
        )

    @pytest.mark.asyncio
    async def test_delete_entry(self, executor, store):
        """删除条目"""
        results = await executor.execute_many(
            [_tc("edit_story_deck", {
                "changes": [
                    {"action": "delete", "key": "旧角色"},
                ]
            })],
            _make_state(),
        )

        assert results[0]["status"] == "success"
        assert "已应用 1 条" in results[0]["content"]
        store.delete_story_deck_entry.assert_called_once_with("旧角色")

    @pytest.mark.asyncio
    async def test_empty_changes(self, executor, store):
        """空 changes 列表"""
        results = await executor.execute_many(
            [_tc("edit_story_deck", {"changes": []})], _make_state()
        )

        assert results[0]["status"] == "success"
        assert "无操作" in results[0]["content"]

    @pytest.mark.asyncio
    async def test_create_missing_key(self, executor, store):
        """create 缺少 key → errors"""
        results = await executor.execute_many(
            [_tc("edit_story_deck", {
                "changes": [
                    {"action": "create", "type": "entity", "content": "无key"},
                ]
            })],
            _make_state(),
        )

        assert results[0]["status"] == "success"
        assert "失败 1 条" in results[0]["content"]
        assert "缺少 key" in results[0]["content"]


# ── edit_fronts ───────────────────────────────────────────────────


class TestEditFronts:
    @pytest.fixture
    def fronts(self):
        """可变 fronts 列表引用"""
        return []

    @pytest.fixture
    def executor(self, fronts):
        event_bus = AgentEventBus(event_store=Mock(spec=EventStore))
        reg = ToolRegistry()
        reg.register(ToolSpec(
            name="edit_fronts",
            description="增量编辑 fronts",
            args_schema=EditFrontsArgs,
            effect=EffectKind.STATE_WRITE,
            executor=make_edit_fronts_executor(fronts),
        ))
        return ToolExecutor(registry=reg, event_bus=event_bus)

    @pytest.mark.asyncio
    async def test_add_thread_to_new_front(self, executor, fronts):
        """add_thread 到不存在的 front → 新建 front + thread"""
        results = await executor.execute_many(
            [_tc("edit_fronts", {
                "changes": [
                    {
                        "action": "add_thread",
                        "front": "主线",
                        "type": "adventure",
                        "thread": {
                            "name": "探险开始",
                            "direction": "走向未知",
                            "milestones": ["发现遗迹", "遭遇守卫"],
                            "outcome": "完成探索",
                            "related": ["遗迹", "守卫"],
                        },
                    }
                ]
            })],
            _make_state(),
        )

        assert results[0]["status"] == "success"
        assert "已应用 1 条" in results[0]["content"]
        assert len(fronts) == 1
        assert fronts[0]["name"] == "主线"
        assert fronts[0]["type"] == "adventure"
        assert len(fronts[0]["threads"]) == 1
        assert fronts[0]["threads"][0]["name"] == "探险开始"

    @pytest.mark.asyncio
    async def test_add_thread_to_existing_front(self, executor, fronts):
        """add_thread 到已有 front → 追加 thread"""
        fronts.append({
            "name": "主线",
            "type": "adventure",
            "threads": [{"name": "探险开始", "direction": "", "milestones": [], "outcome": "", "related": []}],
        })

        results = await executor.execute_many(
            [_tc("edit_fronts", {
                "changes": [
                    {
                        "action": "add_thread",
                        "front": "主线",
                        "thread": {
                            "name": "暗流涌动",
                            "direction": "暗处的威胁",
                            "milestones": [],
                            "outcome": "",
                            "related": [],
                        },
                    }
                ]
            })],
            _make_state(),
        )

        assert results[0]["status"] == "success"
        assert len(fronts[0]["threads"]) == 2

    @pytest.mark.asyncio
    async def test_update_thread(self, executor, fronts):
        """update_thread 更新已有 thread 的字段"""
        fronts.append({
            "name": "主线",
            "type": "adventure",
            "threads": [{
                "name": "探险开始",
                "direction": "旧的走向",
                "milestones": [],
                "outcome": "",
                "related": [],
            }],
        })

        results = await executor.execute_many(
            [_tc("edit_fronts", {
                "changes": [
                    {
                        "action": "update_thread",
                        "front": "主线",
                        "thread": "探险开始",
                        "updates": {"direction": "新的走向", "milestones": ["新里程碑1"]},
                    }
                ]
            })],
            _make_state(),
        )

        assert results[0]["status"] == "success"
        assert fronts[0]["threads"][0]["direction"] == "新的走向"
        assert fronts[0]["threads"][0]["milestones"] == ["新里程碑1"]

    @pytest.mark.asyncio
    async def test_remove_thread(self, executor, fronts):
        """remove_thread 移除已有 thread"""
        fronts.append({
            "name": "主线",
            "type": "adventure",
            "threads": [{"name": "探险开始", "direction": "", "milestones": [], "outcome": "", "related": []}],
        })

        results = await executor.execute_many(
            [_tc("edit_fronts", {
                "changes": [
                    {"action": "remove_thread", "front": "主线", "thread": "探险开始"},
                ]
            })],
            _make_state(),
        )

        assert results[0]["status"] == "success"
        # front 中唯一的 thread 被移除后，front 也被清理
        assert len(fronts) == 0

    @pytest.mark.asyncio
    async def test_empty_changes(self, executor, fronts):
        """空 changes → 无操作"""
        results = await executor.execute_many(
            [_tc("edit_fronts", {"changes": []})], _make_state()
        )

        assert "无操作" in results[0]["content"]
