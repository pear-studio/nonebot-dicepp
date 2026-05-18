"""
单元测试: Persona 数据模型
"""

from datetime import datetime

import pytest


from plugins.DicePP.module.persona.data.models import (
    ScoreDeltas,
    RelationshipState,
    UserProfile,
)
from plugins.DicePP.core.config.pydantic_models import PersonaConfig


class TestScoreDeltas:
    """测试 ScoreDeltas"""

    def test_default_values(self):
        """测试默认值"""
        deltas = ScoreDeltas()
        assert deltas.intimacy == 0.0
        assert deltas.passion == 0.0
        assert deltas.trust == 0.0
        assert deltas.secureness == 0.0

    def test_clamp(self):
        """测试限制范围"""
        deltas = ScoreDeltas(intimacy=10, passion=-10, trust=3, secureness=-3)
        clamped = deltas.clamp(-5.0, 5.0)
        
        assert clamped.intimacy == 5.0
        assert clamped.passion == -5.0
        assert clamped.trust == 3.0
        assert clamped.secureness == -3.0


class TestRelationshipState:
    """测试 RelationshipState"""

    def test_default_values(self):
        """测试默认值"""
        rel = RelationshipState(user_id="test_user")
        assert rel.user_id == "test_user"
        assert rel.intimacy == 40.0
        assert rel.passion == 40.0
        assert rel.trust == 40.0
        assert rel.secureness == 40.0
        assert rel.last_miss_sent_at is None
        assert rel.peak_stage == 0

    def test_composite_score(self):
        """测试综合分数计算"""
        rel = RelationshipState(
            user_id="test",
            intimacy=50,  # 权重 0.3
            passion=40,   # 权重 0.2
            trust=60,     # 权重 0.3
            secureness=70 # 权重 0.2
        )
        # 50*0.3 + 40*0.2 + 60*0.3 + 70*0.2 = 15 + 8 + 18 + 14 = 55
        assert rel.composite_score == 55.0

    def test_get_warmth_level(self):
        """测试温暖度等级（5段）"""
        labels = ["冷淡", "疏远", "友好", "默契", "亲密"]

        # 边界测试
        test_cases = [
            (0, 0, "冷淡"),
            (19.99, 0, "冷淡"),
            (20, 1, "疏远"),
            (39.99, 1, "疏远"),
            (40, 2, "友好"),
            (60, 3, "默契"),
            (80, 4, "亲密"),
            (100, 4, "亲密"),
        ]
        for score, expected_level, expected_label in test_cases:
            rel = RelationshipState(
                user_id="test", intimacy=score, passion=score, trust=score, secureness=score
            )
            level, label = rel.get_warmth_level(labels)
            assert level == expected_level, f"score={score}: expected level {expected_level}, got {level}"
            assert label == expected_label, f"score={score}: expected label {expected_label}, got {label}"

    def test_apply_deltas(self):
        """测试应用好感度变化"""
        rel = RelationshipState(user_id="test", intimacy=40, passion=40, trust=40, secureness=40)
        deltas = ScoreDeltas(intimacy=10, passion=-5, trust=0, secureness=100)

        rel.apply_deltas(deltas, updated_at=datetime(2026, 1, 1, 12, 0, 0))

        assert rel.intimacy == 50.0
        assert rel.passion == 35.0
        assert rel.trust == 40.0
        assert rel.secureness == 100.0  # 上限是100
        # peak_stage 应随 apply_deltas 自动更新
        assert rel.peak_stage == 2  # composite=56.5 -> 友好(2)

    def test_peak_stage_never_decreases(self):
        """peak_stage 随分数下降不应回退"""
        rel = RelationshipState(
            user_id="test", intimacy=65, passion=65, trust=65, secureness=65
        )
        # composite=65 -> 默契(3)，peak_stage 应升为 3
        rel.apply_deltas(ScoreDeltas(), updated_at=datetime(2026, 1, 1, 12, 0, 0))
        assert rel.peak_stage == 3

        # 分数下降回 40（友好），peak_stage 应保持 3
        rel.intimacy = 40
        rel.passion = 40
        rel.trust = 40
        rel.secureness = 40
        rel.apply_deltas(ScoreDeltas(), updated_at=datetime(2026, 1, 1, 13, 0, 0))
        assert rel.peak_stage == 3

    def test_apply_deltas_bounds(self):
        """测试好感度边界"""
        rel = RelationshipState(user_id="test", intimacy=95)
        deltas = ScoreDeltas(intimacy=10)

        rel.apply_deltas(deltas, updated_at=datetime(2026, 1, 1, 12, 0, 0))
        assert rel.intimacy == 100.0  # 不超过100

        rel2 = RelationshipState(user_id="test", intimacy=5)
        deltas2 = ScoreDeltas(intimacy=-10)

        rel2.apply_deltas(deltas2, updated_at=datetime(2026, 1, 1, 12, 0, 0))
        assert rel2.intimacy == 0.0  # 不低于0


class TestUserProfile:
    """测试 UserProfile"""

    def test_merge_facts(self):
        """测试合并事实"""
        profile = UserProfile(user_id="test", facts={"name": "张三", "hobbies": ["读书"]})
        
        new_facts = {
            "name": "李四",  # 不应覆盖已有
            "age": 25,       # 新增
            "hobbies": ["游戏", "读书"]  # 合并列表，去重
        }
        
        profile.merge_facts(new_facts, updated_at=datetime(2026, 1, 1, 12, 0, 0))

        assert profile.facts["name"] == "张三"  # 保持原值
        assert profile.facts["age"] == 25      # 新增
        assert set(profile.facts["hobbies"]) == {"读书", "游戏"}  # 合并去重


class TestPersonaConfig:
    """测试 PersonaConfig"""

    def test_default_values(self):
        """测试配置默认值"""
        config = PersonaConfig()

        assert config.enabled == False
        assert config.character_name == "default"
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
            PersonaConfig(
                segment_soft_limit=150,
                segment_hard_limit=120,
            )
        assert "segment_soft_limit" in str(exc_info.value)

    def test_segment_positive_constraints(self):
        """正数字段边界校验"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            PersonaConfig(segment_max_chars=0)
        with pytest.raises(ValidationError):
            PersonaConfig(segment_count_max=0)
        with pytest.raises(ValidationError):
            PersonaConfig(segment_max_delay=0)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
