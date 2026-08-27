"""Load and persist the two independent DicePP configuration schemas.

Configuration files are sparse overlays of their *own* schema.  A missing
file means "use the model defaults" and is intentionally not materialised on
disk.  ``user.json`` is never merged into a ``BotConfig``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, TypeVar, get_args, get_origin

from pydantic import BaseModel, TypeAdapter, ValidationError
from pydantic_core import SchemaSerializer, SchemaValidator

from plugins.DicePP.utils.logger import logger
from plugins.DicePP.core.config.basic import Paths
from plugins.DicePP.core.config.pydantic_models import BotConfig, UserConfig


_BOTS_DIR = "bots"
_GLOBAL_USER = "user.json"
_ModelT = TypeVar("_ModelT", bound=BaseModel)


class ConfigValidationError(Exception):
    """Raised when a stored or submitted configuration is invalid."""

    def __init__(
        self,
        message: str,
        *,
        validation_error: ValidationError | None = None,
    ) -> None:
        super().__init__(message)
        self.validation_error = validation_error


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Return ``base`` with nested mapping values from ``override`` applied.

    This is used only to materialise one schema's effective value.  It is not
    used to combine ``UserConfig`` and ``BotConfig``.
    """

    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _merge_model_layer(
    default: Dict[str, Any],
    override: Dict[str, Any],
    model_type: type[BaseModel],
) -> Dict[str, Any]:
    """Apply one sparse model layer using schema-aware mapping semantics.

    A mapping field is replaceable with an explicit ``{}`` (for example,
    clearing a provider map).  Nested Pydantic models keep their default when
    given ``{}``, since an empty partial model means "no field overrides".
    Non-empty nested models and mapping values recurse so unchanged siblings
    continue to inherit their defaults.
    """

    result = dict(default)
    for name, value in override.items():
        field = model_type.model_fields[name]
        annotation = field.annotation
        if _is_model_type(annotation):
            if isinstance(value, dict) and value:
                result[name] = _merge_model_layer(
                    result[name], value, annotation
                )
            continue

        dict_value_model = _dict_value_model_type(annotation)
        if dict_value_model is not None and isinstance(value, dict):
            if not value:
                result[name] = {}
                continue
            mapping = dict(result.get(name, {}))
            for key, item in value.items():
                if isinstance(item, dict):
                    base_item = mapping.get(key)
                    if not isinstance(base_item, dict):
                        # Open mappings may contain a legal model key that is
                        # absent from the built-in defaults.  Such a value has
                        # no model instance to inherit from; its required
                        # fields are checked by the final strict validation.
                        base_item = {}
                    mapping[key] = _merge_model_layer(
                        base_item, item, dict_value_model
                    )
                else:
                    mapping[key] = item
            result[name] = mapping
            continue

        result[name] = value
    return result


def _apply_env_overrides(data: Dict[str, Any]) -> Dict[str, Any]:
    """Apply the current BotConfig DICE_* overrides.

    These deployment-time business overrides remain temporarily supported
    until the second configuration-cleanup batch removes them.  They are
    applied only to BotConfig; ``user.json`` is never an environment or Bot
    overlay.  Values are parsed at this boundary so the strict JSON schema
    remains strict for files and Dashboard submissions.
    """

    env_map: Dict[str, Any] = {}

    def set_nested(keys: list[str], value: Any) -> None:
        current = env_map
        for key in keys[:-1]:
            current = current.setdefault(key, {})
        current[keys[-1]] = value

    mappings: dict[str, tuple[list[str], Any]] = {
        "DICE_PERSONA": (["persona"], str),
        "DICE_DICEHUB_API_URL": (["dicehub", "api_url"], str),
        "DICE_DICEHUB_API_KEY": (["dicehub", "api_key"], str),
        "DICE_LOG_LEVEL": (["log", "level"], str),
        "DICE_LOG_WEB_PROVIDER": (["log", "web", "provider"], str),
        "DICE_LOG_WEB_ENDPOINT": (["log", "web", "endpoint"], str),
        "DICE_LOG_WEB_TOKEN": (["log", "web", "token"], str),
        "DICE_LOG_WEB_TIMEOUT_SECONDS": (["log", "web", "timeout_seconds"], float),
    }
    for env_key, (json_path, converter) in mappings.items():
        value = os.environ.get(env_key)
        if value is None:
            continue
        try:
            parsed = converter(value)
        except (TypeError, ValueError) as exc:
            raise ConfigValidationError(
                f"[Config] Invalid environment override {env_key}: {value!r}"
            ) from exc
        set_nested(json_path, parsed)
    return _deep_merge(data, env_map)


