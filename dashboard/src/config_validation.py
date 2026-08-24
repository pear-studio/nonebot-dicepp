"""Dashboard-owned validation for user and Bot configuration candidates."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from dicepp_data import InstanceLayout
from plugins.DicePP.core.config.loader import (
    ConfigValidationError as RuntimeConfigValidationError,
    ResolvedConfigLayers,
    resolve_config_layers,
)


class ConfigurationValidationError(ValueError):
    """A safe, field-oriented validation failure for Dashboard responses."""

    def __init__(self, errors: list[dict[str, str]]) -> None:
        self.errors = errors
        super().__init__("Configuration validation failed")


def read_config_object(path: Path, *, missing_is_empty: bool = True) -> dict[str, Any]:
    if not path.exists():
        if missing_is_empty:
            return {}
        raise FileNotFoundError(path)
    import json

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


def validate_user_candidate(layout: InstanceLayout, candidate_user: dict[str, Any]) -> dict[str, Any]:
    canonical_user: dict[str, Any] | None = None
    errors: list[dict[str, str]] = []
    for label, bot_config in _bot_candidates(layout):
        resolved, candidate_errors = _resolve_layers(
            candidate_user, bot_config, prefix=("bots", label)
        )
        errors.extend(candidate_errors)
        if resolved is not None:
            if canonical_user is None:
                canonical_user = resolved.user
            else:
                assert canonical_user == resolved.user
    _raise_if_invalid(errors)
    assert canonical_user is not None
    return canonical_user


def validate_bot_candidate(
    layout: InstanceLayout, bot_id: str, candidate_bot: dict[str, Any]
) -> dict[str, Any]:
    resolved, errors = _resolve_layers(
        read_config_object(layout.config_user), candidate_bot, prefix=("bots", bot_id)
    )
    _raise_if_invalid(errors)
    assert resolved is not None
    return resolved.account


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
    yield "_template" if template_path.exists() else "fallback", (
        read_config_object(template_path) if template_path.exists() else {}
    )


def _resolve_layers(
    user_config: dict[str, Any],
    bot_config: dict[str, Any],
    *,
    prefix: tuple[str, ...],
) -> tuple[ResolvedConfigLayers | None, list[dict[str, str]]]:
    try:
        resolved = resolve_config_layers(user_config, bot_config)
    except RuntimeConfigValidationError as exc:
        if exc.validation_error is not None:
            return None, _field_errors(exc.validation_error, prefix)
        return None, [{"field": ".".join((*prefix, "configuration")), "message": "Invalid configuration value"}]
    return resolved, []


def _field_errors(exc: Any, prefix: tuple[str, ...]) -> list[dict[str, str]]:
    return [
        {
            "field": ".".join((*prefix, *(str(part) for part in item.get("loc", ())))),
            "message": "Invalid configuration value",
        }
        for item in exc.errors(include_url=False)
    ]


def _raise_if_invalid(errors: list[dict[str, str]]) -> None:
    if errors:
        raise ConfigurationValidationError(errors)
