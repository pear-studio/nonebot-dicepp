"""AgentEventBus + EventStore 测试 — emit、sink 分发、失败隔离"""
import json

import pytest
from unittest.mock import Mock, AsyncMock, MagicMock

from module.persona.agent.event_bus import AgentEventBus, EventStore, EventSink
from module.persona.agent.events import AgentRunStartedPayload, AgentEvent
from module.persona.agent.state import AgentRunState
from module.persona.data.store import PersonaDataStore


def _make_state(**kwargs) -> AgentRunState:
    defaults = dict(
        run_id="r1", interaction_id="t1", user_id="", group_id="",
    )
    defaults.update(kwargs)
    return AgentRunState(**defaults)


class TestEventStore:
    """EventStore 封装 PersonaDataStore"""

    @pytest.mark.asyncio
    async def test_write_run_delegates(self):
        store = Mock(spec=PersonaDataStore)
        store.insert_agent_run = AsyncMock()
        es = EventStore(data_store=store)

        await es.write_run(run_id="r1", interaction_id="t1", user_id="u1", group_id="g1", agent_name="test", run_tag="test")

        store.insert_agent_run.assert_called_once_with(
            run_id="r1", interaction_id="t1", user_id="u1", group_id="g1",
            agent_name="test", run_tag="test",
        )

    @pytest.mark.asyncio
    async def test_update_run_delegates(self):
        store = Mock(spec=PersonaDataStore)
        store.update_agent_run = AsyncMock()
        es = EventStore(data_store=store)

        await es.update_run("r1", status="completed", final_reason="ok")

        store.update_agent_run.assert_called_once_with("r1", status="completed", final_reason="ok")

    @pytest.mark.asyncio
    async def test_write_event_delegates(self):
        store = Mock(spec=PersonaDataStore)
        store.insert_agent_event = AsyncMock()
        es = EventStore(data_store=store)

        event = AgentEvent(run_id="r1", seq=0, event_type="test", payload={})
        await es.write_event(event)

        store.insert_agent_event.assert_awaited_once()
        args = store.insert_agent_event.call_args[1]
        assert args["run_id"] == "r1"
        assert args["seq"] == 0
        assert args["event_type"] == "test"
        assert json.loads(args["payload_json"]) == {}
        assert args["created_at"] == ""

    @pytest.mark.asyncio
    async def test_get_events_delegates(self):
        store = Mock(spec=PersonaDataStore)
        store.get_agent_events = AsyncMock(return_value=[{"seq": 1}])
        es = EventStore(data_store=store)

        events = await es.get_events("r1")
        assert len(events) == 1
        store.get_agent_events.assert_called_once_with("r1")

    @pytest.mark.asyncio
    async def test_data_store_none_write_run_safe(self):
        es = EventStore(data_store=None)
        # 不应该抛出异常
        await es.write_run(run_id="r1", interaction_id="t1", user_id="u1", group_id="g1", agent_name="test", run_tag="test")

    @pytest.mark.asyncio
    async def test_data_store_none_update_run_safe(self):
        es = EventStore(data_store=None)
        await es.update_run("r1", status="completed")

    @pytest.mark.asyncio
    async def test_data_store_none_write_event_safe(self):
        es = EventStore(data_store=None)
        event = AgentEvent(run_id="r1", seq=0, event_type="test", payload={})
        await es.write_event(event)

    @pytest.mark.asyncio
    async def test_data_store_none_get_events_safe(self):
        es = EventStore(data_store=None)
        events = await es.get_events("r1")
        assert events == []


