"""
单元测试: Persona 数据模型
"""

from datetime import datetime

import pytest


from plugins.DicePP.module.persona.data.models import (
    ScoreDeltas,
    RelationshipState,
    UserProfile,
    ModelTier,
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
        assert rel.intimacy == 30.0
        assert rel.passion == 30.0
        assert rel.trust == 30.0
        assert rel.secureness == 30.0

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
        """测试温暖度等级"""
        labels = ["厌倦", "冷淡", "疏远", "友好", "亲近", "亲密"]
        
        rel = RelationshipState(user_id="test", intimacy=5, passion=5, trust=5, secureness=5)
        level, label = rel.get_warmth_level(labels)
        assert level == 0
        assert label == "厌倦"
        
        rel = RelationshipState(user_id="test", intimacy=50, passion=50, trust=50, secureness=50)
        level, label = rel.get_warmth_level(labels)
        assert level == 3
        assert label == "友好"
        
        rel = RelationshipState(user_id="test", intimacy=90, passion=90, trust=90, secureness=90)
        level, label = rel.get_warmth_level(labels)
        assert level == 5
        assert label == "亲密"

    def test_apply_deltas(self):
        """测试应用好感度变化"""
        rel = RelationshipState(user_id="test", intimacy=30, passion=30, trust=30, secureness=30)
        deltas = ScoreDeltas(intimacy=10, passion=-5, trust=0, secureness=100)
        
        rel.apply_deltas(deltas, updated_at=datetime(2026, 1, 1, 12, 0, 0))

        assert rel.intimacy == 40.0
        assert rel.passion == 25.0
        assert rel.trust == 30.0
        assert rel.secureness == 100.0  # 上限是100

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
        assert config.primary_model == "gpt-4o"
        assert config.auxiliary_model == "gpt-4o-mini"
        assert config.max_concurrent_requests == 2
        assert config.daily_limit == 20

    def test_model_tier_enum(self):
        """测试模型层级枚举"""
        assert ModelTier.PRIMARY == "primary"
        assert ModelTier.AUXILIARY == "auxiliary"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
