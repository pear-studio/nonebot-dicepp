"""
Tests for core/config/loader.py

Covers:
  9.1  Hierarchical loading (model defaults < user overrides < account < env vars)
  9.2  Pydantic validation errors
  9.5  Canonical config persistence
"""
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from plugins.DicePP.core.config.loader import ConfigLoader, ConfigValidationError, _deep_merge


# ── helpers ───────────────────────────────────────────────────────────────────


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class _DataDir:
    """Thin wrapper around a tmp directory mimicking the config/ layout."""

    def __init__(self, tmp: Path):
        self.root = tmp
        (tmp / "bots").mkdir(parents=True, exist_ok=True)
        (tmp / "personas").mkdir(parents=True, exist_ok=True)

    @property
    def user_config(self) -> Path:
        return self.root / "user.json"

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


# ── 9.1: Hierarchical loading ─────────────────────────────────────────────────


def test_load_empty_dir_uses_defaults(dd):
    """No files → all Pydantic defaults apply."""
    cfg = dd.loader().load()
    assert cfg.roll.enable is True
    assert cfg.mode.default == "DND5E2024"
    assert cfg.chat_interval == 20


def test_user_config_overrides_model_defaults(dd):
    _write(dd.user_config, {"chat_interval": 99, "nickname": "user_nick"})
    cfg = dd.loader().load()
    assert cfg.chat_interval == 99
    assert cfg.nickname == "user_nick"


def test_account_config_overrides_user_config(dd):
    _write(dd.user_config, {"master": ["instance_master"]})
    _write(dd.account_cfg("bot1"), {"master": ["account_master"]})
    cfg = dd.loader("bot1").load()
    assert cfg.master == ["account_master"]


def test_account_config_deep_merge_does_not_erase_siblings(dd):
    """Account sets nickname; user has chat_interval — both survive deep merge."""
    _write(dd.user_config, {"chat_interval": 99, "nickname": "user_nick"})
    _write(dd.account_cfg("bot1"), {"nickname": "account_nick"})
    cfg = dd.loader("bot1").load()
    assert cfg.chat_interval == 99
    assert cfg.nickname == "account_nick"


def test_sparse_user_provider_override_preserves_builtin_catalog(dd):
    _write(dd.user_config, {"persona_ai": {"providers": {"minimax": {"api_key": "test-key"}}}})

    cfg = dd.loader().load()

    assert cfg.persona_ai.providers["minimax"].api_key == "test-key"
    assert cfg.persona_ai.providers["minimax"].base_url == "https://api.minimaxi.com/v1"
    assert [model.name for model in cfg.persona_ai.providers["minimax"].models] == [
        "MiniMax-M3",
        "MiniMax-M3-t",
    ]
    assert _read(dd.user_config) == {
        "persona_ai": {"providers": {"minimax": {"api_key": "test-key"}}}
    }


def test_user_can_replace_builtin_provider_models(dd):
    models = [
        {
            "name": "local-model",
            "category": "llm",
            "capabilities": ["text"],
        }
    ]
    _write(dd.user_config, {"persona_ai": {"providers": {"minimax": {"models": models}}}})

    cfg = dd.loader().load()

    assert [model.name for model in cfg.persona_ai.providers["minimax"].models] == [
        "local-model"
    ]
    assert cfg.persona_ai.providers["minimax"].base_url == "https://api.minimaxi.com/v1"


def test_persona_fallback_when_missing(dd):
    _write(dd.user_config, {"persona": "nonexistent"})
    cfg = dd.loader().load()
    assert cfg.persona == "nonexistent"
    assert cfg.chat_interval == 20


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
    """Full priority stack: env > account > user > model defaults."""
    _write(dd.user_config, {"nickname": "secret"})
    _write(dd.account_cfg("bot1"), {"nickname": "account"})
    with patch.dict(os.environ, {"DICE_NICKNAME": "env"}):
        cfg = dd.loader("bot1").load()
    assert cfg.nickname == "env"


def test_priority_without_env(dd):
    _write(dd.user_config, {"nickname": "secret"})
    _write(dd.account_cfg("bot1"), {"nickname": "account"})
    cfg = dd.loader("bot1").load()
    assert cfg.nickname == "account"


def test_priority_without_account(dd):
    _write(dd.user_config, {"nickname": "user_overridden"})
    cfg = dd.loader().load()
    assert cfg.nickname == "user_overridden"


# ── account template auto-creation ───────────────────────────────────────────


def test_account_config_auto_created_from_template(dd):
    _write(dd.template(), {"master": ["template_master"]})
    account_path = dd.account_cfg("newbot")
    assert not account_path.exists()
    dd.loader("newbot").load()
    assert account_path.exists()