def _json_object(path: Path) -> Dict[str, Any]:
    """Read one JSON object, raising a clear error for malformed input."""

    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigValidationError(
            f"[Config] Cannot read JSON configuration {path}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise ConfigValidationError(
            f"[Config] Configuration root must be an object in {path}"
        )
    return value


def _load_json_file(path: Path) -> Dict[str, Any]:
    """Load one JSON object (kept as a small internal API for callers)."""

    return _json_object(path)


def _write_json_file_atomic(path: Path, data: Dict[str, Any]) -> None:
    """Atomically write canonical JSON without creating backup files."""

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def _is_model_type(annotation: Any) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)


def _dict_value_model_type(annotation: Any) -> Optional[type[BaseModel]]:
    origin = get_origin(annotation)
    if origin not in (dict, Dict):
        return None
    args = get_args(annotation)
    if len(args) != 2 or not _is_model_type(args[1]):
        return None
    return args[1]


def _list_item_model_type(annotation: Any) -> Optional[type[BaseModel]]:
    origin = get_origin(annotation)
    if origin not in (list,):
        return None
    args = get_args(annotation)
    if len(args) != 1 or not _is_model_type(args[0]):
        return None
    return args[0]


def _config_validation_error(path: Path, message: str) -> ConfigValidationError:
    return ConfigValidationError(f"[Config] Configuration rejected {path}: {message}")


def _dump_json_value(annotation: Any, value: Any) -> Any:
    """Validate one sparse scalar using strict JSON/Pydantic semantics."""

    adapter = TypeAdapter(annotation)
    validated = adapter.validate_python(value, strict=True)
    return adapter.dump_python(validated, mode="json")


def _locate_model_field_core_schema(
    model_type: type[BaseModel], field_name: str
) -> Any | None:
    """Unwrap Pydantic model core-schema wrappers and return one field schema."""

    definitions: dict[str, Any] = {}
    schema: Any = model_type.__pydantic_core_schema__
    visited: set[int] = set()
    while isinstance(schema, dict) and id(schema) not in visited:
        visited.add(id(schema))
        schema_type = schema.get("type")
        if schema_type == "definitions":
            for definition in schema.get("definitions", ()):
                if isinstance(definition, dict) and isinstance(definition.get("ref"), str):
                    definitions[definition["ref"]] = definition
            schema = schema.get("schema")
            continue
        if schema_type == "definition-ref":
            schema = definitions.get(schema.get("schema_ref"))
            continue
        if schema_type == "model-fields":
            field_entry = schema.get("fields", {}).get(field_name)
            return field_entry.get("schema") if isinstance(field_entry, dict) else None
        nested = schema.get("schema")
        if not isinstance(nested, dict):
            return None
        schema = nested
    return None


def _dump_model_field_json_value(
    model_type: type[BaseModel], field_name: str, annotation: Any, value: Any
) -> Any:
    field_schema = _locate_model_field_core_schema(model_type, field_name)
    if field_schema is None:
        return _dump_json_value(annotation, value)
    validated = SchemaValidator(field_schema).validate_python(value, strict=True)
    return SchemaSerializer(field_schema).to_python(validated, mode="json")


def _canonicalize_model_dict(
    model_type: type[BaseModel],
    raw: Dict[str, Any],
    *,
    path: Path,
    field_path: tuple[str, ...] = (),
) -> Dict[str, Any]:
    """Validate a partial model while preserving sparse nested structure."""

    canonical: Dict[str, Any] = {}
    for name, value in raw.items():
        current_path = field_path + (name,)
        field = model_type.model_fields.get(name)
        if field is None:
            raise _config_validation_error(
                path,
                f"unknown field '{'.'.join(current_path)}' is not part of the schema",
            )
        try:
            canonical[name] = _canonicalize_field_value(
                model_type,
                field.annotation,
                value,
                path=path,
                field_path=current_path,
            )
        except ConfigValidationError:
            raise
        except (ValidationError, TypeError, ValueError) as exc:
            raise _config_validation_error(
                path, f"field '{'.'.join(current_path)}' is invalid: {exc}"
            ) from exc
    return canonical


