from __future__ import annotations


import hashlib
import io
import json
import threading
import urllib.error
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from dicepp_data import InstanceLayout
from dicepp_manager.deployment import DEPLOYMENT_SCHEMA_VERSION, MANAGER_VERSION
from dicepp_manager.release import (
    RELEASE_CONTRACT_VERSION,
    ReleaseCancelledError,
    ReleaseContractError,
    ReleaseDownloadError,
    ReleaseError,
    ReleaseManager,
    ReleaseOperation,
    UpdateSettings,
    UrlTransport,
    validate_release_manifest,
)
import dicepp_manager.release as release_module


class Response:
    def __init__(self, body: bytes, *, status: int = 200, headers=None) -> None:
        self.body = body
        self.status = status
        self.headers = headers or {}
        self.offset = 0
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self.body) - self.offset
        result = self.body[self.offset : self.offset + size]
        self.offset += len(result)
        return result

    def close(self) -> None:
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class Transport:
    def __init__(self, routes: dict[str, list[Response]]) -> None:
        self.routes = routes
        self.requests: list[tuple[str, dict[str, str]]] = []

    def open(self, url: str, *, headers=None, timeout=30):
        self.requests.append((url, dict(headers or {})))
        return self.routes[url].pop(0)


def _artifact(filename: str, body: bytes, purpose: str = "linux-bundle") -> dict:
    return {
        "platform": "linux",
        "arch": "amd64",
        "filename": filename,
        "purpose": purpose,
        "size": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
    }


def _velopack_bundle(version: str = "3.1.0") -> tuple[bytes, bytes, dict]:
    nupkg_stream = io.BytesIO()
    with zipfile.ZipFile(nupkg_stream, "w") as archive:
        archive.writestr(
            "DicePP.nuspec",
            f"<package><metadata><version>{version}</version></metadata></package>",
        )
    nupkg = nupkg_stream.getvalue()
    nupkg_name = f"DicePP-{version}-full.nupkg"
    inner = {
        "format_version": 1,
        "dicepp_version": version,
        "velopack_version": version,
        "channel": "stable",
        "platform": "windows",
        "arch": "amd64",
        "nupkg": {
            "filename": nupkg_name,
            "size": len(nupkg),
            "sha256": hashlib.sha256(nupkg).hexdigest(),
        },
    }
    bundle_stream = io.BytesIO()
    with zipfile.ZipFile(bundle_stream, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("manifest.json", json.dumps(inner))
        archive.writestr(nupkg_name, nupkg)
    return bundle_stream.getvalue(), nupkg, inner


def _velopack_artifact(body: bytes) -> dict:
    return {
        "platform": "windows",
        "arch": "amd64",
        "filename": "velopack.win-x64.zip",
        "purpose": "velopack-bundle",
        "size": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
    }


def _manifest(artifacts: list[dict], *, version="3.1.0", channel="stable") -> dict:
    artifacts = list(artifacts)
    if not any(item.get("purpose") == "velopack-bundle" for item in artifacts):
        artifacts.append(
            {
                "platform": "windows",
                "arch": "amd64",
                "filename": "velopack.win-x64.zip",
                "purpose": "velopack-bundle",
                "size": 1,
                "sha256": "2" * 64,
            }
        )
    return {
        "contract_version": RELEASE_CONTRACT_VERSION,
        "version": version,
        "channel": channel,
        "deployment_schema_version": DEPLOYMENT_SCHEMA_VERSION,
        "minimum_manager_version": MANAGER_VERSION,
        "catalog_version": 1,
        "catalog_digest": "1" * 64,
        "change_scope": ["runtime", "dashboard"],
        "automatic_upgrade": True,
        "artifacts": artifacts,
        "fallbacks": {
            "linux_ghcr_images": [
                f"ghcr.io/pear-studio/nonebot-dicepp:v{version}",
                f"ghcr.io/pear-studio/dicepp-dashboard:v{version}",
            ]
        },
    }


def _release(
    manifest: dict,
    body: bytes,
    *,
    prerelease=False,
    asset_id: str = "",
) -> dict:
    artifact = manifest["artifacts"][0]
    manifest_bytes = _json_bytes(manifest)
    return {
        "tag_name": f"v{manifest['version']}",
        "draft": False,
        "prerelease": prerelease,
        "html_url": "https://github.com/example/release",
        "published_at": "2026-07-23T00:00:00Z",
        "assets": [
            {
                "name": "dicepp-release.json",
                "size": len(manifest_bytes),
                "digest": f"sha256:{hashlib.sha256(manifest_bytes).hexdigest()}",
                "browser_download_url": f"https://downloads/manifest{asset_id}",
            },
            {
                "name": artifact["filename"],
                "size": len(body),
                "digest": f"sha256:{artifact['sha256']}",
                "browser_download_url": f"https://downloads/artifact{asset_id}",
            },
        ],
    }


def _json_response(value) -> Response:
    return Response(_json_bytes(value))


def _json_bytes(value) -> bytes:
    return json.dumps(value).encode()


def test_update_settings_default_and_user_overlay(tmp_path: Path) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    layout.config_dir.mkdir()
    layout.config_global.write_text(
        json.dumps(
            {
                "update": {
                    "discovery_enabled": True,
                    "auto_download": False,
                    "channel": "stable",
                    "cache_versions": 2,
                }
            }
        ),
        encoding="utf-8",
    )
    layout.config_user.write_text(
        json.dumps({"update": {"channel": "prerelease"}}),
        encoding="utf-8",
    )

    settings = UpdateSettings.from_layout(layout)

    assert settings.discovery_enabled is True
    assert settings.auto_download is False
    assert settings.channel == "prerelease"
    assert settings.cache_versions == 2


def test_update_settings_safe_defaults_need_no_config_files(tmp_path: Path) -> None:
    settings = UpdateSettings.from_layout(InstanceLayout.from_root(tmp_path))

    assert settings.discovery_enabled is True
    assert settings.auto_download is False
    assert settings.channel == "stable"
    assert settings.cache_versions == 2


def test_disabled_scheduled_discovery_performs_no_network_request(tmp_path: Path) -> None:
    transport = Transport({})
    manager = ReleaseManager(
        layout=InstanceLayout.from_root(tmp_path),
        settings_loader=lambda: UpdateSettings(discovery_enabled=False),
        transport=transport,
        github_api="https://api",
        target=("linux", "amd64"),
    )

    status = manager.discover()

    assert status["available"] is None
    assert transport.requests == []


@pytest.mark.parametrize(
    ("channel", "version", "is_prerelease"),
    [
        ("stable", "3.1.0", False),
        ("prerelease", "3.2.0rc1", True),
    ],
)
def test_discovery_uses_channel_endpoint_without_downloading_package(
    tmp_path: Path,
    channel: str,
    version: str,
    is_prerelease: bool,
) -> None:
    body = b"large release package"
    manifest = _manifest(
        [_artifact(f"DicePP-v{version}-linux-amd64.zip", body)],
        version=version,
        channel=channel,
    )
    release = _release(manifest, body, prerelease=is_prerelease)
    endpoint = "https://api/releases?per_page=100&page=1"
    transport = Transport(
        {
            endpoint: [_json_response([release])],
            "https://downloads/manifest": [_json_response(manifest)],
        }
    )
    manager = ReleaseManager(
        layout=InstanceLayout.from_root(tmp_path),
        settings_loader=lambda: UpdateSettings(channel=channel),
        transport=transport,
        github_api="https://api",
        target=("linux", "amd64"),
        current_version_loader=lambda: "3.0.0",
    )

    status = manager.discover(manual=True)

    assert status["available"]["version"] == version
    assert status["available"]["artifacts"][0]["filename"].endswith("linux-amd64.zip")
    assert [url for url, _headers in transport.requests] == [
        endpoint,
        "https://downloads/manifest",
    ]
    assert not manager.layout.manager_packages_dir.exists()


def test_discovery_rejects_asset_digest_mismatch(tmp_path: Path) -> None:
    body = b"release"
    manifest = _manifest([_artifact("DicePP-v3.1.0-linux-amd64.zip", body)])
    release = _release(manifest, body)
    release["assets"][1]["digest"] = "sha256:" + "0" * 64
    transport = Transport(
        {
            "https://api/releases?per_page=100&page=1": [_json_response([release])],
            "https://downloads/manifest": [_json_response(manifest)],
        }
    )
    manager = ReleaseManager(
        layout=InstanceLayout.from_root(tmp_path),
        transport=transport,
        github_api="https://api",
        target=("linux", "amd64"),
        current_version_loader=lambda: "3.0.0",
    )

    with pytest.raises(ReleaseContractError, match="digest differs"):
        manager.discover(manual=True)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda asset: asset.update(browser_download_url="http://unsafe/manifest"), "HTTPS"),
        (lambda asset: asset.update(size=asset["size"] + 1), "size differs"),
        (lambda asset: asset.update(digest="sha256:" + "0" * 64), "digest differs"),
    ],
)
def test_manifest_asset_bytes_are_authenticated_before_json_parse(
    tmp_path: Path,
    mutation,
    message: str,
) -> None:
    body = b"release"
    manifest = _manifest([_artifact("DicePP-v3.1.0-linux-amd64.zip", body)])
    release = _release(manifest, body)
    mutation(release["assets"][0])
    routes = {
        "https://api/releases?per_page=100&page=1": [_json_response([release])],
        "https://downloads/manifest": [_json_response(manifest)],
    }
    manager = ReleaseManager(
        layout=InstanceLayout.from_root(tmp_path),
        transport=Transport(routes),
        github_api="https://api",
        target=("linux", "amd64"),
        current_version_loader=lambda: "3.0.0",
    )

    with pytest.raises(ReleaseContractError, match=message):
        manager.discover(manual=True)


