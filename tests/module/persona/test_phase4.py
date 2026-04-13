"""
Tests for Persona Phase 4: Cost Control and User Configuration

Covers:
- Quota system (daily limit, exemptions)
- User LLM config (AES encryption, CRUD)
- Roll dice tool
"""
import os
import sys
import json
import pytest
from datetime import datetime
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent.parent / "src" / "plugins" / "DicePP"
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

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


# ── AES Encryption Tests ─────────────────────────────────────────────────────

class TestAESEncryption:
    """测试 AES 加密解密功能"""

    def test_encrypt_decrypt_roundtrip(self, mock_encryption_key):
        """测试加密解密往返"""
        original = "sk-test-api-key-12345"
        encrypted = PersonaDataStore.encrypt_api_key(original)

        assert encrypted is not None
        assert encrypted != original

        decrypted = PersonaDataStore.decrypt_api_key(encrypted)
        assert decrypted == original

    def test_encrypt_empty_string(self, mock_encryption_key):
        """测试空字符串加密"""
        result = PersonaDataStore.encrypt_api_key("")
        assert result == ""

    def test_decrypt_empty_string(self, mock_encryption_key):
        """测试空字符串解密"""
        result = PersonaDataStore.decrypt_api_key("")
        assert result == ""

    def test_encrypt_without_key(self, monkeypatch):
        """测试没有密钥时加密返回 None"""
        monkeypatch.delenv("DICE_PERSONA_SECRET", raising=False)
        result = PersonaDataStore.encrypt_api_key("sk-test")
        assert result is None

    def test_decrypt_without_key(self, monkeypatch):
        """测试没有密钥时解密返回 None"""
        monkeypatch.delenv("DICE_PERSONA_SECRET", raising=False)
        result = PersonaDataStore.decrypt_api_key("some_encrypted_text")
        assert result is None

    def test_different_keys_produce_different_ciphertexts(self, mock_encryption_key):
        """测试不同输入产生不同密文"""
        key1 = "sk-test-key-1"
        key2 = "sk-test-key-2"

        encrypted1 = PersonaDataStore.encrypt_api_key(key1)
        encrypted2 = PersonaDataStore.encrypt_api_key(key2)

        assert encrypted1 != encrypted2


# ── UserLLMConfig Model Tests ────────────────────────────────────────────────

class TestUserLLMConfigModel:
    """测试 UserLLMConfig 模型"""

    def test_user_config_creation(self):
        """测试创建用户配置"""
        config = UserLLMConfig(
            user_id="U123",
            primary_api_key="sk-test",
            primary_model="gpt-4o",
        )
        assert config.user_id == "U123"
        assert config.primary_api_key == "sk-test"
        assert config.primary_model == "gpt-4o"

    def test_user_config_defaults(self):
        """测试用户配置默认值"""
        config = UserLLMConfig(user_id="U123")
        assert config.primary_api_key == ""
        assert config.primary_base_url == ""
        assert config.primary_model == ""
        assert config.auxiliary_api_key == ""


# ── Quota System Tests ───────────────────────────────────────────────────────

class TestQuotaSystem:
    """测试配额系统"""

    @pytest.mark.asyncio
    async def test_daily_usage_tracking(self, tmp_path):
        """测试每日用量追踪"""
        import aiosqlite

        db_path = tmp_path / "test.db"
        async with aiosqlite.connect(str(db_path)) as db:
            store = PersonaDataStore(db, timezone="Asia/Shanghai")
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

        db_path = tmp_path / "test.db"
        async with aiosqlite.connect(str(db_path)) as db:
            store = PersonaDataStore(db, timezone="Asia/Shanghai")
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

        db_path = tmp_path / "test.db"
        async with aiosqlite.connect(str(db_path)) as db:
            store = PersonaDataStore(db, timezone="Asia/Shanghai")
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

        db_path = tmp_path / "test.db"
        async with aiosqlite.connect(str(db_path)) as db:
            store = PersonaDataStore(db, timezone="Asia/Shanghai")
            await store.ensure_tables()

            # 保存配置
            success = await store.save_user_llm_config(sample_user_config)
            assert success is True

            # 读取配置
            retrieved = await store.get_user_llm_config("U123")
            assert retrieved is not None
            assert retrieved.user_id == "U123"
            assert retrieved.primary_api_key == "sk-test123"
            assert retrieved.primary_base_url == "https://api.test.com/v1"
            assert retrieved.primary_model == "gpt-4o"

    @pytest.mark.asyncio
    async def test_get_nonexistent_config(self, tmp_path, mock_encryption_key):
        """测试读取不存在的配置"""
        import aiosqlite

        db_path = tmp_path / "test.db"
        async with aiosqlite.connect(str(db_path)) as db:
            store = PersonaDataStore(db, timezone="Asia/Shanghai")
            await store.ensure_tables()

            config = await store.get_user_llm_config("NONEXISTENT")
            assert config is None

    @pytest.mark.asyncio
    async def test_clear_user_config(self, tmp_path, mock_encryption_key, sample_user_config):
        """测试清除用户配置"""
        import aiosqlite

        db_path = tmp_path / "test.db"
        async with aiosqlite.connect(str(db_path)) as db:
            store = PersonaDataStore(db, timezone="Asia/Shanghai")
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

        db_path = tmp_path / "test.db"
        async with aiosqlite.connect(str(db_path)) as db:
            store = PersonaDataStore(db, timezone="Asia/Shanghai")
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

        db_path = tmp_path / "test.db"
        async with aiosqlite.connect(str(db_path)) as db:
            store = PersonaDataStore(db, timezone="Asia/Shanghai")
            await store.ensure_tables()

            config = UserLLMConfig(
                user_id="U123",
                primary_api_key="sk-test",
            )

            success = await store.save_user_llm_config(config)
            assert success is False


