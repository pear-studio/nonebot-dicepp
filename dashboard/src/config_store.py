"""Dashboard-local configuration reads, validation and writes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from plugins.DicePP.core.config.loader import save_config_file
from plugins.DicePP.core.config.pydantic_models import BotConfig, UserConfig

from .config_validation import (
    ConfigurationValidationError,
    effective_bot_config,
    read_config_object,
    validate_bot_candidate,
    validate_user_candidate,
)


def write_config_object(
    path: Path,
    payload: dict[str, Any],
    *,
    model_type: type[BotConfig] | type[UserConfig],
) -> dict[str, Any]:
    """Validate and save a config object as a recursive sparse overlay."""
    return save_config_file(path, payload, model_type=model_type)


__all__ = [
    "ConfigurationValidationError",
    "read_config_object",
    "validate_bot_candidate",
    "validate_user_candidate",
    "effective_bot_config",
    "write_config_object",
    "BotConfig",
    "UserConfig",
]