def test_discovery_requires_exact_platform_and_architecture(tmp_path: Path) -> None:
    body = b"linux release"
    manifest = _manifest([_artifact("DicePP-v3.1.0-linux-amd64.zip", body)])
    release = _release(manifest, body)
    transport = Transport(
        {
            "https://api/releases?per_page=100&page=1": [_json_response([release])],
            "https://downloads/manifest": [_json_response(manifest)],
        }
    )
    manager = ReleaseManager(
        layout=InstanceLayout.from_root(tmp_path),
        transport=transport,
        github_api="https://api",
        target=("windows", "arm64"),
        current_version_loader=lambda: "3.0.0",
    )

    with pytest.raises(ReleaseContractError, match="No release artifacts"):
        manager.discover(manual=True)


def test_discovery_reports_manager_compatibility_without_downloading(
    tmp_path: Path,
) -> None:
    body = b"future release"
    manifest = _manifest([_artifact("DicePP-v3.1.0-linux-amd64.zip", body)])
    manifest["minimum_manager_version"] = "999.0"
    release = _release(manifest, body)
    transport = Transport(
        {
            "https://api/releases?per_page=100&page=1": [_json_response([release])],
            "https://downloads/manifest": [_json_response(manifest)],
        }
    )
    manager = ReleaseManager(
        layout=InstanceLayout.from_root(tmp_path),
        transport=transport,
        github_api="https://api",
        target=("linux", "amd64"),
        current_version_loader=lambda: "3.0.0",
    )

    status = manager.discover(manual=True)

    assert status["available"]["compatible"] is False
    assert "older than required" in status["available"]["compatibility"]["problems"][0]
    with pytest.raises(ReleaseDownloadError, match="not compatible"):
        manager.download()


def test_different_positive_deployment_schema_is_valid_but_incompatible(
    tmp_path: Path,
) -> None:
    body = b"future topology"
    manifest = _manifest([_artifact("DicePP-v3.1.0-linux-amd64.zip", body)])
    manifest["deployment_schema_version"] = DEPLOYMENT_SCHEMA_VERSION + 1
    validate_release_manifest(manifest)
    release = _release(manifest, body)
    manager = ReleaseManager(
        layout=InstanceLayout.from_root(tmp_path),
        transport=Transport(
            {
                "https://api/releases?per_page=100&page=1": [
                    _json_response([release])
                ],
                "https://downloads/manifest": [_json_response(manifest)],
            }
        ),
        github_api="https://api",
        target=("linux", "amd64"),
        current_version_loader=lambda: "3.0.0",
    )

    status = manager.discover(manual=True)

    assert status["available"]["compatible"] is False
    assert any(
        "Deployment schema mismatch" in problem
        for problem in status["available"]["compatibility"]["problems"]
    )


def test_linux_manager_handoff_gate_fails_closed_without_protocol(
    tmp_path: Path,
) -> None:
    body = b"manager change"
    manifest = _manifest([_artifact("DicePP-v3.1.0-linux-amd64.zip", body)])
    manifest["change_scope"] = ["runtime", "manager"]
    validate_release_manifest(manifest)
    release = _release(manifest, body)
    manager = ReleaseManager(
        layout=InstanceLayout.from_root(tmp_path),
        transport=Transport(
            {
                "https://api/releases?per_page=100&page=1": [
                    _json_response([release])
                ],
                "https://downloads/manifest": [_json_response(manifest)],
            }
        ),
        github_api="https://api",
        target=("linux", "amd64"),
        current_version_loader=lambda: "3.0.0",
    )

    status = manager.discover(manual=True)

    assert status["available"]["compatible"] is False
    assert status["available"]["linux_manager_handoff_protocol"] is None
    assert any(
        "handoff protocol" in problem
        for problem in status["available"]["compatibility"]["problems"]
    )
    with pytest.raises(ReleaseDownloadError, match="not compatible"):
        manager.download()


def test_linux_manager_handoff_gate_accepts_supported_protocol(
    tmp_path: Path,
) -> None:
    body = b"manager change with protocol"
    manifest = _manifest([_artifact("DicePP-v3.1.0-linux-amd64.zip", body)])
    manifest["change_scope"] = ["runtime", "manager"]
    manifest["linux_manager_handoff_protocol"] = 1
    validate_release_manifest(manifest)
    release = _release(manifest, body)
    manager = ReleaseManager(
        layout=InstanceLayout.from_root(tmp_path),
        transport=Transport(
            {
                "https://api/releases?per_page=100&page=1": [
                    _json_response([release])
                ],
                "https://downloads/manifest": [_json_response(manifest)],
            }
        ),
        github_api="https://api",
        target=("linux", "amd64"),
        current_version_loader=lambda: "3.0.0",
    )

    status = manager.discover(manual=True)

    assert status["available"]["compatible"] is True
    assert status["available"]["linux_manager_handoff_protocol"] == 1


def test_windows_manager_scope_is_not_gated_by_linux_handoff_protocol(
    tmp_path: Path,
) -> None:
    body = b"windows manager change"
    manifest = _manifest([_artifact("DicePP-v3.1.0-linux-amd64.zip", body)])
    manifest["change_scope"] = ["runtime", "manager"]
    validate_release_manifest(manifest)
    velopack_body, _nupkg, _inner = _velopack_bundle()
    velopack_artifact = _velopack_artifact(velopack_body)
    manifest["artifacts"] = [
        item
        for item in manifest["artifacts"]
        if item["purpose"] != "velopack-bundle"
    ] + [velopack_artifact]
    manifest_bytes = _json_bytes(manifest)
    release = {
        "tag_name": "v3.1.0",
        "draft": False,
        "prerelease": False,
        "html_url": "https://github.com/example/release",
        "published_at": "2026-07-23T00:00:00Z",
        "assets": [
            {
                "name": "dicepp-release.json",
                "size": len(manifest_bytes),
                "digest": f"sha256:{hashlib.sha256(manifest_bytes).hexdigest()}",
                "browser_download_url": "https://downloads/manifest",
            },
            {
                "name": velopack_artifact["filename"],
                "size": len(velopack_body),
                "digest": f"sha256:{velopack_artifact['sha256']}",
                "browser_download_url": "https://downloads/artifact",
            },
        ],
    }
    manager = ReleaseManager(
        layout=InstanceLayout.from_root(tmp_path),
        transport=Transport(
            {
                "https://api/releases?per_page=100&page=1": [
                    _json_response([release])
                ],
                "https://downloads/manifest": [_json_response(manifest)],
            }
        ),
        github_api="https://api",
        target=("windows", "amd64"),
        current_version_loader=lambda: "3.0.0",
    )

    status = manager.discover(manual=True)

    assert status["available"]["compatible"] is True
    assert status["available"]["linux_manager_handoff_protocol"] is None


def test_discovery_skips_broken_candidate_and_selects_highest_newer_version(
    tmp_path: Path,
) -> None:
    body = b"release"
    bad_manifest = _manifest(
        [_artifact("DicePP-v3.3.0-linux-amd64.zip", body)],
        version="3.3.0",
    )
    good_manifest = _manifest(
        [_artifact("DicePP-v3.2.0-linux-amd64.zip", body)],
        version="3.2.0",
    )
    bad_release = _release(bad_manifest, body, asset_id="-bad")
    bad_release["assets"][0]["digest"] = "sha256:" + "0" * 64
    good_release = _release(good_manifest, body, asset_id="-good")
    transport = Transport(
        {
            "https://api/releases?per_page=100&page=1": [
                _json_response([good_release, bad_release])
            ],
            "https://downloads/manifest-bad": [_json_response(bad_manifest)],
            "https://downloads/manifest-good": [_json_response(good_manifest)],
        }
    )
    manager = ReleaseManager(
        layout=InstanceLayout.from_root(tmp_path),
        transport=transport,
        github_api="https://api",
        target=("linux", "amd64"),
        current_version_loader=lambda: "3.1.0",
    )

    status = manager.discover(manual=True)

    assert status["available"]["version"] == "3.2.0"
    assert status["discovery"]["status"] == "succeeded"
    assert status["discovery"]["candidate_errors"][0].startswith("v3.3.0:")
    assert [url for url, _headers in transport.requests] == [
        "https://api/releases?per_page=100&page=1",
        "https://downloads/manifest-bad",
        "https://downloads/manifest-good",
    ]


def test_discovery_validates_only_highest_eligible_manifest(tmp_path: Path) -> None:
    body = b"release"
    releases = []
    routes = {"https://api/releases?per_page=100&page=1": []}
    for version in ("3.1.0", "3.2.0", "3.3.0"):
        manifest = _manifest(
            [_artifact(f"DicePP-v{version}-linux-amd64.zip", body)],
            version=version,
        )
        suffix = f"-{version}"
        releases.append(_release(manifest, body, asset_id=suffix))
        routes[f"https://downloads/manifest{suffix}"] = [
            _json_response(manifest)
        ]
    routes["https://api/releases?per_page=100&page=1"] = [
        _json_response(releases)
    ]
    transport = Transport(routes)
    manager = ReleaseManager(
        layout=InstanceLayout.from_root(tmp_path),
        transport=transport,
        github_api="https://api",
        target=("linux", "amd64"),
        current_version_loader=lambda: "3.0.0",
    )

    status = manager.discover(manual=True)

    assert status["available"]["version"] == "3.3.0"
    assert len(transport.requests) == 2
    assert transport.requests[-1][0].endswith("manifest-3.3.0")


