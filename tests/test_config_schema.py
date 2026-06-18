"""Test that generated config schema matches config/schema.json.

This test verifies that the schema.json checked into the repo is in sync
with the BotConfig Pydantic model. If this test fails, run::

    python scripts/gen_config_schema.py

and commit the updated config/schema.json.
"""

import json
import sys
from pathlib import Path

import pytest

# Only run if DicePP dependencies are fully importable
try:
    from core.config.pydantic_models import BotConfig
    HAVE_BOT_CONFIG = True
except ImportError:
    HAVE_BOT_CONFIG = False


pytestmark = pytest.mark.skipif(
    not HAVE_BOT_CONFIG,
    reason="DicePP dependencies not importable (BotConfig not available)",
)


def _resolve_ref(ref: str, schema: dict) -> dict:
    """Resolve a JSON Schema ``$ref`` like ``#/$defs/PersonaConfig``."""
    path = ref.lstrip("#/").split("/")
    cur: dict = schema
    for segment in path:
        if isinstance(cur, dict) and segment in cur:
            cur = cur[segment]
        else:
            return {}
    return cur if isinstance(cur, dict) else {}


def _flatten_schema(
    schema: dict,
    full_schema: dict,
    prefix: str = "",
    path_description_map: dict | None = None,
) -> dict[str, str]:
    """Recursively walk a JSON Schema and collect dot-notation path -> description."""
    if path_description_map is None:
        path_description_map = {}

    properties = schema.get("properties", {})
    for key, prop_schema in properties.items():
        path = f"{prefix}.{key}" if prefix else key

        resolved = None
        if "$ref" in prop_schema:
            resolved = _resolve_ref(prop_schema["$ref"], full_schema)

        effective = resolved or prop_schema
        description = effective.get("description", "") or prop_schema.get("description", "")
        type_ = effective.get("type") or prop_schema.get("type")

        if type_ == "object" and "properties" in effective:
            _flatten_schema(effective, full_schema, path, path_description_map)
        elif type_ == "array":
            items = effective.get("items") or prop_schema.get("items", {})
            resolved_items = None
            if "$ref" in items:
                resolved_items = _resolve_ref(items["$ref"], full_schema)
            items_effective = resolved_items or items
            if items_effective.get("type") == "object" and "properties" in items_effective:
                _flatten_schema(items_effective, full_schema, path, path_description_map)

        for comb_key in ("allOf", "anyOf", "oneOf"):
            for sub in prop_schema.get(comb_key, []):
                sub_desc = sub.get("description", "")
                if sub_desc and not description:
                    description = sub_desc

        if not description and "default" in prop_schema:
            default_val = prop_schema["default"]
            if isinstance(default_val, (str, int, float, bool)):
                description = f"默认值: {json.dumps(default_val, ensure_ascii=False)}"

        if description:
            path_description_map[path] = description

    return path_description_map


def test_config_schema_matches():
    """Generated schema from BotConfig must match config/schema.json exactly."""
    schema = BotConfig.model_json_schema()

    flattened: dict[str, str] = {}
    _flatten_schema(schema, schema, "", flattened)

    project_root = Path(__file__).resolve().parent.parent
    schema_path = project_root / "config" / "schema.json"

    if not schema_path.exists():
        pytest.fail(f"config/schema.json not found at {schema_path}")

    with open(schema_path, "r", encoding="utf-8") as f:
        current = json.load(f)

    if flattened == current:
        return

    # Build a detailed diff message
    generated_keys = set(flattened)
    current_keys = set(current)
    only_in_generated = generated_keys - current_keys
    only_in_current = current_keys - generated_keys
    value_diffs = {
        k: (flattened[k], current[k])
        for k in generated_keys & current_keys
        if flattened[k] != current[k]
    }

    lines = [
        f"generated schema differs from {schema_path}",
        f"  Run:  python scripts/gen_config_schema.py",
        f"  Then: git add config/schema.json",
    ]
    if only_in_generated:
        lines.append(f"  Only in generated ({len(only_in_generated)}):")
        for k in sorted(only_in_generated):
            lines.append(f"    + {k}: {flattened[k]}")
    if only_in_current:
        lines.append(f"  Only in current ({len(only_in_current)}):")
        for k in sorted(only_in_current):
            lines.append(f"    - {k}: {current[k]}")
    if value_diffs:
        lines.append(f"  Value differences ({len(value_diffs)}):")
        for k in sorted(value_diffs):
            lines.append(f"    {k}:")
            lines.append(f"      generated: {value_diffs[k][0]}")
            lines.append(f"      current:   {value_diffs[k][1]}")

    pytest.fail("\n".join(lines))
