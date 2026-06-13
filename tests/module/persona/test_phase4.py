"""
Phase 4: Persona 用户配置保存与读取

验证 UserLLMConfig 的保存、读取、清除完整流程。
"""
import os
import pytest
from datetime import datetime

from plugins.DicePP.module.persona.data.models import UserLLMConfig


@pytest.fixture
async def temp_db():
    """创建内存 PersonaDataStore 实例（与 unit/persona/conftest.py 相同的模式）"""
    import aiosqlite
    from plugins.DicePP.module.persona.data.store import PersonaDataStore

    async with aiosqlite.connect(":memory:") as persona_db, \
         aiosqlite.connect(":memory:") as core_db:
        await persona_db.execute("PRAGMA foreign_keys=ON")
        store = PersonaDataStore(":memory:", core_db)
        store._persona_db = persona_db
        await store.ensure_tables()
        yield store


class TestUserConfigSaveGet:
    """验证用户配置的保存和读取完整流程"""

    # ── 无加密密钥时的行为 ─────────────────────────────────────

    @pytest.mark.asyncio
    async def test_save_config_without_secret_returns_false(self, temp_db):
        """无 DICE_PERSONA_SECRET 时 save_user_llm_config 返回 False"""
        config = UserLLMConfig(
            user_id="u1",
            primary_api_key="sk-test-key",
            primary_model="gpt-4o",
        )
        success = await temp_db.save_user_llm_config(config)
        assert success is False

    @pytest.mark.asyncio
    async def test_get_config_without_secret_returns_decrypt_failed(self, temp_db):
        """配置已存在但无 DICE_PERSONA_SECRET → decrypt_failed=True"""
        # 先通过原始 SQL 插入加密数据（模拟有密钥时写入的数据）
        await temp_db._core_db.execute(
            """
            INSERT INTO persona_user_llm_config
            (user_id, primary_api_key_encrypted, primary_base_url, primary_model,
             auxiliary_api_key_encrypted, auxiliary_base_url, auxiliary_model, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("u1", "garbage_encrypted_data", "", "gpt-4o",
             None, "", "", datetime.now().isoformat()),
        )
        await temp_db._core_db.commit()

        fetched = await temp_db.get_user_llm_config("u1")
        assert fetched is not None
        assert fetched.decrypt_failed is True

    @pytest.mark.asyncio
    async def test_get_nonexistent_config_returns_none(self, temp_db):
        """不存在的用户 → get_user_llm_config 返回 None"""
        fetched = await temp_db.get_user_llm_config("u_unknown")
        assert fetched is None

    @pytest.mark.asyncio
    async def test_clear_nonexistent_config_returns_true(self, temp_db):
        """不存在的配置清除 → clear_user_llm_config 返回 True"""
        result = await temp_db.clear_user_llm_config("u_unknown")
        assert result is True

    # ── 有加密密钥时的完整流程 ─────────────────────────────────

    @pytest.mark.asyncio
    async def test_save_and_get_config_with_secret(self, temp_db, monkeypatch):
        """设置 DICE_PERSONA_SECRET → 完整保存和读取流程"""
        monkeypatch.setenv("DICE_PERSONA_SECRET", "test-secret-key-for-testing")

        config = UserLLMConfig(
            user_id="u1",
            primary_api_key="sk-real-key-12345",
            primary_base_url="https://api.example.com",
            primary_model="gpt-4o",
            auxiliary_api_key="sk-aux-key",
            auxiliary_base_url="https://aux.example.com",
            auxiliary_model="claude-3",
        )
        success = await temp_db.save_user_llm_config(config)
        assert success is True

        fetched = await temp_db.get_user_llm_config("u1")
        assert fetched is not None
        assert fetched.user_id == "u1"
        assert fetched.primary_api_key == "sk-real-key-12345"
        assert fetched.primary_base_url == "https://api.example.com"
        assert fetched.primary_model == "gpt-4o"
        assert fetched.auxiliary_api_key == "sk-aux-key"
        assert fetched.auxiliary_base_url == "https://aux.example.com"
        assert fetched.auxiliary_model == "claude-3"
        assert fetched.decrypt_failed is False

    @pytest.mark.asyncio
    async def test_save_overwrites_existing_config(self, temp_db, monkeypatch):
        """重复保存覆盖已有配置"""
        monkeypatch.setenv("DICE_PERSONA_SECRET", "test-secret-key-for-testing")

        config1 = UserLLMConfig(
            user_id="u1",
            primary_api_key="key-v1",
            primary_model="gpt-4o",
        )
        await temp_db.save_user_llm_config(config1)

        config2 = UserLLMConfig(
            user_id="u1",
            primary_api_key="key-v2",
            primary_model="gpt-4o-turbo",
        )
        await temp_db.save_user_llm_config(config2)

        fetched = await temp_db.get_user_llm_config("u1")
        assert fetched.primary_api_key == "key-v2"
        assert fetched.primary_model == "gpt-4o-turbo"

    @pytest.mark.asyncio
    async def test_clear_config_after_save(self, temp_db, monkeypatch):
        """保存后清除 → 读取返回 None"""
        monkeypatch.setenv("DICE_PERSONA_SECRET", "test-secret-key-for-testing")

        config = UserLLMConfig(
            user_id="u1",
            primary_api_key="sk-key-to-clear",
            primary_model="gpt-4o",
        )
        await temp_db.save_user_llm_config(config)

        cleared = await temp_db.clear_user_llm_config("u1")
        assert cleared is True

        fetched = await temp_db.get_user_llm_config("u1")
        assert fetched is None

    @pytest.mark.asyncio
    async def test_different_users_isolated(self, temp_db, monkeypatch):
        """不同用户的配置互相隔离"""
        monkeypatch.setenv("DICE_PERSONA_SECRET", "test-secret-key-for-testing")

        config_a = UserLLMConfig(user_id="u1", primary_api_key="key-a", primary_model="gpt-4o")
        config_b = UserLLMConfig(user_id="u2", primary_api_key="key-b", primary_model="claude-3")

        await temp_db.save_user_llm_config(config_a)
        await temp_db.save_user_llm_config(config_b)

        fetched_a = await temp_db.get_user_llm_config("u1")
        fetched_b = await temp_db.get_user_llm_config("u2")

        assert fetched_a.primary_api_key == "key-a"
        assert fetched_b.primary_api_key == "key-b"
        assert fetched_a.primary_model == "gpt-4o"
        assert fetched_b.primary_model == "claude-3"
