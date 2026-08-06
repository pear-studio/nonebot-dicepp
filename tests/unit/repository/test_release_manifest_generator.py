from __future__ import annotations

from scripts.build.generate_release_manifest import velopack_channel, velopack_version


def test_velopack_uses_semver2_and_architecture_scoped_channels() -> None:
    assert velopack_version("3.0.0rc9") == "3.0.0-rc.9"
    assert velopack_version("v3.1.0") == "3.1.0"
    assert velopack_channel("stable", "amd64") == "win-x64-stable"
    assert velopack_channel("prerelease", "amd64") == "win-x64-prerelease"
