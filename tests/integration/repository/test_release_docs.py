"""Release documentation responsibility contracts."""

from __future__ import annotations

from pathlib import Path

from tests.support.paths import find_repository_root


ROOT = find_repository_root(Path(__file__))
RELEASE_README = ROOT / "docs" / "releases" / "README.md"


def test_release_readme_routes_user_operations_to_platform_docs() -> None:
    """The maintainer release guide must not duplicate the user deployment guide."""
    content = RELEASE_README.read_text(encoding="utf-8")

    assert "[版本更新](../updates.md)" in content
    assert "[Windows 部署](../windows.md)" in content
    assert "[Linux 部署](../linux.md)" in content
    assert "## 用户操作速查" not in content
    assert 'unzip -o "DicePP-${VERSION}-linux-amd64.zip"' not in content
