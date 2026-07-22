"""Canonical definitions of DicePP-managed persistent data."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import string
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Iterable, Protocol

from .layout import InstanceLayout

ARCHIVE_PROFILE_REGULAR = "regular"
ARCHIVE_PROFILE_FULL = "full"


class DataAssetKind(str, Enum):
    FILE = "file"
    FILE_SET = "file_set"
    DIRECTORY = "directory"
    SQLITE = "sqlite"


@dataclass(frozen=True, slots=True)
class SchemaReference:
    name: str
    latest_version: int

    def __post_init__(self) -> None:
        if not self.name or self.latest_version < 1:
            raise ValueError("Schema reference requires a name and positive version")

    def to_dict(self) -> dict[str, str | int]:
        return {"name": self.name, "latest_version": self.latest_version}

    def validate_target(self, target: "SchemaTargetLike") -> None:
        if target.name != self.name or target.latest_version != self.latest_version:
            raise ValueError(
                f"SchemaTarget {target.name}@{target.latest_version} does not match "
                f"catalog reference {self.name}@{self.latest_version}"
            )


class SchemaTargetLike(Protocol):
    name: str
    latest_version: int


@dataclass(frozen=True, slots=True)
class DataAssetMatch:
    asset_id: str
    path: Path
    logical_path: str
    parameters: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class DataAssetRestoreTarget:
    asset_id: str
    path: Path
    scope_root: Path
    logical_path: str
    parameters: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class DataAsset:
    id: str
    area: str
    pattern: str
    restore_scope: str
    kind: DataAssetKind
    profiles: tuple[str, ...]
    schema: SchemaReference | None = None
    excludes: tuple[str, ...] = ()
    restore: str = "exact"

    def __post_init__(self) -> None:
        if self.area not in {"config", "data", "content"}:
            raise ValueError(f"Unsupported asset area: {self.area!r}")
        if not self.id or not self.profiles:
            raise ValueError("DataAsset requires an id and at least one profile")
        pattern = PurePosixPath(self.pattern)
        if pattern.is_absolute() or ".." in pattern.parts or "\\" in self.pattern:
            raise ValueError(f"Unsafe asset pattern: {self.pattern!r}")
        restore_scope = PurePosixPath(self.restore_scope)
        if (
            restore_scope.is_absolute()
            or ".." in restore_scope.parts
            or "\\" in self.restore_scope
            or "*" in self.restore_scope
            or "{" in self.restore_scope
        ):
            raise ValueError(f"Unsafe asset restore scope: {self.restore_scope!r}")
        if self.kind is DataAssetKind.SQLITE and self.schema is None:
            raise ValueError("SQLite assets must reference a SchemaTarget identity")
        if len(self.parameter_names) != len(set(self.parameter_names)) or any(
            not name.isidentifier() for name in self.parameter_names
        ):
            raise ValueError(f"Invalid or duplicate parameters in asset {self.id!r}")

    @property
    def parameter_names(self) -> tuple[str, ...]:
        return tuple(
            field_name
            for _literal, field_name, _format_spec, _conversion
            in string.Formatter().parse(self.pattern)
            if field_name is not None
        )

    @property
    def logical_pattern(self) -> str:
        if self.pattern in {"", "."}:
            return self.area
        return f"{self.area}/{self.pattern}"

    @property
    def logical_glob(self) -> str:
        if self.pattern in {"", "."}:
            return self.area
        return f"{self.area}/{_template_to_glob(self.pattern)}"

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "area": self.area,
            "pattern": self.pattern,
            "restore_scope": self.restore_scope,
            "kind": self.kind.value,
            "profiles": list(self.profiles),
            "schema": self.schema.to_dict() if self.schema else None,
            "excludes": list(self.excludes),
            "restore": self.restore,
        }

    def resolve(self, layout: InstanceLayout, **values: str) -> Path:
        expected = set(self.parameter_names)
        if expected != set(values):
            raise ValueError(
                f"Asset {self.id!r} expects parameters {sorted(expected)}, "
                f"got {sorted(values)}"
            )
        _validate_parameter_values(values)
        relative = self.pattern.format(**values)
        return layout.area_root(self.area) / Path(relative)

    def iter_matches(
        self,
        layout: InstanceLayout,
        **bound_values: str,
    ) -> Iterable[DataAssetMatch]:
        unexpected = set(bound_values) - set(self.parameter_names)
        if unexpected:
            raise ValueError(
                f"Asset {self.id!r} does not define parameters {sorted(unexpected)}"
            )
        _validate_parameter_values(bound_values)
        root = layout.area_root(self.area)
        glob_pattern = _template_to_glob(self.pattern, bound_values)
        candidates = [root] if glob_pattern in {"", "."} else root.glob(glob_pattern)
        for candidate in sorted(candidates, key=lambda path: path.as_posix()):
            if self.kind is DataAssetKind.DIRECTORY:
                yield from self._iter_directory(root, candidate)
            elif (
                _is_safe_regular_file(candidate, root)
                and not self._is_excluded(candidate, root)
            ):
                match = self._match(root, candidate)
                if match is not None:
                    yield match

    def matches_logical_path(self, logical_path: str) -> bool:
        return self.parameters_from_logical_path(logical_path) is not None

    def parameters_from_logical_path(
        self,
        logical_path: str,
    ) -> dict[str, str] | None:
        path = PurePosixPath(logical_path)
        if (
            path.is_absolute()
            or ".." in path.parts
            or "\\" in logical_path
            or path.as_posix() != logical_path
        ):
            return None
        prefix = f"{self.area}/"
        if not logical_path.startswith(prefix):
            return None
        relative = logical_path[len(prefix):]
        if not relative:
            return None
        logical_glob = _template_to_glob(self.pattern)
        if self.kind is DataAssetKind.DIRECTORY:
            if logical_glob in {"", "."}:
                matched = True
            else:
                directory = logical_glob.rstrip("/")
                matched = relative.startswith(f"{directory}/")
            parameters: dict[str, str] = {}
        else:
            parameters = _match_template(self.pattern, relative)
            matched = parameters is not None
        if not matched or any(
            fnmatch.fnmatch(PurePosixPath(relative).name, excluded)
            or fnmatch.fnmatch(relative, excluded)
            for excluded in self.excludes
        ):
            return None
        return parameters

    def restore_target(
        self,
        layout: InstanceLayout,
        logical_path: str,
    ) -> DataAssetRestoreTarget | None:
        parameters = self.parameters_from_logical_path(logical_path)
        if parameters is None:
            return None
        relative = PurePosixPath(logical_path.removeprefix(f"{self.area}/"))
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            return None
        area_root = layout.area_root(self.area)
        scope_root = (
            area_root
            if self.restore_scope in {"", "."}
            else area_root.joinpath(*PurePosixPath(self.restore_scope).parts)
        )
        target = area_root.joinpath(*relative.parts)
        try:
            target.relative_to(scope_root)
        except ValueError:
            return None
        if target == scope_root:
            return None
        return DataAssetRestoreTarget(
            asset_id=self.id,
            path=target,
            scope_root=scope_root,
            logical_path=logical_path,
            parameters=tuple(sorted(parameters.items())),
        )

    def resolve_sibling(self, current_path: str | os.PathLike[str], **values: str) -> Path:
        """Resolve another dynamic file beside a path owned by this asset.

        This is used when a store switches the dynamic identity (for example a
        Persona character) without rediscovering or reassembling its filename.
        """
        current = Path(current_path)
        filename_pattern = PurePosixPath(self.pattern).name
        current_values = _match_template(filename_pattern, current.name)
        if current_values is None:
            raise ValueError(
                f"Path {current.name!r} does not match asset {self.id!r} filename"
            )
        filename_parameters = set(current_values)
        unexpected = set(values) - filename_parameters
        if unexpected:
            raise ValueError(
                f"Asset filename does not define parameters {sorted(unexpected)}"
            )
        _validate_parameter_values(values)
        current_values.update(values)
        return current.with_name(filename_pattern.format(**current_values))

    def _iter_directory(self, area_root: Path, directory: Path) -> Iterable[DataAssetMatch]:
        if (
            not directory.exists()
            or not directory.is_dir()
            or _has_symlink_component(directory, area_root)
        ):
            return
        for current, dirnames, filenames in os.walk(directory, followlinks=False):
            current_path = Path(current)
            dirnames[:] = sorted(
                name for name in dirnames if not (current_path / name).is_symlink()
            )
            for filename in sorted(filenames):
                candidate = current_path / filename
                if (
                    _is_safe_regular_file(candidate, area_root)
                    and not self._is_excluded(candidate, area_root)
                ):
                    match = self._match(area_root, candidate)
                    if match is not None:
                        yield match

    def _is_excluded(self, path: Path, area_root: Path) -> bool:
        relative = path.relative_to(area_root).as_posix()
        return any(
            fnmatch.fnmatch(path.name, excluded) or fnmatch.fnmatch(relative, excluded)
            for excluded in self.excludes
        )

    def _match(self, area_root: Path, path: Path) -> DataAssetMatch | None:
        relative = path.relative_to(area_root).as_posix()
        logical_path = f"{self.area}/{relative}"
        parameters = self.parameters_from_logical_path(logical_path)
        if parameters is None:
            return None
        return DataAssetMatch(
            asset_id=self.id,
            path=path,
            logical_path=logical_path,
            parameters=tuple(sorted(parameters.items())),
        )


@dataclass(frozen=True, slots=True)
class DataAssetCatalog:
    assets: tuple[DataAsset, ...]

    def __post_init__(self) -> None:
        ids = [asset.id for asset in self.assets]
        if len(ids) != len(set(ids)):
            raise ValueError("DataAsset ids must be unique")

    def to_dict(self) -> dict[str, object]:
        return {
            "format_version": 1,
            "assets": [asset.to_dict() for asset in sorted(self.assets, key=lambda item: item.id)],
        }

    @property
    def digest(self) -> str:
        payload = json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def for_profile(self, profile: str) -> tuple[DataAsset, ...]:
        return tuple(asset for asset in self.assets if profile in asset.profiles)

    def collect(self, layout: InstanceLayout, profile: str) -> list[DataAssetMatch]:
        matches = [
            match
            for asset in self.for_profile(profile)
            for match in asset.iter_matches(layout)
        ]
        return sorted(matches, key=lambda item: item.logical_path)

    def find_for_logical_path(
        self,
        logical_path: str,
        *,
        profile: str | None = None,
    ) -> DataAsset | None:
        assets = self.for_profile(profile) if profile is not None else self.assets
        return next(
            (asset for asset in assets if asset.matches_logical_path(logical_path)),
            None,
        )


def _template_to_glob(
    pattern: str,
    bound_values: dict[str, str] | None = None,
) -> str:
    values = bound_values or {}
    parts: list[str] = []
    for literal, field_name, _format_spec, _conversion in string.Formatter().parse(pattern):
        parts.append(literal)
        if field_name is not None:
            parts.append(values.get(field_name, "*"))
    return "".join(parts)


def _match_template(pattern: str, value: str) -> dict[str, str] | None:
    regex_parts: list[str] = []
    seen: set[str] = set()
    for literal, field_name, _format_spec, _conversion in string.Formatter().parse(pattern):
        regex_parts.append(_glob_literal_regex(literal))
        if field_name is not None:
            if not field_name.isidentifier() or field_name in seen:
                raise ValueError(f"Invalid or duplicate asset parameter: {field_name!r}")
            seen.add(field_name)
            regex_parts.append(f"(?P<{field_name}>[^/]+)")
    matched = re.fullmatch("".join(regex_parts), value)
    if matched is None:
        return None
    parameters = matched.groupdict()
    try:
        _validate_parameter_values(parameters)
    except ValueError:
        return None
    return parameters


def _glob_literal_regex(literal: str) -> str:
    parts: list[str] = []
    for character in literal:
        if character == "*":
            parts.append("[^/]*")
        elif character == "?":
            parts.append("[^/]")
        else:
            parts.append(re.escape(character))
    return "".join(parts)


def _validate_parameter_values(values: dict[str, str]) -> None:
    for key, value in values.items():
        if (
            not value
            or value in {".", ".."}
            or "/" in value
            or "\\" in value
            or any(character in value for character in "*?[]")
        ):
            raise ValueError(f"Asset parameter {key!r} must be one path segment")


def _is_safe_regular_file(path: Path, root: Path) -> bool:
    return (
        path.exists()
        and path.is_file()
        and not _has_symlink_component(path, root)
    )


def _has_symlink_component(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    current = root
    if current.is_symlink():
        return True
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


INSTANCE_SCHEMA = SchemaReference("instance", 1)
BOT_CORE_SCHEMA = SchemaReference("bot_core", 1)
BOT_LOG_SCHEMA = SchemaReference("bot_log", 1)
PERSONA_SCHEMA = SchemaReference("persona", 3)

USER_CONFIG_ASSET = DataAsset(
    id="config.user",
    area="config",
    pattern="user.json",
    restore_scope=".",
    kind=DataAssetKind.FILE,
    profiles=(ARCHIVE_PROFILE_REGULAR, ARCHIVE_PROFILE_FULL),
)
BOT_CONFIGS_ASSET = DataAsset(
    id="config.bots",
    area="config",
    pattern="bots/*.json",
    restore_scope="bots",
    kind=DataAssetKind.FILE_SET,
    profiles=(ARCHIVE_PROFILE_REGULAR, ARCHIVE_PROFILE_FULL),
    excludes=("_template.json",),
)
INSTANCE_DB_ASSET = DataAsset(
    id="data.instance",
    area="data",
    pattern="dicepp.db",
    restore_scope=".",
    kind=DataAssetKind.SQLITE,
    profiles=(ARCHIVE_PROFILE_REGULAR, ARCHIVE_PROFILE_FULL),
    schema=INSTANCE_SCHEMA,
)
BOT_CORE_ASSET = DataAsset(
    id="data.bot_core",
    area="data",
    pattern="bots/{bot_id}/bot_data.db",
    restore_scope="bots",
    kind=DataAssetKind.SQLITE,
    profiles=(ARCHIVE_PROFILE_REGULAR, ARCHIVE_PROFILE_FULL),
    schema=BOT_CORE_SCHEMA,
)
BOT_LOG_ASSET = DataAsset(
    id="data.bot_log",
    area="data",
    pattern="bots/{bot_id}/log.db",
    restore_scope="bots",
    kind=DataAssetKind.SQLITE,
    profiles=(ARCHIVE_PROFILE_REGULAR, ARCHIVE_PROFILE_FULL),
    schema=BOT_LOG_SCHEMA,
)
PERSONA_DB_ASSET = DataAsset(
    id="data.persona",
    area="data",
    pattern="bots/{bot_id}/personas_data_{character}.db",
    restore_scope="bots",
    kind=DataAssetKind.SQLITE,
    profiles=(ARCHIVE_PROFILE_REGULAR, ARCHIVE_PROFILE_FULL),
    schema=PERSONA_SCHEMA,
)
LOCAL_IMAGES_ASSET = DataAsset(
    id="data.local_images",
    area="data",
    pattern="local_images",
    restore_scope="local_images",
    kind=DataAssetKind.DIRECTORY,
    profiles=(ARCHIVE_PROFILE_REGULAR, ARCHIVE_PROFILE_FULL),
)
CONTENT_ASSET = DataAsset(
    id="content.user",
    area="content",
    pattern=".",
    restore_scope=".",
    kind=DataAssetKind.DIRECTORY,
    profiles=(ARCHIVE_PROFILE_FULL,),
)

DATA_CATALOG = DataAssetCatalog(
    (
        USER_CONFIG_ASSET,
        BOT_CONFIGS_ASSET,
        INSTANCE_DB_ASSET,
        BOT_CORE_ASSET,
        BOT_LOG_ASSET,
        PERSONA_DB_ASSET,
        LOCAL_IMAGES_ASSET,
        CONTENT_ASSET,
    )
)
