"""
Tests for Persona Phase 4: Cost Control and User Configuration

Covers:
- Quota system (daily limit, exemptions)
- User LLM config (AES encryption, CRUD)
- Roll dice tool
"""
import pytest
from datetime import datetime

from plugins.DicePP.module.persona.data.models import UserLLMConfig, DailyUsage
from plugins.DicePP.module.persona.data.store import PersonaDataStore


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

        assert isinstance(encrypted, str)
        assert encrypted != original

        decrypted = PersonaDataStore.decrypt_api_key(encrypted)
        assert decrypted == original

    def test_encrypt_empty_string(self, mock_encryption_key):
        """测试空字符串加密返回 None"""
        result = PersonaDataStore.encrypt_api_key("")
        assert result is None

    def test_decrypt_empty_string(self, mock_encryption_key):
        """测试空字符串解密返回 None"""
        result = PersonaDataStore.decrypt_api_key("")
        assert result is None

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
        from plugins.DicePP.utils.time import wall_now

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

class TestRollDiceTool:
    """测试掷骰工具"""

    @pytest.mark.asyncio
    async def test_roll_dice_simple(self):
        """测试简单掷骰 — 固定 SequenceRuntime 断言精确输出"""
        from plugins.DicePP.module.persona.tools.roll_dice import roll_dice_executor
        from plugins.DicePP.module.persona.tools.context import ToolContext
        from tests.helpers.sequence_runtime import SequenceRuntime, set_runtime, reset_runtime

        ctx = ToolContext(user_id="u1", group_id="")
        runtime = SequenceRuntime([5])  # d20 → ((5-1)%20)+1 = 5
        token = set_runtime(runtime)
        try:
            result = await roll_dice_executor({"expression": "1d20"}, ctx)
        finally:
            reset_runtime(token)

        assert "掷骰" in result
        assert "[5]" in result, f"应包含 [5]，实际: {result}"
        assert "= 5" in result, f"应包含最终值 5，实际: {result}"

    @pytest.mark.asyncio
    async def test_roll_dice_with_modifier(self):
        """测试带修饰符的掷骰 — 固定 SequenceRuntime 断言精确输出"""
        from plugins.DicePP.module.persona.tools.roll_dice import roll_dice_executor
        from plugins.DicePP.module.persona.tools.context import ToolContext
        from tests.helpers.sequence_runtime import SequenceRuntime, set_runtime, reset_runtime

        ctx = ToolContext(user_id="u1", group_id="")
        runtime = SequenceRuntime([4, 6])  # 2d6 → [4, 6], +3 → 13
        token = set_runtime(runtime)
        try:
            result = await roll_dice_executor({"expression": "2d6+3"}, ctx)
        finally:
            reset_runtime(token)

        assert "掷骰" in result
        assert "[4+6]" in result, f"应包含 [4+6]，实际: {result}"
        assert "= 13" in result, f"应包含最终值 13，实际: {result}"

    @pytest.mark.asyncio
    async def test_roll_dice_invalid_expression(self):
        """测试无效表达式"""
        from plugins.DicePP.module.persona.tools.roll_dice import roll_dice_executor
        from plugins.DicePP.module.persona.tools.context import ToolContext

        ctx = ToolContext(user_id="u1", group_id="")
        result = await roll_dice_executor({"expression": "invalid"}, ctx)

        assert "失败" in result or "无效" in result

    @pytest.mark.asyncio
    async def test_roll_dice_empty_expression(self):
        """测试空表达式"""
        from plugins.DicePP.module.persona.tools.roll_dice import roll_dice_executor
        from plugins.DicePP.module.persona.tools.context import ToolContext

        ctx = ToolContext(user_id="u1", group_id="")
        result = await roll_dice_executor({"expression": ""}, ctx)

        assert "无效" in result or "失败" in result

    @pytest.mark.asyncio
    async def test_roll_dice_too_long(self):
        """测试过长的表达式"""
        from plugins.DicePP.module.persona.tools.roll_dice import roll_dice_executor
        from plugins.DicePP.module.persona.tools.context import ToolContext

        ctx = ToolContext(user_id="u1", group_id="")
        result = await roll_dice_executor({"expression": "1d20" * 50}, ctx)

        assert "过长" in result


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


class TestQuotaExceededException:
    """测试 QuotaExceeded 异常"""

    def test_quota_exceeded_exception(self):
        """测试 QuotaExceeded 异常可被抛出和捕获"""
        from plugins.DicePP.module.persona.llm.router import QuotaExceeded

        with pytest.raises(QuotaExceeded) as exc_info:
            raise QuotaExceeded("今日配额已用完")

        assert "今日配额已用完" in str(exc_info.value)

