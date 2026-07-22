import pytest
from core.command.cq_extractor import _parse_cq_params, extract_segments


class TestParseCqParams:
    def test_basic_key_value(self):
        result = _parse_cq_params("qq=12345,name=测试")
        assert result == {"qq": "12345", "name": "测试"}

    def test_empty_params(self):
        result = _parse_cq_params("")
        assert result == {}

    def test_unescape_amp(self):
        """&amp; 应解码为 & — 核心 bug 场景"""
        result = _parse_cq_params("url=https://a.com?appid=1&amp;fileid=2&amp;spec=0")
        assert result == {"url": "https://a.com?appid=1&fileid=2&spec=0"}

    def test_unescape_lt_gt(self):
        result = _parse_cq_params("name=foo&lt;bar&gt;")
        assert result == {"name": "foo<bar>"}

    def test_numeric_entity_unchanged(self):
        """&#x2F; 不应被 saxutils.unescape 解码 — 只需知道值未被改变"""
        result = _parse_cq_params("url=a&#x2F;b")
        # saxutils 不处理数字实体，值保持原样
        assert result == {"url": "a&#x2F;b"}

    def test_mixed_escaped_and_plain(self):
        """混合：正常值 + 含实体值"""
        result = _parse_cq_params("file=img.jpg,url=https://x?k=1&amp;v=2")
        assert result == {
            "file": "img.jpg",
            "url": "https://x?k=1&v=2",
        }


class TestExtractSegmentsUnescape:
    def test_image_url_amp_unescaped(self):
        """extract_segments 集成级：CQ 码 URL 中 &amp; 应解码为 &"""
        raw_msg = "[CQ:image,url=https://x?k=1&amp;v=2]"
        segments = extract_segments(raw_msg)
        assert len(segments) == 1
        assert segments[0].seg_type == "image"
        assert segments[0].data["url"] == "https://x?k=1&v=2"

    def test_at_name_unescaped(self):
        """AT 的 name 中含实体也应被解码"""
        raw_msg = "[CQ:at,qq=12345,name=foo&amp;bar]"
        segments = extract_segments(raw_msg)
        assert len(segments) == 1
        assert segments[0].seg_type == "at"
        assert segments[0].data["display_name"] == "foo&bar"