def test_discovery_never_offers_current_or_older_version(tmp_path: Path) -> None:
    body = b"old release"
    manifest = _manifest(
        [_artifact("DicePP-v3.0.0-linux-amd64.zip", body)],
        version="3.0.0",
    )
    release = _release(manifest, body)
    transport = Transport(
        {
            "https://api/releases?per_page=100&page=1": [_json_response([release])],
            "https://downloads/manifest": [_json_response(manifest)],
        }
    )
    manager = ReleaseManager(
        layout=InstanceLayout.from_root(tmp_path),
        transport=transport,
        github_api="https://api",
        target=("linux", "amd64"),
        current_version_loader=lambda: "3.0.0",
    )

    status = manager.discover(manual=True)

    assert status["available"] is None
    with pytest.raises(ReleaseDownloadError, match="Check the selected"):
        manager.download()


def test_discovery_cancellation_stops_pagination(tmp_path: Path) -> None:
    cancel_event = threading.Event()

    class CancellingTransport(Transport):
        def open(self, url, *, headers=None, timeout=30):
            response = super().open(url, headers=headers, timeout=timeout)
            cancel_event.set()
            return response

    page1 = "https://api/releases?per_page=100&page=1"
    transport = CancellingTransport(
        {page1: [_json_response([{"tag_name": f"v1.0.{i}"} for i in range(100)])]}
    )
    manager = ReleaseManager(
        layout=InstanceLayout.from_root(tmp_path),
        transport=transport,
        github_api="https://api",
        target=("linux", "amd64"),
        current_version_loader=lambda: "3.0.0",
    )
    operation = ReleaseOperation(
        kind="discovery",
        generation=-1,
        cancel_event=cancel_event,
    )

    with pytest.raises(ReleaseCancelledError, match="cancelled"):
        manager._discover_latest("stable", operation=operation)

    assert [url for url, _headers in transport.requests] == [page1]


def test_rollback_bundle_search_traverses_release_pages(tmp_path: Path) -> None:
    body, _nupkg, _inner = _velopack_bundle("3.0.0")
    manifest = _manifest(
        [_velopack_artifact(body)],
        version="3.0.0",
    )
    release = _release(manifest, body)
    filler = [
        {"tag_name": f"v2.0.{i}", "draft": False, "prerelease": False}
        for i in range(100)
    ]
    page1 = "https://api/releases?per_page=100&page=1"
    page2 = "https://api/releases?per_page=100&page=2"
    transport = Transport(
        {
            page1: [_json_response(filler)],
            page2: [_json_response([release])],
            "https://downloads/manifest": [_json_response(manifest)],
            "https://downloads/artifact": [
                Response(body, headers={"ETag": '"release-etag"'})
            ],
        }
    )
    manager = ReleaseManager(
        layout=InstanceLayout.from_root(tmp_path),
        transport=transport,
        github_api="https://api",
        target=("windows", "amd64"),
        current_version_loader=lambda: "3.0.0",
    )

    path, sha256 = manager.fetch_rollback_bundle("3.0.0")

    assert sha256 == hashlib.sha256(body).hexdigest()
    assert path.read_bytes() == body
    assert [url for url, _headers in transport.requests][:2] == [page1, page2]


def test_download_persists_verified_package_metadata_without_installing(
    tmp_path: Path,
) -> None:
    body = b"verified linux release"
    manifest = _manifest([_artifact("DicePP-v3.1.0-linux-amd64.zip", body)])
    release = _release(manifest, body)
    transport = Transport(
        {
            "https://api/releases?per_page=100&page=1": [_json_response([release])],
            "https://downloads/manifest": [_json_response(manifest)],
            "https://downloads/artifact": [
                Response(body, headers={"ETag": '"release-etag"'})
            ],
        }
    )
    layout = InstanceLayout.from_root(tmp_path)
    manager = ReleaseManager(
        layout=layout,
        transport=transport,
        github_api="https://api",
        target=("linux", "amd64"),
        current_version_loader=lambda: "3.0.0",
    )
    manager.discover(manual=True)

    status = manager.download()

    assert status["download"]["status"] == "verified"
    assert status["download"]["installable"] is True
    assert status["install_supported"] is False
    metadata = json.loads(
        (layout.manager_packages_dir / "3.1.0" / "verified-release.json").read_text()
    )
    assert metadata["artifact"]["sha256"] == manifest["artifacts"][0]["sha256"]
    assert metadata["verified_path"] == "DicePP-v3.1.0-linux-amd64.zip"


def test_windows_velopack_download_verifies_bundle_and_materializes_payload(
    tmp_path: Path,
) -> None:
    body, nupkg, inner = _velopack_bundle()
    artifact = {
        **_velopack_artifact(body),
        "download_url": "https://downloads/velopack-bundle",
    }
    compatibility = {
        "deployment_schema_version": DEPLOYMENT_SCHEMA_VERSION,
        "minimum_manager_version": MANAGER_VERSION,
        "catalog_version": 1,
        "catalog_digest": "1" * 64,
        "automatic_upgrade": True,
        "problems": [],
    }
    manager = ReleaseManager(
        layout=InstanceLayout.from_root(tmp_path),
        transport=Transport(
            {
                "https://downloads/velopack-bundle": [
                    Response(body, headers={"ETag": '"bundle"'})
                ]
            }
        ),
        target=("windows", "amd64"),
    )
    with manager._lock:
        manager._latest_channel = "stable"
        manager._latest = {
            "version": "3.1.0",
            "channel": "stable",
            "change_scope": ["runtime"],
            "linux_manager_handoff_protocol": None,
            "compatible": True,
            "compatibility": compatibility,
            "release_url": "https://example/release",
            "published_at": "2026-07-23T00:00:00Z",
            "artifacts": [artifact],
        }

    result = manager.download(purpose="velopack-bundle")

    assert result["download"]["status"] == "verified"
    version_dir = manager.layout.manager_packages_dir / "3.1.0"
    metadata = json.loads(
        (version_dir / "verified-release.json").read_text(encoding="utf-8")
    )
    assert metadata["artifact"]["purpose"] == "velopack-bundle"
    assert metadata["bundle_manifest"] == inner
    generation = metadata["generation"]
    assert metadata["verified_path"] == f"velopack-{generation}.win-x64.zip"
    assert metadata["payload_verified_path"] == f"payload-{generation}.nupkg"
    assert (
        version_dir / metadata["payload_verified_path"]
    ).read_bytes() == nupkg
    assert set(result["packages"][0]["files"]) == {
        metadata["verified_path"],
        metadata["payload_verified_path"],
        "verified-release.json",
    }


@pytest.mark.parametrize("mutation", ["replaced", "missing", "reparse"])
def test_windows_metadata_is_not_published_if_materialized_payload_changes(
    tmp_path: Path,
    mutation: str,
) -> None:
    body, _nupkg, _inner = _velopack_bundle()
    artifact = _velopack_artifact(body)
    layout = InstanceLayout.from_root(tmp_path / "instance")
    version_dir = layout.manager_packages_dir / "3.1.0"
    version_dir.mkdir(parents=True)
    target = (
        version_dir / "velopack-11111111111111111111111111111111.win-x64.zip"
    )
    target.write_bytes(body)
    manager = ReleaseManager(layout=layout, target=("windows", "amd64"))
    release = {
        "version": "3.1.0",
        "channel": "stable",
        "change_scope": ["runtime"],
        "linux_manager_handoff_protocol": None,
        "compatibility": {
            "deployment_schema_version": DEPLOYMENT_SCHEMA_VERSION,
            "minimum_manager_version": MANAGER_VERSION,
            "catalog_version": 1,
            "catalog_digest": "1" * 64,
            "automatic_upgrade": True,
            "problems": [],
        },
    }
    payload, inner = manager._materialize_velopack_bundle(
        release,
        artifact,
        target,
    )
    outside = tmp_path / "outside.nupkg"
    if mutation == "missing":
        payload.unlink()
    elif mutation == "replaced":
        payload.write_bytes(b"replacement")
    else:
        payload.unlink()
        outside.write_bytes(b"outside")
        try:
            payload.symlink_to(outside)
        except OSError as exc:
            pytest.skip(f"File links are unavailable: {exc}")

    with pytest.raises((ReleaseDownloadError, OSError)):
        manager._write_verified_metadata(
            release,
            artifact,
            target,
            payload_path=payload,
            bundle_manifest=inner,
            completed_at="2026-07-31T00:00:00+00:00",
        )

    assert not (version_dir / "verified-release.json").exists()
    assert not target.exists()
    if mutation == "reparse":
        assert outside.read_bytes() == b"outside"