def canonicalize_config_layer(
    raw: Dict[str, Any],
    *,
    model_type: type[_ModelT],
    path: Path | None = None,
) -> Dict[str, Any]:
    """Strictly validate one sparse layer for its owning schema."""

    if not isinstance(raw, dict):
        raise _config_validation_error(
            path if path is not None else Path("<in-memory configuration>"),
            "configuration root must be an object",
        )
    return _canonicalize_model_dict(
        model_type,
        raw,
        path=path if path is not None else Path("<in-memory configuration>"),
    )


def _canonicalize_field_value(
    owner_model_type: type[BaseModel],
    annotation: Any,
    value: Any,
    *,
    path: Path,
    field_path: tuple[str, ...],
) -> Any:
    if _is_model_type(annotation):
        if not isinstance(value, dict):
            return _dump_json_value(annotation, value)
        return _canonicalize_model_dict(annotation, value, path=path, field_path=field_path)

    dict_value_model = _dict_value_model_type(annotation)
    if dict_value_model is not None:
        if not isinstance(value, dict):
            return _dump_json_value(annotation, value)
        canonical: Dict[str, Any] = {}
        for key, item in value.items():
            item_path = field_path + (str(key),)
            if not isinstance(key, str):
                raise _config_validation_error(
                    path, f"field '{'.'.join(item_path)}' has a non-string key"
                )
            if isinstance(item, dict):
                canonical[key] = _canonicalize_model_dict(
                    dict_value_model, item, path=path, field_path=item_path
                )
            else:
                canonical[key] = _dump_json_value(dict_value_model, item)
        return canonical

    list_item_model = _list_item_model_type(annotation)
    if list_item_model is not None:
        if not isinstance(value, list):
            return _dump_json_value(annotation, value)
        canonical_items = []
        for index, item in enumerate(value):
            if isinstance(item, dict):
                item = _canonicalize_model_dict(
                    list_item_model,
                    item,
                    path=path,
                    field_path=field_path + (str(index),),
                )
            canonical_items.append(item)
        return _dump_model_field_json_value(
            owner_model_type, field_path[-1], annotation, canonical_items
        )

    return _dump_model_field_json_value(owner_model_type, field_path[-1], annotation, value)


def _model_default_dict(model_type: type[BaseModel]) -> Dict[str, Any]:
    return model_type().model_dump(mode="json")


_MISSING = object()


def _sparse_value(value: Any, default: Any) -> Any:
    """Return a recursive sparse value, or ``_MISSING`` if unchanged."""

    if isinstance(value, dict) and isinstance(default, dict):
        result: dict[str, Any] = {}
        for key, child in value.items():
            child_sparse = _sparse_value(child, default.get(key, _MISSING))
            if child_sparse is not _MISSING:
                result[key] = child_sparse
        if result or value != default:
            return result
        return _MISSING
    if default is not _MISSING and value == default:
        return _MISSING
    return value


def sparsify_config(
    config: BaseModel | Dict[str, Any],
    *,
    model_type: type[_ModelT] | None = None,
) -> Dict[str, Any]:
    """Validate and recursively remove values equal to schema defaults."""

    if isinstance(config, BaseModel):
        actual_type: type[BaseModel] = type(config)
        full = config.model_dump(mode="json")
    else:
        if model_type is None:
            raise TypeError("model_type is required when sparsifying a mapping")
        actual_type = model_type
        canonical = canonicalize_config_layer(config, model_type=model_type)
        full = _merge_model_layer(
            _model_default_dict(model_type), canonical, model_type
        )
    try:
        validated = actual_type.model_validate(full, strict=True)
    except ValidationError as exc:
        raise ConfigValidationError(
            f"[Config] Configuration validation failed for {actual_type.__name__}: {exc}",
            validation_error=exc,
        ) from exc
    sparse = _sparse_value(
        validated.model_dump(mode="json"), _model_default_dict(actual_type)
    )
    return {} if sparse is _MISSING else sparse


def save_config_file(
    path: Path,
    config: BaseModel | Dict[str, Any],
    *,
    model_type: type[_ModelT] | None = None,
) -> Dict[str, Any]:
    """Persist one schema as a sparse JSON object and return that object."""

    sparse = sparsify_config(config, model_type=model_type)
    _write_json_file_atomic(path, sparse)
    return sparse


