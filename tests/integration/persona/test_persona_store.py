"""
Tests for Persona Phase 4: Cost Control and User Configuration

Covers:
- Quota system (daily limit, exemptions)
- User LLM config (AES encryption, CRUD)
- Roll dice tool
"""
import pytest
from datetime import datetime

from module.persona.data.models import UserLLMConfig, DailyUsage
from module.persona.data.store import PersonaDataStore


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_encryption_key(monkeypatch):
    """设置测试用的加密密钥"""
    monkeypatch.setenv("DICE_PERSONA_SECRET", "test_secret_key_for_encryption_32bytes")
    yield


@pytest.fixture
def sample_user_config():
    """示例用户配置"""
    return UserLLMConfig(
        user_id="U123",
        primary_api_key="sk-test123",
        primary_base_url="https://api.test.com/v1",
        primary_model="gpt-4o",
        auxiliary_api_key="sk-test456",
        auxiliary_base_url="https://api.test.com/v1",
        auxiliary_model="gpt-4o-mini",
    )


# ── Quota System Tests ───────────────────────────────────────────────────────

class TestQuotaSystem:
    """测试配额系统"""

    @pytest.mark.asyncio
    async def test_daily_usage_tracking(self, tmp_path):
        """测试每日用量追踪"""
        import aiosqlite

        async with aiosqlite.connect(":memory:") as persona_db, \
             aiosqlite.connect(":memory:") as core_db:
            store = PersonaDataStore(":memory:", core_db, timezone="Asia/Shanghai")
            store._persona_db = persona_db
            await store.ensure_tables()

            user_id = "U123"
            date = "2024-01-15"

            # 初始用量为 0
            usage = await store.get_daily_usage(user_id, date)
            assert usage == 0

            # 增加用量
            await store.increment_daily_usage(user_id, date)
            await store.increment_daily_usage(user_id, date)

            usage = await store.get_daily_usage(user_id, date)
            assert usage == 2

    @pytest.mark.asyncio
    async def test_daily_usage_separate_dates(self, tmp_path):
        """测试不同日期的用量分开计算"""
        import aiosqlite

        async with aiosqlite.connect(":memory:") as persona_db, \
             aiosqlite.connect(":memory:") as core_db:
            store = PersonaDataStore(":memory:", core_db, timezone="Asia/Shanghai")
            store._persona_db = persona_db
            await store.ensure_tables()

            user_id = "U123"

            # 昨天的用量
            await store.increment_daily_usage(user_id, "2024-01-14")
            await store.increment_daily_usage(user_id, "2024-01-14")

            # 今天的用量
            await store.increment_daily_usage(user_id, "2024-01-15")

            yesterday_usage = await store.get_daily_usage(user_id, "2024-01-14")
            today_usage = await store.get_daily_usage(user_id, "2024-01-15")

            assert yesterday_usage == 2
            assert today_usage == 1

    @pytest.mark.asyncio
    async def test_daily_usage_different_users(self, tmp_path):
        """测试不同用户的用量分开计算"""
        import aiosqlite

        async with aiosqlite.connect(":memory:") as persona_db, \
             aiosqlite.connect(":memory:") as core_db:
            store = PersonaDataStore(":memory:", core_db, timezone="Asia/Shanghai")
            store._persona_db = persona_db
            await store.ensure_tables()

            date = "2024-01-15"

            await store.increment_daily_usage("U123", date)
            await store.increment_daily_usage("U123", date)
            await store.increment_daily_usage("U456", date)

            usage_u123 = await store.get_daily_usage("U123", date)
            usage_u456 = await store.get_daily_usage("U456", date)

            assert usage_u123 == 2
            assert usage_u456 == 1


# ── User Config CRUD Tests ───────────────────────────────────────────────────

