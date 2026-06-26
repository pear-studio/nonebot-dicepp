"""
Persona character card service for the DicePP Dashboard.

Reads, writes, lists, creates, and deletes character cards stored as
YAML files under content/characters/{name}/character.yaml.

No imports from the runtime persona module — uses PyYAML directly so
the dashboard stays decoupled from nonebot2.
"""

import logging
import os
import shutil
from pathlib import Path
from typing import Any

import yaml

from ._helpers import _read_json_safe
from .config import DashboardPaths

logger = logging.getLogger("dashboard.persona")

# ── Character YAML ↔ structured JSON ──────────────────────────────────────────

# Field defaults mirroring Character / PersonaExtensions / LoreEntry models.
# These are used when YAML is missing optional fields so the frontend always
# receives a consistent shape.
_BASIC_DEFAULTS: dict[str, Any] = {
    "description": "",
    "personality": "",
    "scenario": "",
    "system_prompt": "",
}

_DIALOGUE_DEFAULTS: dict[str, Any] = {
    "mes_example": "",
}

_LORE_ENTRY_DEFAULTS: dict[str, Any] = {
    "keys": [],
    "content": "",
    "enabled": True,
    "selective": False,
    "secondary_keys": [],
    "order": 100,
    "exact_match": False,
    "min_match_length": None,
}

_EXTENSIONS_DEFAULTS: dict[str, Any] = {
    "relation_labels": [],
    "world": "",
    "daily_events_count": 5,
    "event_day_start_hour": 8,
    "event_day_end_hour": 22,
    "event_jitter_minutes": 60,
    "event_day_start_jitter_minutes": 30,
    "event_day_end_jitter_minutes": 30,
    "refuse_messages": None,
    "share_message_examples": None,
    "sleep_messages": None,
    "image_gen_style": "",
    "image_gen_appearance": "",
}

# Fields that have been deprecated and should be stripped on read & write.
_DEPRECATED_EXTENSION_FIELDS = {"warmth_labels", "first_mes", "initial_relationship"}


def _apply_defaults(target: dict, defaults: dict) -> dict:
    """Fill missing keys in *target* with values from *defaults* (not in-place)."""
    result = dict(target)
    for k, v in defaults.items():
        if k not in result or result[k] is None:
            result[k] = v
    return result


def yaml_to_json(yaml_text: str) -> dict:
    """Parse character.yaml text into the structured JSON format.

    Returns a dict with keys: _display_name (top-level YAML 'name' field),
    basic, dialogue, character_book, extensions.
    Missing / empty fields are filled with model defaults.
    Deprecated fields (warmth_labels, first_mes, initial_relationship) are dropped.
    """
    raw = yaml.safe_load(yaml_text) or {}
    if not isinstance(raw, dict):
        raw = {}

    # Extract top-level name as display_name metadata
    yn = raw.get("name")
    display_name = yn.strip() if isinstance(yn, str) and yn.strip() else ""

    # ── Basic fields ───────────────────────────────────────────────────────
    basic = {
        "description": str(raw.get("description", "") or ""),
        "personality": str(raw.get("personality", "") or ""),
        "scenario": str(raw.get("scenario", "") or ""),
        "system_prompt": str(raw.get("system_prompt", "") or ""),
    }

    # ── Dialogue ───────────────────────────────────────────────────────────
    dialogue = {
        "mes_example": str(raw.get("mes_example", "") or ""),
    }

    # ── Character book ─────────────────────────────────────────────────────
    cb = raw.get("character_book") or {}
    raw_entries = cb.get("entries", []) if isinstance(cb, dict) else []
    if not isinstance(raw_entries, list):
        raw_entries = []
    entries = []
    for e in raw_entries:
        if not isinstance(e, dict):
            continue
        entry = _apply_defaults(e, _LORE_ENTRY_DEFAULTS)
        entries.append(entry)
    character_book = {"entries": entries}

    # ── Extensions ─────────────────────────────────────────────────────────
    ext_raw = raw.get("extensions", {}) or {}
    if isinstance(ext_raw, dict):
        persona_ext = ext_raw.get("persona", {}) or {}
        if not isinstance(persona_ext, dict):
            persona_ext = {}
    else:
        persona_ext = {}

    # Drop deprecated keys
    for key in _DEPRECATED_EXTENSION_FIELDS:
        persona_ext.pop(key, None)

    extensions = _apply_defaults(persona_ext, _EXTENSIONS_DEFAULTS)

    return {
        "_display_name": display_name,
        "basic": basic,
        "dialogue": dialogue,
        "character_book": character_book,
        "extensions": extensions,
    }


