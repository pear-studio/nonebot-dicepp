#!/usr/bin/env python3
"""
gen_config_schema.py — Generate flattened config/schema.json from BotConfig pydantic model.

Usage:
    python scripts/gen_config_schema.py

Output:
    config/schema.json — flat JSON object of dot-notation path -> description mappings.
"""
import json
import sys
from pathlib import Path
from typing import Any

# ── Add DicePP plugin root to sys.path so we can import BotConfig ──────────
# Same pattern used by bot.py: src/plugins/DicePP is the import root, so
# "from core.config.pydantic_models import BotConfig" works directly.
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
_DICEPP_DIR = str(_PROJECT_ROOT / "src" / "plugins" / "DicePP")
if _DICEPP_DIR not in sys.path:
    sys.path.insert(0, _DICEPP_DIR)


def _resolve_ref(ref: str, schema: dict) -> dict:
    """Resolve a JSON Schema $ref like '#/$defs/PersonaConfig' against the schema."""
    path = ref.lstrip("#/").split("/")
    cur: Any = schema
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
    """
    Recursively walk a JSON Schema and collect dot-notation path -> description.
    Resolves $ref to $defs/definitions for nested Pydantic models.
    """
    if path_description_map is None:
        path_description_map = {}

    properties = schema.get("properties", {})
    for key, prop_schema in properties.items():
        path = f"{prefix}.{key}" if prefix else key

        # Resolve $ref
        resolved = None
        if "$ref" in prop_schema:
            resolved = _resolve_ref(prop_schema["$ref"], full_schema)

        # Use resolved schema if available, falling back to prop_schema itself
        effective = resolved or prop_schema
        description = effective.get("description", "") or prop_schema.get("description", "")

        type_ = effective.get("type") or prop_schema.get("type")

        if type_ == "object" and "properties" in effective:
            # Nested object with properties -> recurse with effective schema
            _flatten_schema(effective, full_schema, path, path_description_map)
        elif type_ == "array":
            items = effective.get("items") or prop_schema.get("items", {})
            resolved_items = None
            if "$ref" in items:
                resolved_items = _resolve_ref(items["$ref"], full_schema)
            items_effective = resolved_items or items
            if items_effective.get("type") == "object" and "properties" in items_effective:
                _flatten_schema(items_effective, full_schema, path, path_description_map)

        # Collect description from composition keywords
        for comb_key in ("allOf", "anyOf", "oneOf"):
            for sub in prop_schema.get(comb_key, []):
                sub_desc = sub.get("description", "")
                if sub_desc and not description:
                    description = sub_desc

        # Fallback: use default value as hint
        if not description and "default" in prop_schema:
            default_val = prop_schema["default"]
            if isinstance(default_val, (str, int, float, bool)):
                description = f"默认值: {json.dumps(default_val, ensure_ascii=False)}"

        if description:
            path_description_map[path] = description

    return path_description_map


def main():
    # Import the target model (DicePP path is already on sys.path)
    try:
        from core.config.pydantic_models import BotConfig
    except ImportError as exc:
        print(f"ERROR: Cannot import BotConfig - make sure DicePP src is on sys.path.\n{exc}", file=sys.stderr)
        sys.exit(1)

    # Generate JSON Schema (Pydantic v2)
    schema = BotConfig.model_json_schema()

    # Flatten to dot-notation path -> description
    flattened: dict[str, str] = {}
    _flatten_schema(schema, schema, "", flattened)

    # Write output
    output_path = _PROJECT_ROOT / "config" / "schema.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(flattened, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"OK - wrote {len(flattened)} paths to {output_path}")
    for path, desc in sorted(flattened.items()):
        print(f"  {path}: {desc}")


if __name__ == "__main__":
    main()