@pytest.mark.parametrize(
    "failure",
    ["bundle", "payload", "rebind", "metadata"],
)
def test_failed_windows_generation_preserves_previously_verified_generation(
    monkeypatch,
    tmp_path: Path,
    failure: str,
) -> None:
    body, nupkg, _inner = _velopack_bundle()
    artifact = _velopack_artifact(body)
    layout = InstanceLayout.from_root(tmp_path)
    version_dir = layout.manager_packages_dir / "3.1.0"
    version_dir.mkdir(parents=True)
    manager = ReleaseManager(layout=layout, target=("windows", "amd64"))
    release = {
        "version": "3.1.0",
        "channel": "stable",
        "change_scope": ["runtime"],
        "linux_manager_handoff_protocol": None,
        "compatibility": {
            "deployment_schema_version": DEPLOYMENT_SCHEMA_VERSION,
            "minimum_manager_version": MANAGER_VERSION,
            "catalog_version": 1,
            "catalog_digest": "1" * 64,
            "automatic_upgrade": True,
            "problems": [],
        },
    }

    old_token = "1" * 32
    old_bundle = version_dir / f"velopack-{old_token}.win-x64.zip"
    old_bundle.write_bytes(body)
    old_payload, old_inner = manager._materialize_velopack_bundle(
        release,
        artifact,
        old_bundle,
    )
    metadata_path = manager._write_verified_metadata(
        release,
        artifact,
        old_bundle,
        payload_path=old_payload,
        bundle_manifest=old_inner,
        completed_at="2026-07-31T00:00:00+00:00",
    )
    old_metadata = metadata_path.read_bytes()

    new_token = "2" * 32
    new_bundle = version_dir / f"velopack-{new_token}.win-x64.zip"
    new_bundle.write_bytes(body)
    new_payload, new_inner = manager._materialize_velopack_bundle(
        release,
        artifact,
        new_bundle,
    )
    if failure == "bundle":
        new_bundle.write_bytes(b"invalid bundle")
    elif failure == "payload":
        new_payload.write_bytes(b"invalid payload")
    elif failure == "rebind":
        original_validate = release_module._validate_regular_path
        calls = 0

        def report_changed_identity(path, trusted_parent, *, allow_missing):
            nonlocal calls
            result = original_validate(
                path,
                trusted_parent,
                allow_missing=allow_missing,
            )
            if path == new_payload and result is not None:
                calls += 1
                if calls == 2:
                    return SimpleNamespace(
                        st_mode=result.st_mode,
                        st_nlink=result.st_nlink,
                        st_dev=result.st_dev,
                        st_ino=result.st_ino + 1,
                    )
            return result

        monkeypatch.setattr(
            release_module,
            "_validate_regular_path",
            report_changed_identity,
        )
    else:
        monkeypatch.setattr(
            release_module,
            "_atomic_publish_json",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError("injected metadata failure")
            ),
        )

    with pytest.raises((ReleaseDownloadError, OSError)):
        manager._write_verified_metadata(
            release,
            artifact,
            new_bundle,
            payload_path=new_payload,
            bundle_manifest=new_inner,
            completed_at="2026-07-31T01:00:00+00:00",
        )

    assert metadata_path.read_bytes() == old_metadata
    assert old_bundle.read_bytes() == body
    assert old_payload.read_bytes() == nupkg
    assert not new_bundle.exists()
    assert not new_payload.exists()


def test_windows_generation_handles_reject_replacement_until_metadata_switch(
    monkeypatch,
    tmp_path: Path,
) -> None:
    if release_module.os.name != "nt":
        pytest.skip("Windows file-share semantics")
    body, _nupkg, _inner = _velopack_bundle()
    artifact = _velopack_artifact(body)
    layout = InstanceLayout.from_root(tmp_path)
    version_dir = layout.manager_packages_dir / "3.1.0"
    version_dir.mkdir(parents=True)
    manager = ReleaseManager(layout=layout, target=("windows", "amd64"))
    release = {
        "version": "3.1.0",
        "channel": "stable",
        "change_scope": ["runtime"],
        "linux_manager_handoff_protocol": None,
        "compatibility": {
            "deployment_schema_version": DEPLOYMENT_SCHEMA_VERSION,
            "minimum_manager_version": MANAGER_VERSION,
            "catalog_version": 1,
            "catalog_digest": "1" * 64,
            "automatic_upgrade": True,
            "problems": [],
        },
    }
    token = "3" * 32
    target = version_dir / f"velopack-{token}.win-x64.zip"
    target.write_bytes(body)
    payload, inner = manager._materialize_velopack_bundle(
        release,
        artifact,
        target,
    )
    replacement = version_dir / "replacement.zip"
    replacement.write_bytes(body)
    original_validate = release_module.validate_velopack_bundle
    attempts = []

    def attempt_replacement(path, **kwargs):
        with pytest.raises(OSError):
            replacement.replace(path)
        attempts.append("bundle")
        with pytest.raises(OSError):
            payload.write_bytes(b"replacement")
        attempts.append("payload")
        return original_validate(path, **kwargs)

    monkeypatch.setattr(
        release_module,
        "validate_velopack_bundle",
        attempt_replacement,
    )

    metadata_path = manager._write_verified_metadata(
        release,
        artifact,
        target,
        payload_path=payload,
        bundle_manifest=inner,
        completed_at="2026-07-31T00:00:00+00:00",
    )

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert attempts == ["bundle", "payload"]
    assert metadata["generation"] == token
    assert metadata["verified_path"] == target.name
    assert metadata["payload_verified_path"] == payload.name
    assert target.read_bytes() == body
    assert replacement.read_bytes() == body


def test_repeated_windows_download_replaces_one_complete_generation(
    tmp_path: Path,
) -> None:
    body, _nupkg, _inner = _velopack_bundle()
    artifact = {
        **_velopack_artifact(body),
        "download_url": "https://downloads/velopack-bundle",
    }
    compatibility = {
        "deployment_schema_version": DEPLOYMENT_SCHEMA_VERSION,
        "minimum_manager_version": MANAGER_VERSION,
        "catalog_version": 1,
        "catalog_digest": "1" * 64,
        "automatic_upgrade": True,
        "problems": [],
    }
    manager = ReleaseManager(
        layout=InstanceLayout.from_root(tmp_path),
        transport=Transport(
            {
                "https://downloads/velopack-bundle": [
                    Response(body, headers={"ETag": '"one"'}),
                    Response(body, headers={"ETag": '"two"'}),
                ]
            }
        ),
        target=("windows", "amd64"),
    )
    with manager._lock:
        manager._latest_channel = "stable"
        manager._latest = {
            "version": "3.1.0",
            "channel": "stable",
            "change_scope": ["runtime"],
            "linux_manager_handoff_protocol": None,
            "compatible": True,
            "compatibility": compatibility,
            "release_url": "https://example/release",
            "published_at": "2026-07-23T00:00:00Z",
            "artifacts": [artifact],
        }

    manager.download(purpose="velopack-bundle")
    manager.download(purpose="velopack-bundle")

    version_dir = manager.layout.manager_packages_dir / "3.1.0"
    metadata = json.loads(
        (version_dir / "verified-release.json").read_text(encoding="utf-8")
    )
    assert sorted(path.name for path in version_dir.iterdir()) == sorted(
        [
            "verified-release.json",
            metadata["verified_path"],
            metadata["payload_verified_path"],
        ]
    )
    assert metadata["verified_path"] == (
        f"velopack-{metadata['generation']}.win-x64.zip"
    )
    assert metadata["payload_verified_path"] == (
        f"payload-{metadata['generation']}.nupkg"
    )


def test_next_windows_download_removes_only_unpublished_managed_orphans(
    tmp_path: Path,
) -> None:
    body, _nupkg, _inner = _velopack_bundle()
    artifact = {
        **_velopack_artifact(body),
        "download_url": "https://downloads/velopack-bundle",
    }
    layout = InstanceLayout.from_root(tmp_path / "instance")
    version_dir = layout.manager_packages_dir / "3.1.0"
    version_dir.mkdir(parents=True)
    orphan_names = {
        f"velopack-{'1' * 32}.win-x64.zip",
        f"payload-{'1' * 32}.nupkg",
        f"velopack-{'2' * 32}.win-x64.zip",
        f"payload-{'3' * 32}.nupkg",
    }
    for name in orphan_names:
        (version_dir / name).write_bytes(b"crash orphan")
    decoys = {
        f"velopack-{'4' * 31}.win-x64.zip",
        f"payload-{'5' * 32}.nupkg.backup",
        "operator-note.txt",
    }
    for name in decoys:
        (version_dir / name).write_bytes(b"must remain")
    outside = tmp_path / "outside.zip"
    outside.write_bytes(b"outside target must remain")
    reparse_name = f"velopack-{'6' * 32}.win-x64.zip"
    reparse = version_dir / reparse_name
    try:
        reparse.symlink_to(outside)
    except OSError:
        reparse = None
    compatibility = {
        "deployment_schema_version": DEPLOYMENT_SCHEMA_VERSION,
        "minimum_manager_version": MANAGER_VERSION,
        "catalog_version": 1,
        "catalog_digest": "1" * 64,
        "automatic_upgrade": True,
        "problems": [],
    }
    manager = ReleaseManager(
        layout=layout,
        transport=Transport(
            {
                "https://downloads/velopack-bundle": [
                    Response(body, headers={"ETag": '"bundle"'})
                ]
            }
        ),
        target=("windows", "amd64"),
    )
    with manager._lock:
        manager._latest_channel = "stable"
        manager._latest = {
            "version": "3.1.0",
            "channel": "stable",
            "change_scope": ["runtime"],
            "linux_manager_handoff_protocol": None,
            "compatible": True,
            "compatibility": compatibility,
            "release_url": "https://example/release",
            "published_at": "2026-07-23T00:00:00Z",
            "artifacts": [artifact],
        }

    manager.download(purpose="velopack-bundle")

    metadata = json.loads(
        (version_dir / "verified-release.json").read_text(encoding="utf-8")
    )
    remaining = {path.name for path in version_dir.iterdir()}
    assert not orphan_names & remaining
    assert decoys <= remaining
    assert metadata["verified_path"] in remaining
    assert metadata["payload_verified_path"] in remaining
    if reparse is not None:
        assert reparse_name not in remaining
    assert outside.read_bytes() == b"outside target must remain"
    packages_guard = release_module._capture_trusted_directory(
        layout.manager_packages_dir,
        root=layout.root,
    )
    version_guard = release_module._capture_trusted_directory(
        version_dir,
        root=layout.root,
        parent=packages_guard,
    )
    release_module._cleanup_velopack_orphans(
        version_guard,
        in_progress_generation="f" * 32,
    )
    assert (version_dir / metadata["verified_path"]).is_file()
    assert (version_dir / metadata["payload_verified_path"]).is_file()


