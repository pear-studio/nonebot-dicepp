"""Pure Persona encryption, roll-tool, and exception tests."""

import pytest

from module.persona.data.store import PersonaDataStore


@pytest.fixture
def mock_encryption_key(monkeypatch):
    monkeypatch.setenv("DICE_PERSONA_SECRET", "test_secret_key_for_encryption_32bytes")
    yield


class TestAESEncryption:
    def test_encrypt_decrypt_roundtrip(self, mock_encryption_key):
        original = "sk-test-api-key-12345"
        encrypted = PersonaDataStore.encrypt_api_key(original)
        assert isinstance(encrypted, str)
        assert encrypted != original
        assert PersonaDataStore.decrypt_api_key(encrypted) == original

    def test_encrypt_empty_string(self, mock_encryption_key):
        assert PersonaDataStore.encrypt_api_key("") is None

    def test_decrypt_empty_string(self, mock_encryption_key):
        assert PersonaDataStore.decrypt_api_key("") is None

    def test_encrypt_without_key(self, monkeypatch):
        monkeypatch.delenv("DICE_PERSONA_SECRET", raising=False)
        assert PersonaDataStore.encrypt_api_key("sk-test") is None

    def test_decrypt_without_key(self, monkeypatch):
        monkeypatch.delenv("DICE_PERSONA_SECRET", raising=False)
        assert PersonaDataStore.decrypt_api_key("some_encrypted_text") is None

    def test_different_keys_produce_different_ciphertexts(self, mock_encryption_key):
        encrypted1 = PersonaDataStore.encrypt_api_key("sk-test-key-1")
        encrypted2 = PersonaDataStore.encrypt_api_key("sk-test-key-2")
        assert encrypted1 != encrypted2


class TestRollDiceTool:
    async def _roll(self, expression: str):
        from module.persona.agent.runtime_types import ToolExecutionContext
        from module.persona.tools.roll_dice import ROLL_DICE_TOOL

        return await ROLL_DICE_TOOL.handler(
            ROLL_DICE_TOOL.args_schema(expression=expression),
            ToolExecutionContext("r1", "tc1", 0, 0),
        )

    @pytest.mark.asyncio
    async def test_roll_dice_simple(self):
        from tests.support.sequence_runtime import SequenceRuntime, reset_runtime, set_runtime

        token = set_runtime(SequenceRuntime([5]))
        try:
            result = await self._roll("1d20")
        finally:
            reset_runtime(token)
        assert result.status == "success"
        assert "掷骰" in result.observation
        assert "[5]" in result.observation, f"应包含 [5]，实际: {result.observation}"
        assert "= 5" in result.observation, f"应包含最终值 5，实际: {result.observation}"

    @pytest.mark.asyncio
    async def test_roll_dice_with_modifier(self):
        from tests.support.sequence_runtime import SequenceRuntime, reset_runtime, set_runtime

        token = set_runtime(SequenceRuntime([4, 6]))
        try:
            result = await self._roll("2d6+3")
        finally:
            reset_runtime(token)
        assert result.status == "success"
        assert "掷骰" in result.observation
        assert "[4+6]" in result.observation, f"应包含 [4+6]，实际: {result.observation}"
        assert "= 13" in result.observation, f"应包含最终值 13，实际: {result.observation}"

    @pytest.mark.asyncio
    async def test_roll_dice_invalid_expression(self):
        result = await self._roll("invalid")
        assert result.status == "error"
        assert "失败" in result.observation or "无效" in result.observation

    @pytest.mark.asyncio
    async def test_roll_dice_empty_expression(self):
        result = await self._roll("")
        assert result.status == "error"
        assert "无效" in result.observation or "失败" in result.observation

    @pytest.mark.asyncio
    async def test_roll_dice_too_long(self):
        result = await self._roll("1d20" * 50)
        assert result.status == "error"
        assert "过长" in result.observation


class TestQuotaExceededException:
    def test_quota_exceeded_exception(self):
        from module.persona.llm.router import QuotaExceeded

        with pytest.raises(QuotaExceeded) as exc_info:
            raise QuotaExceeded("今日配额已用完")
        assert "今日配额已用完" in str(exc_info.value)