class TestAgentEventBus:
    """AgentEventBus emit / sink 分发"""

    @pytest.fixture
    def mock_event_store(self):
        store = Mock(spec=PersonaDataStore)
        store.insert_agent_event = AsyncMock()
        return store

    @pytest.mark.asyncio
    async def test_emit_writes_event_to_store(self, mock_event_store):
        es = EventStore(data_store=mock_event_store)
        bus = AgentEventBus(event_store=es)
        state = _make_state()

        payload = AgentRunStartedPayload(run_id="r1", interaction_id="t1", user_id="u1", group_id="g1", agent_name="agent", run_tag="chat")
        event = await bus.emit("AgentRunStarted", payload, state)

        assert event.event_type == "AgentRunStarted"
        assert event.run_id == "r1"
        assert event.seq == 1
        assert event.created_at != ""
        mock_event_store.insert_agent_event.assert_awaited_once()
        args = mock_event_store.insert_agent_event.call_args.kwargs
        assert args["run_id"] == event.run_id
        assert args["seq"] == event.seq
        assert args["event_type"] == "AgentRunStarted"
        assert json.loads(args["payload_json"]) == {
            "run_id": "r1",
            "interaction_id": "t1",
            "user_id": "u1",
            "group_id": "g1",
            "agent_name": "agent",
            "run_tag": "chat",
        }
        assert args["created_at"] == event.created_at

    @pytest.mark.asyncio
    async def test_seq_increments(self, mock_event_store):
        es = EventStore(data_store=mock_event_store)
        bus = AgentEventBus(event_store=es)
        state = _make_state()
        payload = AgentRunStartedPayload(run_id="r1", interaction_id="t1", user_id="u1", group_id="g1", agent_name="agent", run_tag="chat")

        e1 = await bus.emit("e1", payload, state)
        e2 = await bus.emit("e2", payload, state)
        e3 = await bus.emit("e3", payload, state)

        assert e1.seq == 1
        assert e2.seq == 2
        assert e3.seq == 3

    @pytest.mark.asyncio
    async def test_emit_distributes_to_sinks(self, mock_event_store):
        es = EventStore(data_store=mock_event_store)
        sink = Mock(spec=EventSink)
        sink.on_event = AsyncMock()
        bus = AgentEventBus(event_store=es, sinks=[sink])
        state = _make_state()

        payload = AgentRunStartedPayload(run_id="r1", interaction_id="t1", user_id="u1", group_id="g1", agent_name="agent", run_tag="chat")
        event = await bus.emit("AgentRunStarted", payload, state)

        sink.on_event.assert_awaited_once_with(event, state)
        assert event.event_type == "AgentRunStarted"

    @pytest.mark.asyncio
    async def test_sink_failure_does_not_block(self, mock_event_store):
        es = EventStore(data_store=mock_event_store)
        failing_sink = Mock(spec=EventSink)
        failing_sink.on_event = AsyncMock(side_effect=RuntimeError("sink crash"))
        sink2 = Mock(spec=EventSink)
        sink2.on_event = AsyncMock()
        bus = AgentEventBus(event_store=es, sinks=[failing_sink, sink2])
        state = _make_state()

        payload = AgentRunStartedPayload(run_id="r1", interaction_id="t1", user_id="u1", group_id="g1", agent_name="agent", run_tag="chat")
        event = await bus.emit("AgentRunStarted", payload, state)

        failing_sink.on_event.assert_awaited_once_with(event, state)
        sink2.on_event.assert_awaited_once_with(event, state)
        assert len(state.sink_failures) == 1  # 失败被记录
        assert "sink crash" in state.sink_failures[0]

    @pytest.mark.asyncio
    async def test_store_write_failure_logged_not_raised(self, mock_event_store):
        mock_event_store.insert_agent_event = AsyncMock(side_effect=RuntimeError("db error"))
        es = EventStore(data_store=mock_event_store)
        bus = AgentEventBus(event_store=es)
        state = _make_state()

        payload = AgentRunStartedPayload(run_id="r1", interaction_id="t1", user_id="u1", group_id="g1", agent_name="agent", run_tag="chat")
        # 存储失败不应阻止 emit 继续分发
        event = await bus.emit("AgentRunStarted", payload, state)
        assert event.event_type == "AgentRunStarted"

    @pytest.mark.asyncio
    async def test_dict_payload_passed_through(self, mock_event_store):
        es = EventStore(data_store=mock_event_store)
        bus = AgentEventBus(event_store=es)
        state = _make_state()

        event = await bus.emit("test", {"key": "value", "num": 42}, state)
        assert event.payload["key"] == "value"
        assert event.payload["num"] == 42

    @pytest.mark.asyncio
    async def test_sink_failure_accumulates(self, mock_event_store):
        es = EventStore(data_store=mock_event_store)
        s1 = Mock(spec=EventSink)
        s1.on_event = AsyncMock(side_effect=RuntimeError("fail1"))
        s2 = Mock(spec=EventSink)
        s2.on_event = AsyncMock(side_effect=RuntimeError("fail2"))
        bus = AgentEventBus(event_store=es, sinks=[s1, s2])
        state = _make_state()

        payload = AgentRunStartedPayload(run_id="r1", interaction_id="t1", user_id="u1", group_id="g1", agent_name="agent", run_tag="chat")
        await bus.emit("AgentRunStarted", payload, state)

        assert len(state.sink_failures) == 2
        assert "fail1" in state.sink_failures[0]
        assert "fail2" in state.sink_failures[1]