@pytest.mark.parametrize(
    "metadata_state",
    [
        "writer",
        "malformed",
        "invalid-utf8",
        "array",
        "scalar",
        "oversized",
        "reparse",
    ],
)
def test_orphan_cleanup_fails_closed_when_generation_metadata_is_unreadable(
    tmp_path: Path,
    metadata_state: str,
) -> None:
    if metadata_state == "writer" and release_module.os.name != "nt":
        pytest.skip("Windows sharing-conflict semantics")
    layout = InstanceLayout.from_root(tmp_path / "instance")
    version_dir = layout.manager_packages_dir / "3.1.0"
    version_dir.mkdir(parents=True)
    current_token = "1" * 32
    current_bundle = version_dir / f"velopack-{current_token}.win-x64.zip"
    current_payload = version_dir / f"payload-{current_token}.nupkg"
    current_bundle.write_bytes(b"current bundle")
    current_payload.write_bytes(b"current payload")
    orphan = version_dir / f"velopack-{'2' * 32}.win-x64.zip"
    orphan.write_bytes(b"must remain while metadata is unknown")
    metadata = version_dir / "verified-release.json"
    valid_metadata = {
        "generation": current_token,
        "verified_path": current_bundle.name,
        "payload_verified_path": current_payload.name,
    }
    outside = tmp_path / "outside-metadata.json"
    if metadata_state == "malformed":
        metadata.write_bytes(b"{")
    elif metadata_state == "invalid-utf8":
        metadata.write_bytes(b"\xff")
    elif metadata_state == "array":
        metadata.write_bytes(b"[]")
    elif metadata_state == "scalar":
        metadata.write_bytes(b"42")
    elif metadata_state == "oversized":
        metadata.write_bytes(b" " * (release_module.MAX_RELEASE_JSON_BYTES + 1))
    elif metadata_state == "reparse":
        outside.write_text(json.dumps(valid_metadata), encoding="utf-8")
        try:
            metadata.symlink_to(outside)
        except OSError as exc:
            pytest.skip(f"File links are unavailable: {exc}")
    else:
        metadata.write_text(json.dumps(valid_metadata), encoding="utf-8")
    packages_guard = release_module._capture_trusted_directory(
        layout.manager_packages_dir,
        root=layout.root,
    )
    version_guard = release_module._capture_trusted_directory(
        version_dir,
        root=layout.root,
        parent=packages_guard,
    )

    if metadata_state == "writer":
        with metadata.open("r+b"):
            release_module._cleanup_velopack_orphans(
                version_guard,
                in_progress_generation="f" * 32,
            )
    else:
        release_module._cleanup_velopack_orphans(
            version_guard,
            in_progress_generation="f" * 32,
        )

    assert current_bundle.read_bytes() == b"current bundle"
    assert current_payload.read_bytes() == b"current payload"
    assert orphan.read_bytes() == b"must remain while metadata is unknown"
    assert release_module.os.path.lexists(metadata)
    if metadata_state == "reparse":
        assert outside.read_text(encoding="utf-8") == json.dumps(valid_metadata)


def test_orphan_cleanup_runs_when_generation_metadata_is_confirmed_missing(
    tmp_path: Path,
) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    version_dir = layout.manager_packages_dir / "3.1.0"
    version_dir.mkdir(parents=True)
    orphan = version_dir / f"payload-{'2' * 32}.nupkg"
    orphan.write_bytes(b"crash orphan")
    packages_guard = release_module._capture_trusted_directory(
        layout.manager_packages_dir,
        root=layout.root,
    )
    version_guard = release_module._capture_trusted_directory(
        version_dir,
        root=layout.root,
        parent=packages_guard,
    )

    release_module._cleanup_velopack_orphans(
        version_guard,
        in_progress_generation="f" * 32,
    )

    assert not orphan.exists()


@pytest.mark.parametrize(
    "metadata_body",
    [
        pytest.param(
            lambda: b" " * (release_module.MAX_RELEASE_JSON_BYTES + 1),
            id="oversized",
        ),
        pytest.param(lambda: b"[]", id="array"),
        pytest.param(lambda: b"null", id="scalar"),
        pytest.param(lambda: b"\xff", id="invalid-utf8"),
        pytest.param(lambda: b"{", id="invalid-json"),
    ],
)
def test_unparseable_generation_metadata_does_not_block_artifact_download(
    tmp_path: Path,
    metadata_body,
) -> None:
    body, _nupkg, _inner = _velopack_bundle()
    artifact = {
        **_velopack_artifact(body),
        "download_url": "https://downloads/velopack-bundle",
    }
    layout = InstanceLayout.from_root(tmp_path)
    version_dir = layout.manager_packages_dir / "3.1.0"
    version_dir.mkdir(parents=True)
    metadata = version_dir / "verified-release.json"
    original_metadata = metadata_body()
    metadata.write_bytes(original_metadata)
    orphan = version_dir / f"payload-{'2' * 32}.nupkg"
    orphan.write_bytes(b"must remain")
    manager = ReleaseManager(
        layout=layout,
        transport=Transport(
            {
                "https://downloads/velopack-bundle": [
                    Response(body, headers={"ETag": '"bundle"'})
                ]
            }
        ),
        target=("windows", "amd64"),
    )

    target = manager._download_artifact("3.1.0", artifact)

    assert target.read_bytes() == body
    assert metadata.read_bytes() == original_metadata
    assert orphan.read_bytes() == b"must remain"


def test_verified_state_is_persisted_only_after_package_and_metadata(
    monkeypatch,
    tmp_path: Path,
) -> None:
    body = b"verified linux release"
    manifest = _manifest([_artifact("DicePP-v3.1.0-linux-amd64.zip", body)])
    release = _release(manifest, body)
    layout = InstanceLayout.from_root(tmp_path)
    target = (
        layout.manager_packages_dir
        / "3.1.0"
        / "DicePP-v3.1.0-linux-amd64.zip"
    )
    manager = ReleaseManager(
        layout=layout,
        transport=Transport(
            {
                "https://api/releases?per_page=100&page=1": [
                    _json_response([release])
                ],
                "https://downloads/manifest": [_json_response(manifest)],
                "https://downloads/artifact": [
                    Response(body, headers={"ETag": '"release-etag"'})
                ],
            }
        ),
        github_api="https://api",
        target=("linux", "amd64"),
        current_version_loader=lambda: "3.0.0",
    )
    manager.discover(manual=True)
    events = []
    original = release_module._atomic_write_json

    def observe(path: Path, payload, **kwargs):
        original(path, payload, **kwargs)
        events.append(
            (
                path.name,
                payload.get("download", {}).get("status")
                if isinstance(payload, dict)
                else None,
                target.is_file(),
            )
        )

    monkeypatch.setattr(release_module, "_atomic_write_json", observe)

    manager.download()

    metadata_index = next(
        index
        for index, event in enumerate(events)
        if event[0] == "verified-release.json"
    )
    state_index = next(
        index
        for index, event in enumerate(events)
        if event[:2] == ("release-state.json", "verified")
    )
    assert events[metadata_index][2] is True
    assert metadata_index < state_index


def test_range_ignored_restarts_from_zero_and_verifies(tmp_path: Path) -> None:
    body = b"complete verified package"
    artifact = {
        **_artifact("DicePP-v3.1.0-linux-amd64.zip", body),
        "download_url": "https://downloads/artifact",
    }
    layout = InstanceLayout.from_root(tmp_path)
    version_dir = layout.manager_packages_dir / "3.1.0"
    version_dir.mkdir(parents=True)
    part = version_dir / f"{artifact['filename']}.part"
    part.write_bytes(body[:8])
    part.with_suffix(part.suffix + ".json").write_text(
        json.dumps(
            {
                "url": artifact["download_url"],
                "sha256": artifact["sha256"],
                "validator": '"old-etag"',
            }
        ),
        encoding="utf-8",
    )
    transport = Transport(
        {
            "https://downloads/artifact": [
                Response(body, headers={"ETag": '"new-etag"'}),
                Response(body, headers={"ETag": '"new-etag"'}),
            ]
        }
    )
    manager = ReleaseManager(
        layout=layout,
        transport=transport,
        target=("linux", "amd64"),
    )

    target = manager._download_artifact("3.1.0", artifact)

    assert target.read_bytes() == body
    assert transport.requests[0][1]["Range"] == "bytes=8-"
    assert "Range" not in transport.requests[1][1]
    assert not part.exists()
    assert not part.with_suffix(part.suffix + ".json").exists()


def test_valid_resume_requires_exact_content_range_and_validator(tmp_path: Path) -> None:
    body = b"0123456789abcdef"
    artifact = {
        **_artifact("DicePP-v3.1.0-linux-amd64.zip", body),
        "download_url": "https://downloads/artifact",
    }
    layout = InstanceLayout.from_root(tmp_path)
    version_dir = layout.manager_packages_dir / "3.1.0"
    version_dir.mkdir(parents=True)
    part = version_dir / f"{artifact['filename']}.part"
    offset = 5
    part.write_bytes(body[:offset])
    metadata = part.with_suffix(part.suffix + ".json")
    metadata.write_text(
        json.dumps(
            {
                "url": artifact["download_url"],
                "sha256": artifact["sha256"],
                "validator": '"etag"',
            }
        ),
        encoding="utf-8",
    )
    transport = Transport(
        {
            "https://downloads/artifact": [
                Response(
                    body[offset:],
                    status=206,
                    headers={
                        "ETag": '"etag"',
                        "Content-Range": f"bytes {offset}-{len(body) - 1}/{len(body)}",
                    },
                )
            ]
        }
    )
    manager = ReleaseManager(
        layout=layout,
        transport=transport,
        target=("linux", "amd64"),
    )

    target = manager._download_artifact("3.1.0", artifact)

    assert target.read_bytes() == body
    assert len(transport.requests) == 1