class TestUserConfigCRUD:
    """测试用户配置 CRUD 操作"""

    @pytest.mark.asyncio
    async def test_save_and_get_user_config(self, tmp_path, mock_encryption_key, sample_user_config):
        """测试保存和读取用户配置"""
        import aiosqlite

        async with aiosqlite.connect(":memory:") as persona_db, \
             aiosqlite.connect(":memory:") as core_db:
            store = PersonaDataStore(":memory:", core_db, timezone="Asia/Shanghai")
            store._persona_db = persona_db
            await store.ensure_tables()

            # 保存配置
            success = await store.save_user_llm_config(sample_user_config)
            assert success is True

            # 读取配置
            retrieved = await store.get_user_llm_config("U123")
            assert retrieved.user_id == "U123"
            assert retrieved.primary_api_key == "sk-test123"
            assert retrieved.primary_base_url == "https://api.test.com/v1"
            assert retrieved.primary_model == "gpt-4o"

    @pytest.mark.asyncio
    async def test_get_nonexistent_config(self, tmp_path, mock_encryption_key):
        """测试读取不存在的配置"""
        import aiosqlite

        async with aiosqlite.connect(":memory:") as persona_db, \
             aiosqlite.connect(":memory:") as core_db:
            store = PersonaDataStore(":memory:", core_db, timezone="Asia/Shanghai")
            store._persona_db = persona_db
            await store.ensure_tables()

            config = await store.get_user_llm_config("NONEXISTENT")
            assert config is None

    @pytest.mark.asyncio
    async def test_clear_user_config(self, tmp_path, mock_encryption_key, sample_user_config):
        """测试清除用户配置"""
        import aiosqlite

        async with aiosqlite.connect(":memory:") as persona_db, \
             aiosqlite.connect(":memory:") as core_db:
            store = PersonaDataStore(":memory:", core_db, timezone="Asia/Shanghai")
            store._persona_db = persona_db
            await store.ensure_tables()

            # 先保存配置
            await store.save_user_llm_config(sample_user_config)

            # 清除配置
            success = await store.clear_user_llm_config("U123")
            assert success is True

            # 确认已清除
            config = await store.get_user_llm_config("U123")
            assert config is None

    @pytest.mark.asyncio
    async def test_update_user_config(self, tmp_path, mock_encryption_key):
        """测试更新用户配置"""
        import aiosqlite

        async with aiosqlite.connect(":memory:") as persona_db, \
             aiosqlite.connect(":memory:") as core_db:
            store = PersonaDataStore(":memory:", core_db, timezone="Asia/Shanghai")
            store._persona_db = persona_db
            await store.ensure_tables()

            # 初始配置
            config1 = UserLLMConfig(
                user_id="U123",
                primary_api_key="sk-old",
                primary_model="gpt-3.5",
            )
            await store.save_user_llm_config(config1)

            # 更新配置
            config2 = UserLLMConfig(
                user_id="U123",
                primary_api_key="sk-new",
                primary_model="gpt-4",
            )
            await store.save_user_llm_config(config2)

            # 验证更新
            retrieved = await store.get_user_llm_config("U123")
            assert retrieved.primary_api_key == "sk-new"
            assert retrieved.primary_model == "gpt-4"

    @pytest.mark.asyncio
    async def test_save_without_encryption_key(self, tmp_path, monkeypatch):
        """测试没有加密密钥时保存失败"""
        import aiosqlite

        monkeypatch.delenv("DICE_PERSONA_SECRET", raising=False)

        async with aiosqlite.connect(":memory:") as persona_db, \
             aiosqlite.connect(":memory:") as core_db:
            store = PersonaDataStore(":memory:", core_db, timezone="Asia/Shanghai")
            store._persona_db = persona_db
            await store.ensure_tables()

            config = UserLLMConfig(
                user_id="U123",
                primary_api_key="sk-test",
            )

            success = await store.save_user_llm_config(config)
            assert success is False

    @pytest.mark.asyncio
    async def test_get_config_decrypt_failed_without_secret(self, tmp_path):
        """配置已存在但加密数据无效或无密钥时 decrypt_failed=True"""
        import aiosqlite
        from utils.time import wall_now

        async with aiosqlite.connect(":memory:") as persona_db, \
             aiosqlite.connect(":memory:") as core_db:
            store = PersonaDataStore(":memory:", core_db, timezone="Asia/Shanghai")
            store._persona_db = persona_db
            await store.ensure_tables()

            # 直接插入无效的加密数据（模拟有密钥时写入但现无密钥或数据损坏）
            await store._core_db.execute(
                """
                INSERT INTO persona_user_llm_config
                (user_id, primary_api_key_encrypted, primary_base_url, primary_model,
                 auxiliary_api_key_encrypted, auxiliary_base_url, auxiliary_model, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("u1", "garbage_encrypted_data", "", "gpt-4o",
                 None, "", "", wall_now().isoformat()),
            )
            await store._core_db.commit()

            fetched = await store.get_user_llm_config("u1")
            assert fetched is not None
            assert fetched.decrypt_failed is True

    @pytest.mark.asyncio
    async def test_clear_nonexistent_config_returns_true(self, tmp_path):
        """不存在的配置清除 → clear_user_llm_config 返回 True"""
        import aiosqlite

        async with aiosqlite.connect(":memory:") as persona_db, \
             aiosqlite.connect(":memory:") as core_db:
            store = PersonaDataStore(":memory:", core_db, timezone="Asia/Shanghai")
            store._persona_db = persona_db
            await store.ensure_tables()

            result = await store.clear_user_llm_config("u_unknown")
            assert result is True


# ── Roll Dice Tool Tests ─────────────────────────────────────────────────────

# ── R6: 补充测试覆盖 ──────────────────────────────────────────────────────────

class TestWhitelistMembership:
    """测试白名单成员判定"""

    @pytest.mark.asyncio
    async def test_whitelist_user_exempt_from_quota(self, tmp_path, monkeypatch):
        """测试白名单用户豁免配额"""
        import aiosqlite

        async with aiosqlite.connect(":memory:") as persona_db, \
             aiosqlite.connect(":memory:") as core_db:
            store = PersonaDataStore(":memory:", core_db, timezone="Asia/Shanghai")
            store._persona_db = persona_db
            await store.ensure_tables()

            # 添加用户到白名单
            await store.add_user_to_whitelist("WHITELISTED_USER")

            # 白名单用户应被识别
            assert await store.is_user_whitelisted("WHITELISTED_USER") is True
            # 非白名单用户不应被识别
            assert await store.is_user_whitelisted("REGULAR_USER") is False

    @pytest.mark.asyncio
    async def test_whitelist_group_exempt_from_quota(self, tmp_path):
        """测试白名单群豁免配额"""
        import aiosqlite

        async with aiosqlite.connect(":memory:") as persona_db, \
             aiosqlite.connect(":memory:") as core_db:
            store = PersonaDataStore(":memory:", core_db, timezone="Asia/Shanghai")
            store._persona_db = persona_db
            await store.ensure_tables()

            # 添加群到白名单
            await store.add_group_to_whitelist("WHITELISTED_GROUP")

            # 群在白名单中应豁免
            assert await store.is_group_whitelisted("WHITELISTED_GROUP") is True
            assert await store.is_group_whitelisted("REGULAR_GROUP") is False

