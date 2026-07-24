"""Release quick-start documentation contracts."""

from __future__ import annotations

from pathlib import Path

from tests.support.paths import find_repository_root


ROOT = find_repository_root(Path(__file__))
RELEASE_README = ROOT / "docs" / "releases" / "README.md"


def test_linux_newcomer_quick_start_is_a_complete_offline_bundle_flow() -> None:
    """The release README must not present metadata as a deployable compose setup."""
    content = RELEASE_README.read_text(encoding="utf-8")

    assert "[完整 Linux 部署说明](../linux.md)" in content
    assert 'unzip -o "DicePP-${VERSION}-linux-amd64.zip" -d "${PACKAGE_DIR}"' in content
    assert "sha256sum -c checksums.sha256" in content
    assert "cp docker-compose.yml .." in content
    assert 'docker load -i "images/DicePP-${VERSION}-linux-amd64-images.tar"' in content
    assert "DICEPP_IMAGE_TAG=${VERSION} docker compose up -d --pull never" in content
    assert "仅供 Manager" in content