def json_to_yaml(data: dict) -> str:
    """Convert structured JSON back to character.yaml text.

    The input dict has the same shape as yaml_to_json output.
    Default-valued fields are omitted for a clean YAML output.
    Deprecated fields are never emitted.
    """
    basic = data.get("basic", {})
    dialogue = data.get("dialogue", {})
    character_book = data.get("character_book", {})
    extensions = data.get("extensions", {})

    yaml_data: dict[str, Any] = {}

    # Basic fields: emit when non-default, plus always emit description/personality
    for k, default in _BASIC_DEFAULTS.items():
        val = basic.get(k, default)
        if val != default:
            yaml_data[k] = val
        elif k in ("description", "personality"):
            # Always emit description + personality even if empty (core fields)
            yaml_data[k] = val

    # Dialogue
    mes = dialogue.get("mes_example", "")
    if mes:
        yaml_data["mes_example"] = mes

    # Character book
    entries = character_book.get("entries", [])
    if entries:
        clean_entries = []
        for e in entries:
            clean = {}
            for k, default in _LORE_ENTRY_DEFAULTS.items():
                val = e.get(k, default)
                if val != default:
                    clean[k] = val
            # keys and content are required
            clean["keys"] = e.get("keys", [])
            clean["content"] = e.get("content", "")
            clean_entries.append(clean)
        yaml_data["character_book"] = {"entries": clean_entries}

    # Extensions
    ext_out: dict[str, Any] = {}
    for k, default in _EXTENSIONS_DEFAULTS.items():
        val = extensions.get(k, default)
        if val != default:
            ext_out[k] = val
    if ext_out:
        yaml_data["extensions"] = {"persona": ext_out}

    return yaml.dump(yaml_data, sort_keys=False, allow_unicode=True, default_flow_style=False)


# ── Directory helpers ──────────────────────────────────────────────────────────


def _char_dir(name: str) -> Path:
    """Return content/characters/{name}/."""
    return DashboardPaths.CONTENT_DIR / "characters" / name


def _char_yaml_path(name: str) -> Path:
    """Return content/characters/{name}/character.yaml."""
    return _char_dir(name) / "character.yaml"


# ── Bot-to-character mapping ───────────────────────────────────────────────────


def compute_bot_character_map() -> dict[str, list[str]]:
    """Scan config/bots/*.json and return {character_name: [bot_id, ...]}.

    Skips _template.json and unreadable files.
    A bot without persona_ai.character_name is assumed to use "default".
    """
    bots_dir = DashboardPaths.CONFIG_BOTS_DIR
    result: dict[str, list[str]] = {}
    if not bots_dir.exists():
        return result

    for f in sorted(bots_dir.iterdir()):
        if f.suffix != ".json" or f.stem == "_template":
            continue
        cfg = _read_json_safe(f)
        char_name = cfg.get("persona_ai", {}).get("character_name", "default") or "default"
        result.setdefault(char_name, []).append(f.stem)

    return result


# ── Character list ─────────────────────────────────────────────────────────────


