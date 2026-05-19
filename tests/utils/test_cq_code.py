import pytest
from io import BytesIO
from pathlib import Path
from utils.cq_code import get_cq_image, get_cq_reply, get_cq_at


@pytest.mark.unit
class TestGetCqImage:
    def test_string_path(self):
        result = get_cq_image("/path/to/image.jpg")
        assert "CQ:image" in result
        assert "file://" in result

    def test_bytes(self):
        result = get_cq_image(b"fake binary data")
        assert "CQ:image" in result
        assert "base64://" in result

    def test_bytesio(self):
        data = BytesIO(b"fake binary data")
        result = get_cq_image(data)
        assert "CQ:image" in result
        assert "base64://" in result

    def test_path_object(self):
        result = get_cq_image(Path("/path/to/image.png"))
        assert "CQ:image" in result
        assert "file:///" in result


@pytest.mark.unit
class TestGetCqReply:
    def test_numeric_id(self):
        result = get_cq_reply("12345")
        assert "CQ:reply" in result
        assert "12345" in result

    def test_non_numeric_id(self):
        result = get_cq_reply("invalid")
        assert result == ""


@pytest.mark.unit
class TestGetCqAt:
    def test_numeric_user_id(self):
        result = get_cq_at("123456")
        assert "CQ:at" in result
        assert "123456" in result

    def test_non_numeric_user_id(self):
        result = get_cq_at("username")
        assert result == "@username"

