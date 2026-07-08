"""
单元测试: Persona 数据模型 (三维好感度)
"""
from datetime import datetime
import pytest
from plugins.DicePP.module.persona.data.models import ScoreDeltas, RelationshipState, UserProfile, DEFAULT_RELATION_LABELS
from plugins.DicePP.module.persona.character.models import Character, PersonaExtensions
from plugins.DicePP.core.config.pydantic_models import PersonaConfig

class TestScoreDeltas:
    """测试 ScoreDeltas (intimacy + reputation_delta)"""

    def test_default_values(self):
        """测试默认值"""
        deltas = ScoreDeltas()
        assert deltas.intimacy == 0.0
        assert deltas.reputation_delta == 0.0

    @pytest.mark.parametrize('intimacy_in,rep_in,expected_intimacy,expected_rep', [(10, -40, 5.0, -30.0), (3, -5, 3.0, -5.0), (-2, 0, -2.0, 0.0)])
    def test_clamp(self, intimacy_in, rep_in, expected_intimacy, expected_rep):
        """测试 ScoreDeltas.clamp 边界条件"""
        deltas = ScoreDeltas(intimacy=intimacy_in, reputation_delta=rep_in)
        clamped = deltas.clamp()
        assert clamped.intimacy == expected_intimacy
        assert clamped.reputation_delta == expected_rep

class TestRelationshipState:
    """测试 RelationshipState (三维模型)"""

    def test_default_values(self):
        """测试默认值"""
        rel = RelationshipState(user_id='test_user')
        assert rel.user_id == 'test_user'
        assert rel.familiarity == 0.0
        assert rel.peak_familiarity == 0.0
        assert rel.intimacy == 0.0
        assert rel.peak_intimacy == 0.0
        assert rel.reputation == 100.0
        assert rel.last_miss_sent_at is None

    def test_composite_score(self):
        """测试综合分数计算: composite = familiarity × 0.6 + intimacy × 0.4"""
        rel = RelationshipState(user_id='test', familiarity=50, intimacy=50)
        assert rel.composite_score == 50.0
        rel2 = RelationshipState(user_id='test2', familiarity=80, intimacy=10)
        assert rel2.composite_score == 52.0

    def test_get_relation_level(self):
        """测试关系等级（5段）"""
        labels = ['冷淡', '疏远', '友好', '默契', '亲密']
        test_cases = [(0, 0, '冷淡'), (19.99, 0, '冷淡'), (20, 1, '疏远'), (39.99, 1, '疏远'), (40, 2, '友好'), (60, 3, '默契'), (80, 4, '亲密'), (100, 4, '亲密')]
        for score, expected_level, expected_label in test_cases:
            rel = RelationshipState(user_id='test', familiarity=score, intimacy=score)
            level, label = rel.get_relation_level(labels)
            assert level == expected_level, f'score={score}: expected level {expected_level}, got {level}'
            assert label == expected_label, f'score={score}: expected label {expected_label}, got {label}'

    def test_apply_deltas(self):
        """测试应用亲密度变化"""
        rel = RelationshipState(user_id='test', intimacy=40.0, reputation=80.0)
        deltas = ScoreDeltas(intimacy=10, reputation_delta=-20)
        rel.apply_deltas(deltas, updated_at=datetime(2026, 1, 1, 12, 0, 0))
        assert rel.intimacy == 50.0
        assert rel.reputation == 60.0
        assert rel.peak_intimacy == 50.0

    def test_peak_intimacy_never_decreases(self):
        """peak_intimacy 不会因衰减而下降"""
        rel = RelationshipState(user_id='test', intimacy=65.0, peak_intimacy=65.0)
        deltas = ScoreDeltas(intimacy=-10)
        rel.apply_deltas(deltas, updated_at=datetime(2026, 1, 1, 12, 0, 0))
        assert rel.intimacy == 55.0
        assert rel.peak_intimacy == 65.0

    def test_apply_deltas_bounds(self):
        """测试好感度边界"""
        rel = RelationshipState(user_id='test', intimacy=95)
        deltas = ScoreDeltas(intimacy=10)
        rel.apply_deltas(deltas, updated_at=datetime(2026, 1, 1, 12, 0, 0))
        assert rel.intimacy == 100.0
        rel2 = RelationshipState(user_id='test', intimacy=5, reputation=5)
        deltas2 = ScoreDeltas(intimacy=-10, reputation_delta=-10)
        rel2.apply_deltas(deltas2, updated_at=datetime(2026, 1, 1, 12, 0, 0))
        assert rel2.intimacy == 0.0
        assert rel2.reputation == 0.0

    def test_apply_familiarity_delta(self):
        """测试应用熟悉度增量"""
        rel = RelationshipState(user_id='test', familiarity=10.0)
        rel.apply_familiarity_delta(5.0, updated_at=datetime(2026, 1, 1, 12, 0, 0))
        assert rel.familiarity == 15.0
        assert rel.peak_familiarity == 15.0
        rel.apply_familiarity_delta(-3.0, updated_at=datetime(2026, 1, 1, 13, 0, 0))
        assert rel.familiarity == 12.0
        assert rel.peak_familiarity == 15.0

    @pytest.mark.parametrize('delta_in,expected_delta,initial_rep,expected_rep', [(10, 0.0, 100.0, 100.0), (-5, -5.0, 50.0, 45.0), (5, 0.0, 99.0, 99.0)])
    def test_reputation_bounds(self, delta_in, expected_delta, initial_rep, expected_rep):
        """测试信誉边界：clamp 后 apply_deltas 结果正确"""
        deltas = ScoreDeltas(reputation_delta=delta_in)
        clamped = deltas.clamp()
        assert clamped.reputation_delta == expected_delta
        rel = RelationshipState(user_id='test', reputation=initial_rep)
        rel.apply_deltas(clamped, updated_at=datetime(2026, 1, 1, 12, 0, 0))
        assert rel.reputation == expected_rep

