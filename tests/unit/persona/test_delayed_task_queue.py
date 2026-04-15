import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, AsyncMock

from plugins.DicePP.module.persona.proactive.delayed_task_queue import DelayedTaskQueue


@pytest.fixture
def mock_store():
    store = MagicMock()
    store.add_delayed_task = AsyncMock(return_value=1)
    store.poll_delayed_tasks = AsyncMock(return_value=[])
    store.complete_delayed_task = AsyncMock()
    store.fail_delayed_task = AsyncMock()
    return store


@pytest.mark.asyncio
async def test_enqueue_event_share(mock_store):
    queue = DelayedTaskQueue(mock_store)
    task_id = await queue.enqueue_event_share("evt_1", "下雨了", 0.8, 2)
    assert task_id == 1
    mock_store.add_delayed_task.assert_called_once()
    call = mock_store.add_delayed_task.call_args
    assert call.kwargs["task_type"] == "event_share"


@pytest.mark.asyncio
async def test_tick_share_desire_below_threshold(mock_store):
    from plugins.DicePP.module.persona.data.models import DelayedTask

    mock_store.poll_delayed_tasks.return_value = [
        DelayedTask(
            id=1,
            task_type="event_share",
            payload={"event_description": "下雨了", "share_desire": 0.2},
            scheduled_at=datetime.now(),
        )
    ]
    queue = DelayedTaskQueue(mock_store, share_threshold=0.5)
    results = await queue.tick(on_share=AsyncMock())
    assert results == []
    mock_store.complete_delayed_task.assert_called_once_with(1)


@pytest.mark.asyncio
async def test_tick_share_event(mock_store):
    from plugins.DicePP.module.persona.data.models import DelayedTask

    mock_store.poll_delayed_tasks.return_value = [
        DelayedTask(
            id=2,
            task_type="event_share",
            payload={"event_description": "出太阳了", "share_desire": 0.8},
            scheduled_at=datetime.now(),
        )
    ]
    on_share = AsyncMock(return_value=[{"content": "出太阳了~"}])
    queue = DelayedTaskQueue(mock_store, share_threshold=0.5)
    results = await queue.tick(on_share=on_share)
    assert len(results) == 1
    assert results[0]["content"] == "出太阳了~"
    mock_store.complete_delayed_task.assert_called_once_with(2)
