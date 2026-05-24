"""返回 WebUI 单文件 HTML。"""
from pathlib import Path


_HTML_PATH = Path(__file__).resolve().parent / "static" / "admin.html"


def render() -> str:
    if not _HTML_PATH.exists():
        return "<h1>admin.html missing</h1>"
    return _HTML_PATH.read_text(encoding="utf-8")
