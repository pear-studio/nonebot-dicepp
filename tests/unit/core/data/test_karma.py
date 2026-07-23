import pytest
from datetime import datetime
from plugins.DicePP.core.data.models.karma import UserKarma


class TestUserKarma:
    @pytest.mark.parametrize(
        ("user_id", "group_id", "value", "expected_value"),
        [
            ("user123", "group456", None, 0),
            ("user456", "group123", None, 0),
            ("user789", "group012", 100, 100),
            ("alice", "g1", -5, -5),
        ],
    )
    def test_construction(self, user_id: str, group_id: str, value: object, expected_value: int):
        kwargs = {"user_id": user_id, "group_id": group_id}
        if value is not None:
            kwargs["value"] = value
        karma = UserKarma(**kwargs)

        assert karma.user_id == user_id
        assert karma.group_id == group_id
        assert karma.value == expected_value
        assert isinstance(karma.last_update, datetime)

    def test_serialization_roundtrip(self):
        karma = UserKarma(user_id="user123", group_id="group456", value=50)
        serialized = karma.model_dump_json()

        karma2 = UserKarma.model_validate_json(serialized)
        assert karma.user_id == karma2.user_id
        assert karma.group_id == karma2.group_id
        assert karma.value == karma2.value
        assert karma.last_update == karma2.last_update
