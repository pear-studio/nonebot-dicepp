import pytest
from datetime import datetime
from plugins.DicePP.module.persona.data.store import PersonaDataStore
from plugins.DicePP.module.persona.data.models import DailyEvent


@pytest.mark.asyncio
async def test_add_and_get_daily_event_with_new_fields(tmp_path):
    db_path = tmp_path / "test.db"
    import aiosqlite
    db = await aiosqlite.connect(str(db_path))
    store = PersonaDataStore(db)
    await store.ensure_tables()

    await store.add_daily_event(
        date="2024-01-01",
        event_type="scheduled",
        description="测试中",
        reaction="不错",
        share_desire=0.75,
        duration_minutes=30,
    )

    events = await store.get_daily_events("2024-01-01")
    assert len(events) == 1
    ev = events[0]
    assert ev.share_desire == 0.75
    assert ev.duration_minutes == 30
    assert ev.description == "测试中"
    assert ev.reaction == "不错"
    assert ev.event_type == "scheduled"

    await db.close()