class TestUserProfile:
    """测试 UserProfile"""

    def test_merge_facts(self):
        """测试合并事实"""
        profile = UserProfile(user_id='test', facts={'name': '张三', 'hobbies': ['读书']})
        new_facts = {'name': '李四', 'age': 25, 'hobbies': ['游戏', '读书']}
        profile.merge_facts(new_facts, updated_at=datetime(2026, 1, 1, 12, 0, 0))
        assert profile.facts['name'] == '张三'
        assert profile.facts['age'] == 25
        assert set(profile.facts['hobbies']) == {'读书', '游戏'}

class TestPersonaConfig:
    """测试 PersonaConfig"""

    def test_default_values(self):
        """测试配置默认值"""
        config = PersonaConfig()
        assert config.enabled == False
        assert config.whitelist_enabled == True
        assert config.providers == {}
        assert config.max_concurrent_requests == 2
        assert config.daily_limit == 20

    def test_segment_defaults(self):
        """测试分段回复配置默认值"""
        config = PersonaConfig()
        assert config.segment_enabled is True
        assert config.segment_target_chars == 30
        assert config.segment_max_chars == 80
        assert config.segment_soft_limit == 100
        assert config.segment_hard_limit == 120
        assert config.segment_count_max == 10
        assert config.segment_max_delay == 10.0
        assert config.segment_round_callbacks_max == 3

    def test_segment_soft_limit_must_not_exceed_hard(self):
        """soft_limit > hard_limit 时构造应抛 ValidationError"""
        from pydantic import ValidationError
        with pytest.raises(ValidationError) as exc_info:
            PersonaConfig(segment_soft_limit=150, segment_hard_limit=120)
        assert 'segment_soft_limit' in str(exc_info.value)

    def test_segment_positive_constraints(self):
        """正数字段边界校验"""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            PersonaConfig(segment_max_chars=0)
        with pytest.raises(ValidationError):
            PersonaConfig(segment_count_max=0)
        with pytest.raises(ValidationError):
            PersonaConfig(segment_max_delay=0)

    def test_extra_ignore(self):
        """旧配置字段应被忽略（extra='ignore'）"""
        config = PersonaConfig(decay_rate_per_hour=0.5, decay_daily_cap=5.0, relationship_refuse_prob_base=0.5, relationship_refuse_prob_max=0.9)
        assert config.enabled == False

    # ── from_persona 消费端字段完整性 ──
    # 每个 from_persona 方法直接访问 persona.<field>，若字段未在 PersonaConfig
    # 中声明，pydantic 不会生成对应属性，导致 AttributeError。
    # 这些测试确保新增字段不会因 PersonaConfig 遗漏而崩溃。

    _FROMPERSONA_FIELDS = [
        # DecayConfig.from_persona
        "decay_enabled", "decay_grace_period_hours", "decay_familiarity_half_life_days",
        "decay_intimacy_half_life_days", "decay_floor_ratio",
        # ChatConfig.from_persona
        "max_history_turns", "max_history_tokens", "max_diary_context_chars",
        "timezone", "lore_token_budget", "tools_max_rounds",
        "relationship_refuse_enabled", "reputation_refuse_threshold",
        "scoring_interval", "max_messages", "group_max_age_minutes",
        "group_context_budget_tokens", "group_max_messages",
        "group_single_message_max_tokens", "segment_target_chars",
        "segment_max_chars", "segment_soft_limit", "segment_hard_limit",
        "segment_count_max", "segment_max_delay", "segment_round_callbacks_max",
        "private_session_gap_seconds", "group_session_gap_seconds",
        "private_session_token_budget", "group_session_token_budget",
        # ProactiveConfig.from_persona
        "proactive_enabled", "proactive_min_interval_hours", "proactive_max_shares",
        "proactive_share_time_window_minutes", "proactive_event_share_threshold",
        "proactive_miss_enabled", "proactive_miss_min_hours", "proactive_miss_min_score",
        "proactive_share_message_concurrent", "proactive_share_max_chars",
        "proactive_share_context_history_limit",
        # CharacterLifeConfig.from_persona
        "character_life_enabled", "character_life_jitter_minutes",
        "character_life_min_event_interval_minutes", "character_life_chain_max_depth",
        "character_life_chain_force_extend_once_prob", "character_life_default_energy",
        "character_life_default_mood", "character_life_default_health",
        "character_life_recovery_energy",
        "story_deck_max_injection", "story_deck_max_entries",
        "front_max_campaign", "front_max_adventure", "threads_per_front",
        "sa_max_rounds",
        # LifeConfig.from_persona
        "proactive_event_share_delay_min", "proactive_event_share_delay_max",
        "trace_enabled", "trace_max_age_days", "score_history_max_age_days",
        "scoring_failures_max_age_days", "daily_events_keep_days", "diary_keep_days",
    ]

    def test_from_persona_fields_exist(self):
        """PersonaConfig 必须声明所有 from_persona 消费端访问的字段"""
        config = PersonaConfig()
        missing = []
        for field in self._FROMPERSONA_FIELDS:
            if not hasattr(config, field):
                missing.append(field)
        assert missing == [], (
            f"PersonaConfig 缺少 {len(missing)} 个 from_persona 消费端需要的字段: {missing}"
        )

    def test_from_persona_no_attribute_error(self):
        """所有 from_persona 调用 PersonaConfig() 不应抛 AttributeError"""
        from plugins.DicePP.module.persona.life.character_life import CharacterLifeConfig
        from plugins.DicePP.module.persona.chat.chat_config import ChatConfig
        from plugins.DicePP.module.persona.life.proactive_config import ProactiveConfig
        from plugins.DicePP.module.persona.life.simulator import LifeConfig
        from plugins.DicePP.module.persona.game.decay import DecayConfig

        cfg = PersonaConfig()
        for factory in [CharacterLifeConfig, ChatConfig, ProactiveConfig, LifeConfig, DecayConfig]:
            try:
                result = factory.from_persona(cfg)
                assert result is not None, f"{factory.__name__}.from_persona() returned None"
            except AttributeError as e:
                pytest.fail(f"{factory.__name__}.from_persona(PersonaConfig()) raised AttributeError: {e}")

