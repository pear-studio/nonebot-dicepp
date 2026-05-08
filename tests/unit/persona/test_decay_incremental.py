"""时间衰减：批处理与对话路径共用增量水位，避免对同一空闲窗口重复扣减。"""


from datetime import datetime, timedelta


from plugins.DicePP.module.persona.data.models import RelationshipState
from plugins.DicePP.module.persona.game.decay import DecayCalculator, DecayConfig


def test_decay_incremental_same_moment_no_double_apply():
    t0 = datetime(2026, 1, 1, 12, 0, 0)
    t_batch = datetime(2026, 1, 5, 12, 0, 0)
    calc = DecayCalculator(
        DecayConfig(
            enabled=True,
            grace_period_hours=0,
            decay_rate_per_hour=1.0,
            daily_cap=100.0,
        ),
        timezone_name="UTC",
    )
    rel = RelationshipState(
        user_id="u1",
        group_id="",
        intimacy=80.0,
        passion=80.0,
        trust=80.0,
        secureness=80.0,
        last_interaction_at=t0,
        last_relationship_decay_applied_at=None,
        last_miss_sent_at=t0,
        peak_stage=0,
    )
    d1, _ = calc.calculate_decay(rel, now=t_batch)
    assert d1.intimacy < -0.01
    rel.apply_deltas(d1, updated_at=t_batch)
    rel.last_relationship_decay_applied_at = t_batch

    d2, _ = calc.calculate_decay(rel, now=t_batch)
    assert abs(d2.intimacy) < 0.01


def test_decay_incremental_after_batch_user_message_only_new_idle():
    t0 = datetime(2026, 1, 1, 12, 0, 0)
    t_batch = datetime(2026, 1, 5, 12, 0, 0)
    t_msg = datetime(2026, 1, 6, 12, 0, 0)
    calc = DecayCalculator(
        DecayConfig(
            enabled=True,
            grace_period_hours=0,
            decay_rate_per_hour=1.0,
            daily_cap=50.0,
        ),
        timezone_name="UTC",
    )
    rel = RelationshipState(
        user_id="u1",
        group_id="",
        intimacy=80.0,
        passion=80.0,
        trust=80.0,
        secureness=80.0,
        last_interaction_at=t0,
        last_relationship_decay_applied_at=None,
        last_miss_sent_at=t0,
        peak_stage=0,
    )
    d_batch, _ = calc.calculate_decay(rel, now=t_batch)
    rel.apply_deltas(d_batch, updated_at=t_batch)
    rel.last_relationship_decay_applied_at = t_batch

    d_chat, _ = calc.calculate_decay(rel, now=t_msg)
    # 仅 1 天增量：约 24h * 1.0 = 24，受 daily_cap 50
    # 但 batch 后 peak_stage 自动升为 1（floor=20），当前 composite=30，allowed_decay=10
    assert d_chat.intimacy == -10.0


def test_decay_after_miss_accounts_full_idle():
    """想念后首次衰减按 last_interaction_at 起算，补扣全部 idle（受 daily_cap 限制）"""
    t0 = datetime(2026, 1, 1, 12, 0, 0)
    t_miss = datetime(2026, 1, 3, 12, 0, 0)
    calc = DecayCalculator(
        DecayConfig(
            enabled=True,
            grace_period_hours=0,
            decay_rate_per_hour=1.0,
            daily_cap=100.0,
        ),
        timezone_name="UTC",
    )
    rel = RelationshipState(
        user_id="u1",
        group_id="",
        intimacy=80.0,
        passion=80.0,
        trust=80.0,
        secureness=80.0,
        last_interaction_at=t0,
        last_relationship_decay_applied_at=None,
        last_miss_sent_at=None,
        peak_stage=0,
    )
    # 想念前：开关关闭，不衰减
    d1, reason1 = calc.calculate_decay(rel, now=t_miss)
    assert abs(d1.intimacy) < 0.01
    assert "开关关闭" in reason1

    # 想念发出：打开开关
    rel.last_miss_sent_at = t_miss

    # 想念后首次衰减：按 last_interaction_at 起算，补扣 48h idle
    d2, reason2 = calc.calculate_decay(rel, now=t_miss)
    assert d2.intimacy == -48.0
    assert "增量可衰减 48.00h" in reason2


def test_decay_switch_off_then_on():
    """开关型衰减：想念前不衰减，想念后正常衰减，用户回应后重置"""
    t0 = datetime(2026, 1, 1, 12, 0, 0)
    t_miss = datetime(2026, 1, 3, 12, 0, 0)
    t_after_miss = datetime(2026, 1, 4, 12, 0, 0)
    calc = DecayCalculator(
        DecayConfig(
            enabled=True,
            grace_period_hours=0,
            decay_rate_per_hour=1.0,
            daily_cap=100.0,
        ),
        timezone_name="UTC",
    )
    rel = RelationshipState(
        user_id="u1",
        group_id="",
        intimacy=80.0,
        passion=80.0,
        trust=80.0,
        secureness=80.0,
        last_interaction_at=t0,
        last_relationship_decay_applied_at=None,
        last_miss_sent_at=None,  # 开关关闭
        peak_stage=0,
    )
    # 想念前：开关关闭，不衰减
    d1, reason1 = calc.calculate_decay(rel, now=t_miss)
    assert abs(d1.intimacy) < 0.01
    assert "开关关闭" in reason1

    # 想念发出：打开开关
    rel.last_miss_sent_at = t_miss

    # 想念后：正常衰减
    d2, reason2 = calc.calculate_decay(rel, now=t_after_miss)
    assert d2.intimacy < -20.0

    # 用户回应：重置开关和水位
    rel.last_miss_sent_at = None
    rel.last_relationship_decay_applied_at = None
    rel.last_interaction_at = t_after_miss

    d3, reason3 = calc.calculate_decay(rel, now=t_after_miss)
    assert abs(d3.intimacy) < 0.01
    assert "开关关闭" in reason3
