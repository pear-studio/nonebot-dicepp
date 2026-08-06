"""GitHub Release discovery and verified package downloads.

Discovery and download are deliberately separate state-machine operations.
Neither operation installs a package or changes the running DicePP version.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import math
import os
import platform as host_platform
import re
import stat
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from packaging.version import InvalidVersion, Version

from dicepp_meta import get_version

from .deployment import DEPLOYMENT_SCHEMA_VERSION, MANAGER_VERSION
from ._path_security import (
    UnsafePathError,
    assert_contained_no_reparse,
    assert_directory_no_reparse,
    delete_path_entry_no_follow,
    is_reparse_point,
    open_regular_binary_no_follow,
)
from .velopack_bundle import (
    MAX_VELOPACK_BUNDLE_BYTES,
    VELOPACK_BUNDLE_NAME,
    VelopackBundleError,
    extract_verified_nupkg,
    validate_velopack_bundle,
    validate_velopack_bundle_manifest,
)

RELEASE_CONTRACT_VERSION = 2
RELEASE_MANIFEST_NAME = "dicepp-release.json"
DEFAULT_GITHUB_API = "https://api.github.com/repos/pear-studio/nonebot-dicepp"
_GITHUB_API_VERSION = "2022-11-28"
MAX_RELEASE_JSON_BYTES = 2 * 1024 * 1024
MAX_LINUX_BUNDLE_BYTES = 16 * 1024**3
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,199}$")
_VELOPACK_GENERATION_RE = re.compile(
    r"^velopack-([0-9a-f]{32})\.win-x64\.zip$"
)
_VELOPACK_PAYLOAD_GENERATION_RE = re.compile(
    r"^payload-([0-9a-f]{32})\.nupkg$"
)
_CONTENT_RANGE_RE = re.compile(r"^bytes ([0-9]+)-([0-9]+)/([0-9]+)$")
# Bounded download retry budget: consecutive failures that left no new bytes
# abort the download; any attempt that grows the .part file resets both the
# failure count and the backoff sequence.
_DOWNLOAD_MAX_NO_PROGRESS_FAILURES = 5
_DOWNLOAD_BACKOFF_SECONDS = (5.0, 10.0, 20.0, 40.0, 60.0)


class ReleaseError(RuntimeError):
    """Base error for release discovery and package download."""


class ReleaseContractError(ReleaseError):
    """A release or package contract is malformed."""


class ReleaseDownloadError(ReleaseError):
    """A release package could not be downloaded or verified."""


class ReleaseCancelledError(ReleaseDownloadError):
    """A release operation was cooperatively cancelled during shutdown."""


class _ArtifactTruncatedError(ReleaseDownloadError):
    """Download ended before the manifest size; the partial can be resumed."""


class _ArtifactDigestError(ReleaseDownloadError):
    """Download completed but failed SHA-256 verification."""


class _ArtifactConnectionError(ReleaseDownloadError):
    """The connection broke mid-stream; the partial can be resumed."""


@dataclass(frozen=True, slots=True)
class ReleaseOperation:
    kind: str
    generation: int
    cancel_event: threading.Event


@dataclass(frozen=True, slots=True)
class TrustedDirectory:
    path: Path
    resolved: Path
    root: Path
    device: int
    inode: int
    parent: "TrustedDirectory | None" = None


class _PublishedGenerationState(Enum):
    CONFIRMED_ABSENT = "confirmed_absent"
    VALID_CURRENT = "valid_current"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class _PublishedGeneration:
    state: _PublishedGenerationState
    token: str | None = None


@dataclass(frozen=True, slots=True)
class UpdateSettings:
    discovery_enabled: bool = True
    auto_download: bool = False
    channel: str = "stable"
    check_interval_hours: float = 24.0
    cache_versions: int = 2

    def __post_init__(self) -> None:
        if type(self.discovery_enabled) is not bool:
            raise ValueError("update.discovery_enabled must be a boolean")
        if type(self.auto_download) is not bool:
            raise ValueError("update.auto_download must be a boolean")
        if type(self.channel) is not str or self.channel not in {
            "stable",
            "prerelease",
        }:
            raise ValueError("update.channel must be stable or prerelease")
        interval = self.check_interval_hours
        if (
            isinstance(interval, bool)
            or type(interval) not in {int, float}
            or not math.isfinite(float(interval))
            or interval <= 0
        ):
            raise ValueError(
                "update.check_interval_hours must be a finite positive number"
            )
        if type(self.cache_versions) is not int or not 1 <= self.cache_versions <= 20:
            raise ValueError("update.cache_versions must be an integer between 1 and 20")

    @classmethod
    def from_layout(cls, layout) -> "UpdateSettings":
        merged: dict[str, Any] = {}
        for path in (layout.config_global, layout.config_user):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                continue
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"Cannot read update settings from {path}: {exc}") from exc
            section = value.get("update", {}) if isinstance(value, dict) else {}
            if not isinstance(section, dict):
                raise ValueError(f"update settings in {path} must be an object")
            merged.update(section)
        return cls(
            discovery_enabled=_strict_bool(merged, "discovery_enabled", True),
            auto_download=_strict_bool(merged, "auto_download", False),
            channel=merged.get("channel", "stable"),
            check_interval_hours=merged.get("check_interval_hours", 24.0),
            cache_versions=merged.get("cache_versions", 2),
        )


def _strict_bool(values: Mapping[str, Any], key: str, default: bool) -> bool:
    value = values.get(key, default)
    if type(value) is not bool:
        raise ValueError(f"update.{key} must be a boolean")
    return value


def current_target(
    system: str | None = None,
    machine: str | None = None,
) -> tuple[str, str]:
    raw_system = (system or host_platform.system()).lower()
    raw_machine = (machine or host_platform.machine()).lower()
    platforms = {"windows": "windows", "linux": "linux"}
    arches = {
        "amd64": "amd64",
        "x86_64": "amd64",
        "x64": "amd64",
        "arm64": "arm64",
        "aarch64": "arm64",
    }
    if raw_system not in platforms or raw_machine not in arches:
        raise ReleaseContractError(
            f"Unsupported release target: {raw_system}/{raw_machine}"
        )
    return platforms[raw_system], arches[raw_machine]


def validate_release_manifest(payload: Any) -> dict[str, Any]:
    """Validate contract syntax without applying current-host compatibility."""
    if not isinstance(payload, dict):
        raise ReleaseContractError("Release manifest must be an object")
    required = {
        "contract_version",
        "version",
        "channel",
        "deployment_schema_version",
        "minimum_manager_version",
        "catalog_version",
        "catalog_digest",
        "change_scope",
        "artifacts",
        "fallbacks",
        "automatic_upgrade",
    }
    unknown = set(payload) - required
    missing = required - set(payload)
    if missing or unknown:
        raise ReleaseContractError(
            "Release manifest fields mismatch "
            f"(missing={sorted(missing)}, unknown={sorted(unknown)})"
        )
    if (
        type(payload["contract_version"]) is not int
        or payload["contract_version"] != RELEASE_CONTRACT_VERSION
    ):
        raise ReleaseContractError("Unsupported release contract version")
    try:
        version = Version(payload["version"])
        Version(payload["minimum_manager_version"])
    except (InvalidVersion, TypeError) as exc:
        raise ReleaseContractError("Invalid release or Manager version") from exc
    if payload["channel"] not in {"stable", "prerelease"}:
        raise ReleaseContractError("Invalid release channel")
    if payload["channel"] == "stable" and version.is_prerelease:
        raise ReleaseContractError("Stable manifest cannot contain a prerelease version")
    if payload["channel"] == "prerelease" and not version.is_prerelease:
        raise ReleaseContractError("Prerelease manifest must contain a prerelease version")
    if (
        type(payload["deployment_schema_version"]) is not int
        or payload["deployment_schema_version"] < 1
    ):
        raise ReleaseContractError("Invalid deployment schema version")
    if type(payload["automatic_upgrade"]) is not bool:
        raise ReleaseContractError("automatic_upgrade must be a boolean")
    if type(payload["catalog_version"]) is not int or payload["catalog_version"] < 1:
        raise ReleaseContractError("Invalid Catalog version")
    if (
        not isinstance(payload["catalog_digest"], str)
        or not _SHA256_RE.fullmatch(payload["catalog_digest"])
    ):
        raise ReleaseContractError("Invalid Catalog digest")
    if (
        not isinstance(payload["change_scope"], list)
        or not payload["change_scope"]
        or not all(type(item) is str and bool(item.strip()) for item in payload["change_scope"])
    ):
        raise ReleaseContractError("change_scope must be a non-empty string list")
    if payload["automatic_upgrade"] and "manager" in payload["change_scope"]:
        raise ReleaseContractError(
            "automatic_upgrade cannot be enabled when change_scope includes manager"
        )
    artifacts = payload["artifacts"]
    if not isinstance(artifacts, list) or not artifacts:
        raise ReleaseContractError("Release manifest has no artifacts")
    seen: set[tuple[str, str, str]] = set()
    normalized = dict(payload)
    normalized["artifacts"] = [
        _validate_artifact(artifact, seen) for artifact in artifacts
    ]
    _validate_artifact_set(normalized)
    fallbacks = payload["fallbacks"]
    if (
        not isinstance(fallbacks, dict)
        or set(fallbacks) != {"linux_ghcr_images"}
        or not isinstance(fallbacks["linux_ghcr_images"], list)
        or len(fallbacks["linux_ghcr_images"]) != 2
        or not all(
            type(item) is str and item.startswith("ghcr.io/pear-studio/")
            for item in fallbacks["linux_ghcr_images"]
        )
    ):
        raise ReleaseContractError("Invalid release fallback metadata")
    return normalized


def _validate_artifact(
    artifact: Any,
    seen: set[tuple[str, str, str]],
) -> dict[str, Any]:
    required = {"platform", "arch", "filename", "purpose", "size", "sha256"}
    if not isinstance(artifact, dict) or set(artifact) != required:
        raise ReleaseContractError("Release artifact fields mismatch")
    if artifact["platform"] not in {"windows", "linux"}:
        raise ReleaseContractError("Invalid artifact platform")
    if artifact["arch"] not in {"amd64", "arm64"}:
        raise ReleaseContractError("Invalid artifact architecture")
    filename = artifact["filename"]
    if (
        type(filename) is not str
        or not _SAFE_FILENAME_RE.fullmatch(filename)
        or Path(filename).name != filename
    ):
        raise ReleaseContractError("Unsafe artifact filename")
    if type(artifact["purpose"]) is not str or not artifact["purpose"]:
        raise ReleaseContractError("Invalid artifact purpose")
    if type(artifact["size"]) is not int or artifact["size"] <= 0:
        raise ReleaseContractError("Invalid artifact size")
    if (
        artifact["platform"] == "linux"
        and artifact["purpose"] == "linux-bundle"
        and artifact["size"] > MAX_LINUX_BUNDLE_BYTES
    ):
        raise ReleaseContractError(
            "Linux bundle exceeds the automatic-upgrade size limit"
        )
    if (
        artifact["platform"] == "windows"
        and artifact["purpose"] == "velopack-bundle"
        and (
            artifact["filename"] != VELOPACK_BUNDLE_NAME
            or artifact["size"] > MAX_VELOPACK_BUNDLE_BYTES
        )
    ):
        raise ReleaseContractError("Invalid Windows Velopack bundle artifact")
    digest = artifact["sha256"]
    if type(digest) is not str or not _SHA256_RE.fullmatch(digest):
        raise ReleaseContractError("Invalid artifact SHA-256")
    key = (artifact["platform"], artifact["arch"], artifact["purpose"])
    if key in seen:
        raise ReleaseContractError(f"Duplicate artifact target/purpose: {key}")
    seen.add(key)
    return dict(artifact)


def _validate_artifact_set(manifest: dict[str, Any]) -> None:
    windows = [
        item for item in manifest["artifacts"] if item["platform"] == "windows"
    ]
    if any(
        item["purpose"] not in {"portable", "setup", "velopack-bundle"}
        for item in windows
    ):
        raise ReleaseContractError(
            "Unsupported Windows release artifact purpose"
        )
    bundles = [
        item for item in manifest["artifacts"]
        if item["purpose"] == "velopack-bundle"
    ]
    if (
        len(bundles) != 1
        or (
            bundles[0]["platform"],
            bundles[0]["arch"],
            bundles[0]["filename"],
        )
        != (
            "windows",
            "amd64",
            VELOPACK_BUNDLE_NAME,
        )
    ):
        raise ReleaseContractError(
            "Release contract requires the single Windows Velopack bundle"
        )


class UrlResponse:
    """Small response adapter used by the production urllib transport."""

    def __init__(self, response) -> None:
        self.status = getattr(response, "status", None) or response.getcode()
        self.headers = response.headers
        self._response = response

    def read(self, size: int = -1) -> bytes:
        return self._response.read(size)

    def close(self) -> None:
        self._response.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class UrlTransport:
    def open(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float = 30.0,
    ) -> UrlResponse:
        request = urllib.request.Request(url, headers=dict(headers or {}))
        try:
            return UrlResponse(urllib.request.urlopen(request, timeout=timeout))
        except urllib.error.HTTPError as exc:
            # A stale range commonly returns 416.  Preserve it as a response so
            # the download state machine can discard the partial and retry once.
            if exc.code == 416:
                return UrlResponse(exc)
            raise ReleaseError(f"Release source returned HTTP {exc.code}") from exc
        except (OSError, TimeoutError, urllib.error.URLError) as exc:
            raise ReleaseError(f"Release source is unavailable: {exc}") from exc


class ReleaseManager:
    def __init__(
        self,
        *,
        layout,
        settings_loader: Callable[[], UpdateSettings] | None = None,
        current_version_loader: Callable[[], str] | None = None,
        transport: Any | None = None,
        github_api: str = DEFAULT_GITHUB_API,
        target: tuple[str, str] | None = None,
        now: Callable[[], datetime] | None = None,
        scheduler_error_delay: float = 60.0,
        protected_versions_loader: Callable[[], set[str]] | None = None,
    ) -> None:
        self.layout = layout
        self.settings_loader = settings_loader or (
            lambda: UpdateSettings.from_layout(layout)
        )
        self.current_version_loader = current_version_loader or get_version
        self.transport = transport or UrlTransport()
        self.github_api = github_api.rstrip("/")
        self.target = target or current_target()
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.scheduler_error_delay = scheduler_error_delay
        self.protected_versions_loader = protected_versions_loader or set
        self._lock = threading.RLock()
        self._generation_lock = threading.RLock()
        self._latest: dict[str, Any] | None = None
        self._latest_channel: str | None = None
        self._active: ReleaseOperation | None = None
        self._operation_generation = 0
        self._discovery: dict[str, Any] = {
            "status": "idle",
            "last_checked": None,
            "channel": None,
            "error": None,
            "candidate_errors": [],
        }
        self._download: dict[str, Any] = {"status": "idle"}
        self._load_state()

    def status(self) -> dict[str, Any]:
        settings = self.settings_loader()
        self._sync_channel(settings.channel)
        with self._lock:
            self._discard_stale_latest_locked(persist=True)
            packages = self._local_packages()
            return {
                "settings": {
                    "discovery_enabled": settings.discovery_enabled,
                    "auto_download": settings.auto_download,
                    "channel": settings.channel,
                    "check_interval_hours": settings.check_interval_hours,
                    "cache_versions": settings.cache_versions,
                },
                "target": {"platform": self.target[0], "arch": self.target[1]},
                "current_version": _normalized_version(self.current_version_loader()),
                "available": self._latest,
                "discovery": dict(self._discovery),
                "download": dict(self._download),
                "packages": packages,
                "install_supported": False,
            }

    def queue_discovery(
        self,
        *,
        manual: bool = False,
    ) -> ReleaseOperation | None:
        settings = self.settings_loader()
        if not manual and not settings.discovery_enabled:
            return False
        self._sync_channel(settings.channel)
        with self._lock:
            if self._active is not None:
                return None
            operation = self._reserve_locked("discovery")
            self._discovery = {
                **self._discovery,
                "status": "checking",
                "channel": settings.channel,
                "error": None,
                "candidate_errors": [],
            }
            self._persist_state_locked()
            return operation

    def discover(
        self,
        *,
        manual: bool = False,
        reservation: ReleaseOperation | None = None,
    ) -> dict[str, Any]:
        operation = reservation
        setting_channel: str | None = None
        try:
            settings = self.settings_loader()
            setting_channel = settings.channel
            if not manual and not settings.discovery_enabled:
                if operation is not None:
                    self._release_operation(operation)
                return self.status()
            if operation is None:
                operation = self.queue_discovery(manual=manual)
                if operation is None:
                    raise ReleaseError("Another release operation is already running")
            self._require_operation(operation, "discovery")
            latest, candidate_errors = self._discover_latest(
                settings.channel,
                operation=operation,
            )
        except Exception as exc:
            with self._lock:
                if operation is not None and self._owns_locked(operation):
                    self._active = None
                    self._latest = None
                    self._latest_channel = setting_channel
                    self._discovery = {
                        **self._discovery,
                        "status": (
                            "interrupted"
                            if isinstance(exc, ReleaseCancelledError)
                            else "failed"
                        ),
                        "last_checked": _iso(self.now()),
                        "channel": setting_channel,
                        "error": str(exc) or type(exc).__name__,
                    }
                    self._persist_state_locked()
            raise
        with self._lock:
            self._require_operation_locked(operation, "discovery")
            self._active = None
            self._latest = latest
            self._latest_channel = settings.channel
            self._discovery = {
                "status": "succeeded",
                "last_checked": _iso(self.now()),
                "channel": settings.channel,
                "error": None,
                "candidate_errors": candidate_errors,
            }
            self._persist_state_locked()
        return self.status()

    def queue_download(self) -> ReleaseOperation | None:
        settings = self.settings_loader()
        self._sync_channel(settings.channel)
        with self._lock:
            self._discard_stale_latest_locked(persist=True)
            if self._active is not None:
                return None
            if self._latest is None or self._latest_channel != settings.channel:
                raise ReleaseDownloadError(
                    "Check the selected release channel before downloading"
                )
            operation = self._reserve_locked("download")
            self._download = {"status": "queued"}
            self._persist_state_locked()
            return operation

    def download(
        self,
        *,
        purpose: str | None = None,
        reservation: ReleaseOperation | None = None,
    ) -> dict[str, Any]:
        operation = reservation
        try:
            settings = self.settings_loader()
            self._sync_channel(settings.channel)
            if operation is None:
                operation = self.queue_download()
                if operation is None:
                    raise ReleaseDownloadError(
                        "Another release operation is already running"
                    )
            self._require_operation(operation, "download")
            with self._lock:
                self._discard_stale_latest_locked(persist=True)
                release = self._latest
                release_channel = self._latest_channel
            if release is None or release_channel != settings.channel:
                raise ReleaseDownloadError("Release channel changed")
            if not release["compatible"]:
                raise ReleaseDownloadError(
                    "Release is not compatible with this Manager"
                )
            artifacts = release["artifacts"]
            selected_artifacts: list[dict[str, Any]]
            if purpose is None:
                preferred = (
                    "linux-bundle"
                    if self.target[0] == "linux"
                    else "velopack-bundle"
                )
                artifact = next(
                    (item for item in artifacts if item["purpose"] == preferred),
                    None,
                )
                if artifact is None:
                    raise ReleaseDownloadError(
                        f"No automatic-update artifact for {self.target[0]}"
                    )
            else:
                artifact = next(
                    (item for item in artifacts if item["purpose"] == purpose),
                    None,
                )
                if artifact is None:
                    raise ReleaseDownloadError(
                        f"No {purpose!r} artifact for current target"
                    )
            selected_artifacts = [artifact]
            total_size = sum(item["size"] for item in selected_artifacts)
            with self._lock:
                self._require_operation_locked(operation, "download")
                self._download = {
                    "status": "downloading",
                    "version": release["version"],
                    "filename": artifact["filename"],
                    "bytes_downloaded": 0,
                    "size": total_size,
                }
                self._persist_state_locked()
            payload_path: Path | None = None
            bundle_manifest: dict[str, Any] | None = None
            if artifact["purpose"] == "velopack-bundle":
                with self._generation_lock:
                    target = self._download_artifact(
                        release["version"],
                        artifact,
                        operation=operation,
                    )
                    payload_path, bundle_manifest = (
                        self._materialize_velopack_bundle(
                            release,
                            artifact,
                            target,
                        )
                    )
                    completed_at = _iso(self.now())
                    metadata = self._write_verified_metadata(
                        release,
                        artifact,
                        target,
                        payload_path=payload_path,
                        bundle_manifest=bundle_manifest,
                        completed_at=completed_at,
                    )
            else:
                target = self._download_artifact(
                    release["version"],
                    artifact,
                    operation=operation,
                )
                completed_at = _iso(self.now())
                metadata = self._write_verified_metadata(
                    release,
                    artifact,
                    target,
                    completed_at=completed_at,
                )
            self._prune(
                settings.cache_versions,
                protected_version=release["version"],
            )
            if (
                not target.is_file()
                or target.is_symlink()
                or not metadata.is_file()
                or metadata.is_symlink()
            ):
                raise ReleaseDownloadError(
                    "Verified target disappeared during cache retention"
                )
        except Exception as exc:
            self.fail_download(exc, reservation=operation)
            try:
                if "settings" in locals():
                    self._prune(settings.cache_versions, protected_version=None)
            except (OSError, ReleaseError):
                pass
            raise
        with self._lock:
            self._require_operation_locked(operation, "download")
            self._active = None
            self._download = {
                **self._download,
                "status": "verified",
                "bytes_downloaded": total_size,
                "path": str(target),
                "sha256": artifact["sha256"],
                "completed_at": completed_at,
                "installable": True,
            }
            self._persist_state_locked()
        return self.status()

    def fail_download(
        self,
        error: BaseException,
        *,
        reservation: ReleaseOperation | None,
    ) -> None:
        with self._lock:
            if reservation is None or not self._owns_locked(reservation):
                return
            self._active = None
            self._download = {
                **self._download,
                "status": (
                    "interrupted"
                    if isinstance(error, ReleaseCancelledError)
                    else "failed"
                ),
                "error": str(error) or type(error).__name__,
            }
            self._persist_state_locked()

    def record_scheduler_error(self, error: BaseException) -> None:
        with self._lock:
            self._discovery = {
                **self._discovery,
                "status": "failed",
                "last_checked": _iso(self.now()),
                "error": str(error) or type(error).__name__,
            }
            self._persist_state_locked()

    def cancel_active(self) -> None:
        with self._lock:
            operation = self._active
        if operation is not None:
            operation.cancel_event.set()

    def _reserve_locked(self, kind: str) -> ReleaseOperation:
        self._operation_generation += 1
        operation = ReleaseOperation(
            kind=kind,
            generation=self._operation_generation,
            cancel_event=threading.Event(),
        )
        self._active = operation
        return operation

    def _owns_locked(self, operation: ReleaseOperation) -> bool:
        return (
            self._active is not None
            and self._active.kind == operation.kind
            and self._active.generation == operation.generation
        )

    def _require_operation_locked(
        self,
        operation: ReleaseOperation,
        kind: str,
    ) -> None:
        if operation.kind != kind or not self._owns_locked(operation):
            raise ReleaseError(f"Lost ownership of {kind} release operation")

    def _require_operation(
        self,
        operation: ReleaseOperation,
        kind: str,
    ) -> None:
        with self._lock:
            self._require_operation_locked(operation, kind)

    def _release_operation(self, operation: ReleaseOperation) -> None:
        with self._lock:
            if self._owns_locked(operation):
                self._active = None

    @staticmethod
    def _check_cancelled(operation: ReleaseOperation) -> None:
        if operation.cancel_event.is_set():
            raise ReleaseCancelledError("Release operation cancelled during shutdown")

    def _iter_release_entries(
        self,
        operation: ReleaseOperation | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield release entries across the paginated GitHub releases endpoint.

        Fetches up to 10 pages of 100 entries and stops at the first short
        page.  Every page payload must be a list; non-dict entries are
        skipped.  When *operation* is given, cancellation is checked before
        each page request.
        """
        for page in range(1, 11):
            if operation is not None:
                self._check_cancelled(operation)
            payload = self._get_json(
                f"{self.github_api}/releases?per_page=100&page={page}"
            )
            if not isinstance(payload, list):
                raise ReleaseContractError("GitHub releases response must be a list")
            for item in payload:
                if isinstance(item, dict):
                    yield item
            if len(payload) < 100:
                break

    def _discover_latest(
        self,
        channel: str,
        *,
        operation: ReleaseOperation,
    ) -> tuple[dict[str, Any] | None, list[str]]:
        releases = list(self._iter_release_entries(operation))
        wanted_prerelease = channel == "prerelease"
        current = Version(_normalized_version(self.current_version_loader()))
        candidates: list[tuple[Version, dict[str, Any]]] = []
        errors: list[str] = []
        for release in releases:
            if release.get("draft") or release.get("prerelease") is not wanted_prerelease:
                continue
            label = str(release.get("tag_name") or "<untagged>")
            try:
                tag = release.get("tag_name")
                if type(tag) is not str:
                    raise InvalidVersion("missing tag")
                version = Version(tag.removeprefix("v"))
            except (InvalidVersion, TypeError) as exc:
                errors.append(f"{label}: {exc}")
                continue
            if version > current:
                candidates.append((version, release))
        candidates.sort(key=lambda item: item[0], reverse=True)
        for _version, release in candidates[:20]:
            self._check_cancelled(operation)
            label = str(release.get("tag_name") or "<untagged>")
            try:
                return (
                    self._parse_release(release, expected_channel=channel),
                    errors[:20],
                )
            except Exception as exc:
                errors.append(f"{label}: {exc}")
        if candidates:
            summary = "; ".join(errors[:5])
            raise ReleaseContractError(
                f"No valid {channel} release candidates"
                + (f": {summary}" if summary else "")
            )
        return None, errors[:20]

    def fetch_rollback_bundle(self, version: str) -> tuple[Path, str]:
        """Download the verified Velopack bundle for *version*.

        Supplies Windows rollback material for the currently installed
        version when the local packages directory does not hold it (first
        Portable upgrade, or a Setup directory that Update.exe does not
        maintain).  The artifact is selected and verified against the
        Release contract (size + SHA-256), never local directory state.

        Deliberately not serialized through the operation lock and not
        cancellable on Manager shutdown: it runs on a throwaway
        ReleaseOperation so it never queues behind (or deadlocks against)
        a user-driven download of the target version.  Concurrent fetches
        of the same version race on the same ``.part`` file, but the loser
        fails safely on the post-download digest check and the caller
        re-verifies/re-fetches on the next attempt.
        """
        wanted = Version(_normalized_version(version))
        release = self._find_release_by_version(wanted)
        artifact = next(
            (
                item
                for item in release["artifacts"]
                if item.get("purpose") == "velopack-bundle"
            ),
            None,
        )
        if artifact is None:
            raise ReleaseContractError(
                f"Release {wanted} has no Velopack bundle artifact"
            )
        with self._generation_lock:
            path = self._download_artifact(str(wanted), artifact)
            payload_path, bundle_manifest = self._materialize_velopack_bundle(
                release,
                artifact,
                path,
            )
            self._write_verified_metadata(
                release,
                artifact,
                path,
                payload_path=payload_path,
                bundle_manifest=bundle_manifest,
                completed_at=_iso(self.now()),
            )
        return path, str(artifact["sha256"])

    def _find_release_by_version(self, wanted: Version) -> dict[str, Any]:
        for release in self._iter_release_entries():
            if release.get("draft"):
                continue
            tag = release.get("tag_name")
            if type(tag) is not str:
                continue
            try:
                tag_version = Version(tag.removeprefix("v"))
            except InvalidVersion:
                continue
            if tag_version != wanted:
                continue
            channel = "prerelease" if release.get("prerelease") else "stable"
            try:
                return self._parse_release(release, expected_channel=channel)
            except Exception as exc:
                raise ReleaseContractError(
                    f"Release {wanted} is unusable: {exc}"
                ) from exc
        raise ReleaseContractError(
            f"No GitHub Release found for version {wanted}"
        )

    def _parse_release(
        self,
        release: Any,
        *,
        expected_channel: str,
    ) -> dict[str, Any]:
        if not isinstance(release, dict):
            raise ReleaseContractError("GitHub release must be an object")
        assets = release.get("assets")
        if not isinstance(assets, list):
            raise ReleaseContractError("GitHub release assets are missing")
        by_name = {
            item.get("name"): item
            for item in assets
            if isinstance(item, dict) and type(item.get("name")) is str
        }
        if len(by_name) != len(assets):
            raise ReleaseContractError(
                "GitHub Release has duplicate or malformed assets"
            )
        manifest_asset = by_name.get(RELEASE_MANIFEST_NAME)
        if not isinstance(manifest_asset, dict):
            raise ReleaseContractError(f"{RELEASE_MANIFEST_NAME} is missing")
        manifest_url = manifest_asset.get("browser_download_url")
        if type(manifest_url) is not str or not manifest_url.startswith("https://"):
            raise ReleaseContractError("Release manifest URL must use HTTPS")
        manifest_size = manifest_asset.get("size")
        if (
            type(manifest_size) is not int
            or manifest_size <= 0
            or manifest_size > MAX_RELEASE_JSON_BYTES
        ):
            raise ReleaseContractError("Invalid release manifest asset size")
        manifest_digest = _github_sha256(manifest_asset.get("digest"))
        manifest = validate_release_manifest(
            self._get_json(
                manifest_url,
                expected_size=manifest_size,
                expected_sha256=manifest_digest,
            )
        )
        actual_channel = "prerelease" if release.get("prerelease") else "stable"
        if actual_channel != expected_channel or manifest["channel"] != expected_channel:
            raise ReleaseContractError(
                "Release channel does not match the selected channel"
            )
        tag = release.get("tag_name")
        if type(tag) is not str:
            raise ReleaseContractError("Release tag is missing")
        try:
            tag_version = Version(tag.removeprefix("v"))
        except InvalidVersion as exc:
            raise ReleaseContractError("Release tag is not a valid version") from exc
        if tag_version != Version(manifest["version"]):
            raise ReleaseContractError("Release tag and manifest version differ")
        selected: list[dict[str, Any]] = []
        for artifact in manifest["artifacts"]:
            if (artifact["platform"], artifact["arch"]) != self.target:
                continue
            github_asset = by_name.get(artifact["filename"])
            if not isinstance(github_asset, dict):
                raise ReleaseContractError(
                    f"Release asset is missing: {artifact['filename']}"
                )
            if (
                type(github_asset.get("size")) is not int
                or github_asset["size"] != artifact["size"]
            ):
                raise ReleaseContractError(
                    f"Release asset size differs: {artifact['filename']}"
                )
            if _github_sha256(github_asset.get("digest")) != artifact["sha256"]:
                raise ReleaseContractError(
                    f"Release asset digest differs: {artifact['filename']}"
                )
            url = github_asset.get("browser_download_url")
            if type(url) is not str or not url.startswith("https://"):
                raise ReleaseContractError("Release artifact URL must use HTTPS")
            selected.append({**artifact, "download_url": url})
        if not selected:
            raise ReleaseContractError(
                f"No release artifacts for {self.target[0]}/{self.target[1]}"
            )
        problems: list[str] = []
        if manifest["deployment_schema_version"] != DEPLOYMENT_SCHEMA_VERSION:
            problems.append(
                "Deployment schema mismatch: "
                f"{manifest['deployment_schema_version']} != "
                f"{DEPLOYMENT_SCHEMA_VERSION}"
            )
        if Version(manifest["minimum_manager_version"]) > Version(MANAGER_VERSION):
            problems.append(
                f"Manager {MANAGER_VERSION} is older than required "
                f"{manifest['minimum_manager_version']}"
            )
        if not manifest["automatic_upgrade"]:
            problems.append("Release requires a manual deployment migration")
        return {
            "version": manifest["version"],
            "channel": manifest["channel"],
            "change_scope": manifest["change_scope"],
            "compatible": not problems,
            "compatibility": {
                "deployment_schema_version": manifest["deployment_schema_version"],
                "minimum_manager_version": manifest["minimum_manager_version"],
                "catalog_version": manifest["catalog_version"],
                "catalog_digest": manifest["catalog_digest"],
                "automatic_upgrade": manifest["automatic_upgrade"],
                "problems": problems,
            },
            "release_url": release.get("html_url"),
            "published_at": release.get("published_at"),
            "artifacts": selected,
        }

    def _get_json(
        self,
        url: str,
        *,
        expected_size: int | None = None,
        expected_sha256: str | None = None,
    ) -> Any:
        with self.transport.open(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "DicePP-Manager",
                "X-GitHub-Api-Version": _GITHUB_API_VERSION,
            },
        ) as response:
            if response.status != 200:
                raise ReleaseError(
                    f"Release source returned HTTP {response.status}"
                )
            raw = response.read(MAX_RELEASE_JSON_BYTES + 1)
        if len(raw) > MAX_RELEASE_JSON_BYTES:
            raise ReleaseContractError("Release JSON exceeds the 2 MiB limit")
        if expected_size is not None and len(raw) != expected_size:
            raise ReleaseContractError("Release JSON asset size differs")
        if expected_sha256 is not None and _sha256_bytes(raw) != expected_sha256:
            raise ReleaseContractError("Release JSON asset digest differs")
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReleaseContractError("Release JSON is invalid") from exc

    def _download_artifact(
        self,
        version: str,
        artifact: dict[str, Any],
        *,
        operation: ReleaseOperation | None = None,
    ) -> Path:
        operation = operation or ReleaseOperation(
            kind="download",
            generation=-1,
            cancel_event=threading.Event(),
        )
        self._check_cancelled(operation)
        packages_dir = self._trusted_packages_root(create=True)
        version_dir = self._trusted_version_dir(
            packages_dir,
            version,
            create=True,
        )
        packages_guard = _capture_trusted_directory(
            packages_dir,
            root=self.layout.root,
        )
        version_guard = _capture_trusted_directory(
            version_dir,
            root=self.layout.root,
            parent=packages_guard,
        )
        if _is_windows_velopack_artifact(artifact):
            with self._generation_lock:
                generation = os.urandom(16).hex()
                target = version_dir / _velopack_bundle_generation_name(
                    generation
                )
                _cleanup_velopack_orphans(
                    version_guard,
                    in_progress_generation=generation,
                )
        else:
            target = version_dir / artifact["filename"]
        part = version_dir / f"{artifact['filename']}.part"
        metadata_path = version_dir / f"{artifact['filename']}.part.json"
        _require_regular_children(target, part, metadata_path)
        target_info = _validate_regular_path(
            target,
            version_guard,
            allow_missing=True,
        )
        if (
            target_info is not None
            and target.stat().st_size == artifact["size"]
            and _sha256_file(target) == artifact["sha256"]
        ):
            return target
        _unlink_trusted_file(target, version_guard, missing_ok=True)
        attempts = 0
        no_progress_failures = 0
        backoff_index = 0
        while True:
            before = _existing_file_size(part)
            attempts += 1
            try:
                return self._download_artifact_once(
                    artifact,
                    operation=operation,
                    target=target,
                    part=part,
                    metadata_path=metadata_path,
                    version_guard=version_guard,
                )
            except ReleaseCancelledError:
                raise
            except (
                _ArtifactTruncatedError,
                _ArtifactDigestError,
                _ArtifactConnectionError,
            ) as exc:
                reason = exc
            except ReleaseError as exc:
                cause = exc.__cause__
                if (
                    type(exc) is not ReleaseError
                    or cause is None
                    or isinstance(cause, urllib.error.HTTPError)
                ):
                    # HTTP status errors and local validation failures do not
                    # benefit from a retry; only connection-level open
                    # failures (ReleaseError with a non-HTTPError cause) do.
                    raise
                reason = exc
            if _existing_file_size(part) > before:
                no_progress_failures = 0
                backoff_index = 0
            else:
                no_progress_failures += 1
                if no_progress_failures >= _DOWNLOAD_MAX_NO_PROGRESS_FAILURES:
                    raise ReleaseDownloadError(
                        f"{reason} after {attempts} attempts"
                    ) from reason
            backoff = _DOWNLOAD_BACKOFF_SECONDS[
                min(backoff_index, len(_DOWNLOAD_BACKOFF_SECONDS) - 1)
            ]
            backoff_index += 1
            if operation.cancel_event.wait(backoff):
                self._check_cancelled(operation)

    def _download_artifact_once(
        self,
        artifact: dict[str, Any],
        *,
        operation: ReleaseOperation,
        target: Path,
        part: Path,
        metadata_path: Path,
        version_guard: TrustedDirectory,
    ) -> Path:
        """Run one download attempt, resuming from any kept .part file."""
        offset = 0
        validator: str | None = None
        part_info = _validate_regular_path(
            part,
            version_guard,
            allow_missing=True,
        )
        metadata_info = _validate_regular_path(
            metadata_path,
            version_guard,
            allow_missing=True,
        )
        if part_info is not None and metadata_info is not None:
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                metadata = {}
            if (
                metadata.get("url") == artifact["download_url"]
                and metadata.get("sha256") == artifact["sha256"]
                and type(metadata.get("validator")) is str
                and metadata["validator"]
            ):
                offset = part.stat().st_size
                validator = metadata["validator"]
        if offset == artifact["size"]:
            if _sha256_file(part) == artifact["sha256"]:
                _replace_trusted_file(part, target, version_guard)
                _fsync_trusted_directory(version_guard)
                _unlink_trusted_file(metadata_path, version_guard, missing_ok=True)
                _fsync_trusted_directory(version_guard)
                return target
            _unlink_trusted_file(part, version_guard, missing_ok=True)
            _unlink_trusted_file(metadata_path, version_guard, missing_ok=True)
            offset = 0
            validator = None
        elif offset > artifact["size"]:
            _unlink_trusted_file(part, version_guard, missing_ok=True)
            _unlink_trusted_file(metadata_path, version_guard, missing_ok=True)
            offset = 0
            validator = None
        headers = {
            "Accept": "application/octet-stream",
            "User-Agent": "DicePP-Manager",
        }
        if offset and validator:
            headers.update({"Range": f"bytes={offset}-", "If-Range": validator})
        self._check_cancelled(operation)
        response = self.transport.open(artifact["download_url"], headers=headers)
        resume = offset > 0 and _valid_resume_response(
            response,
            offset=offset,
            expected_size=artifact["size"],
            validator=validator,
        )
        if offset and not resume:
            response.close()
            _unlink_trusted_file(part, version_guard, missing_ok=True)
            _unlink_trusted_file(metadata_path, version_guard, missing_ok=True)
            _fsync_trusted_directory(version_guard)
            offset = 0
            response = self.transport.open(
                artifact["download_url"],
                headers={
                    "Accept": "application/octet-stream",
                    "User-Agent": "DicePP-Manager",
                },
            )
        if response.status not in ({206} if resume else {200}):
            response.close()
            raise ReleaseDownloadError(
                f"Unexpected artifact response HTTP {response.status}"
            )
        current_validator = _response_validator(response.headers) or ""
        try:
            _atomic_write_json(
                metadata_path,
                {
                    "url": artifact["download_url"],
                    "sha256": artifact["sha256"],
                    "validator": current_validator,
                },
                trusted_parent=version_guard,
            )
        except Exception:
            response.close()
            raise
        try:
            expected_remaining = artifact["size"] - offset
            received = 0
            with response, _open_regular_binary(
                part,
                version_guard,
                append=resume,
            ) as output:
                while True:
                    self._check_cancelled(operation)
                    allowance = expected_remaining - received
                    try:
                        chunk = response.read(min(1024 * 1024, allowance + 1))
                    except (
                        http.client.IncompleteRead,
                        ConnectionError,
                        TimeoutError,
                        urllib.error.URLError,
                    ) as exc:
                        if isinstance(exc, urllib.error.HTTPError):
                            raise
                        raise _ArtifactConnectionError(
                            f"Artifact download connection failed: {exc}"
                        ) from exc
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > expected_remaining:
                        raise ReleaseDownloadError(
                            "Artifact response exceeds manifest size"
                        )
                    output.write(chunk)
                    with self._lock:
                        self._download["bytes_downloaded"] = output.tell()
                output.flush()
                os.fsync(output.fileno())
            if part.stat().st_size != artifact["size"]:
                # A cleanly truncated body keeps its .part file so the next
                # attempt resumes with a Range request.
                raise _ArtifactTruncatedError(
                    "Downloaded artifact size differs from manifest"
                )
            if _sha256_file(part) != artifact["sha256"]:
                _unlink_trusted_file(part, version_guard, missing_ok=True)
                _unlink_trusted_file(metadata_path, version_guard, missing_ok=True)
                _fsync_trusted_directory(version_guard)
                raise _ArtifactDigestError(
                    "Downloaded artifact SHA-256 differs from manifest"
                )
            _replace_trusted_file(part, target, version_guard)
            _fsync_trusted_directory(version_guard)
            _unlink_trusted_file(metadata_path, version_guard, missing_ok=True)
            _fsync_trusted_directory(version_guard)
            return target
        except (
            _ArtifactTruncatedError,
            _ArtifactDigestError,
            _ArtifactConnectionError,
        ):
            raise
        except ReleaseDownloadError:
            _unlink_trusted_file(part, version_guard, missing_ok=True)
            _unlink_trusted_file(metadata_path, version_guard, missing_ok=True)
            _fsync_trusted_directory(version_guard)
            raise
        except Exception:
            if not current_validator:
                _unlink_trusted_file(part, version_guard, missing_ok=True)
                _unlink_trusted_file(metadata_path, version_guard, missing_ok=True)
                _fsync_trusted_directory(version_guard)
            raise

    def _materialize_velopack_bundle(
        self,
        release: dict[str, Any],
        artifact: dict[str, Any],
        target: Path,
    ) -> tuple[Path, dict[str, Any]]:
        packages_guard = _capture_trusted_directory(
            self.layout.manager_packages_dir,
            root=self.layout.root,
        )
        version_guard = _capture_trusted_directory(
            target.parent,
            root=self.layout.root,
            parent=packages_guard,
        )
        target_info = _validate_regular_path(
            target,
            version_guard,
            allow_missing=False,
        )
        generation = _velopack_generation_from_bundle_name(target.name)
        payload = target.parent / _velopack_payload_generation_name(generation)
        payload_identity: tuple[int, int] | None = None
        try:
            bundle = validate_velopack_bundle(
                target,
                expected_dicepp_version=release["version"],
                expected_channel=release["channel"],
                expected_size=artifact["size"],
                expected_sha256=artifact["sha256"],
            )
            _require_regular_children(payload)
            extract_verified_nupkg(
                bundle,
                target.parent,
                destination_name=payload.name,
            )
            payload_info = _validate_regular_path(
                payload,
                version_guard,
                allow_missing=False,
            )
            if payload_info is None:
                raise ReleaseDownloadError(
                    "Velopack payload disappeared after extraction"
                )
            payload_identity = (payload_info.st_dev, payload_info.st_ino)
            _fsync_trusted_directory(version_guard)
            return payload, bundle.manifest
        except (OSError, VelopackBundleError, ReleaseError) as exc:
            if payload_identity is not None:
                _unlink_trusted_file_if_identity(
                    payload,
                    version_guard,
                    payload_identity,
                )
            if target_info is not None:
                _unlink_trusted_file_if_identity(
                    target,
                    version_guard,
                    (target_info.st_dev, target_info.st_ino),
                )
            _fsync_trusted_directory(version_guard)
            if isinstance(exc, ReleaseDownloadError):
                raise
            raise ReleaseDownloadError(
                f"Downloaded Velopack bundle is invalid: {exc}"
            ) from exc

    def _write_verified_metadata(
        self,
        release: dict[str, Any],
        artifact: dict[str, Any],
        target: Path,
        *,
        payload_path: Path | None = None,
        bundle_manifest: dict[str, Any] | None = None,
        completed_at: str,
    ) -> Path:
        destination = target.parent / "verified-release.json"
        _require_regular_children(destination)
        packages_guard = _capture_trusted_directory(
            self.layout.manager_packages_dir,
            root=self.layout.root,
        )
        version_guard = _capture_trusted_directory(
            target.parent,
            root=self.layout.root,
            parent=packages_guard,
        )
        windows_bundle = (
            artifact.get("platform") == "windows"
            and artifact.get("purpose") == "velopack-bundle"
        )
        target_info: os.stat_result | None = None
        payload_info: os.stat_result | None = None
        previous_generation = _read_managed_velopack_generation(
            destination,
            version_guard,
        )
        previous_paths = {
            path.name for path, _identity in previous_generation
        }
        try:
            target_info = _validate_regular_path(
                target,
                version_guard,
                allow_missing=False,
            )
            if windows_bundle:
                if payload_path is None or not isinstance(bundle_manifest, dict):
                    raise ReleaseDownloadError(
                        "Windows bundle manifest and payload are required"
                    )
                generation = _velopack_generation_from_bundle_name(target.name)
                if (
                    payload_path.name
                    != _velopack_payload_generation_name(generation)
                ):
                    raise ReleaseDownloadError(
                        "Windows bundle and payload generations differ"
                    )
                normalized_manifest = validate_velopack_bundle_manifest(
                    bundle_manifest,
                    expected_dicepp_version=release["version"],
                    expected_channel=release["channel"],
                )
                payload_info = _validate_regular_path(
                    payload_path,
                    version_guard,
                    allow_missing=False,
                )
                with (
                    open_regular_binary_no_follow(target) as authorized_bundle,
                    open_regular_binary_no_follow(
                        payload_path
                    ) as authorized_payload,
                ):
                    opened_bundle = os.fstat(authorized_bundle.fileno())
                    opened_payload = os.fstat(authorized_payload.fileno())
                    bundle_digest = _sha256_handle(authorized_bundle)
                    payload_digest = _sha256_handle(authorized_payload)
                    validated_bundle = validate_velopack_bundle(
                        target,
                        expected_dicepp_version=release["version"],
                        expected_channel=release["channel"],
                        expected_size=artifact["size"],
                        expected_sha256=artifact["sha256"],
                    )
                    rebound_target = _validate_regular_path(
                        target,
                        version_guard,
                        allow_missing=False,
                    )
                    rebound_payload = _validate_regular_path(
                        payload_path,
                        version_guard,
                        allow_missing=False,
                    )
                    if (
                        normalized_manifest != bundle_manifest
                        or validated_bundle.manifest != bundle_manifest
                        or target_info is None
                        or payload_info is None
                        or rebound_target is None
                        or rebound_payload is None
                        or opened_bundle.st_nlink != 1
                        or opened_bundle.st_size != artifact["size"]
                        or bundle_digest != artifact["sha256"]
                        or opened_payload.st_nlink != 1
                        or opened_payload.st_size
                        != bundle_manifest["nupkg"]["size"]
                        or payload_digest
                        != bundle_manifest["nupkg"]["sha256"]
                        or not _same_file_identity(
                            opened_bundle,
                            target_info,
                            rebound_target,
                        )
                        or not _same_file_identity(
                            opened_payload,
                            payload_info,
                            rebound_payload,
                        )
                        or (
                            validated_bundle.device,
                            validated_bundle.inode,
                        )
                        != (opened_bundle.st_dev, opened_bundle.st_ino)
                    ):
                        raise ReleaseDownloadError(
                            "Windows bundle generation changed before metadata publish"
                        )
                    _atomic_publish_json(
                        destination,
                        _verified_metadata_payload(
                            release,
                            artifact,
                            target,
                            payload_path=payload_path,
                            bundle_manifest=bundle_manifest,
                            generation=generation,
                            completed_at=completed_at,
                        ),
                        trusted_parent=version_guard,
                    )
            elif payload_path is not None or bundle_manifest is not None:
                raise ReleaseDownloadError(
                    "Non-Windows artifact has unexpected bundle metadata"
                )
            else:
                _atomic_write_json(
                    destination,
                    _verified_metadata_payload(
                        release,
                        artifact,
                        target,
                        payload_path=None,
                        bundle_manifest=None,
                        generation=None,
                        completed_at=completed_at,
                    ),
                    trusted_parent=version_guard,
                )
        except Exception as exc:
            if windows_bundle:
                if (
                    payload_path is not None
                    and payload_info is not None
                    and payload_path.name not in previous_paths
                ):
                    _unlink_trusted_file_if_identity(
                        payload_path,
                        version_guard,
                        (payload_info.st_dev, payload_info.st_ino),
                    )
                if target_info is not None and target.name not in previous_paths:
                    _unlink_trusted_file_if_identity(
                        target,
                        version_guard,
                        (target_info.st_dev, target_info.st_ino),
                    )
                try:
                    _fsync_trusted_directory(version_guard)
                except OSError:
                    pass
            if isinstance(exc, VelopackBundleError):
                raise ReleaseDownloadError(
                    f"Windows bundle generation is invalid: {exc}"
                ) from exc
            raise
        if windows_bundle:
            current = {target.name}
            if payload_path is not None:
                current.add(payload_path.name)
            for old_path, old_identity in previous_generation:
                if old_path.name not in current:
                    _unlink_trusted_file_if_identity(
                        old_path,
                        version_guard,
                        old_identity,
                    )
            try:
                _fsync_trusted_directory(version_guard)
            except OSError:
                pass
        return destination

    def _local_packages(self) -> list[dict[str, Any]]:
        try:
            root = self._trusted_packages_root(create=False)
        except FileNotFoundError:
            return []
        root_guard = _capture_trusted_directory(
            root,
            root=self.layout.root,
        )
        result: list[dict[str, Any]] = []
        for directory in root.iterdir():
            if not directory.is_dir() or directory.is_symlink():
                continue
            try:
                trusted = self._trusted_version_dir(
                    root,
                    directory.name,
                    create=False,
                )
                version_guard = _capture_trusted_directory(
                    trusted,
                    root=self.layout.root,
                    parent=root_guard,
                )
                metadata_path = trusted / "verified-release.json"
                if (
                    not metadata_path.is_file()
                    or metadata_path.is_symlink()
                ):
                    continue
                _validate_regular_path(
                    metadata_path,
                    version_guard,
                    allow_missing=False,
                )
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                artifact = metadata.get("artifact")
                completed_at = metadata.get("completed_at")
                filename = (
                    artifact.get("filename")
                    if isinstance(artifact, dict)
                    else None
                )
                verified_name = metadata.get("verified_path")
                if (
                    type(completed_at) is not str
                    or type(filename) is not str
                    or not _SAFE_FILENAME_RE.fullmatch(filename)
                    or type(verified_name) is not str
                    or not _SAFE_FILENAME_RE.fullmatch(verified_name)
                ):
                    continue
                _parse_iso(completed_at)
                package = trusted / verified_name
                if not package.is_file() or package.is_symlink():
                    continue
                _validate_regular_path(
                    package,
                    version_guard,
                    allow_missing=False,
                )
                files = [verified_name, metadata_path.name]
                if artifact.get("purpose") == "velopack-bundle":
                    bundle_manifest = metadata.get("bundle_manifest")
                    payload_name = metadata.get("payload_verified_path")
                    generation = metadata.get("generation")
                    if (
                        not isinstance(bundle_manifest, dict)
                        or type(payload_name) is not str
                        or not _SAFE_FILENAME_RE.fullmatch(payload_name)
                        or type(generation) is not str
                        or verified_name
                        != _velopack_bundle_generation_name(generation)
                        or payload_name
                        != _velopack_payload_generation_name(generation)
                    ):
                        continue
                    payload_path = trusted / payload_name
                    if not payload_path.is_file() or payload_path.is_symlink():
                        continue
                    _validate_regular_path(
                        payload_path,
                        version_guard,
                        allow_missing=False,
                    )
                    validated = validate_velopack_bundle(
                        package,
                        expected_dicepp_version=metadata.get("version"),
                        expected_channel=metadata.get("channel"),
                        expected_size=artifact.get("size"),
                        expected_sha256=artifact.get("sha256"),
                    )
                    if (
                        validated.manifest != bundle_manifest
                        or payload_path.stat().st_size != validated.nupkg_size
                        or _sha256_file(payload_path) != validated.nupkg_sha256
                    ):
                        continue
                    files.append(payload_name)
                result.append(
                    {
                        "version": directory.name,
                        "files": files,
                        "completed_at": completed_at,
                    }
                )
            except (OSError, ValueError, ReleaseError, json.JSONDecodeError):
                continue
        return sorted(
            result,
            key=lambda item: _parse_iso(item["completed_at"]),
            reverse=True,
        )

    def _prune(
        self,
        keep: int,
        *,
        protected_version: str | None,
    ) -> None:
        root = self._trusted_packages_root(create=False)
        root_guard = _capture_trusted_directory(
            root,
            root=self.layout.root,
        )
        entries: list[tuple[datetime, str]] = []
        for directory in root.iterdir():
            if directory.is_symlink() or not directory.is_dir():
                continue
            try:
                trusted = self._trusted_version_dir(
                    root,
                    directory.name,
                    create=False,
                )
                version_guard = _capture_trusted_directory(
                    trusted,
                    root=self.layout.root,
                    parent=root_guard,
                )
                timestamp = datetime.fromtimestamp(
                    trusted.stat().st_mtime,
                    tz=timezone.utc,
                )
                metadata = trusted / "verified-release.json"
                if metadata.is_file() and not metadata.is_symlink():
                    _validate_regular_path(
                        metadata,
                        version_guard,
                        allow_missing=False,
                    )
                    payload = json.loads(metadata.read_text(encoding="utf-8"))
                    completed_at = payload.get("completed_at")
                    if type(completed_at) is str:
                        timestamp = _parse_iso(completed_at)
                entries.append((timestamp, directory.name))
            except (OSError, ValueError, ReleaseError, json.JSONDecodeError):
                continue
        entries.sort(reverse=True)
        protected = (
            _safe_version_segment(protected_version)
            if protected_version is not None
            else None
        )
        keep_versions = {
            _safe_version_segment(version)
            for version in self.protected_versions_loader()
        }
        if protected is not None:
            keep_versions.add(protected)
        for _timestamp, version in entries:
            if len(keep_versions) >= keep:
                break
            keep_versions.add(version)
        # Additive protection, outside the recency keep budget: rollback
        # material fetched for the currently installed version lives in its
        # (metadata-less) version directory; never prune it out from under
        # an in-flight upgrade transaction.
        try:
            keep_versions.add(
                _safe_version_segment(self.current_version_loader())
            )
        except (ReleaseContractError, TypeError):
            pass
        for _timestamp, version in entries:
            if version in keep_versions:
                continue
            directory = self._trusted_version_dir(
                root,
                version,
                create=False,
            )
            version_guard = _capture_trusted_directory(
                directory,
                root=self.layout.root,
                parent=root_guard,
            )
            children = list(directory.iterdir())
            try:
                for child in children:
                    _unlink_trusted_file(
                        child,
                        version_guard,
                        missing_ok=False,
                    )
            except (FileNotFoundError, ReleaseDownloadError):
                continue
            _assert_trusted_directory(version_guard)
            directory.rmdir()
            _fsync_trusted_directory(root_guard)
        if protected is not None:
            self._trusted_version_dir(root, protected, create=False)

    def _trusted_packages_root(self, *, create: bool) -> Path:
        base = Path(os.path.abspath(self.layout.root))
        if create:
            base.mkdir(parents=True, exist_ok=True)
        try:
            assert_contained_no_reparse(
                base,
                root=base,
                allow_missing=False,
            )
            assert_directory_no_reparse(base)
        except (OSError, UnsafePathError) as exc:
            raise ReleaseDownloadError(
                f"Untrusted instance root for package storage: {base}"
            ) from exc
        manager = base / "manager"
        packages = manager / "packages"
        for path in (manager, packages):
            try:
                assert_contained_no_reparse(
                    path,
                    root=base,
                    allow_missing=True,
                )
            except (OSError, UnsafePathError) as exc:
                raise ReleaseDownloadError(
                    f"Untrusted Manager package directory: {path}"
                ) from exc
            if not os.path.lexists(path):
                if not create:
                    raise FileNotFoundError(path)
                path.mkdir()
                _fsync_directory(path.parent)
            try:
                assert_contained_no_reparse(
                    path,
                    root=base,
                    allow_missing=False,
                )
                assert_directory_no_reparse(path)
            except (OSError, UnsafePathError) as exc:
                raise ReleaseDownloadError(
                    f"Untrusted Manager package directory: {path}"
                ) from exc
        return packages

    def _trusted_version_dir(
        self,
        root: Path,
        version: str,
        *,
        create: bool,
    ) -> Path:
        trusted_root = self._trusted_packages_root(create=create)
        if trusted_root != root:
            raise ReleaseDownloadError("Manager package root changed")
        path = trusted_root / _safe_version_segment(version)
        try:
            assert_contained_no_reparse(
                path,
                root=trusted_root,
                allow_missing=True,
            )
        except (OSError, UnsafePathError) as exc:
            raise ReleaseDownloadError(
                "Untrusted release version directory"
            ) from exc
        if not os.path.lexists(path):
            if not create:
                raise FileNotFoundError(path)
            path.mkdir()
            _fsync_directory(trusted_root)
        try:
            assert_contained_no_reparse(
                path,
                root=trusted_root,
                allow_missing=False,
            )
            assert_directory_no_reparse(path)
        except (OSError, UnsafePathError) as exc:
            raise ReleaseDownloadError(
                "Untrusted release version directory"
            ) from exc
        return path

    def _sync_channel(self, channel: str) -> None:
        with self._lock:
            if self._latest_channel is not None and self._latest_channel != channel:
                self._latest = None
                self._latest_channel = None
                self._download = {"status": "idle"}
                self._persist_state_locked()

    def _discard_stale_latest_locked(self, *, persist: bool) -> None:
        if self._latest is None:
            return
        try:
            _validate_cached_latest(
                self._latest,
                channel=self._latest_channel,
                current_version=_normalized_version(
                    self.current_version_loader()
                ),
                target=self.target,
            )
        except (InvalidVersion, ReleaseContractError, TypeError):
            self._latest = None
            self._latest_channel = None
            if self._download.get("status") in {"idle", "queued"}:
                self._download = {"status": "idle"}
            if persist:
                self._persist_state_locked()

    @property
    def _state_path(self) -> Path:
        return self.layout.manager_state_dir / "release-state.json"

    def _load_state(self) -> None:
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return
            discovery = payload.get("discovery")
            if isinstance(discovery, dict):
                self._discovery = {
                    **self._discovery,
                    **discovery,
                    "status": (
                        "interrupted"
                        if discovery.get("status") == "checking"
                        else discovery.get("status", "idle")
                    ),
                }
            latest = payload.get("available")
            channel = payload.get("channel")
            if channel in {"stable", "prerelease"}:
                if latest is None:
                    self._latest_channel = channel
                elif isinstance(latest, dict):
                    _validate_cached_latest(
                        latest,
                        channel=channel,
                        current_version=_normalized_version(
                            self.current_version_loader()
                        ),
                        target=self.target,
                    )
                    self._latest = latest
                    self._latest_channel = channel
            download = payload.get("download")
            if isinstance(download, dict):
                self._download = {
                    **download,
                    "status": (
                        "interrupted"
                        if download.get("status") in {"queued", "downloading"}
                        else download.get("status", "idle")
                    ),
                }
        except (InvalidVersion, ReleaseContractError, TypeError):
            self._latest = None
            self._latest_channel = None
            self._download = {"status": "idle"}
            self._persist_state_locked()
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return

    def _persist_state_locked(self) -> None:
        _atomic_write_json(
            self._state_path,
            {
                "format_version": 1,
                "channel": self._latest_channel,
                "available": self._latest,
                "discovery": self._discovery,
                "download": self._download,
            },
        )