# ── Roll Dice Tool Tests ─────────────────────────────────────────────────────

class TestRollDiceTool:
    """测试掷骰工具"""

    @pytest.mark.asyncio
    async def test_roll_dice_simple(self):
        """测试简单掷骰"""
        from module.persona.orchestrator import PersonaOrchestrator

        # 使用模拟的 orchestrator 来测试掷骰方法
        class MockOrchestrator:
            _handle_roll_dice = PersonaOrchestrator._handle_roll_dice

        mock = MockOrchestrator()
        result = await mock._handle_roll_dice("1d20")

        assert "掷骰" in result
        # 结果应该包含一个 1-20 的数字
        assert any(str(i) in result for i in range(1, 21))

    @pytest.mark.asyncio
    async def test_roll_dice_with_modifier(self):
        """测试带修饰符的掷骰"""
        from module.persona.orchestrator import PersonaOrchestrator

        class MockOrchestrator:
            _handle_roll_dice = PersonaOrchestrator._handle_roll_dice

        mock = MockOrchestrator()
        result = await mock._handle_roll_dice("2d6+3")

        assert "掷骰" in result
        # 结果应该包含计算后的值（5-15）
        assert "=" in result

    @pytest.mark.asyncio
    async def test_roll_dice_invalid_expression(self):
        """测试无效表达式"""
        from module.persona.orchestrator import PersonaOrchestrator

        class MockOrchestrator:
            _handle_roll_dice = PersonaOrchestrator._handle_roll_dice

        mock = MockOrchestrator()
        result = await mock._handle_roll_dice("invalid")

        assert "失败" in result or "无效" in result

    @pytest.mark.asyncio
    async def test_roll_dice_empty_expression(self):
        """测试空表达式"""
        from module.persona.orchestrator import PersonaOrchestrator

        class MockOrchestrator:
            _handle_roll_dice = PersonaOrchestrator._handle_roll_dice

        mock = MockOrchestrator()
        result = await mock._handle_roll_dice("")

        assert "无效" in result or "失败" in result

    @pytest.mark.asyncio
    async def test_roll_dice_too_long(self):
        """测试过长的表达式"""
        from module.persona.orchestrator import PersonaOrchestrator

        class MockOrchestrator:
            _handle_roll_dice = PersonaOrchestrator._handle_roll_dice

        mock = MockOrchestrator()
        result = await mock._handle_roll_dice("1d20" * 50)  # 很长的表达式

        assert "过长" in result


# ── R6: 补充测试覆盖 ──────────────────────────────────────────────────────────

