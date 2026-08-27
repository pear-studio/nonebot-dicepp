"""Dashboard-owned validation for the independent config schemas."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dicepp_data import InstanceLayout
from plugins.DicePP.core.config.loader import (
    ConfigValidationError as RuntimeConfigValidationError,
    canonicalize_config_layer,
    validate_config_candidate,
)
from plugins.DicePP.core.config.pydantic_models import BotConfig, UserConfig


class ConfigurationValidationError(ValueError):
    """A safe, field-oriented validation failure for Dashboard responses."""

    def __init__(self, errors: list[dict[str, str]]) -> None:
        self.errors = errors
        super().__init__("Configuration validation failed")


def read_config_object(path: Path, *, missing_is_empty: bool = True) -> dict[str, Any]:
    """Read a JSON object without creating missing configuration files."""

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
    layout: InstanceLayout, candidate_user: dict[str, Any]
) -> dict[str, Any]:
    """Validate a candidate against ``UserConfig`` only."""

    del layout  # UserConfig is instance-wide and does not depend on Bot files.
    try:
        validate_config_candidate(candidate_user, model_type=UserConfig)
        return canonicalize_config_layer(candidate_user, model_type=UserConfig)
    except RuntimeConfigValidationError as exc:
        raise ConfigurationValidationError(_field_errors(exc, ("user",))) from exc


def validate_bot_candidate(
    layout: InstanceLayout, bot_id: str, candidate_bot: dict[str, Any]
) -> dict[str, Any]:
    """Validate a candidate against ``BotConfig`` only.

    ``user.json`` is deliberately not read here: it is not a Bot override
    layer.  The runtime validation still materialises the Bot defaults so
    cross-field validators run before a save.
    """

    del layout
    try:
        validate_config_candidate(candidate_bot, model_type=BotConfig)
        return canonicalize_config_layer(candidate_bot, model_type=BotConfig)
    except RuntimeConfigValidationError as exc:
        raise ConfigurationValidationError(_field_errors(exc, ("bots", bot_id))) from exc


def _field_errors(exc: RuntimeConfigValidationError, prefix: tuple[str, ...]) -> list[dict[str, str]]:
    if exc.validation_error is not None:
        return [
            {
                "field": ".".join((*prefix, *(str(part) for part in item.get("loc", ())))),
                "message": "Invalid configuration value",
            }
            for item in exc.validation_error.errors(include_url=False)
        ]
    return [{"field": ".".join((*prefix, "configuration")), "message": str(exc)}]


def effective_bot_config(
    bot_id: str, raw: dict[str, Any] | None = None
) -> BotConfig:
    """Return effective Bot defaults plus one sparse Bot layer."""

    try:
        return validate_config_candidate(raw or {}, model_type=BotConfig)
    except RuntimeConfigValidationError as exc:
        raise ConfigurationValidationError(_field_errors(exc, ("bots", bot_id))) from exc


__all__ = [
    "ConfigurationValidationError",
    "read_config_object",
    "validate_bot_candidate",
    "validate_user_candidate",
    "effective_bot_config",
]
