"""
ConfigLoader: hierarchical JSON configuration loader for DicePP.

Priority (high → low):
  1. Environment variables  (DICE_* prefix)
  2. Account config         Data/bots/{account}.local.json
  3. Global secrets         Data/config.local.json
  4. Persona config         Data/personas/{persona}.json  (bot.config.llm.personality only)
  5. Global defaults        Data/config.json
"""
import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import ValidationError

from utils.logger import dice_log
from core.config.pydantic_models import BotConfig

_BOTS_DIR = "bots"
_PERSONAS_DIR = "personas"
_GLOBAL_CONFIG = "config.json"
_GLOBAL_SECRETS = "config.local.json"
_ACCOUNT_TEMPLATE = "_template.json"


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
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
        return json.loads(text)
    except json.JSONDecodeError as exc:
        dice_log(f"[Config] [Load] JSON parse error in {path}: {exc}")
        return {}
    except OSError as exc:
        dice_log(f"[Config] [Load] Cannot read {path}: {exc}")
        return {}


def _apply_env_overrides(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Apply DICE_* environment variables as overrides.
    Mapping: DICE_MASTER → master, DICE_LLM_API_KEY → llm.api_key
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
        "DICE_LLM_ENABLED": ["llm", "enabled"],
        "DICE_LLM_API_KEY": ["llm", "api_key"],
        "DICE_LLM_BASE_URL": ["llm", "base_url"],
        "DICE_LLM_MODEL": ["llm", "model"],
        "DICE_LLM_PERSONALITY": ["llm", "personality"],
        "DICE_DICEHUB_API_URL": ["dicehub", "api_url"],
        "DICE_DICEHUB_API_KEY": ["dicehub", "api_key"],
        "DICE_DICEHUB_ENABLE": ["dicehub", "enable"],
        "DICE_DICEHUB_HEARTBEAT_INTERVAL": ["dicehub", "heartbeat_interval"],
    }

    for env_key, path in env_mappings.items():
        val = os.environ.get(env_key)
        if val is not None:
            _set_nested(env_map, path, val)

    return _deep_merge(data, env_map)


class ConfigLoader:
    """
    Loads BotConfig from layered JSON files and environment variables.

    Usage:
        loader = ConfigLoader(data_path, account)
        config = loader.load()
        loader.reload()   # atomic hot-reload
    """

    def __init__(self, data_path: str, account: str):
        self._data_path = Path(data_path)
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

    def reload(self) -> BotConfig:
        """
        Atomically reload config.  On validation failure keeps old config
        and raises so the caller can report the error.
        """
        new_cfg = self._build_config()   # may raise ValidationError
        self._config = new_cfg           # atomic reference swap
        return new_cfg

    # ── internals ───────────────────────────────────────────────────────────

    def _build_config(self) -> BotConfig:
        # Layer 5 (lowest): global defaults
        raw = _load_json_file(self._data_path / _GLOBAL_CONFIG)

        # Layer 4: global secrets
        raw = _deep_merge(raw, _load_json_file(self._data_path / _GLOBAL_SECRETS))

        # Layer 3: persona llm_personality (only the personality field)
        # Full persona localization is handled by PersonaLoader separately.
        # We still read the persona name from the partially-merged raw so far.
        persona_name = raw.get("persona", "default")
        persona_path = self._data_path / _PERSONAS_DIR / f"{persona_name}.json"
        if not persona_path.exists() and persona_name != "default":
            dice_log(f"[Config] [Load] Persona '{persona_name}' not found, falling back to 'default'")
            persona_name = "default"
            persona_path = self._data_path / _PERSONAS_DIR / "default.json"
        persona_data = _load_json_file(persona_path)
        if "llm_personality" in persona_data:
            raw.setdefault("llm", {})["personality"] = persona_data["llm_personality"]

        # Layer 2: account config
        account_cfg = self._ensure_account_config()
        raw = _deep_merge(raw, account_cfg)

        # Layer 1 (highest): environment variables
        raw = _apply_env_overrides(raw)

        # Validate and return typed config
        try:
            cfg = BotConfig.model_validate(raw)
        except ValidationError as exc:
            raise ConfigValidationError(
                f"[Config] Configuration validation failed for account '{self._account}':\n{exc}"
            ) from exc

        if not cfg.master:
            dice_log(f"[Config] [Warn] No master configured for account '{self._account}'. "
                     f"Edit {self._account_config_path} to set master IDs.")
        return cfg

    @property
    def _account_config_path(self) -> Path:
        return self._data_path / _BOTS_DIR / f"{self._account}.local.json"

    def _ensure_account_config(self) -> Dict[str, Any]:
        """Return account config dict, auto-creating from template if missing."""
        path = self._account_config_path
        if not path.exists():
            template = self._data_path / _BOTS_DIR / _ACCOUNT_TEMPLATE
            if template.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(template, path)
                dice_log(f"[Config] [Init] Created account config from template: {path}. "
                         f"Please edit this file to set your master/admin IDs.")
            else:
                dice_log(f"[Config] [Warn] No account config or template found for '{self._account}'.")
                return {}
        return _load_json_file(path)


class ConfigValidationError(Exception):
    """Raised when Pydantic validation fails during config load/reload."""
