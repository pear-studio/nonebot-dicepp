"""NicknameCommand pure logic tests."""

from plugins.DicePP.module.common.nickname_command import MAX_NICKNAME_LENGTH, NicknameCommand


class TestNicknameCommandPureLogic:
    def test_legal_nickname_normal(self):
        assert NicknameCommand.is_legal_nickname("测试用户")

    def test_legal_nickname_ascii(self):
        assert NicknameCommand.is_legal_nickname("dm")

    def test_illegal_nickname_empty(self):
        assert not NicknameCommand.is_legal_nickname("")

    def test_illegal_nickname_starts_with_dot(self):
        assert not NicknameCommand.is_legal_nickname(".bot")

    def test_illegal_nickname_too_long(self):
        assert not NicknameCommand.is_legal_nickname("x" * (MAX_NICKNAME_LENGTH + 1))

    def test_legal_nickname_max_length(self):
        assert NicknameCommand.is_legal_nickname("x" * MAX_NICKNAME_LENGTH)
