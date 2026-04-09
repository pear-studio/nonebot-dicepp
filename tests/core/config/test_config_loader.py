"""
Tests for core/config/loader.py

Covers:
  9.1  Hierarchical loading (global defaults < secrets < persona < account < env vars)
  9.2  Pydantic validation errors
  9.5  Atomic config update (reload keeps old config on failure)
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent.parent / "src" / "plugins" / "DicePP"
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from core.config.loader import ConfigLoader, ConfigValidationError, _deep_merge


# ── helpers ───────────────────────────────────────────────────────────────────


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


class _DataDir:
    """Thin wrapper around a tmp directory mimicking Data/."""

    def __init__(self, tmp: Path):
        self.root = tmp
        (tmp / "bots").mkdir(parents=True, exist_ok=True)
        (tmp / "personas").mkdir(parents=True, exist_ok=True)

    @property
    def global_cfg(self) -> Path:
        return self.root / "global.json"

    @property
    def global_secrets(self) -> Path:
        return self.root / "secrets.json"

    def account_cfg(self, account: str) -> Path:
        return self.root / "bots" / f"{account}.json"

    def template(self) -> Path:
        return self.root / "bots" / "_template.json"

    def persona(self, name: str) -> Path:
        return self.root / "personas" / f"{name}.json"

    def loader(self, account: str = "test_account") -> ConfigLoader:
        return ConfigLoader(str(self.root), account)


@pytest.fixture
def dd(tmp_path):
    return _DataDir(tmp_path)


# ── _deep_merge ───────────────────────────────────────────────────────────────


def test_deep_merge_flat():
    result = _deep_merge({"a": 1, "b": 2}, {"b": 99, "c": 3})
    assert result == {"a": 1, "b": 99, "c": 3}


def test_deep_merge_nested():
    base = {"llm": {"enabled": False, "model": "old"}}
    override = {"llm": {"model": "new"}}
    result = _deep_merge(base, override)
    assert result["llm"]["enabled"] is False
    assert result["llm"]["model"] == "new"


def test_deep_merge_does_not_mutate_base():
    base = {"x": {"y": 1}}
    _deep_merge(base, {"x": {"z": 2}})
    assert "z" not in base["x"]


# ── 9.1: Hierarchical loading ─────────────────────────────────────────────────


def test_load_empty_dir_uses_defaults(dd):
    """No files → all Pydantic defaults apply."""
    cfg = dd.loader().load()
    assert cfg.roll.enable is True
    assert cfg.mode.default == "DND5E2024"
    assert cfg.chat_interval == 20


def test_global_config_overrides_pydantic_defaults(dd):
    _write(dd.global_cfg, {"chat_interval": 99, "mode": {"default": "COC7"}})
    cfg = dd.loader().load()
    assert cfg.chat_interval == 99
    assert cfg.mode.default == "COC7"


def test_global_secrets_override_global_config(dd):
    _write(dd.global_cfg, {"persona_ai": {"primary_model": "global-model"}})
    _write(dd.global_secrets, {"persona_ai": {"primary_api_key": "secret_key"}})
    cfg = dd.loader().load()
    assert cfg.persona_ai.primary_api_key == "secret_key"
    assert cfg.persona_ai.primary_model == "global-model"


def test_account_config_overrides_global_secrets(dd):
    _write(dd.global_secrets, {"master": ["global_master"]})
    _write(dd.account_cfg("bot1"), {"master": ["account_master"]})
    cfg = dd.loader("bot1").load()
    assert cfg.master == ["account_master"]


def test_account_config_deep_merge_does_not_erase_siblings(dd):
    """Account sets persona_ai.primary_api_key; global has persona_ai.primary_model — both survive."""
    _write(dd.global_cfg, {"persona_ai": {"primary_model": "global-model", "enabled": True}})
    _write(dd.account_cfg("bot1"), {"persona_ai": {"primary_api_key": "my-key"}})
    cfg = dd.loader("bot1").load()
    assert cfg.persona_ai.primary_model == "global-model"
    assert cfg.persona_ai.primary_api_key == "my-key"
    assert cfg.persona_ai.enabled is True


def test_persona_fallback_when_missing(dd):
    _write(dd.global_cfg, {"persona": "nonexistent"})
    cfg = dd.loader().load()
    # Falls back silently to default; no exception
    assert cfg is not None


def test_env_var_overrides_account_config(dd):
    _write(dd.account_cfg("bot1"), {"master": ["file_master"]})
    with patch.dict(os.environ, {"DICE_MASTER": "env_master"}):
        cfg = dd.loader("bot1").load()
    assert cfg.master == ["env_master"]  # comma-split → single-item list


def test_env_var_master_comma_separated(dd):
    with patch.dict(os.environ, {"DICE_MASTER": "id1,id2,id3"}):
        cfg = dd.loader().load()
    assert cfg.master == ["id1", "id2", "id3"]


def test_env_var_nested_persona_ai_model(dd):
    with patch.dict(os.environ, {"DICE_NICKNAME": "env-nickname"}):
        cfg = dd.loader().load()
    assert cfg.nickname == "env-nickname"


def test_priority_order_all_layers(dd):
    """Full priority stack: env > account > secrets > global."""
    _write(dd.global_cfg, {"nickname": "global"})
    _write(dd.global_secrets, {"nickname": "secret"})
    _write(dd.account_cfg("bot1"), {"nickname": "account"})
    with patch.dict(os.environ, {"DICE_NICKNAME": "env"}):
        cfg = dd.loader("bot1").load()
    assert cfg.nickname == "env"


def test_priority_without_env(dd):
    _write(dd.global_cfg, {"nickname": "global"})
    _write(dd.global_secrets, {"nickname": "secret"})
    _write(dd.account_cfg("bot1"), {"nickname": "account"})
    cfg = dd.loader("bot1").load()
    assert cfg.nickname == "account"


def test_priority_without_account(dd):
    _write(dd.global_cfg, {"nickname": "global"})
    _write(dd.global_secrets, {"nickname": "secret"})
    cfg = dd.loader().load()
    assert cfg.nickname == "secret"


# ── account template auto-creation ───────────────────────────────────────────


def test_account_config_auto_created_from_template(dd):
    _write(dd.template(), {"master": ["template_master"]})
    account_path = dd.account_cfg("newbot")
    assert not account_path.exists()
    dd.loader("newbot").load()
    assert account_path.exists()


def test_no_template_no_account_still_loads(dd):
    cfg = dd.loader("orphan").load()
    assert cfg is not None  # graceful, not an exception


# ── malformed JSON ────────────────────────────────────────────────────────────


def test_malformed_global_config_ignored(dd):
    dd.global_cfg.write_text("{ this is not json }", encoding="utf-8")
    cfg = dd.loader().load()
    assert cfg is not None  # fallback to defaults


def test_malformed_account_config_ignored(dd):
    _write(dd.account_cfg("bot1"), {})  # write empty first to create file
    dd.account_cfg("bot1").write_text("BAD JSON", encoding="utf-8")
    cfg = dd.loader("bot1").load()
    assert cfg is not None


# ── 9.2: Pydantic validation errors ──────────────────────────────────────────


def test_invalid_type_raises_config_validation_error(dd):
    _write(dd.global_cfg, {"chat_interval": "not-a-number"})
    with pytest.raises(ConfigValidationError):
        dd.loader().load()


def test_invalid_nested_type_raises_error(dd):
    _write(dd.global_cfg, {"persona_ai": {"timeout": "oops"}})
    with pytest.raises(ConfigValidationError):
        dd.loader().load()


def test_valid_bool_string_accepted(dd):
    """Pydantic coerces string booleans when using lenient validators."""
    _write(dd.global_cfg, {"persona_ai": {"enabled": True}})
    cfg = dd.loader().load()
    assert cfg.persona_ai.enabled is True


# ── 9.5: Atomic update / reload ──────────────────────────────────────────────


def test_reload_updates_config(dd):
    _write(dd.global_cfg, {"chat_interval": 10})
    loader = dd.loader()
    cfg1 = loader.load()
    assert cfg1.chat_interval == 10

    _write(dd.global_cfg, {"chat_interval": 42})
    cfg2 = loader.reload()
    assert cfg2.chat_interval == 42
    assert loader.config.chat_interval == 42


def test_reload_keeps_old_config_on_validation_failure(dd):
    _write(dd.global_cfg, {"chat_interval": 10})
    loader = dd.loader()
    cfg_before = loader.load()

    _write(dd.global_cfg, {"chat_interval": "bad-type"})
    with pytest.raises(ConfigValidationError):
        loader.reload()

    # Old config must still be accessible
    assert loader.config.chat_interval == 10
    assert loader.config is cfg_before


def test_reload_atomic_on_success(dd):
    _write(dd.global_cfg, {"nickname": "before"})
    loader = dd.loader()
    loader.load()

    _write(dd.global_cfg, {"nickname": "after"})
    new_cfg = loader.reload()
    assert new_cfg.nickname == "after"
    assert loader.config is new_cfg


def test_reload_with_new_account_file(dd):
    loader = dd.loader("mybot")
    loader.load()

    _write(dd.account_cfg("mybot"), {"master": ["new_master"]})
    cfg = loader.reload()
    assert "new_master" in cfg.master


def test_config_property_lazy_loads(dd):
    _write(dd.global_cfg, {"nickname": "lazy"})
    loader = dd.loader()
    assert loader._config is None
    cfg = loader.config  # triggers lazy load
    assert cfg.nickname == "lazy"
    assert loader._config is not None
