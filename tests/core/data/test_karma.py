import pytest
from datetime import datetime
from core.data.models.karma import UserKarma


@pytest.mark.unit
class TestUserKarma:
    def test_init(self):
        karma = UserKarma(user_id="user123", group_id="group456")
        assert karma.user_id == "user123"
        assert karma.group_id == "group456"
        assert karma.value == 0
        assert isinstance(karma.last_update, datetime)

    def test_init_with_value(self):
        karma = UserKarma(user_id="user123", group_id="group456", value=100)
        assert karma.value == 100

    def test_serialization(self):
        karma = UserKarma(user_id="user123", group_id="group456", value=50)
        serialized = karma.model_dump_json()

        karma2 = UserKarma.model_validate_json(serialized)
        assert karma.user_id == karma2.user_id
        assert karma.group_id == karma2.group_id
        assert karma.value == karma2.value

    def test_default_last_update(self):
        before = datetime.now()
        karma = UserKarma(user_id="user123", group_id="group456")
        after = datetime.now()
        assert karma.last_update >= before
        assert karma.last_update <= after
