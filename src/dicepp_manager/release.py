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
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from packaging.version import InvalidVersion, Version

from dicepp_meta import get_version

from .deployment import DEPLOYMENT_SCHEMA_VERSION, MANAGER_VERSION

RELEASE_CONTRACT_VERSION = 1
RELEASE_MANIFEST_NAME = "dicepp-release.json"
DEFAULT_GITHUB_API = "https://api.github.com/repos/pear-studio/nonebot-dicepp"
_GITHUB_API_VERSION = "2022-11-28"
MAX_RELEASE_JSON_BYTES = 2 * 1024 * 1024
MAX_LINUX_BUNDLE_BYTES = 16 * 1024**3
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,199}$")
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
    digest = artifact["sha256"]
    if type(digest) is not str or not _SHA256_RE.fullmatch(digest):
        raise ReleaseContractError("Invalid artifact SHA-256")
    key = (artifact["platform"], artifact["arch"], artifact["purpose"])
    if key in seen:
        raise ReleaseContractError(f"Duplicate artifact target/purpose: {key}")
    seen.add(key)
    return dict(artifact)


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
                    "linux-bundle" if self.target[0] == "linux" else "velopack-full"
                )
                artifact = next(
                    (item for item in artifacts if item["purpose"] == preferred),
                    artifacts[0],
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
            if self.target[0] == "windows" and artifact["purpose"] == "velopack-full":
                companion_purposes = {"velopack-releases", "velopack-assets"}
                companions = [
                    item for item in artifacts if item["purpose"] in companion_purposes
                ]
                if {item["purpose"] for item in companions} != companion_purposes:
                    raise ReleaseDownloadError(
                        "Velopack update feed assets are incomplete"
                    )
                selected_artifacts = [artifact, *companions]
            else:
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
            downloaded: list[tuple[dict[str, Any], Path]] = []
            for selected in selected_artifacts:
                target_path = self._download_artifact(
                    release["version"],
                    selected,
                    operation=operation,
                )
                downloaded.append((selected, target_path))
            target = downloaded[0][1]
            completed_at = _iso(self.now())
            metadata = self._write_verified_metadata(
                release,
                artifact,
                target,
                companions=downloaded[1:],
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

    def fetch_rollback_package(self, version: str) -> tuple[Path, str]:
        """Download the verified Velopack full package for *version*.

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
                if item.get("purpose") == "velopack-full"
            ),
            None,
        )
        if artifact is None:
            raise ReleaseContractError(
                f"Release {wanted} has no Velopack full package artifact"
            )
        path = self._download_artifact(str(wanted), artifact)
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

    def _write_verified_metadata(
        self,
        release: dict[str, Any],
        artifact: dict[str, Any],
        target: Path,
        *,
        companions: list[tuple[dict[str, Any], Path]] | None = None,
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
        _validate_regular_path(target, version_guard, allow_missing=False)
        _atomic_write_json(
            destination,
            {
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
                "verified_path": target.name,
                "companions": [
                    {
                        "artifact": {
                            key: companion[key]
                            for key in (
                                "platform",
                                "arch",
                                "filename",
                                "purpose",
                                "size",
                                "sha256",
                            )
                        },
                        "verified_path": companion_path.name,
                    }
                    for companion, companion_path in (companions or [])
                ],
                "completed_at": completed_at,
            },
            trusted_parent=version_guard,
        )
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
                filename = artifact.get("filename") if isinstance(artifact, dict) else None
                if (
                    type(completed_at) is not str
                    or type(filename) is not str
                    or not _SAFE_FILENAME_RE.fullmatch(filename)
                ):
                    continue
                _parse_iso(completed_at)
                package = trusted / filename
                if not package.is_file() or package.is_symlink():
                    continue
                _validate_regular_path(
                    package,
                    version_guard,
                    allow_missing=False,
                )
                files = [filename, metadata_path.name]
                companions = metadata.get("companions", [])
                if not isinstance(companions, list):
                    continue
                companion_valid = True
                for companion in companions:
                    companion_name = (
                        companion.get("verified_path")
                        if isinstance(companion, dict)
                        else None
                    )
                    if (
                        type(companion_name) is not str
                        or not _SAFE_FILENAME_RE.fullmatch(companion_name)
                    ):
                        companion_valid = False
                        break
                    companion_path = trusted / companion_name
                    if not companion_path.is_file() or companion_path.is_symlink():
                        companion_valid = False
                        break
                    _validate_regular_path(
                        companion_path,
                        version_guard,
                        allow_missing=False,
                    )
                    files.append(companion_name)
                if not companion_valid:
                    continue
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
        base = self.layout.root.resolve()
        if create:
            base.mkdir(parents=True, exist_ok=True)
        manager = base / "manager"
        packages = manager / "packages"
        for path in (manager, packages):
            if path.is_symlink():
                raise ReleaseDownloadError(
                    f"Untrusted Manager package directory: {path}"
                )
            if not path.exists():
                if not create:
                    raise FileNotFoundError(path)
                path.mkdir()
                _fsync_directory(path.parent)
            if path.is_symlink() or not path.is_dir():
                raise ReleaseDownloadError(
                    f"Untrusted Manager package directory: {path}"
                )
            if path.resolve() != path.absolute():
                raise ReleaseDownloadError(
                    f"Manager package directory is redirected: {path}"
                )
            if not path.resolve().is_relative_to(base):
                raise ReleaseDownloadError(
                    "Manager package directory escapes the instance root"
                )
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
        if path.is_symlink():
            raise ReleaseDownloadError("Untrusted release version directory")
        if not path.exists():
            if not create:
                raise FileNotFoundError(path)
            path.mkdir()
            _fsync_directory(trusted_root)
        if (
            path.is_symlink()
            or not path.is_dir()
            or path.resolve() != path.absolute()
            or not path.resolve().is_relative_to(trusted_root.resolve())
        ):
            raise ReleaseDownloadError("Untrusted release version directory")
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
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_regular_children(*paths: Path) -> None:
    for path in paths:
        if path.is_symlink() or (
            path.exists() and not path.is_file()
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
    """Capture a lightweight cross-platform directory identity.

    This rejects deterministic ancestor replacement and link attacks. A
    privileged/same-user actor racing between the final identity check and one
    filesystem syscall is outside this small-project threat model; eliminating
    that nanosecond window would require platform-specific directory handles.
    """
    if parent is not None:
        _assert_trusted_directory(parent)
    root_resolved = root.resolve(strict=True)
    if path.is_symlink():
        raise ReleaseDownloadError(f"Untrusted package directory: {path}")
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
    if directory.path.is_symlink():
        raise ReleaseDownloadError(
            f"Trusted package directory was replaced: {directory.path}"
        )
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
    if path.is_symlink():
        raise ReleaseDownloadError(
            f"Release package path is a symbolic link: {path.name}"
        )
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
