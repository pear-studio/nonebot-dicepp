import unittest
import pytest
from io import BytesIO
from pathlib import Path
from utils.cq_code import get_cq_image, get_cq_reply, get_cq_at


@pytest.mark.unit
class TestGetCqImage(unittest.TestCase):
    def test_string_path(self):
        result = get_cq_image("/path/to/image.jpg")
        self.assertIn("CQ:image", result)
        self.assertIn("file://", result)

    def test_bytes(self):
        result = get_cq_image(b"fake binary data")
        self.assertIn("CQ:image", result)
        self.assertIn("base64://", result)

    def test_bytesio(self):
        data = BytesIO(b"fake binary data")
        result = get_cq_image(data)
        self.assertIn("CQ:image", result)
        self.assertIn("base64://", result)

    def test_path_object(self):
        result = get_cq_image(Path("/path/to/image.png"))
        self.assertIn("CQ:image", result)
        self.assertIn("file:///", result)


@pytest.mark.unit
class TestGetCqReply(unittest.TestCase):
    def test_numeric_id(self):
        result = get_cq_reply("12345")
        self.assertIn("CQ:reply", result)
        self.assertIn("12345", result)

    def test_non_numeric_id(self):
        result = get_cq_reply("invalid")
        self.assertEqual(result, "")


@pytest.mark.unit
class TestGetCqAt(unittest.TestCase):
    def test_numeric_user_id(self):
        result = get_cq_at("123456")
        self.assertIn("CQ:at", result)
        self.assertIn("123456", result)

    def test_non_numeric_user_id(self):
        result = get_cq_at("username")
        self.assertEqual(result, "@username")


if __name__ == '__main__':
    unittest.main()