def list_characters() -> list[dict[str, Any]]:
    """Return enriched character list for the dashboard grid.

    Each entry: name, display_name, description_snippet, is_default,
    used_by, has_book.
    """
    chars_dir = DashboardPaths.CONTENT_DIR / "characters"
    bot_map = compute_bot_character_map()
    characters: list[dict[str, Any]] = []

    if not chars_dir.exists():
        return characters

    for d in sorted(chars_dir.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        yaml_path = d / "character.yaml"
        if not yaml_path.exists():
            continue

        display_name = d.name
        description_snippet = ""
        has_book = False

        try:
            yaml_text = yaml_path.read_text(encoding="utf-8")
            parsed = yaml_to_json(yaml_text)
            display_name = parsed.get("_display_name") or d.name
            desc = parsed["basic"].get("description", "")
            if desc:
                description_snippet = desc[:80] + ("..." if len(desc) > 80 else "")
            entries = parsed.get("character_book", {}).get("entries", [])
            has_book = any(e.get("enabled", True) for e in entries)
        except (yaml.YAMLError, UnicodeDecodeError, OSError):
            logger.warning("Failed to parse character.yaml for %s", d.name)

        characters.append({
            "name": d.name,
            "display_name": display_name,
            "description_snippet": description_snippet,
            "is_default": d.name == "default",
            "used_by": bot_map.get(d.name, []),
            "has_book": has_book,
        })

    return characters


# ── Single character get ───────────────────────────────────────────────────────


def get_character(name: str) -> dict[str, Any]:
    """Read and parse a single character card, returning structured JSON.

    Raises FileNotFoundError if the character.yaml is missing.
    """
    yaml_path = _char_yaml_path(name)
    if not yaml_path.exists():
        raise FileNotFoundError(f"Character not found: {name}")

    yaml_text = yaml_path.read_text(encoding="utf-8")
    parsed = yaml_to_json(yaml_text)

    return {
        "name": name,
        "display_name": parsed.get("_display_name") or name,
        "is_default": name == "default",
        **parsed,
    }


# ── Save ───────────────────────────────────────────────────────────────────────


def save_character(name: str, data: dict[str, Any]) -> None:
    """Validate and write structured character data to character.yaml.

    *data* should have the same shape as get_character() output:
    {basic, dialogue, character_book, extensions, display_name?}.

    Writes atomically via .tmp + fsync + os.replace.
    """
    display_name = data.get("display_name", name)
    if not isinstance(display_name, str):
        display_name = str(display_name)

    # Build the YAML content
    sections_data = {
        "basic": data.get("basic", {}),
        "dialogue": data.get("dialogue", {}),
        "character_book": data.get("character_book", {}),
        "extensions": data.get("extensions", {}),
    }
    yaml_body = json_to_yaml(sections_data)

    # Prepend the name field at the top (use yaml.dump for safe quoting)
    name_line = yaml.dump({"name": display_name}, sort_keys=False, allow_unicode=True, default_flow_style=False).strip()
    yaml_text = f'{name_line}\n{yaml_body}'

    yaml_path = _char_yaml_path(name)
    yaml_path.parent.mkdir(parents=True, exist_ok=True)

    # Atomic write
    tmp_path = yaml_path.with_suffix(".tmp")
    tmp_path.write_text(yaml_text, encoding="utf-8")
    fd = os.open(str(tmp_path), os.O_RDONLY)
    os.fsync(fd)
    os.close(fd)
    os.replace(tmp_path, yaml_path)


# ── Create ─────────────────────────────────────────────────────────────────────


def create_character(name: str, display_name: str = "") -> None:
    """Create a new character directory with a minimal character.yaml.

    Raises FileExistsError if the directory already exists.
    """
    char_dir = _char_dir(name)
    if char_dir.exists():
        raise FileExistsError(f"Character already exists: {name}")

    char_dir.mkdir(parents=True, exist_ok=False)

    dn = display_name or name
    name_line = yaml.dump({"name": dn}, sort_keys=False, allow_unicode=True, default_flow_style=False).strip()
    yaml_text = f'{name_line}\n'

    yaml_path = char_dir / "character.yaml"
    yaml_path.write_text(yaml_text, encoding="utf-8")


# ── Delete ─────────────────────────────────────────────────────────────────────


def delete_character(name: str) -> None:
    """Delete a character directory and all its contents.

    Raises FileNotFoundError if the directory does not exist.
    """
    char_dir = _char_dir(name)
    if not char_dir.exists():
        raise FileNotFoundError(f"Character not found: {name}")

    shutil.rmtree(char_dir)
