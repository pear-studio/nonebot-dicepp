"""ConversationScope 值对象单元测试（阶段 1 · Step 1）。

覆盖：构造入口、群/私聊语义、相等/哈希、frozen 不可变、可作 dict-key / set 成员。
"""

from __future__ import annotations

import pytest

from plugins.DicePP.module.persona.life.conversation_scope import (
    NS_CHAT_GROUP,
    NS_CHAT_PRIVATE,
    NS_LIFE_DM,
    NS_LIFE_CHARACTER,
    ConversationScope,
)


class TestScopeConstruction:
    def test_from_chat_with_group_id_is_group(self):
        scope = ConversationScope.from_chat(user_id="u1", group_id="g1")
        assert scope.namespace == NS_CHAT_GROUP
        assert scope.key == "g1"
        assert scope.is_group
        assert not scope.is_private

    def test_from_chat_without_group_id_is_private(self):
        scope = ConversationScope.from_chat(user_id="u1", group_id="")
        assert scope.namespace == NS_CHAT_PRIVATE
        assert scope.key == "u1"
        assert scope.is_private
        assert not scope.is_group

    def test_for_group_helper(self):
        scope = ConversationScope.for_group("g42")
        assert scope == ConversationScope(NS_CHAT_GROUP, "g42")
        assert scope.is_group

    def test_for_private_helper(self):
        scope = ConversationScope.for_private("u42")
        assert scope == ConversationScope(NS_CHAT_PRIVATE, "u42")
        assert scope.is_private


class TestScopeSemantics:
    def test_equality_same_namespace_key(self):
        assert ConversationScope.for_group("g1") == ConversationScope.for_group("g1")

    def test_group_and_private_differ_on_same_id(self):
        # 关键隔离不变量：群聊 scope 与私聊 scope 即使裸 id 相同也必须不等
        group = ConversationScope.for_group("x")
        private = ConversationScope.for_private("x")
        assert group != private

    def test_different_keys_differ(self):
        assert ConversationScope.for_group("g1") != ConversationScope.for_group("g2")

    def test_hashable_usable_as_dict_key(self):
        cache = {ConversationScope.for_group("g1"): "conv-1"}
        assert cache[ConversationScope.for_group("g1")] == "conv-1"

    def test_usable_in_set_dedup(self):
        s = {
            ConversationScope.for_group("g1"),
            ConversationScope.for_group("g1"),
            ConversationScope.for_private("g1"),
        }
        assert len(s) == 2

    def test_frozen_is_immutable(self):
        scope = ConversationScope.for_group("g1")
        with pytest.raises(Exception):
            scope.key = "g2"  # type: ignore[misc]


class TestLifeScope:
    """A1: Life namespace constants, factory helpers, is_life property."""

    def test_ns_life_dm_constant(self):
        assert NS_LIFE_DM == "life.dm"

    def test_ns_life_character_constant(self):
        assert NS_LIFE_CHARACTER == "life.character"

    def test_for_life_dm(self):
        scope = ConversationScope.for_life_dm("char123")
        assert scope.namespace == NS_LIFE_DM
        assert scope.key == "char123"

    def test_for_life_character(self):
        scope = ConversationScope.for_life_character("char456")
        assert scope.namespace == NS_LIFE_CHARACTER
        assert scope.key == "char456"

    def test_is_life_true_for_dm(self):
        assert ConversationScope.for_life_dm("any").is_life

    def test_is_life_true_for_character(self):
        assert ConversationScope.for_life_character("any").is_life

    def test_is_life_false_for_chat_group(self):
        assert not ConversationScope.for_group("g1").is_life

    def test_is_life_false_for_chat_private(self):
        assert not ConversationScope.for_private("u1").is_life

    def test_is_life_false_from_chat(self):
        assert not ConversationScope.from_chat("u1", "").is_life
        assert not ConversationScope.from_chat("u1", "g1").is_life