class TestRelationLevel:
    """测试关系等级判定"""

    def test_relation_level_cold(self):
        """composite < 20 应为冷淡区间（0）"""
        rel = RelationshipState(user_id='test', familiarity=5.0, intimacy=5.0)
        ext = PersonaExtensions()
        char = Character(name='Test', extensions=ext)
        relation_level, label = rel.get_relation_level(char.get_relation_labels())
        assert relation_level == 0, f'Expected 0 (cold), got {relation_level}'

    def test_relation_level_distant(self):
        """composite 30 应在疏远区间（1）"""
        rel = RelationshipState(user_id='test', familiarity=30.0, intimacy=30.0)
        ext = PersonaExtensions()
        char = Character(name='Test', extensions=ext)
        relation_level, label = rel.get_relation_level(char.get_relation_labels())
        assert relation_level == 1, f'Expected 1 (distant), got {relation_level}'

    def test_default_relation_labels(self):
        """测试默认关系标签"""
        assert DEFAULT_RELATION_LABELS == ['冷淡', '疏远', '友好', '默契', '亲密']

class TestCharacterState:
    """测试 CharacterState 模型 -- extra='ignore' 配置"""

    def test_extra_ignore(self):
        """CharacterState 接受额外字段时不报错"""
        from plugins.DicePP.module.persona.data.models import CharacterState
        state = CharacterState(energy=80, unknown_field='should be ignored', legacy_extra={'old_key': 'old_value'})
        assert state.energy == 80

    def test_default_values(self):
        """默认值验证"""
        from plugins.DicePP.module.persona.data.models import CharacterState
        state = CharacterState()
        assert state.energy is None
        assert state.mood is None
        assert state.health is None

    def test_extra_ignore_does_not_store_unknown(self):
        """extra 字段不会出现在 model_dump 中"""
        from plugins.DicePP.module.persona.data.models import CharacterState
        state = CharacterState(energy=80, extra_field='ignored')
        dumped = state.model_dump()
        assert 'extra_field' not in dumped
        assert dumped['energy'] == 80
if __name__ == '__main__':
    pytest.main([__file__, '-v'])