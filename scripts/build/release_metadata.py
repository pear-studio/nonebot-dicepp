"""Strict parser for release notes fields consumed by publish automation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from packaging.version import InvalidVersion, Version


_REQUIRED_FIELDS = {
    "镜像",
    "Windows",
    "数据变更",
    "配置变更",
    "变更范围",
    "自动升级",
    "最低 Manager 版本",
}
_DECLARED_SCOPES = {
    "runtime",
    "dashboard",
    "manager",
    "deployment",
    "windows",
    "linux",
    "documentation",
    "data",
    "config",
}


@dataclass(frozen=True, slots=True)
class ReleaseMetadata:
    version: str
    data_changed: bool
    config_changed: bool
    change_scope: tuple[str, ...]
    automatic_upgrade: bool
    minimum_manager_version: str


def parse_release_metadata(
    path: Path,
    *,
    expected_version: str,
) -> ReleaseMetadata:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or not lines[0].startswith("# "):
        raise ValueError("Release notes must start with '# vX.Y.Z'")
    expected = Version(expected_version.removeprefix("v"))
    try:
        declared = Version(lines[0].removeprefix("# ").removeprefix("v").strip())
    except InvalidVersion as exc:
        raise ValueError("Release notes title has an invalid version") from exc
    if declared != expected:
        raise ValueError(
            f"Release notes version {declared} does not match {expected}"
        )
    fields: dict[str, str] = {}
    for line in lines[1:]:
        if line.startswith("## "):
            break
        if not line.startswith("- ") or ":" not in line:
            continue
        key, value = line.removeprefix("- ").split(":", 1)
        key = key.strip()
        if key not in _REQUIRED_FIELDS:
            continue
        if key in fields:
            raise ValueError(f"Duplicate release metadata field: {key}")
        fields[key] = value.strip().strip("`")
    missing = _REQUIRED_FIELDS - set(fields)
    if missing:
        raise ValueError(f"Missing release metadata fields: {sorted(missing)}")
    normalized = str(expected)
    expected_image = (
        f"ghcr.io/pear-studio/nonebot-dicepp:v{normalized}"
    )
    if fields["镜像"] != expected_image:
        raise ValueError("Release image tag does not match release version")
    data_changed = _yes_no(fields["数据变更"], "数据变更")
    config_changed = _yes_no(fields["配置变更"], "配置变更")
    automatic_upgrade = _yes_no(fields["自动升级"], "自动升级")
    raw_scopes = [item.strip() for item in fields["变更范围"].split(",")]
    if not raw_scopes or any(not item for item in raw_scopes):
        raise ValueError("变更范围 must be a comma-separated non-empty list")
    if len(set(raw_scopes)) != len(raw_scopes):
        raise ValueError("变更范围 contains duplicate values")
    unknown = set(raw_scopes) - _DECLARED_SCOPES
    if unknown:
        raise ValueError(f"Unknown release change scopes: {sorted(unknown)}")
    if data_changed != ("data" in raw_scopes):
        raise ValueError("数据变更 conflicts with 变更范围 data")
    if config_changed != ("config" in raw_scopes):
        raise ValueError("配置变更 conflicts with 变更范围 config")
    minimum_manager_version = fields["最低 Manager 版本"]
    try:
        Version(minimum_manager_version)
    except InvalidVersion as exc:
        raise ValueError("最低 Manager 版本 is invalid") from exc
    return ReleaseMetadata(
        version=normalized,
        data_changed=data_changed,
        config_changed=config_changed,
        change_scope=tuple(raw_scopes),
        automatic_upgrade=automatic_upgrade,
        minimum_manager_version=minimum_manager_version,
    )


def _yes_no(value: str, field: str) -> bool:
    if value == "yes":
        return True
    if value == "no":
        return False
    raise ValueError(f"{field} must be exactly yes or no")