def test_production_transport_exposes_416_and_download_restarts_without_range(
    monkeypatch,
    tmp_path: Path,
) -> None:
    body = b"complete package"
    artifact = {
        **_artifact("DicePP-v3.1.0-linux-amd64.zip", body),
        "download_url": "https://downloads/artifact",
    }
    layout = InstanceLayout.from_root(tmp_path)
    version_dir = layout.manager_packages_dir / "3.1.0"
    version_dir.mkdir(parents=True)
    part = version_dir / f"{artifact['filename']}.part"
    part.write_bytes(body[:4])
    part.with_suffix(part.suffix + ".json").write_text(
        json.dumps(
            {
                "url": artifact["download_url"],
                "sha256": artifact["sha256"],
                "validator": '"etag"',
            }
        ),
        encoding="utf-8",
    )
    calls = []

    class UrllibResponse:
        status = 200
        headers = {"ETag": '"new"'}

        def __init__(self):
            self.stream = io.BytesIO(body)

        def read(self, size=-1):
            return self.stream.read(size)

        def close(self):
            pass

        def getcode(self):
            return self.status

    def fake_urlopen(request, timeout):
        calls.append(dict(request.header_items()))
        if len(calls) == 1:
            raise urllib.error.HTTPError(
                request.full_url,
                416,
                "Range Not Satisfiable",
                {"ETag": '"etag"'},
                io.BytesIO(b""),
            )
        return UrllibResponse()

    monkeypatch.setattr(release_module.urllib.request, "urlopen", fake_urlopen)
    manager = ReleaseManager(
        layout=layout,
        transport=UrlTransport(),
        target=("linux", "amd64"),
    )

    target = manager._download_artifact("3.1.0", artifact)

    assert target.read_bytes() == body
    assert "Range" in calls[0]
    assert "Range" not in calls[1]


def test_digest_failure_removes_untrusted_partial(
    monkeypatch,
    tmp_path: Path,
) -> None:
    expected = b"expected"
    artifact = {
        **_artifact("DicePP-v3.1.0-linux-amd64.zip", expected),
        "download_url": "https://downloads/artifact",
    }
    transport = Transport(
        {
            "https://downloads/artifact": [
                Response(b"corrupt!", headers={"ETag": '"etag"'})
                for _ in range(5)
            ]
        }
    )
    monkeypatch.setattr(
        release_module, "_DOWNLOAD_BACKOFF_SECONDS", (0.0,) * 5
    )
    layout = InstanceLayout.from_root(tmp_path)
    manager = ReleaseManager(
        layout=layout,
        transport=transport,
        target=("linux", "amd64"),
    )

    with pytest.raises(ReleaseDownloadError, match="SHA-256") as excinfo:
        manager._download_artifact("3.1.0", artifact)

    assert "after 5 attempts" in str(excinfo.value)
    assert len(transport.requests) == 5
    version_dir = layout.manager_packages_dir / "3.1.0"
    assert list(version_dir.iterdir()) == []


def test_artifact_stream_aborts_immediately_when_manifest_size_is_exceeded(
    tmp_path: Path,
) -> None:
    expected = b"small"
    artifact = {
        **_artifact("DicePP-v3.1.0-linux-amd64.zip", expected),
        "download_url": "https://downloads/artifact",
    }

    class EndlessResponse(Response):
        def __init__(self) -> None:
            super().__init__(b"", headers={"ETag": '"etag"'})
            self.read_calls = 0

        def read(self, size: int = -1) -> bytes:
            self.read_calls += 1
            return b"x" * size

    response = EndlessResponse()
    layout = InstanceLayout.from_root(tmp_path)
    manager = ReleaseManager(
        layout=layout,
        transport=Transport({"https://downloads/artifact": [response]}),
        target=("linux", "amd64"),
    )

    with pytest.raises(ReleaseDownloadError, match="exceeds manifest"):
        manager._download_artifact("3.1.0", artifact)

    assert response.read_calls == 1
    assert list((layout.manager_packages_dir / "3.1.0").iterdir()) == []


def test_resumed_artifact_stream_uses_the_same_remaining_size_limit(
    tmp_path: Path,
) -> None:
    body = b"0123456789"
    artifact = {
        **_artifact("package.zip", body),
        "download_url": "https://downloads/artifact",
    }
    layout = InstanceLayout.from_root(tmp_path)
    version_dir = layout.manager_packages_dir / "3.1.0"
    version_dir.mkdir(parents=True)
    part = version_dir / "package.zip.part"
    part.write_bytes(body[:3])
    part.with_suffix(".part.json").write_text(
        json.dumps(
            {
                "url": artifact["download_url"],
                "sha256": artifact["sha256"],
                "validator": '"etag"',
            }
        ),
        encoding="utf-8",
    )
    manager = ReleaseManager(
        layout=layout,
        transport=Transport(
            {
                "https://downloads/artifact": [
                    Response(
                        body[3:] + b"x",
                        status=206,
                        headers={
                            "ETag": '"etag"',
                            "Content-Range": "bytes 3-9/10",
                        },
                    )
                ]
            }
        ),
        target=("linux", "amd64"),
    )

    with pytest.raises(ReleaseDownloadError, match="exceeds manifest"):
        manager._download_artifact("3.1.0", artifact)

    assert list(version_dir.iterdir()) == []


def test_truncated_download_resumes_with_range_and_verifies(
    monkeypatch,
    tmp_path: Path,
) -> None:
    body = b"partial body that completes on resume"
    artifact = {
        **_artifact("DicePP-v3.1.0-linux-amd64.zip", body),
        "download_url": "https://downloads/artifact",
    }
    cut = 12
    transport = Transport(
        {
            "https://downloads/artifact": [
                Response(body[:cut], headers={"ETag": '"etag"'}),
                Response(
                    body[cut:],
                    status=206,
                    headers={
                        "ETag": '"etag"',
                        "Content-Range": f"bytes {cut}-{len(body) - 1}/{len(body)}",
                    },
                ),
            ]
        }
    )
    monkeypatch.setattr(
        release_module, "_DOWNLOAD_BACKOFF_SECONDS", (0.0,) * 5
    )
    manager = ReleaseManager(
        layout=InstanceLayout.from_root(tmp_path),
        transport=transport,
        target=("linux", "amd64"),
    )

    target = manager._download_artifact("3.1.0", artifact)

    assert target.read_bytes() == body
    assert len(transport.requests) == 2
    assert "Range" not in transport.requests[0][1]
    assert transport.requests[1][1]["Range"] == f"bytes={cut}-"
    assert transport.requests[1][1]["If-Range"] == '"etag"'
    version_dir = manager.layout.manager_packages_dir / "3.1.0"
    assert not (version_dir / f"{artifact['filename']}.part").exists()
    assert not (version_dir / f"{artifact['filename']}.part.json").exists()


def test_zero_progress_truncation_fails_after_five_attempts(
    monkeypatch,
    tmp_path: Path,
) -> None:
    body = b"0123456789abcdef"
    artifact = {
        **_artifact("package.zip", body),
        "download_url": "https://downloads/artifact",
    }
    layout = InstanceLayout.from_root(tmp_path)
    version_dir = layout.manager_packages_dir / "3.1.0"
    version_dir.mkdir(parents=True)
    part = version_dir / "package.zip.part"
    offset = 5
    part.write_bytes(body[:offset])
    part.with_suffix(".part.json").write_text(
        json.dumps(
            {
                "url": artifact["download_url"],
                "sha256": artifact["sha256"],
                "validator": '"etag"',
            }
        ),
        encoding="utf-8",
    )
    transport = Transport(
        {
            "https://downloads/artifact": [
                Response(
                    b"",
                    status=206,
                    headers={
                        "ETag": '"etag"',
                        "Content-Range": f"bytes {offset}-{len(body) - 1}/{len(body)}",
                    },
                )
                for _ in range(5)
            ]
        }
    )
    monkeypatch.setattr(
        release_module, "_DOWNLOAD_BACKOFF_SECONDS", (0.0,) * 5
    )
    manager = ReleaseManager(
        layout=layout,
        transport=transport,
        target=("linux", "amd64"),
    )

    with pytest.raises(ReleaseDownloadError, match="after 5 attempts"):
        manager._download_artifact("3.1.0", artifact)

    assert len(transport.requests) == 5
    assert all(
        headers["Range"] == f"bytes={offset}-"
        for _url, headers in transport.requests
    )
    assert part.read_bytes() == body[:offset]
    assert part.with_suffix(".part.json").is_file()


