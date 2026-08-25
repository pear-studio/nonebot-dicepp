"""
ConfigLoader: hierarchical JSON configuration loader for DicePP.

Priority (high → low):
  1. Environment variables  (DICE_* prefix)
  2. Account config         config/bots/{account}.json
  3. Global user overrides  config/user.json
  4. Pydantic defaults and built-in provider catalog
"""
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, get_args, get_origin

from pydantic import BaseModel, TypeAdapter, ValidationError
from pydantic_core import SchemaSerializer, SchemaValidator

from plugins.DicePP.utils.logger import logger
from plugins.DicePP.core.config.pydantic_models import BotConfig
from plugins.DicePP.core.config.basic import Paths

_BOTS_DIR = "bots"
_GLOBAL_USER = "user.json"
_ACCOUNT_TEMPLATE = "_template.json"
_COMMENT_METADATA_PREFIX = "_comment"

def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge override into base (override wins)."""
    result = base.copy()
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _load_json_file(path: Path) -> Dict[str, Any]:
    """Load a JSON file, returning empty dict on missing or parse error."""
    data, _ = _load_json_file_for_rewrite(path)
    return data


def _load_json_file_for_rewrite(path: Path) -> tuple[Dict[str, Any], bool]:
    """Load a JSON object and report whether it is safe to rewrite."""
    if not path.exists():
        return {}, False
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
        if not isinstance(data, dict):
            logger.error(f"[Config] [Load] JSON root must be an object in {path}.")
            return {}, False
        return data, True
    except json.JSONDecodeError as exc:
        logger.error(f"[Config] [Load] JSON parse error in {path}: {exc}")
        return {}, False
    except OSError as exc:
        logger.error(f"[Config] [Load] Cannot read {path}: {exc}")
        return {}, False


def _write_json_file_atomic(path: Path, data: Dict[str, Any]) -> None:
    """Atomically write canonical JSON without creating backup files."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _is_model_type(annotation: Any) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)


def _dict_value_model_type(annotation: Any) -> Optional[type[BaseModel]]:
    origin = get_origin(annotation)
    if origin not in (dict, Dict):
        return None
    args = get_args(annotation)
    if len(args) != 2:
        return None
    value_type = args[1]
    return value_type if _is_model_type(value_type) else None


def _list_item_model_type(annotation: Any) -> Optional[type[BaseModel]]:
    origin = get_origin(annotation)
    if origin not in (list,):
        return None
    args = get_args(annotation)
    if len(args) != 1:
        return None
    item_type = args[0]
    return item_type if _is_model_type(item_type) else None


def _config_validation_error(path: Path, message: str) -> "ConfigValidationError":
    return ConfigValidationError(f"[Config] Canonical rewrite rejected {path}: {message}")


def _dump_json_value(annotation: Any, value: Any) -> Any:
    adapter = TypeAdapter(annotation)
    validated = adapter.validate_python(value)
    return adapter.dump_python(validated, mode="json")


def _dump_model_field_json_value(
    model_type: type[BaseModel],
    field_name: str,
    annotation: Any,
    value: Any,
) -> Any:
    """Validate a scalar through its owning field, including field validators."""
    field_schema = _locate_model_field_core_schema(model_type, field_name)
    if field_schema is None:
        return _dump_json_value(annotation, value)
    validated = SchemaValidator(field_schema).validate_python(value)
    return SchemaSerializer(field_schema).to_python(validated, mode="json")


