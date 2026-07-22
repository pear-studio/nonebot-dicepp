import pytest

from shell.session import create_session, format_session_info


class TestSessionValidation:
    def test_validate_session_name_empty(self):
        with pytest.raises(ValueError, match="empty"):
            create_session("")

    def test_validate_session_name_too_long(self):
        with pytest.raises(ValueError, match="too long"):
            create_session("a" * 33)

    def test_validate_session_name_invalid_chars(self):
        with pytest.raises(ValueError, match="invalid characters"):
            create_session("test/session")

    def test_format_session_info(self):
        session = {
            "name": "my_session",
            "group_id": "my_group",
            "size_bytes": 1536,
            "last_used": 0,
            "created": 0,
        }
        line = format_session_info(session)
        assert "my_session" in line
        assert "my_group" in line
        assert "1.5KB" in line or "1536B" in line