def test_progress_resets_the_no_progress_budget(
    monkeypatch,
    tmp_path: Path,
) -> None:
    body = b"0123456789abcdefghij"
    artifact = {
        **_artifact("package.zip", body),
        "download_url": "https://downloads/artifact",
    }

    def partial(start: int, end: int) -> Response:
        if start == 0:
            return Response(body[start:end], headers={"ETag": '"etag"'})
        return Response(
            body[start:end],
            status=206,
            headers={
                "ETag": '"etag"',
                "Content-Range": f"bytes {start}-{len(body) - 1}/{len(body)}",
            },
        )

    # 每段进展之间插入 4 次零进展截断；没有预算重置时累计 12 次零进展
    # 早已超过 5 次上限。
    responses = (
        [partial(0, 0) for _ in range(4)]
        + [partial(0, 8)]
        + [partial(8, 8) for _ in range(4)]
        + [partial(8, 16)]
        + [partial(16, 16) for _ in range(4)]
        + [partial(16, len(body))]
    )
    transport = Transport({"https://downloads/artifact": responses})
    monkeypatch.setattr(
        release_module, "_DOWNLOAD_BACKOFF_SECONDS", (0.0,) * 5
    )
    manager = ReleaseManager(
        layout=InstanceLayout.from_root(tmp_path),
        transport=transport,
        target=("linux", "amd64"),
    )

    target = manager._download_artifact("3.1.0", artifact)

    assert target.read_bytes() == body
    assert len(transport.requests) == 15


def test_cancel_during_retry_backoff_raises_promptly(tmp_path: Path) -> None:
    body = b"interrupted download body"
    artifact = {
        **_artifact("package.zip", body),
        "download_url": "https://downloads/artifact",
    }

    class BackoffCancelEvent:
        def __init__(self) -> None:
            self._set = False
            self.waits: list[float] = []

        def is_set(self) -> bool:
            return self._set

        def set(self) -> None:
            self._set = True

        def wait(self, timeout: float | None = None) -> bool:
            self.waits.append(timeout)
            self._set = True
            return True

    cancel_event = BackoffCancelEvent()
    operation = ReleaseOperation(
        kind="download",
        generation=-1,
        cancel_event=cancel_event,
    )
    manager = ReleaseManager(
        layout=InstanceLayout.from_root(tmp_path),
        transport=Transport(
            {
                "https://downloads/artifact": [
                    Response(body[:6], headers={"ETag": '"etag"'})
                ]
            }
        ),
        target=("linux", "amd64"),
    )

    with pytest.raises(ReleaseCancelledError, match="cancelled"):
        manager._download_artifact("3.1.0", artifact, operation=operation)

    assert cancel_event.waits == [5.0]
    part = (
        manager.layout.manager_packages_dir / "3.1.0" / "package.zip.part"
    )
    assert part.read_bytes() == body[:6]


def test_digest_failure_redownloads_from_zero_and_verifies(
    monkeypatch,
    tmp_path: Path,
) -> None:
    body = b"expected"
    artifact = {
        **_artifact("package.zip", body),
        "download_url": "https://downloads/artifact",
    }
    transport = Transport(
        {
            "https://downloads/artifact": [
                Response(b"corrupt!", headers={"ETag": '"etag"'}),
                Response(body, headers={"ETag": '"etag"'}),
            ]
        }
    )
    monkeypatch.setattr(
        release_module, "_DOWNLOAD_BACKOFF_SECONDS", (0.0,) * 5
    )
    manager = ReleaseManager(
        layout=InstanceLayout.from_root(tmp_path),
        transport=transport,
        target=("linux", "amd64"),
    )

    target = manager._download_artifact("3.1.0", artifact)

    assert target.read_bytes() == body
    assert len(transport.requests) == 2
    assert all(
        "Range" not in headers for _url, headers in transport.requests
    )


def test_http_error_fails_immediately_without_retry(
    monkeypatch,
    tmp_path: Path,
) -> None:
    artifact = {
        **_artifact("package.zip", b"forbidden package"),
        "download_url": "https://downloads/artifact",
    }
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(dict(request.header_items()))
        raise urllib.error.HTTPError(
            request.full_url,
            403,
            "Forbidden",
            {},
            io.BytesIO(b""),
        )

    monkeypatch.setattr(release_module.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(
        release_module, "_DOWNLOAD_BACKOFF_SECONDS", (0.0,) * 5
    )
    manager = ReleaseManager(
        layout=InstanceLayout.from_root(tmp_path),
        transport=UrlTransport(),
        target=("linux", "amd64"),
    )

    with pytest.raises(ReleaseError, match="HTTP 403"):
        manager._download_artifact("3.1.0", artifact)

    assert len(calls) == 1


def test_artifact_replace_is_followed_by_parent_directory_fsync(
    monkeypatch,
    tmp_path: Path,
) -> None:
    body = b"atomic package"
    artifact = {
        **_artifact("DicePP-v3.1.0-linux-amd64.zip", body),
        "download_url": "https://downloads/artifact",
    }
    layout = InstanceLayout.from_root(tmp_path)
    target = layout.manager_packages_dir / "3.1.0" / artifact["filename"]
    observations = []

    def observe_fsync(path: Path):
        observations.append((path, target.exists()))

    monkeypatch.setattr(release_module, "_fsync_directory", observe_fsync)
    manager = ReleaseManager(
        layout=layout,
        transport=Transport(
            {
                "https://downloads/artifact": [
                    Response(body, headers={"ETag": '"etag"'})
                ]
            }
        ),
        target=("linux", "amd64"),
    )

    manager._download_artifact("3.1.0", artifact)

    assert any(path == target.parent and exists for path, exists in observations)
    version_creation = observations.index(
        (layout.manager_packages_dir, False)
    )
    durable_target = next(
        index
        for index, observation in enumerate(observations)
        if observation == (target.parent, True)
    )
    assert version_creation < durable_target


def test_atomic_metadata_failure_never_exposes_destination(
    monkeypatch,
    tmp_path: Path,
) -> None:
    destination = tmp_path / "state" / "release.json"
    monkeypatch.setattr(
        release_module.os,
        "fsync",
        lambda _fd: (_ for _ in ()).throw(OSError("injected fsync failure")),
    )

    with pytest.raises(OSError, match="injected"):
        release_module._atomic_write_json(destination, {"version": "3.1.0"})

    assert not destination.exists()


@pytest.mark.parametrize("kind", ["part", "json-tmp"])
def test_dangling_package_symlink_cannot_write_outside_instance(
    tmp_path: Path,
    kind: str,
) -> None:
    layout = InstanceLayout.from_root(tmp_path / "instance")
    outside = tmp_path / "outside-created"
    if kind == "part":
        version_dir = layout.manager_packages_dir / "3.1.0"
        version_dir.mkdir(parents=True)
        link = version_dir / "package.zip.part"
    else:
        destination = layout.manager_state_dir / "release-state.json"
        destination.parent.mkdir(parents=True)
        link = destination.with_name(f"{destination.name}.tmp")
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"File symlinks are unavailable: {exc}")

    with pytest.raises(ReleaseDownloadError, match="symbolic link|regular file"):
        if kind == "part":
            manager = ReleaseManager(
                layout=layout,
                transport=Transport(
                    {
                        "https://downloads/artifact": [
                            Response(b"package", headers={"ETag": '"etag"'})
                        ]
                    }
                ),
                target=("linux", "amd64"),
            )
            manager._download_artifact(
                "3.1.0",
                {
                    **_artifact("package.zip", b"package"),
                    "download_url": "https://downloads/artifact",
                },
            )
        else:
            release_module._atomic_write_json(destination, {"ok": True})

    assert not outside.exists()


@pytest.mark.parametrize("kind", ["target", "part", "metadata"])
def test_package_download_rejects_hardlinked_mutable_files(
    tmp_path: Path,
    kind: str,
) -> None:
    body = b"package"
    layout = InstanceLayout.from_root(tmp_path / "instance")
    version_dir = layout.manager_packages_dir / "3.1.0"
    version_dir.mkdir(parents=True)
    outside = tmp_path / "outside"
    if kind == "metadata":
        outside.write_text(
            json.dumps(
                {
                    "url": "https://downloads/artifact",
                    "sha256": hashlib.sha256(body).hexdigest(),
                    "validator": '"etag"',
                }
            ),
            encoding="utf-8",
        )
        linked = version_dir / "package.zip.part.json"
    elif kind == "part":
        outside.write_bytes(body[:2])
        linked = version_dir / "package.zip.part"
    else:
        outside.write_bytes(body)
        linked = version_dir / "package.zip"
    try:
        release_module.os.link(outside, linked)
    except OSError as exc:
        pytest.skip(f"Hard links are unavailable: {exc}")
    before = outside.read_bytes()
    manager = ReleaseManager(
        layout=layout,
        transport=Transport(
            {
                "https://downloads/artifact": [
                    Response(body, headers={"ETag": '"etag"'})
                ]
            }
        ),
        target=("linux", "amd64"),
    )

    with pytest.raises(ReleaseDownloadError, match="private regular file"):
        manager._download_artifact(
            "3.1.0",
            {
                **_artifact("package.zip", body),
                "download_url": "https://downloads/artifact",
            },
        )

    assert outside.read_bytes() == before


def test_replaced_version_directory_cannot_redirect_download_outside_instance(
    tmp_path: Path,
) -> None:
    body = b"package"
    layout = InstanceLayout.from_root(tmp_path / "instance")
    outside = tmp_path / "outside"
    outside.mkdir()

    class ReplacingTransport:
        def open(self, _url, *, headers=None, timeout=30):
            version_dir = layout.manager_packages_dir / "3.1.0"
            moved = layout.manager_packages_dir / "3.1.0-moved"
            version_dir.rename(moved)
            try:
                version_dir.symlink_to(outside, target_is_directory=True)
            except OSError as exc:
                moved.rename(version_dir)
                pytest.skip(f"Directory symlinks are unavailable: {exc}")
            return Response(body, headers={"ETag": '"etag"'})

    manager = ReleaseManager(
        layout=layout,
        transport=ReplacingTransport(),
        target=("linux", "amd64"),
    )

    with pytest.raises(ReleaseDownloadError, match="directory.*replaced|identity"):
        manager._download_artifact(
            "3.1.0",
            {
                **_artifact("package.zip", body),
                "download_url": "https://downloads/artifact",
            },
        )

    assert list(outside.iterdir()) == []