class TestQuotaExemptions:
    """测试配额豁免场景"""

    @pytest.mark.asyncio
    async def test_whitelist_user_exempt_from_quota(self, tmp_path, monkeypatch):
        """测试白名单用户豁免配额"""
        import aiosqlite
        from module.persona.llm.router import LLMRouter
        from core.config.pydantic_models import PersonaConfig

        db_path = tmp_path / "test.db"
        async with aiosqlite.connect(str(db_path)) as db:
            store = PersonaDataStore(db, timezone="Asia/Shanghai")
            await store.ensure_tables()

            # 添加用户到白名单
            await store.add_user_to_whitelist("WHITELISTED_USER")

            # 创建 mock config（启用白名单）
            config = PersonaConfig(whitelist_enabled=True)

            # 创建 router（配额限制为 2 次）
            router = LLMRouter(
                primary_api_key="test-key",
                primary_base_url="https://api.test.com/v1",
                primary_model="gpt-4o",
                daily_limit=2,
                quota_check_enabled=True,
                data_store=store,
                config=config,
            )

            # 白名单用户应豁免配额检查
            assert await router._is_exempt_from_quota("WHITELISTED_USER", "") is True

            # 非白名单用户不应豁免
            assert await router._is_exempt_from_quota("REGULAR_USER", "") is False

    @pytest.mark.asyncio
    async def test_whitelist_group_exempt_from_quota(self, tmp_path):
        """测试白名单群豁免配额"""
        import aiosqlite

        db_path = tmp_path / "test.db"
        async with aiosqlite.connect(str(db_path)) as db:
            store = PersonaDataStore(db, timezone="Asia/Shanghai")
            await store.ensure_tables()

            # 添加群到白名单
            await store.add_group_to_whitelist("WHITELISTED_GROUP")

            # 群在白名单中应豁免
            assert await store.is_group_whitelisted("WHITELISTED_GROUP") is True
            assert await store.is_group_whitelisted("REGULAR_GROUP") is False


class TestUserKeyClientSelection:
    """测试用户自定义 Key 的客户端选择逻辑"""

    def test_primary_client_with_user_config(self):
        """测试主模型使用用户配置"""
        from module.persona.llm.router import LLMRouter, ModelTier
        from module.persona.data.models import UserLLMConfig

        router = LLMRouter(
            primary_api_key="default-key",
            primary_base_url="https://default.com/v1",
            primary_model="gpt-4o",
        )

        # 用户配置
        user_config = UserLLMConfig(
            user_id="U123",
            primary_api_key="user-key",
            primary_model="gpt-4-turbo",
            primary_base_url="https://user.com/v1",
        )

        # 获取客户端
        client = router._get_client_for_tier(ModelTier.PRIMARY, user_config)

        # 应使用用户配置
        assert client.api_key == "user-key"
        assert client.model == "gpt-4-turbo"
        assert client.base_url == "https://user.com/v1"

    def test_primary_client_without_user_config(self):
        """测试主模型没有用户配置时使用默认"""
        from module.persona.llm.router import LLMRouter, ModelTier

        router = LLMRouter(
            primary_api_key="default-key",
            primary_base_url="https://default.com/v1",
            primary_model="gpt-4o",
        )

        # 没有用户配置
        client = router._get_client_for_tier(ModelTier.PRIMARY, None)

        # 应使用默认配置
        assert client.api_key == "default-key"
        assert client.model == "gpt-4o"

    def test_auxiliary_client_fallback_to_primary(self):
        """测试辅助模型回退到主模型配置"""
        from module.persona.llm.router import LLMRouter, ModelTier
        from module.persona.data.models import UserLLMConfig

        router = LLMRouter(
            primary_api_key="default-key",
            primary_base_url="https://default.com/v1",
            primary_model="gpt-4o",
            auxiliary_api_key="aux-key",
            auxiliary_model="gpt-3.5",
        )

        # 用户只配置了主模型
        user_config = UserLLMConfig(
            user_id="U123",
            primary_api_key="user-primary-key",
            primary_model="user-model",
        )

        # 获取辅助模型客户端
        client = router._get_client_for_tier(ModelTier.AUXILIARY, user_config)

        # 应使用用户的主模型配置
        assert client.api_key == "user-primary-key"
        assert client.model == "user-model"


class TestQuotaExceededException:
    """测试 QuotaExceeded 异常"""

    def test_quota_exceeded_exception(self):
        """测试 QuotaExceeded 异常可被抛出和捕获"""
        from module.persona.llm.router import QuotaExceeded

        with pytest.raises(QuotaExceeded) as exc_info:
            raise QuotaExceeded("今日配额已用完")

        assert "今日配额已用完" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_quota_exceeded_raised_when_limit_reached(self, tmp_path):
        """测试配额超限时抛出异常"""
        import aiosqlite
        from module.persona.llm.router import LLMRouter, QuotaExceeded

        db_path = tmp_path / "test.db"
        async with aiosqlite.connect(str(db_path)) as db:
            store = PersonaDataStore(db, timezone="Asia/Shanghai")
            await store.ensure_tables()

            # 创建 router（配额限制为 0 次，立即触发超限）
            router = LLMRouter(
                primary_api_key="test-key",
                primary_base_url="https://api.test.com/v1",
                primary_model="gpt-4o",
                daily_limit=0,  # 配额为 0，任何请求都会超限
                quota_check_enabled=True,
                data_store=store,
            )

            # 应抛出 QuotaExceeded
            with pytest.raises(QuotaExceeded):
                await router.generate(
                    messages=[{"role": "user", "content": "hello"}],
                    user_id="TEST_USER",
                    group_id="",
                )
