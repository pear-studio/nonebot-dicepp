import pytest
from unittest.mock import AsyncMock, MagicMock

from plugins.DicePP.module.persona.proactive.target_selector import TargetSelector
from plugins.DicePP.module.persona.proactive.models import ShareTarget


@pytest.fixture
def mock_data_store():
    store = AsyncMock()
    store.is_user_muted = AsyncMock(return_value=False)
    store.get_top_relationships = AsyncMock(return_value=[])
    store.get_all_group_activities = AsyncMock(return_value=[])
    return store


@pytest.fixture
def bot_config():
    cfg = MagicMock()
    cfg.proactive_always_send_users = []
    cfg.proactive_always_send_groups = []
    return cfg


@pytest.mark.asyncio
async def test_force_users_generated(mock_data_store, bot_config):
    bot_config.proactive_always_send_users = ["u1", "u2"]
    selector = TargetSelector(data_store=mock_data_store, bot_config=bot_config)
    targets = await selector.select_share_targets()
    assert len(targets) == 2
    assert all(t.policy == "force" for t in targets)
    assert {t.user_id for t in targets} == {"u1", "u2"}


@pytest.mark.asyncio
async def test_force_groups_generated(mock_data_store, bot_config):
    bot_config.proactive_always_send_groups = ["g1"]
    selector = TargetSelector(data_store=mock_data_store, bot_config=bot_config)
    targets = await selector.select_share_targets()
    assert len(targets) == 1
    assert targets[0].group_id == "g1"
    assert targets[0].is_group is True
    assert targets[0].policy == "force"


@pytest.mark.asyncio
async def test_force_and_normal_dedup(mock_data_store, bot_config):
    bot_config.proactive_always_send_users = ["u1"]

    rel = MagicMock()
    rel.user_id = "u1"
    rel.group_id = ""
    rel.composite_score = 80
    mock_data_store.get_top_relationships = AsyncMock(return_value=[rel])

    selector = TargetSelector(data_store=mock_data_store, bot_config=bot_config)
    targets = await selector.select_share_targets()
    assert len(targets) == 1
    assert targets[0].policy == "force"


def test_is_force_user(mock_data_store, bot_config):
    bot_config.proactive_always_send_users = ["u1"]
    selector = TargetSelector(data_store=mock_data_store, bot_config=bot_config)
    assert selector.is_force_user("u1") is True
    assert selector.is_force_user("u2") is False


@pytest.mark.asyncio
async def test_normal_high_score_user(mock_data_store, bot_config):
    rel = MagicMock()
    rel.user_id = "u3"
    rel.group_id = ""
    rel.composite_score = 75
    mock_data_store.get_top_relationships = AsyncMock(return_value=[rel])

    selector = TargetSelector(data_store=mock_data_store, bot_config=bot_config)
    targets = await selector.select_share_targets()
    assert len(targets) == 1
    assert targets[0].user_id == "u3"
    assert targets[0].policy == "normal"
    assert targets[0].priority == 175  # 100 + 75


@pytest.mark.asyncio
async def test_normal_medium_score_user(mock_data_store, bot_config):
    rel = MagicMock()
    rel.user_id = "u4"
    rel.group_id = ""
    rel.composite_score = 50
    mock_data_store.get_top_relationships = AsyncMock(return_value=[rel])

    selector = TargetSelector(data_store=mock_data_store, bot_config=bot_config)
    targets = await selector.select_share_targets()
    assert len(targets) == 1
    assert targets[0].user_id == "u4"
    assert targets[0].policy == "normal"
    assert targets[0].priority == 100  # 50 + 50


@pytest.mark.asyncio
async def test_normal_group_activity(mock_data_store, bot_config):
    from plugins.DicePP.module.persona.data.models import GroupActivity
    mock_data_store.get_all_group_activities = AsyncMock(return_value=[
        GroupActivity(group_id="g2", score=70.0)
    ])

    selector = TargetSelector(data_store=mock_data_store, bot_config=bot_config)
    targets = await selector.select_share_targets()
    assert len(targets) == 1
    assert targets[0].group_id == "g2"
    assert targets[0].is_group is True
    assert targets[0].policy == "normal"
    assert targets[0].priority == 70


@pytest.mark.asyncio
async def test_empty_string_in_config_skipped(mock_data_store, bot_config):
    bot_config.proactive_always_send_users = ["", "u1"]
    bot_config.proactive_always_send_groups = ["", "g1"]
    selector = TargetSelector(data_store=mock_data_store, bot_config=bot_config)
    targets = await selector.select_share_targets()
    assert len(targets) == 2
    assert targets[0].user_id == "u1"
    assert targets[1].group_id == "g1"


@pytest.mark.asyncio
async def test_exception_in_normal_path_returns_force_targets(mock_data_store, bot_config):
    bot_config.proactive_always_send_users = ["u1"]
    mock_data_store.get_top_relationships = AsyncMock(side_effect=Exception("db error"))

    selector = TargetSelector(data_store=mock_data_store, bot_config=bot_config)
    targets = await selector.select_share_targets()
    assert len(targets) == 1
    assert targets[0].user_id == "u1"
    assert targets[0].policy == "force"


@pytest.mark.asyncio
async def test_duplicate_force_ids_deduplicated(mock_data_store, bot_config):
    bot_config.proactive_always_send_users = ["u1", "u1", "u2"]
    bot_config.proactive_always_send_groups = ["g1", "g1"]
    selector = TargetSelector(data_store=mock_data_store, bot_config=bot_config)
    targets = await selector.select_share_targets()
    assert len(targets) == 3
    assert sum(1 for t in targets if t.user_id == "u1") == 1
    assert sum(1 for t in targets if t.group_id == "g1") == 1