def test_cache_retention_keeps_two_most_recent_complete_versions(tmp_path: Path) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    completed = {
        "3.0.0": "2026-07-23T03:00:00+00:00",
        "3.1.0": "2026-07-23T01:00:00+00:00",
        "3.2.0": "2026-07-23T02:00:00+00:00",
    }
    for version, completed_at in completed.items():
        directory = layout.manager_packages_dir / version
        directory.mkdir(parents=True)
        (directory / "package.zip").write_bytes(version.encode())
        (directory / "verified-release.json").write_text(
            json.dumps(
                {
                    "completed_at": completed_at,
                    "artifact": {"filename": "package.zip"},
                }
            ),
            encoding="utf-8",
        )
    manager = ReleaseManager(layout=layout, target=("linux", "amd64"))

    manager._prune(2, protected_version="3.2.0")

    assert sorted(item.name for item in layout.manager_packages_dir.iterdir()) == [
        "3.0.0",
        "3.2.0",
    ]


def test_cache_retention_also_bounds_failed_partial_versions(
    tmp_path: Path,
) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    for index, version in enumerate(("3.0.0", "3.1.0", "3.2.0")):
        directory = layout.manager_packages_dir / version
        directory.mkdir(parents=True)
        (directory / "package.zip.part").write_bytes(version.encode())
        timestamp = 1_700_000_000 + index
        release_module.os.utime(directory, (timestamp, timestamp))
    manager = ReleaseManager(layout=layout, target=("linux", "amd64"))

    manager._prune(2, protected_version=None)

    assert sorted(item.name for item in layout.manager_packages_dir.iterdir()) == [
        "3.1.0",
        "3.2.0",
    ]


def test_cache_retention_never_removes_versions_required_for_upgrade_recovery(
    tmp_path: Path,
) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    for index, version in enumerate(("3.0.0", "3.1.0", "3.2.0", "3.3.0")):
        directory = layout.manager_packages_dir / version
        directory.mkdir(parents=True)
        (directory / "package.zip.part").write_bytes(version.encode())
        timestamp = 1_700_000_000 + index
        release_module.os.utime(directory, (timestamp, timestamp))
    manager = ReleaseManager(
        layout=layout,
        target=("linux", "amd64"),
        protected_versions_loader=lambda: {"3.0.0", "3.1.0", "3.2.0"},
    )

    manager._prune(2, protected_version=None)

    assert sorted(item.name for item in layout.manager_packages_dir.iterdir()) == [
        "3.0.0",
        "3.1.0",
        "3.2.0",
    ]


def test_release_result_survives_restart_and_channel_change_invalidates_it(
    tmp_path: Path,
) -> None:
    body = b"release"
    manifest = _manifest([_artifact("DicePP-v3.1.0-linux-amd64.zip", body)])
    release = _release(manifest, body)
    layout = InstanceLayout.from_root(tmp_path)
    selected = {"channel": "stable"}
    manager = ReleaseManager(
        layout=layout,
        settings_loader=lambda: UpdateSettings(channel=selected["channel"]),
        current_version_loader=lambda: "3.0.0",
        transport=Transport(
            {
                "https://api/releases?per_page=100&page=1": [
                    _json_response([release])
                ],
                "https://downloads/manifest": [_json_response(manifest)],
            }
        ),
        github_api="https://api",
        target=("linux", "amd64"),
    )
    manager.discover(manual=True)

    restarted = ReleaseManager(
        layout=layout,
        settings_loader=lambda: UpdateSettings(channel=selected["channel"]),
        current_version_loader=lambda: "3.0.0",
        transport=Transport({}),
        github_api="https://api",
        target=("linux", "amd64"),
    )
    persisted = restarted.status()
    assert persisted["available"]["version"] == "3.1.0"
    assert persisted["discovery"]["last_checked"]

    selected["channel"] = "prerelease"
    changed = restarted.status()
    assert changed["available"] is None
    with pytest.raises(ReleaseDownloadError, match="Check the selected"):
        restarted.queue_download()


def test_persisted_candidate_is_cleared_when_current_version_catches_up(
    tmp_path: Path,
) -> None:
    body = b"release"
    manifest = _manifest([_artifact("DicePP-v3.1.0-linux-amd64.zip", body)])
    release = _release(manifest, body)
    layout = InstanceLayout.from_root(tmp_path)
    manager = ReleaseManager(
        layout=layout,
        current_version_loader=lambda: "3.0.0",
        transport=Transport(
            {
                "https://api/releases?per_page=100&page=1": [
                    _json_response([release])
                ],
                "https://downloads/manifest": [_json_response(manifest)],
            }
        ),
        github_api="https://api",
        target=("linux", "amd64"),
    )
    manager.discover(manual=True)

    restarted = ReleaseManager(
        layout=layout,
        current_version_loader=lambda: "3.1.0",
        transport=Transport({}),
        github_api="https://api",
        target=("linux", "amd64"),
    )

    assert restarted.status()["available"] is None
    persisted = json.loads(restarted._state_path.read_text(encoding="utf-8"))
    assert persisted["available"] is None
    with pytest.raises(ReleaseDownloadError, match="Check the selected"):
        restarted.queue_download()


def test_malformed_persisted_candidate_cannot_construct_a_download(
    tmp_path: Path,
) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    layout.manager_state_dir.mkdir(parents=True)
    layout.manager_state_dir.joinpath("release-state.json").write_text(
        json.dumps(
            {
                "format_version": 1,
                "channel": "stable",
                "available": {
                    "version": "99.0",
                    "channel": "stable",
                    "compatible": True,
                    "artifacts": [
                        {
                            "filename": "outside.zip",
                            "download_url": "file:///outside",
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    manager = ReleaseManager(
        layout=layout,
        current_version_loader=lambda: "3.0.0",
        target=("linux", "amd64"),
    )

    assert manager.status()["available"] is None
    with pytest.raises(ReleaseDownloadError, match="Check the selected"):
        manager.queue_download()


def test_operation_errors_cannot_release_a_newer_reservation(
    tmp_path: Path,
) -> None:
    manager = ReleaseManager(
        layout=InstanceLayout.from_root(tmp_path),
        target=("linux", "amd64"),
    )
    discovery = manager.queue_discovery(manual=True)
    assert discovery is not None
    manager.record_scheduler_error(ValueError("scheduler config"))
    assert manager.queue_download() is None

    wrong = ReleaseOperation(
        kind="discovery",
        generation=discovery.generation + 1,
        cancel_event=threading.Event(),
    )
    manager.fail_download(ValueError("unrelated"), reservation=wrong)
    assert manager.queue_download() is None

    discovery.cancel_event.set()
    with pytest.raises(ReleaseDownloadError, match="cancelled"):
        manager.discover(manual=True, reservation=discovery)
    assert manager.queue_discovery(manual=True) is not None


def test_settings_failure_releases_only_its_download_reservation(
    tmp_path: Path,
) -> None:
    settings_calls = 0

    def settings_loader():
        nonlocal settings_calls
        settings_calls += 1
        if settings_calls > 1:
            raise ValueError("invalid update settings")
        return UpdateSettings()

    manager = ReleaseManager(
        layout=InstanceLayout.from_root(tmp_path),
        settings_loader=settings_loader,
        target=("linux", "amd64"),
    )
    with manager._lock:
        manager._latest_channel = "stable"
        manager._latest = {
            "version": "3.1.0",
            "channel": "stable",
            "change_scope": ["runtime"],
            "linux_manager_handoff_protocol": None,
            "compatible": True,
            "compatibility": {
                "deployment_schema_version": DEPLOYMENT_SCHEMA_VERSION,
                "minimum_manager_version": MANAGER_VERSION,
                "catalog_version": 1,
                "catalog_digest": "1" * 64,
                "automatic_upgrade": True,
                "problems": [],
            },
            "release_url": "https://example/release",
            "published_at": "2026-07-23T00:00:00Z",
            "artifacts": [
                {
                    **_artifact("package.zip", b"package"),
                    "download_url": "https://downloads/artifact",
                }
            ],
        }
    reservation = manager.queue_download()
    assert reservation is not None

    with pytest.raises(ValueError, match="invalid update settings"):
        manager.download(reservation=reservation)

    manager.settings_loader = lambda: UpdateSettings()
    assert manager.queue_discovery(manual=True) is not None


@pytest.mark.parametrize("redirect_level", ["manager", "version"])
def test_package_storage_rejects_redirected_directories(
    tmp_path: Path,
    redirect_level: str,
) -> None:
    layout = InstanceLayout.from_root(tmp_path / "instance")
    outside = tmp_path / "outside"
    outside.mkdir(parents=True)
    try:
        if redirect_level == "manager":
            layout.root.mkdir(parents=True)
            (layout.root / "manager").symlink_to(outside, target_is_directory=True)
        else:
            layout.manager_packages_dir.mkdir(parents=True)
            (layout.manager_packages_dir / "3.1.0").symlink_to(
                outside,
                target_is_directory=True,
            )
    except OSError as exc:
        pytest.skip(f"Directory symlinks are unavailable: {exc}")
    artifact = {
        **_artifact("DicePP-v3.1.0-linux-amd64.zip", b"package"),
        "download_url": "https://downloads/artifact",
    }
    manager = ReleaseManager(layout=layout, target=("linux", "amd64"))

    with pytest.raises(ReleaseDownloadError, match="Untrusted|redirected"):
        manager._download_artifact("3.1.0", artifact)

    assert list(outside.iterdir()) == []