def _github_sha256(value: Any) -> str:
    if type(value) is not str or not value.startswith("sha256:"):
        raise ReleaseContractError("GitHub asset SHA-256 digest is missing")
    digest = value.removeprefix("sha256:")
    if not _SHA256_RE.fullmatch(digest):
        raise ReleaseContractError("GitHub asset SHA-256 digest is invalid")
    return digest


def _validate_cached_latest(
    latest: Any,
    *,
    channel: str | None,
    current_version: str,
    target: tuple[str, str],
) -> None:
    if not isinstance(latest, dict) or channel not in {"stable", "prerelease"}:
        raise ReleaseContractError("Persisted release candidate is malformed")
    required = {
        "version",
        "channel",
        "change_scope",
        "compatible",
        "compatibility",
        "release_url",
        "published_at",
        "artifacts",
    }
    if set(latest) != required or latest["channel"] != channel:
        raise ReleaseContractError("Persisted release candidate fields mismatch")
    try:
        version = Version(latest["version"])
        current = Version(current_version)
    except (InvalidVersion, TypeError) as exc:
        raise ReleaseContractError("Persisted release version is invalid") from exc
    if version <= current:
        raise ReleaseContractError("Persisted release is not newer than DicePP")
    if type(latest["compatible"]) is not bool:
        raise ReleaseContractError("Persisted compatibility is invalid")
    if (
        not isinstance(latest["change_scope"], list)
        or not latest["change_scope"]
        or not all(type(item) is str and item for item in latest["change_scope"])
    ):
        raise ReleaseContractError("Persisted change scope is invalid")
    compatibility = latest["compatibility"]
    if (
        not isinstance(compatibility, dict)
        or type(compatibility.get("deployment_schema_version")) is not int
        or type(compatibility.get("minimum_manager_version")) is not str
        or type(compatibility.get("catalog_version")) is not int
        or type(compatibility.get("catalog_digest")) is not str
        or not _SHA256_RE.fullmatch(compatibility["catalog_digest"])
        or type(compatibility.get("automatic_upgrade")) is not bool
        or not isinstance(compatibility.get("problems"), list)
    ):
        raise ReleaseContractError("Persisted compatibility metadata is invalid")
    try:
        Version(compatibility["minimum_manager_version"])
    except InvalidVersion as exc:
        raise ReleaseContractError("Persisted Manager version is invalid") from exc
    artifacts = latest["artifacts"]
    if not isinstance(artifacts, list) or not artifacts:
        raise ReleaseContractError("Persisted release has no artifacts")
    seen: set[tuple[str, str, str]] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict) or set(artifact) != {
            "platform",
            "arch",
            "filename",
            "purpose",
            "size",
            "sha256",
            "download_url",
        }:
            raise ReleaseContractError("Persisted artifact fields mismatch")
        base = {key: value for key, value in artifact.items() if key != "download_url"}
        _validate_artifact(base, seen)
        if (artifact["platform"], artifact["arch"]) != target:
            raise ReleaseContractError("Persisted artifact target changed")
        if (
            type(artifact["download_url"]) is not str
            or not artifact["download_url"].startswith("https://")
        ):
            raise ReleaseContractError("Persisted artifact URL is invalid")


