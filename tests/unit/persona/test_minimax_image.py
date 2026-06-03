"""MiniMaxImageProvider 单元测试 — classify_error 错误码 2013 细分"""
import pytest

from plugins.DicePP.module.persona.llm.errors import ErrorKind
from plugins.DicePP.module.persona.llm.providers.minimax_image import MiniMaxImageProvider
from plugins.DicePP.module.persona.llm.providers.protocol import ErrorClass


class TestClassifyError2013:
    """错误码 2013 的细分分类"""

    def test_invalid_params_prompt_length_is_retryable(self):
        """参数错误：prompt 过长 — 来自真实 API 返回"""
        e = RuntimeError(
            "image gen API error [2013]: invalid params, prompt length must be less than 1500"
        )
        assert MiniMaxImageProvider.classify_error(e) == ErrorClass.RETRYABLE

    def test_invalid_params_chat_setting_is_retryable(self):
        """参数错误：chat setting — 来自 docs/deploy.md 真实案例"""
        e = RuntimeError(
            "image gen API error [2013]: invalid params, invalid chat setting"
        )
        assert MiniMaxImageProvider.classify_error(e) == ErrorClass.RETRYABLE

    def test_content_moderation_is_non_retryable(self):
        """内容审核不通过 — 不可重试"""
        e = RuntimeError(
            "image gen API error [2013]: content moderation failed"
        )
        assert MiniMaxImageProvider.classify_error(e) == ErrorClass.NON_RETRYABLE


class TestClassifyErrorOtherCodes:
    """其他错误码回归保护"""

    @pytest.mark.parametrize("code", [1001, 1002, 1004, 1008, 2056])
    def test_known_non_retryable_codes(self, code):
        e = RuntimeError(f"image gen API error [{code}]: some error")
        assert MiniMaxImageProvider.classify_error(e) == ErrorClass.NON_RETRYABLE

    def test_unknown_code_is_retryable(self):
        e = RuntimeError("image gen API error [9999]: unknown error")
        assert MiniMaxImageProvider.classify_error(e) == ErrorClass.RETRYABLE


class TestClassifyErrorKind:
    """classify_error_kind 细粒度错误分类"""

    def test_1026_content_filtered(self):
        e = RuntimeError("image gen API error [1026]: input new_sensitive")
        assert MiniMaxImageProvider.classify_error_kind(e) == ErrorKind.CONTENT_FILTERED

    def test_1027_content_filtered(self):
        e = RuntimeError("image gen API error [1027]: output content error")
        assert MiniMaxImageProvider.classify_error_kind(e) == ErrorKind.CONTENT_FILTERED

    def test_new_sensitive_keyword(self):
        e = RuntimeError("image gen failed: new_sensitive check")
        assert MiniMaxImageProvider.classify_error_kind(e) == ErrorKind.CONTENT_FILTERED

    def test_2013_content_moderation(self):
        """2013 内容审核 → CONTENT_FILTERED"""
        e = RuntimeError("image gen API error [2013]: content moderation failed")
        assert MiniMaxImageProvider.classify_error_kind(e) == ErrorKind.CONTENT_FILTERED

    def test_2013_invalid_params_not_content_filtered(self):
        """2013 参数错误不误杀为内容过滤"""
        e = RuntimeError("image gen API error [2013]: invalid params, prompt too long")
        assert MiniMaxImageProvider.classify_error_kind(e) is None

    def test_unknown_error_not_content_filtered(self):
        e = RuntimeError("image gen API error [9999]: unknown error")
        assert MiniMaxImageProvider.classify_error_kind(e) is None