def test_no_template_no_account_still_loads(dd):
    cfg = dd.loader("orphan").load()
    assert cfg.mode.default == "DND5E2024"
    assert cfg.chat_interval == 20


# ── malformed JSON ────────────────────────────────────────────────────────────


def test_malformed_account_config_ignored(dd):
    _write(dd.account_cfg("bot1"), {})  # write empty first to create file
    malformed = "BAD JSON"
    dd.account_cfg("bot1").write_text(malformed, encoding="utf-8")
    cfg = dd.loader("bot1").load()
    assert cfg.mode.default == "DND5E2024"
    assert cfg.chat_interval == 20
    assert dd.account_cfg("bot1").read_text(encoding="utf-8") == malformed


# ── 9.2: Pydantic validation errors ──────────────────────────────────────────


def test_invalid_known_field_raises_without_rewriting(dd):
    original = {"master": "not-a-list"}
    _write(dd.user_config, original)
    with pytest.raises(ConfigValidationError):
        dd.loader().load()
    assert _read(dd.user_config) == original


def test_valid_bool_string_accepted(dd):
    """String boolean 'true' is coerced to True by Pydantic."""
    _write(dd.user_config, {"persona_ai": {"enabled": 'true'}})
    cfg = dd.loader().load()
    assert cfg.persona_ai.enabled is True


# ── canonical rewrite ────────────────────────────────────────────────────────


def test_canonical_rewrite_preserves_comment_metadata(dd):
    _write(dd.user_config, {
        "_comment": "top-level guidance",
        "chat_interval": 99,
        "persona_ai": {
            "_comment_persona": "nested guidance",
            "enabled": True,
        },
    })

    cfg = dd.loader().load()
    saved = _read(dd.user_config)

    assert saved["_comment"] == "top-level guidance"
    assert saved["persona_ai"]["_comment_persona"] == "nested guidance"
    assert "_comment" not in cfg.model_dump()
    assert "_comment_persona" not in cfg.persona_ai.model_dump()
def test_canonical_rewrite_uses_current_search_max_chars_field(dd):
    _write(dd.user_config, {
        "persona_ai": {
            "search_max_chars": 111,
        },
    })

    cfg = dd.loader().load()
    saved = _read(dd.user_config)

    assert cfg.persona_ai.search_max_chars == 111
    assert saved["persona_ai"]["search_max_chars"] == 111


def test_unknown_field_fails_without_rewriting(dd):
    original = {"chat_interval": 42, "removed_setting": True}
    _write(dd.user_config, original)

    with pytest.raises(ConfigValidationError):
        dd.loader().load()

    assert _read(dd.user_config) == original


def test_canonical_rewrite_does_not_write_env_overrides(dd):
    _write(dd.account_cfg("bot1"), {"nickname": "file_nick"})

    with patch.dict(os.environ, {"DICE_NICKNAME": "env_nick"}):
        cfg = dd.loader("bot1").load()

    assert cfg.nickname == "env_nick"
    assert _read(dd.account_cfg("bot1"))["nickname"] == "file_nick"
    assert list(dd.root.rglob("*.bak")) == []


def test_canonical_rewrite_keeps_user_and_account_layers_partial(dd):
    _write(dd.user_config, {"chat_interval": 44, "nickname": "user_nick"})
    _write(dd.account_cfg("bot1"), {"master": ["account_master"]})

    cfg = dd.loader("bot1").load()

    assert cfg.chat_interval == 44
    assert cfg.nickname == "user_nick"
    assert cfg.master == ["account_master"]
    assert _read(dd.user_config) == {"chat_interval": 44, "nickname": "user_nick"}
    assert _read(dd.account_cfg("bot1")) == {"master": ["account_master"]}


def test_canonical_rewrite_keeps_nested_user_and_account_layers_partial(dd):
    _write(dd.user_config, {"persona_ai": {"enabled": True}})
    _write(dd.account_cfg("bot1"), {"roll": {"enable": False}})

    cfg = dd.loader("bot1").load()

    assert cfg.persona_ai.enabled is True
    assert cfg.roll.enable is False
    assert _read(dd.user_config) == {"persona_ai": {"enabled": True}}
    assert _read(dd.account_cfg("bot1")) == {"roll": {"enable": False}}


def test_config_property_lazy_loads(dd):
    _write(dd.user_config, {"nickname": "lazy"})
    loader = dd.loader()
    assert loader._config is None
    cfg = loader.config  # triggers lazy load
    assert cfg.nickname == "lazy"
    assert loader._config is cfg