def _response_validator(headers: Mapping[str, str]) -> str | None:
    return headers.get("ETag") or headers.get("Last-Modified")


def _existing_file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _valid_resume_response(
    response,
    *,
    offset: int,
    expected_size: int,
    validator: str | None,
) -> bool:
    if response.status != 206 or not validator:
        return False
    match = _CONTENT_RANGE_RE.fullmatch(response.headers.get("Content-Range", ""))
    if match is None:
        return False
    start, end, total = (int(value) for value in match.groups())
    return (
        start == offset
        and end == expected_size - 1
        and total == expected_size
        and start <= end
        and _response_validator(response.headers) == validator
    )


def _safe_version_segment(value: str) -> str:
    try:
        normalized = str(Version(value))
    except (InvalidVersion, TypeError) as exc:
        raise ReleaseContractError("Invalid package version") from exc
    if (
        not _SAFE_FILENAME_RE.fullmatch(normalized)
        or Path(normalized).name != normalized
    ):
        raise ReleaseContractError("Unsafe package version")
    return normalized


def _normalized_version(value: str) -> str:
    try:
        return str(Version(value.removeprefix("v")))
    except (InvalidVersion, AttributeError) as exc:
        raise ReleaseContractError(f"Invalid DicePP version: {value!r}") from exc


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    with open_regular_binary_no_follow(path) as handle:
        return _sha256_handle(handle)


