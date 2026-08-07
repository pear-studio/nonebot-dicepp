"""Pure validation for Manager-owned configuration candidates.

The runtime merges ``BotConfig defaults < user.json < bots/<id>.json`` before
constructing :class:`BotConfig`.  Manager must apply the same precedence before
it accepts a write, but it must not use ``ConfigLoader`` here: loading through
that class may canonicalize and rewrite existing production files.
"""

from __future__ import annotations

import json
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
    """A safe, field-oriented validation failure suitable for the HTTP API."""

    def __init__(self, errors: list[dict[str, str]]) -> None:
        self.errors = errors
        super().__init__("Configuration validation failed")


def read_config_object(path: Path, *, missing_is_empty: bool = True) -> dict[str, Any]:
    """Read one config document without normalizing or rewriting it.

    Candidate equivalence starts after parsing a JSON object.  An existing
    malformed/unreadable file is a storage-integrity error for Manager, while
    Runtime's fail-soft boot policy is intentionally outside that contract.
    """
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
) -> dict[str, Any]:
    """Validate and return one canonical sparse user layer."""
    bot_candidates = list(_bot_candidates(layout))
    errors: list[dict[str, str]] = []
    canonical_user: dict[str, Any] | None = None
    for label, bot_config in bot_candidates:
        prefix = ("bots", label)
        resolved, candidate_errors = _resolve_layers(
            candidate_user,
            bot_config,
            prefix=prefix,
        )
        errors.extend(candidate_errors)
        if resolved is not None:
            if canonical_user is None:
                canonical_user = resolved.user
            else:
                # Canonicalization of one layer cannot depend on its peers.
                assert canonical_user == resolved.user
    _raise_if_invalid(errors)
    assert canonical_user is not None
    return canonical_user


def validate_bot_candidate(
    layout: InstanceLayout,
    bot_id: str,
    candidate_bot: dict[str, Any],
) -> dict[str, Any]:
    """Validate and return one canonical sparse Bot/account layer."""
    user_raw = read_config_object(layout.config_user)
    resolved, errors = _resolve_layers(
        user_raw,
        candidate_bot,
        prefix=("bots", bot_id),
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
    if template_path.exists():
        yield "_template", read_config_object(template_path)
    else:
        # A future account without a template starts from an empty Bot layer.
        yield "fallback", {}


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
        return None, [
            {
                "field": ".".join((*prefix, "configuration")),
                "message": "Invalid configuration value",
            }
        ]
    return resolved, []


def _field_errors(
    exc: Any,
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
