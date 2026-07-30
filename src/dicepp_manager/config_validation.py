"""Pure validation for Manager-owned configuration candidates.

The runtime merges ``global.json < user.json < bots/<id>.json`` before
constructing :class:`BotConfig`.  Manager must apply the same precedence before
it accepts a write, but it must not use ``ConfigLoader`` here: loading through
that class may canonicalize and rewrite existing production files.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from dicepp_data import InstanceLayout
from plugins.DicePP.core.config.pydantic_models import BotConfig


class ConfigurationValidationError(ValueError):
    """A safe, field-oriented validation failure suitable for the HTTP API."""

    def __init__(self, errors: list[dict[str, str]]) -> None:
        self.errors = errors
        super().__init__("Configuration validation failed")


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Return the runtime-equivalent recursive merge without mutating inputs."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def read_config_object(path: Path, *, missing_is_empty: bool = True) -> dict[str, Any]:
    """Read one config document without normalizing or rewriting it."""
    if not path.exists():
        if missing_is_empty:
            return {}
        raise FileNotFoundError(path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigurationValidationError(
            [{"field": "configuration", "message": "Stored configuration is unreadable"}]
        ) from exc
    if not isinstance(value, dict):
        raise ConfigurationValidationError(
            [{"field": "configuration", "message": "Configuration must be a JSON object"}]
        )
    return value


def validate_user_candidate(
    layout: InstanceLayout,
    candidate_user: dict[str, Any],
) -> None:
    """Validate a user override against every current and future Bot candidate."""
    global_config = read_config_object(layout.config_global)
    errors: list[dict[str, str]] = []
    for label, bot_config in _bot_candidates(layout):
        errors.extend(
            _validate_merged(
                global_config,
                candidate_user,
                bot_config,
                prefix=("bots", label),
            )
        )
    _raise_if_invalid(errors)


def validate_bot_candidate(
    layout: InstanceLayout,
    bot_id: str,
    candidate_bot: dict[str, Any],
) -> None:
    """Validate one Bot override using the current global and user layers."""
    errors = _validate_merged(
        read_config_object(layout.config_global),
        read_config_object(layout.config_user),
        candidate_bot,
        prefix=("bots", bot_id),
    )
    _raise_if_invalid(errors)


def _bot_candidates(layout: InstanceLayout) -> Iterable[tuple[str, dict[str, Any]]]:
    bots_dir = layout.config_bots_dir
    bot_paths = (
        sorted(path for path in bots_dir.glob("*.json") if path.name != "_template.json")
        if bots_dir.exists()
        else []
    )
    for path in bot_paths:
        yield path.stem, read_config_object(path)

    template_path = bots_dir / "_template.json"
    if template_path.exists():
        yield "_template", read_config_object(template_path)
    else:
        # A future account without a template starts from an empty Bot layer.
        yield "fallback", {}


def _validate_merged(
    global_config: dict[str, Any],
    user_config: dict[str, Any],
    bot_config: dict[str, Any],
    *,
    prefix: tuple[str, ...],
) -> list[dict[str, str]]:
    merged = deep_merge(deep_merge(global_config, user_config), bot_config)
    try:
        BotConfig.model_validate(merged)
    except ValidationError as exc:
        return _field_errors(exc, prefix)
    return []


def _field_errors(
    exc: ValidationError,
    prefix: tuple[str, ...],
) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    for item in exc.errors(include_url=False):
        location = item.get("loc", ())
        parts = [*prefix, *(str(part) for part in location)]
        errors.append(
            {
                "field": ".".join(parts) or "configuration",
                # Do not expose Pydantic's ``input`` or model-validator text:
                # either can contain a user-provided credential or identifier.
                "message": "Invalid configuration value",
            }
        )
    return errors


def _raise_if_invalid(errors: list[dict[str, str]]) -> None:
    if errors:
        raise ConfigurationValidationError(errors)