def load_config_file(path: Path, model_type: type[_ModelT]) -> _ModelT:
    """Load one config file or return a fresh model-default instance."""

    raw = _json_object(path)
    return validate_config_candidate(raw, model_type=model_type, path=path)


def validate_config_candidate(
    raw: Dict[str, Any],
    *,
    model_type: type[_ModelT],
    path: Path | None = None,
) -> _ModelT:
    """Validate a sparse candidate against exactly one owning schema."""

    canonical = canonicalize_config_layer(raw, model_type=model_type, path=path)
    effective = _merge_model_layer(
        _model_default_dict(model_type), canonical, model_type
    )
    try:
        return model_type.model_validate(effective, strict=True)
    except ValidationError as exc:
        raise ConfigValidationError(
            f"[Config] Configuration validation failed for {path}: {exc}",
            validation_error=exc,
        ) from exc


@dataclass(frozen=True)
class ResolvedConfigLayers:
    """Independent user and Bot layers plus the effective Bot config."""

    config: BotConfig
    user: Dict[str, Any]
    account: Dict[str, Any]
    user_config: UserConfig


def resolve_config_layers(
    user_raw: Dict[str, Any],
    account_raw: Dict[str, Any],
    *,
    account: str = "",
    user_path: Path | None = None,
    account_path: Path | None = None,
    apply_environment: bool = False,
) -> ResolvedConfigLayers:
    """Validate independent schemas and materialise the Bot config."""

    canonical_user = canonicalize_config_layer(
        user_raw, model_type=UserConfig, path=user_path
    )
    canonical_account = canonicalize_config_layer(
        account_raw, model_type=BotConfig, path=account_path
    )
    user_effective = _merge_model_layer(
        _model_default_dict(UserConfig), canonical_user, UserConfig
    )
    bot_effective = _merge_model_layer(
        _model_default_dict(BotConfig), canonical_account, BotConfig
    )
    if apply_environment:
        bot_effective = _apply_env_overrides(bot_effective)
    try:
        user_config = UserConfig.model_validate(user_effective, strict=True)
        config = BotConfig.model_validate(bot_effective, strict=True)
    except ValidationError as exc:
        raise ConfigValidationError(
            f"[Config] Configuration validation failed for account '{account}':\n{exc}",
            validation_error=exc,
        ) from exc
    return ResolvedConfigLayers(
        config=config,
        user=canonical_user,
        account=canonical_account,
        user_config=user_config,
    )


class ConfigLoader:
    """Load one Bot config and the independent instance UserConfig."""

    def __init__(self, data_path: Optional[str] = None, account: str = ""):
        self._data_path = Path(data_path) if data_path is not None else Paths.CONFIG_DIR
        self._account = account
        self._config: Optional[BotConfig] = None
        self._user_config: Optional[UserConfig] = None

    @property
    def config(self) -> BotConfig:
        if self._config is None:
            self._config = self.load()
        return self._config

    @property
    def user_config(self) -> UserConfig:
        if self._user_config is None:
            self.load_user_config()
        assert self._user_config is not None
        return self._user_config

    def load_user_config(self) -> UserConfig:
        self._user_config = load_config_file(self._data_path / _GLOBAL_USER, UserConfig)
        return self._user_config

    def load(self) -> BotConfig:
        """Load Bot and User files without creating or rewriting either file."""

        user_path = self._data_path / _GLOBAL_USER
        account_path = self._account_config_path
        user_raw = _json_object(user_path)
        account_raw = _json_object(account_path)
        resolved = resolve_config_layers(
            user_raw,
            account_raw,
            account=self._account,
            user_path=user_path,
            account_path=account_path,
            apply_environment=True,
        )
        self._user_config = resolved.user_config
        self._config = resolved.config
        if not self._config.master:
            logger.warning(
                f"[Config] [Warn] No master configured for account '{self._account}'. "
                f"Edit {account_path} to set it."
            )
        return self._config

    @property
    def _account_config_path(self) -> Path:
        return self._data_path / _BOTS_DIR / f"{self._account}.json"


__all__ = [
    "BotConfig",
    "UserConfig",
    "ConfigLoader",
    "ConfigValidationError",
    "ResolvedConfigLayers",
    "canonicalize_config_layer",
    "load_config_file",
    "save_config_file",
    "sparsify_config",
    "validate_config_candidate",
    "resolve_config_layers",
    "_deep_merge",
]