def _locate_model_field_core_schema(
    model_type: type[BaseModel],
    field_name: str,
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
                if isinstance(definition, dict) and isinstance(
                    definition.get("ref"), str
                ):
                    definitions[definition["ref"]] = definition
            schema = schema.get("schema")
            continue
        if schema_type == "definition-ref":
            reference = schema.get("schema_ref")
            schema = definitions.get(reference)
            continue
        if schema_type == "model-fields":
            field_entry = schema.get("fields", {}).get(field_name)
            return field_entry.get("schema") if isinstance(field_entry, dict) else None

        # model, function-before/after/wrap, default, nullable and the other
        # single-schema wrappers all expose their inner contract as `schema`.
        nested = schema.get("schema")
        if not isinstance(nested, dict):
            return None
        schema = nested
    return None


def _canonicalize_model_dict(
    model_type: type[BaseModel],
    raw: Dict[str, Any],
    *,
    path: Path,
    field_path: tuple[str, ...] = (),
) -> Dict[str, Any]:
    canonical: Dict[str, Any] = {}
    for name, value in raw.items():
        if name.startswith(_COMMENT_METADATA_PREFIX):
            canonical[name] = value
            continue

        field = model_type.model_fields.get(name)
        current_path = field_path + (name,)
        if field is None:
            raise _config_validation_error(
                path,
                f"unknown field '{'.'.join(current_path)}' is not part of the current schema",
            )

        try:
            canonical[name] = _canonicalize_field_value(
                model_type,
                field.annotation,
                value,
                path=path,
                field_path=current_path,
            )
        except ValidationError as exc:
            raise _config_validation_error(
                path,
                f"field '{'.'.join(current_path)}' is invalid: {exc}",
            ) from exc

    return canonical


def canonicalize_config_layer(
    raw: Dict[str, Any],
    *,
    path: Path | None = None,
) -> Dict[str, Any]:
    """Validate and canonicalize one sparse runtime config layer."""
    return _canonicalize_model_dict(
        BotConfig,
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
        return _canonicalize_model_dict(
            annotation,
            value,
            path=path,
            field_path=field_path,
        )

    dict_value_model = _dict_value_model_type(annotation)
    if dict_value_model is not None:
        if not isinstance(value, dict):
            return _dump_json_value(annotation, value)
        canonical: Dict[str, Any] = {}
        for key, item in value.items():
            item_path = field_path + (str(key),)
            if not isinstance(item, dict):
                canonical[str(key)] = _dump_json_value(dict_value_model, item)
            else:
                canonical[str(key)] = _canonicalize_model_dict(
                    dict_value_model,
                    item,
                    path=path,
                    field_path=item_path,
                )
        return canonical

    list_item_model = _list_item_model_type(annotation)
    if list_item_model is not None and isinstance(value, list):
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
        return _dump_json_value(annotation, canonical_items)

    return _dump_model_field_json_value(
        owner_model_type,
        field_path[-1],
        annotation,
        value,
    )


def _apply_env_overrides(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Apply DICE_* environment variables as overrides.
    Mapping examples: DICE_MASTER → master, DICE_NICKNAME → nickname
    Only a curated set of env vars is supported.
    """
    env_map: Dict[str, Any] = {}

    def _set_nested(d: Dict, keys: list, value: str) -> None:
        for key in keys[:-1]:
            d = d.setdefault(key, {})
        d[keys[-1]] = value

    # List-type env vars are comma-separated: "id1,id2" → ["id1", "id2"]
    for list_key, json_path in [("DICE_MASTER", ["master"]), ("DICE_ADMIN", ["admin"])]:
        val = os.environ.get(list_key)
        if val is not None:
            items = [v.strip() for v in val.split(",") if v.strip()]
            _set_nested(env_map, json_path, items)

    env_mappings = {
        "DICE_PERSONA": ["persona"],
        "DICE_NICKNAME": ["nickname"],
        "DICE_COMMAND_SPLIT": ["command_split"],
        "DICE_DICEHUB_API_URL": ["dicehub", "api_url"],
        "DICE_DICEHUB_API_KEY": ["dicehub", "api_key"],
        "DICE_LOG_LEVEL": ["log", "level"],
        "DICE_LOG_WEB_PROVIDER": ["log", "web", "provider"],
        "DICE_LOG_WEB_ENDPOINT": ["log", "web", "endpoint"],
        "DICE_LOG_WEB_TOKEN": ["log", "web", "token"],
        "DICE_LOG_WEB_TIMEOUT_SECONDS": ["log", "web", "timeout_seconds"],
    }

    for env_key, path in env_mappings.items():
        val = os.environ.get(env_key)
        if val is not None:
            _set_nested(env_map, path, val)

    return _deep_merge(data, env_map)


@dataclass(frozen=True)
class ResolvedConfigLayers:
    """Pure result of resolving user and account layers."""

    config: BotConfig
    user: Dict[str, Any]
    account: Dict[str, Any]


def resolve_config_layers(
    user_raw: Dict[str, Any],
    account_raw: Dict[str, Any],
    *,
    account: str = "",
    user_path: Path | None = None,
    account_path: Path | None = None,
    apply_environment: bool = False,
) -> ResolvedConfigLayers:
    """Resolve config layers without reading or writing files.

    This is the single acceptance contract shared by the Bot and Dashboard:
    canonicalize each sparse layer, merge it over a fresh built-in model, then
    validate cross-layer constraints on the final configuration.
    """
    canonical_user = canonicalize_config_layer(
        user_raw,
        path=user_path,
    )
    canonical_account = canonicalize_config_layer(
        account_raw,
        path=account_path,
    )
    merged = BotConfig().model_dump(mode="json")
    merged = _deep_merge(merged, canonical_user)
    merged = _deep_merge(merged, canonical_account)
    if apply_environment:
        merged = _apply_env_overrides(merged)
    try:
        config = BotConfig.model_validate(merged)
    except ValidationError as exc:
        raise ConfigValidationError(
            f"[Config] Configuration validation failed for account '{account}':\n{exc}",
            validation_error=exc,
        ) from exc
    return ResolvedConfigLayers(
        config=config,
        user=canonical_user,
        account=canonical_account,
    )


class ConfigLoader:
    """
    Loads BotConfig from layered JSON files and environment variables.

    Usage:
        loader = ConfigLoader(account="my_bot_id")
        config = loader.load()
    """

    def __init__(self, data_path: Optional[str] = None, account: str = ""):
        self._data_path = Path(data_path) if data_path is not None else Paths.CONFIG_DIR
        self._account = account
        self._config: Optional[BotConfig] = None

    # ── public API ──────────────────────────────────────────────────────────

    @property
    def config(self) -> BotConfig:
        if self._config is None:
            self._config = self.load()
        return self._config

    def load(self) -> BotConfig:
        """Load config from scratch, store and return it."""
        cfg = self._build_config()
        self._config = cfg
        return cfg

    # ── internals ───────────────────────────────────────────────────────────

    def _build_config(self) -> BotConfig:
        # Layer 3: instance-wide user overrides
        user_path = self._data_path / _GLOBAL_USER
        user_disk_raw, user_can_rewrite = _load_json_file_for_rewrite(user_path)

        # Layer 2: account config
        self._ensure_account_config()
        account_disk_raw, account_can_rewrite = _load_json_file_for_rewrite(self._account_config_path)
        resolved = resolve_config_layers(
            user_disk_raw,
            account_disk_raw,
            account=self._account,
            user_path=user_path,
            account_path=self._account_config_path,
            apply_environment=True,
        )
        cfg = resolved.config

        # Persistence deliberately remains a Runtime concern.  Malformed or
        # unreadable files are represented by can_rewrite=False and left alone.
        if user_can_rewrite and user_disk_raw != resolved.user:
            _write_json_file_atomic(user_path, resolved.user)
        if account_can_rewrite and account_disk_raw != resolved.account:
            _write_json_file_atomic(self._account_config_path, resolved.account)

        if not cfg.master:
            logger.warning(f"[Config] [Warn] No master configured for account '{self._account}'. "
                           f"Edit {self._account_config_path} to set master IDs.")
        return cfg

    @property
    def _account_config_path(self) -> Path:
        return self._data_path / _BOTS_DIR / f"{self._account}.json"

    def _ensure_account_config(self) -> Dict[str, Any]:
        """Return account config dict, auto-creating from template if missing."""
        path = self._account_config_path
        if not path.exists():
            template = self._data_path / _BOTS_DIR / _ACCOUNT_TEMPLATE
            if template.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(template, path)
                logger.info(f"[Config] [Init] Created account config from template: {path}. "
                            f"Please edit this file to set your master/admin IDs.")
            else:
                logger.warning(f"[Config] [Warn] No account config or template found for '{self._account}'.")
                return {}
        return _load_json_file(path)


class ConfigValidationError(Exception):
    """Raised when Pydantic validation fails during config load."""

    def __init__(
        self,
        message: str,
        *,
        validation_error: ValidationError | None = None,
    ) -> None:
        super().__init__(message)
        self.validation_error = validation_error
