from __future__ import annotations

import hashlib

import pytest

from dicepp_manager.deployment import DEPLOYMENT_SCHEMA_VERSION, MANAGER_VERSION
from dicepp_manager.release import (
    RELEASE_CONTRACT_VERSION,
    ReleaseContractError,
    UpdateSettings,
    validate_release_manifest,
)


def _artifact(filename: str, body: bytes, purpose: str = "linux-bundle") -> dict:
    return {
        "platform": "linux",
        "arch": "amd64",
        "filename": filename,
        "purpose": purpose,
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


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("cache_versions", True),
        ("cache_versions", 2.0),
        ("check_interval_hours", True),
        ("check_interval_hours", float("nan")),
        ("check_interval_hours", float("inf")),
    ],
)
def test_update_settings_rejects_bool_coercion_and_non_finite_numbers(
    field: str,
    value,
) -> None:
    with pytest.raises(ValueError):
        UpdateSettings(**{field: value})


def test_manifest_rejects_unknown_schema_and_invalid_digest() -> None:
    payload = _manifest([_artifact("DicePP-v3.1.0-linux-amd64.zip", b"bundle")])
    payload["deployment_schema_version"] = True
    with pytest.raises(ReleaseContractError, match="deployment schema"):
        validate_release_manifest(payload)

    payload = _manifest([_artifact("DicePP-v3.1.0-linux-amd64.zip", b"bundle")])
    payload["artifacts"][0]["sha256"] = "not-a-digest"
    with pytest.raises(ReleaseContractError, match="SHA-256"):
        validate_release_manifest(payload)


def test_manifest_rejects_automatic_manager_change_scope() -> None:
    payload = _manifest(
        [_artifact("DicePP-v3.1.0-linux-amd64.zip", b"bundle")]
    )
    payload["change_scope"] = ["runtime", "manager"]

    with pytest.raises(
        ReleaseContractError, match="change_scope includes manager"
    ):
        validate_release_manifest(payload)


def test_manifest_rejects_oversized_linux_bundle_before_download() -> None:
    payload = _manifest(
        [_artifact("DicePP-v3.1.0-linux-amd64.zip", b"bundle")]
    )
    payload["artifacts"][0]["size"] = 16 * 1024**3 + 1

    with pytest.raises(ReleaseContractError, match="size limit"):
        validate_release_manifest(payload)


def test_contract_v2_hard_cut_rejects_v1_and_standalone_windows_package() -> None:
    payload = _manifest(
        [_artifact("DicePP-v3.1.0-linux-amd64.zip", b"bundle")]
    )
    payload["contract_version"] = 1
    with pytest.raises(ReleaseContractError, match="contract version"):
        validate_release_manifest(payload)

    standalone = _manifest(
        [
            {
                "platform": "windows",
                "arch": "amd64",
                "filename": "DicePP-3.1.0-full.nupkg",
                "purpose": "velopack-full",
                "size": 1,
                "sha256": "1" * 64,
            }
        ]
    )
    with pytest.raises(
        ReleaseContractError,
        match="Windows release artifact purpose",
    ):
        validate_release_manifest(standalone)


def test_contract_v2_rejects_manifest_without_windows_bundle() -> None:
    payload = _manifest(
        [_artifact("DicePP-v3.1.0-linux-amd64.zip", b"bundle")]
    )
    payload["artifacts"] = [
        item
        for item in payload["artifacts"]
        if item["purpose"] != "velopack-bundle"
    ]

    with pytest.raises(ReleaseContractError, match="Windows Velopack bundle"):
        validate_release_manifest(payload)