def _sha256_handle(handle) -> str:
    handle.seek(0)
    digest = hashlib.sha256()
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def _is_windows_velopack_artifact(artifact: Mapping[str, Any]) -> bool:
    return (
        artifact.get("platform") == "windows"
        and artifact.get("arch") == "amd64"
        and artifact.get("purpose") == "velopack-bundle"
        and artifact.get("filename") == VELOPACK_BUNDLE_NAME
    )


def _velopack_bundle_generation_name(generation: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{32}", generation):
        raise ReleaseDownloadError("Invalid Velopack generation identifier")
    return f"velopack-{generation}.win-x64.zip"


def _velopack_payload_generation_name(generation: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{32}", generation):
        raise ReleaseDownloadError("Invalid Velopack generation identifier")
    return f"payload-{generation}.nupkg"


def _velopack_generation_from_bundle_name(filename: str) -> str:
    match = _VELOPACK_GENERATION_RE.fullmatch(filename)
    if match is None:
        raise ReleaseDownloadError(
            "Velopack bundle is not stored in an isolated generation"
        )
    return match.group(1)


def _verified_metadata_payload(
    release: Mapping[str, Any],
    artifact: Mapping[str, Any],
    target: Path,
    *,
    payload_path: Path | None,
    bundle_manifest: dict[str, Any] | None,
    generation: str | None,
    completed_at: str,
) -> dict[str, Any]:
    return {
        "contract_version": RELEASE_CONTRACT_VERSION,
        "version": release["version"],
        "channel": release["channel"],
        "change_scope": release["change_scope"],
        "compatibility": release["compatibility"],
        "artifact": {
            key: artifact[key]
            for key in (
                "platform",
                "arch",
                "filename",
                "purpose",
                "size",
                "sha256",
            )
        },
        "generation": generation,
        "verified_path": target.name,
        "bundle_manifest": bundle_manifest,
        "payload_verified_path": (
            payload_path.name if payload_path is not None else None
        ),
        "completed_at": completed_at,
    }


def _same_file_identity(
    *records: os.stat_result,
) -> bool:
    first = records[0]
    identity = (first.st_dev, first.st_ino)
    return all(
        stat.S_ISREG(record.st_mode)
        and record.st_nlink == 1
        and (record.st_dev, record.st_ino) == identity
        for record in records
    )


def _read_managed_velopack_generation(
    metadata_path: Path,
    version_guard: TrustedDirectory,
) -> list[tuple[Path, tuple[int, int]]]:
    """Capture only files from a previously published managed generation."""

    try:
        metadata_info = _validate_regular_path(
            metadata_path,
            version_guard,
            allow_missing=True,
        )
        if metadata_info is None:
            return []
        with open_regular_binary_no_follow(metadata_path) as handle:
            raw = handle.read(MAX_RELEASE_JSON_BYTES + 1)
        if len(raw) > MAX_RELEASE_JSON_BYTES:
            return []
        metadata = json.loads(raw.decode("utf-8"))
        generation = metadata.get("generation")
        bundle_name = metadata.get("verified_path")
        payload_name = metadata.get("payload_verified_path")
        if (
            type(generation) is not str
            or bundle_name != _velopack_bundle_generation_name(generation)
            or payload_name != _velopack_payload_generation_name(generation)
        ):
            return []
        result: list[tuple[Path, tuple[int, int]]] = []
        for name in (bundle_name, payload_name):
            path = version_guard.path / name
            info = _validate_regular_path(
                path,
                version_guard,
                allow_missing=False,
            )
            if info is None:
                return []
            result.append((path, (info.st_dev, info.st_ino)))
        return result
    except (
        OSError,
        ReleaseError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ):
        return []


def _published_velopack_generation(
    metadata_path: Path,
    version_guard: TrustedDirectory,
) -> _PublishedGeneration:
    try:
        info = _validate_regular_path(
            metadata_path,
            version_guard,
            allow_missing=True,
        )
        if info is None:
            return _PublishedGeneration(
                _PublishedGenerationState.CONFIRMED_ABSENT
            )
        with open_regular_binary_no_follow(metadata_path) as handle:
            raw = handle.read(MAX_RELEASE_JSON_BYTES + 1)
        if len(raw) > MAX_RELEASE_JSON_BYTES:
            return _PublishedGeneration(_PublishedGenerationState.UNKNOWN)
        metadata = json.loads(raw.decode("utf-8"))
        if not isinstance(metadata, dict):
            return _PublishedGeneration(_PublishedGenerationState.UNKNOWN)
        generation = metadata.get("generation")
        if (
            type(generation) is str
            and metadata.get("verified_path")
            == _velopack_bundle_generation_name(generation)
            and metadata.get("payload_verified_path")
            == _velopack_payload_generation_name(generation)
        ):
            return _PublishedGeneration(
                _PublishedGenerationState.VALID_CURRENT,
                generation,
            )
    except (
        OSError,
        ReleaseError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ):
        pass
    return _PublishedGeneration(_PublishedGenerationState.UNKNOWN)


def _cleanup_velopack_orphans(
    version_guard: TrustedDirectory,
    *,
    in_progress_generation: str,
) -> None:
    """Best-effort cleanup of exact managed names not referenced by metadata."""

    published = _published_velopack_generation(
        version_guard.path / "verified-release.json",
        version_guard,
    )
    if published.state is _PublishedGenerationState.UNKNOWN:
        return
    protected = {in_progress_generation}
    if published.state is _PublishedGenerationState.VALID_CURRENT:
        if published.token is None:
            return
        protected.add(published.token)
    try:
        entries = list(os.scandir(version_guard.path))
    except OSError:
        return
    for entry in entries:
        bundle_match = _VELOPACK_GENERATION_RE.fullmatch(entry.name)
        payload_match = _VELOPACK_PAYLOAD_GENERATION_RE.fullmatch(entry.name)
        match = bundle_match or payload_match
        if match is None or match.group(1) in protected:
            continue
        try:
            candidate = version_guard.path / entry.name
            info = candidate.lstat()
        except OSError:
            continue
        _unlink_trusted_file_if_identity(
            candidate,
            version_guard,
            (info.st_dev, info.st_ino),
        )


def _require_regular_children(*paths: Path) -> None:
    for path in paths:
        if (
            os.path.lexists(path)
            and (
                is_reparse_point(path)
                or not path.is_file()
            )
        ):
            raise ReleaseDownloadError(
                f"Release package path is not a regular file: {path.name}"
            )


def _capture_trusted_directory(
    path: Path,
    *,
    root: Path,
    parent: TrustedDirectory | None = None,
) -> TrustedDirectory:
    """Capture a non-reparse directory identity inside the trusted root."""
    if parent is not None:
        _assert_trusted_directory(parent)
    try:
        assert_contained_no_reparse(path, root=root, allow_missing=False)
        assert_directory_no_reparse(path)
    except (OSError, UnsafePathError) as exc:
        raise ReleaseDownloadError(f"Untrusted package directory: {path}") from exc
    root_resolved = root.resolve(strict=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise ReleaseDownloadError(f"Untrusted package directory: {path}")
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root_resolved):
        raise ReleaseDownloadError("Package directory escapes the instance root")
    guard = TrustedDirectory(
        path=path,
        resolved=resolved,
        root=root_resolved,
        device=info.st_dev,
        inode=info.st_ino,
        parent=parent,
    )
    _assert_trusted_directory(guard)
    return guard


def _assert_trusted_directory(directory: TrustedDirectory) -> None:
    if directory.parent is not None:
        _assert_trusted_directory(directory.parent)
    try:
        assert_contained_no_reparse(
            directory.path,
            root=directory.root,
            allow_missing=False,
        )
        assert_directory_no_reparse(directory.path)
    except (OSError, UnsafePathError) as exc:
        raise ReleaseDownloadError(
            f"Trusted package directory was replaced: {directory.path}"
        ) from exc
    info = directory.path.lstat()
    resolved = directory.path.resolve(strict=True)
    if (
        not stat.S_ISDIR(info.st_mode)
        or (info.st_dev, info.st_ino) != (directory.device, directory.inode)
        or resolved != directory.resolved
        or not resolved.is_relative_to(directory.root)
    ):
        raise ReleaseDownloadError(
            f"Trusted package directory identity changed: {directory.path}"
        )


def _coerce_trusted_directory(
    parent: Path | TrustedDirectory,
) -> TrustedDirectory:
    if isinstance(parent, TrustedDirectory):
        _assert_trusted_directory(parent)
        return parent
    return _capture_trusted_directory(parent, root=parent)


def _validate_regular_path(
    path: Path,
    trusted_parent: Path | TrustedDirectory,
    *,
    allow_missing: bool,
) -> os.stat_result | None:
    directory = _coerce_trusted_directory(trusted_parent)
    if path.parent != directory.path:
        raise ReleaseDownloadError(
            f"Release package path has an unexpected parent: {path.name}"
        )
    try:
        assert_contained_no_reparse(
            path,
            root=directory.path,
            allow_missing=allow_missing,
        )
    except FileNotFoundError:
        if allow_missing:
            return None
        raise
    except (OSError, UnsafePathError) as exc:
        raise ReleaseDownloadError(
            f"Release package path is a symbolic link or reparse point: {path.name}"
        ) from exc
    try:
        info = path.lstat()
    except FileNotFoundError:
        if allow_missing:
            return None
        raise
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ReleaseDownloadError(
            f"Release package path is not a private regular file: {path.name}"
        )
    _assert_trusted_directory(directory)
    resolved = path.resolve(strict=True)
    if resolved.parent != directory.resolved:
        raise ReleaseDownloadError(
            f"Release package path escapes its version directory: {path.name}"
        )
    return info


def _open_regular_binary(
    path: Path,
    trusted_parent: Path | TrustedDirectory,
    *,
    append: bool,
):
    """Open a package file without following links.

    POSIX uses ``O_NOFOLLOW``. Windows lacks that portable flag, so no content
    is written until lstat/resolve and opened-file identity have been checked
    after ``os.open``; this prevents the pre-existing/dangling-link cases and
    bounds the remaining reparse-point race to the platform primitive.
    """

    before = _validate_regular_path(path, trusted_parent, allow_missing=True)
    flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise ReleaseDownloadError(
                f"Opened package path is not a regular file: {path.name}"
            )
        after = _validate_regular_path(path, trusted_parent, allow_missing=False)
        if after is None or (
            hasattr(opened, "st_ino")
            and opened.st_ino
            and after.st_ino
            and (opened.st_dev, opened.st_ino) != (after.st_dev, after.st_ino)
        ):
            raise ReleaseDownloadError(
                f"Release package path changed while opening: {path.name}"
            )
        if before is not None and (
            before.st_dev,
            before.st_ino,
        ) != (
            after.st_dev,
            after.st_ino,
        ):
            raise ReleaseDownloadError(
                f"Release package path changed while opening: {path.name}"
            )
        if append:
            os.lseek(descriptor, 0, os.SEEK_END)
        else:
            os.ftruncate(descriptor, 0)
        return os.fdopen(descriptor, "ab" if append else "wb")
    except Exception:
        os.close(descriptor)
        raise


def _unlink_trusted_file(
    path: Path,
    trusted_parent: Path | TrustedDirectory,
    *,
    missing_ok: bool,
) -> None:
    try:
        _validate_regular_path(path, trusted_parent, allow_missing=missing_ok)
    except FileNotFoundError:
        if missing_ok:
            return
        raise
    if not path.exists():
        return
    # Revalidate immediately before unlink. Replacing a regular file with a
    # link can only remove the link itself; directories are rejected.
    _validate_regular_path(path, trusted_parent, allow_missing=False)
    path.unlink()


def _unlink_trusted_file_if_identity(
    path: Path,
    trusted_parent: Path | TrustedDirectory,
    identity: tuple[int, int],
) -> bool:
    """Best-effort removal that never unlinks a replacement generation."""

    try:
        directory = _coerce_trusted_directory(trusted_parent)
        if path.parent != directory.path:
            return False
        _assert_trusted_directory(directory)
        return delete_path_entry_no_follow(
            path,
            expected_identity=identity,
        )
    except (OSError, ReleaseError):
        return False


def _discard_trusted_child(
    path: Path,
    trusted_parent: Path | TrustedDirectory,
) -> None:
    """Remove one direct child without following a link or reparse point."""

    directory = _coerce_trusted_directory(trusted_parent)
    if path.parent != directory.path:
        raise ReleaseDownloadError(
            f"Refusing to discard path outside trusted directory: {path}"
        )
    _assert_trusted_directory(directory)
    if not os.path.lexists(path):
        return
    if is_reparse_point(path):
        try:
            path.unlink()
        except (IsADirectoryError, PermissionError):
            os.rmdir(path)
        return
    _unlink_trusted_file(path, directory, missing_ok=True)


def _replace_trusted_file(
    source: Path,
    target: Path,
    trusted_parent: Path | TrustedDirectory,
) -> None:
    _validate_regular_path(source, trusted_parent, allow_missing=False)
    _validate_regular_path(target, trusted_parent, allow_missing=True)
    source.replace(target)
    _validate_regular_path(target, trusted_parent, allow_missing=False)


def _atomic_write_json(
    path: Path,
    payload: Any,
    *,
    trusted_parent: Path | TrustedDirectory | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    directory = _coerce_trusted_directory(trusted_parent or path.parent)
    temporary = path.with_name(f"{path.name}.tmp")
    _require_regular_children(path, temporary)
    encoded = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    try:
        with _open_regular_binary(
            temporary,
            directory,
            append=False,
        ) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        _replace_trusted_file(temporary, path, directory)
        _fsync_trusted_directory(directory)
    except Exception:
        try:
            _unlink_trusted_file(temporary, directory, missing_ok=True)
        except ReleaseDownloadError:
            pass
        raise


def _atomic_publish_json(
    path: Path,
    payload: Any,
    *,
    trusted_parent: Path | TrustedDirectory,
) -> None:
    """Publish a final pointer without removing the previous pointer first.

    No exception is raised after the atomic replace commits, so callers never
    mistake a published generation for a failed one and delete its files.
    """

    directory = _coerce_trusted_directory(trusted_parent)
    temporary = path.with_name(
        f"{path.stem}-{os.urandom(8).hex()}.publish.tmp"
    )
    _require_regular_children(path, temporary)
    encoded = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    committed = False
    try:
        with _open_regular_binary(
            temporary,
            directory,
            append=False,
        ) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        _validate_regular_path(temporary, directory, allow_missing=False)
        _validate_regular_path(path, directory, allow_missing=True)
        _assert_trusted_directory(directory)
        os.replace(temporary, path)
        committed = True
    finally:
        if not committed:
            _unlink_trusted_file_if_identity(
                temporary,
                directory,
                _path_identity_or_impossible(temporary),
            )
    try:
        _fsync_trusted_directory(directory)
    except (OSError, ReleaseError):
        # The pointer is already atomically committed. Reporting failure here
        # would make the caller delete the generation it now references.
        pass


def _path_identity_or_impossible(path: Path) -> tuple[int, int]:
    try:
        info = path.lstat()
        return (info.st_dev, info.st_ino)
    except OSError:
        return (-1, -1)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        # Windows does not expose a portable directory fsync. File fsync and
        # atomic replace still provide the strongest cross-platform primitive.
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_trusted_directory(directory: TrustedDirectory) -> None:
    _assert_trusted_directory(directory)
    _fsync_directory(directory.path)
    _assert_trusted_directory(directory)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return parsed.astimezone(timezone.utc)


__all__ = [
    "MAX_LINUX_BUNDLE_BYTES",
    "MAX_RELEASE_JSON_BYTES",
    "RELEASE_CONTRACT_VERSION",
    "RELEASE_MANIFEST_NAME",
    "ReleaseContractError",
    "ReleaseDownloadError",
    "ReleaseError",
    "ReleaseManager",
    "UpdateSettings",
    "UrlTransport",
    "current_target",
    "validate_release_manifest",
]
