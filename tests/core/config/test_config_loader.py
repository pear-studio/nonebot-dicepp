"""
Tests for core/config/loader.py

Covers:
  9.1  Hierarchical loading (global defaults < user overrides < account < env vars)
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


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _assert_json_subset(expected, actual) -> None:
    if isinstance(expected, dict):
        assert isinstance(actual, dict)
        for key, value in expected.items():
            assert key in actual, f"canonical rewrite dropped {key!r}"
            _assert_json_subset(value, actual[key])
        return
    if isinstance(expected, list):
        assert isinstance(actual, list)
        assert len(actual) == len(expected)
        for expected_item, actual_item in zip(expected, actual):
            _assert_json_subset(expected_item, actual_item)
        return
    assert actual == expected


class _DataDir:
    """Thin wrapper around a tmp directory mimicking the config/ layout."""

    def __init__(self, tmp: Path):
        self.root = tmp
        (tmp / "bots").mkdir(parents=True, exist_ok=True)
        (tmp / "personas").mkdir(parents=True, exist_ok=True)

    @property
    def global_cfg(self) -> Path:
        return self.root / "global.json"

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


def test_user_config_override_global_config(dd):
    _write(dd.global_cfg, {"chat_interval": 99})
    _write(dd.user_config, {"nickname": "user_nick"})
    cfg = dd.loader().load()
    assert cfg.chat_interval == 99
    assert cfg.nickname == "user_nick"


def test_account_config_overrides_user_config(dd):
    _write(dd.user_config, {"master": ["global_master"]})
    _write(dd.account_cfg("bot1"), {"master": ["account_master"]})
    cfg = dd.loader("bot1").load()
    assert cfg.master == ["account_master"]


def test_account_config_deep_merge_does_not_erase_siblings(dd):
    """Account sets nickname; global has chat_interval — both survive deep merge."""
    _write(dd.global_cfg, {"chat_interval": 99, "nickname": "global_nick"})
    _write(dd.account_cfg("bot1"), {"nickname": "account_nick"})
    cfg = dd.loader("bot1").load()
    assert cfg.chat_interval == 99
    assert cfg.nickname == "account_nick"


def test_persona_fallback_when_missing(dd):
    _write(dd.global_cfg, {"persona": "nonexistent"})
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
    """Full priority stack: env > account > user > global."""
    _write(dd.global_cfg, {"nickname": "global"})
    _write(dd.user_config, {"nickname": "secret"})
    _write(dd.account_cfg("bot1"), {"nickname": "account"})
    with patch.dict(os.environ, {"DICE_NICKNAME": "env"}):
        cfg = dd.loader("bot1").load()
    assert cfg.nickname == "env"


def test_priority_without_env(dd):
    _write(dd.global_cfg, {"nickname": "global"})
    _write(dd.user_config, {"nickname": "secret"})
    _write(dd.account_cfg("bot1"), {"nickname": "account"})
    cfg = dd.loader("bot1").load()
    assert cfg.nickname == "account"


def test_priority_without_account(dd):
    _write(dd.global_cfg, {"nickname": "global"})
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


def test_malformed_global_config_ignored(dd):
    malformed = "{ this is not json }"
    dd.global_cfg.write_text(malformed, encoding="utf-8")
    cfg = dd.loader().load()
    assert cfg.mode.default == "DND5E2024"
    assert cfg.chat_interval == 20
    assert dd.global_cfg.read_text(encoding="utf-8") == malformed


def test_malformed_account_config_ignored(dd):
    _write(dd.account_cfg("bot1"), {})  # write empty first to create file
    malformed = "BAD JSON"
    dd.account_cfg("bot1").write_text(malformed, encoding="utf-8")
    cfg = dd.loader("bot1").load()
    assert cfg.mode.default == "DND5E2024"
    assert cfg.chat_interval == 20
    assert dd.account_cfg("bot1").read_text(encoding="utf-8") == malformed


# ── 9.2: Pydantic validation errors ──────────────────────────────────────────


def test_critical_invalid_type_raises_config_validation_error(dd):
    _write(dd.global_cfg, {"master": "not-a-list"})
    with pytest.raises(ConfigValidationError):
        dd.loader().load()


def test_valid_bool_string_accepted(dd):
    """String boolean 'true' is coerced to True by Pydantic."""
    _write(dd.global_cfg, {"persona_ai": {"enabled": 'true'}})
    cfg = dd.loader().load()
    assert cfg.persona_ai.enabled is True


# ── canonical rewrite ────────────────────────────────────────────────────────


def test_global_config_rewrites_new_default_fields(dd):
    _write(dd.global_cfg, {"chat_interval": 99})

    cfg = dd.loader().load()
    saved = _read(dd.global_cfg)

    assert cfg.chat_interval == 99
    assert saved["chat_interval"] == 99
    assert saved["roll"]["enable"] is True
    assert saved["persona_ai"]["max_history_turns"] == 10


def test_global_config_does_not_default_missing_critical_fields(dd):
    _write(dd.global_cfg, {"chat_interval": 99})

    dd.loader().load()
    saved = _read(dd.global_cfg)

    assert saved["chat_interval"] == 99
    assert saved["roll"]["enable"] is True
    assert "master" not in saved
    assert "admin" not in saved
    assert "friend_token" not in saved
    assert "white_list_group" not in saved
    assert "white_list_user" not in saved
    assert "character_path" not in saved["persona_ai"]
    assert "api_url" not in saved["dicehub"]
    assert "api_key" not in saved["dicehub"]
    assert "upload_endpoint" not in saved["log"]
    assert "upload_token" not in saved["log"]
    assert "data_path" not in saved["deck"]


def test_canonical_rewrite_drops_unknown_ordinary_fields(dd):
    _write(dd.global_cfg, {
        "chat_interval": 99,
        "old_plain_field": True,
        "persona_ai": {
            "enabled": True,
            "max_short_term_chars": 1500,
        },
    })

    dd.loader().load()
    saved = _read(dd.global_cfg)

    assert "old_plain_field" not in saved
    assert "max_short_term_chars" not in saved["persona_ai"]
    assert saved["persona_ai"]["enabled"] is True


def test_canonical_rewrite_preserves_comment_metadata(dd):
    _write(dd.global_cfg, {
        "_comment": "top-level guidance",
        "chat_interval": 99,
        "persona_ai": {
            "_comment_persona": "nested guidance",
            "enabled": True,
        },
    })

    with patch("core.config.loader.logger.warning") as warning:
        cfg = dd.loader().load()
    saved = _read(dd.global_cfg)

    assert saved["_comment"] == "top-level guidance"
    assert saved["persona_ai"]["_comment_persona"] == "nested guidance"
    assert "_comment" not in cfg.model_dump()
    assert "_comment_persona" not in cfg.persona_ai.model_dump()
    assert not any(
        call.args
        and str(call.args[0]).startswith("[Config] Dropping unknown field")
        for call in warning.call_args_list
    )


def test_shipped_global_config_has_no_fields_dropped_by_canonical_rewrite(dd):
    project_root = Path(__file__).resolve().parents[3]
    shipped = _read(project_root / "config" / "global.json")
    _write(dd.global_cfg, shipped)

    dd.loader().load()

    _assert_json_subset(shipped, _read(dd.global_cfg))


def test_canonical_rewrite_defaultizes_recoverable_ordinary_field_error(dd):
    _write(dd.global_cfg, {"chat_interval": "not-a-number"})

    cfg = dd.loader().load()
    saved = _read(dd.global_cfg)

    assert cfg.chat_interval == 20
    assert saved["chat_interval"] == 20


def test_canonical_rewrite_keeps_critical_field_errors_hard(dd):
    original = {"master": "not-a-list"}
    _write(dd.global_cfg, original)

    with pytest.raises(ConfigValidationError):
        dd.loader().load()

    assert _read(dd.global_cfg) == original
    assert list(dd.root.rglob("*.bak")) == []


def test_unknown_fields_with_critical_sounding_substrings_are_not_rejected(dd):
    """Fields like 'executive_summary' contain markers ('exec') but are NOT
    critical because the marker must appear as a whole underscore-delimited
    token.

    'evaluation_url' IS critical ('url' is a whole token) and tested
    separately.
    """
    _write(dd.global_cfg, {
        "chat_interval": 42,
        "executive_summary": "should be dropped not rejected",
        "pathological_case": 123,
    })

    cfg = dd.loader().load()

    assert cfg.chat_interval == 42
    saved = _read(dd.global_cfg)
    assert "executive_summary" not in saved
    assert "pathological_case" not in saved


def test_unknown_fields_with_url_token_are_still_critical(dd):
    """'url' as a standalone underscore-delimited token is critical
    (e.g. 'evaluation_url' splits into ['evaluation', 'url'])."""
    _write(dd.global_cfg, {
        "chat_interval": 42,
        "evaluation_url": "https://example.com",
    })

    with pytest.raises(ConfigValidationError):
        dd.loader().load()


def test_unknown_fields_with_token_as_boundary_word_still_critical(dd):
    """'token' as a whole underscore-delimited token IS critical (e.g. 'my_token',
    'token_type')."""
    _write(dd.global_cfg, {
        "chat_interval": 42,
        "my_token": "secret-value",
    })

    with pytest.raises(ConfigValidationError):
        dd.loader().load()


def test_unknown_fields_are_dropped_without_crashing(dd):
    """Dropping an unknown non-critical field must not crash startup."""
    _write(dd.global_cfg, {
        "chat_interval": 42,
        "old_plain_field": True,
        "another_unknown": {"nested": "value"},
    })

    cfg = dd.loader().load()
    saved = _read(dd.global_cfg)

    assert cfg.chat_interval == 42
    assert "old_plain_field" not in saved
    assert "another_unknown" not in saved


def test_canonical_rewrite_does_not_write_env_overrides(dd):
    _write(dd.account_cfg("bot1"), {"nickname": "file_nick"})

    with patch.dict(os.environ, {"DICE_NICKNAME": "env_nick"}):
        cfg = dd.loader("bot1").load()

    assert cfg.nickname == "env_nick"
    assert _read(dd.account_cfg("bot1"))["nickname"] == "file_nick"
    assert list(dd.root.rglob("*.bak")) == []


def test_canonical_rewrite_keeps_user_and_account_layers_partial(dd):
    _write(dd.global_cfg, {"chat_interval": 44})
    _write(dd.user_config, {"nickname": "user_nick"})
    _write(dd.account_cfg("bot1"), {"master": ["account_master"]})

    cfg = dd.loader("bot1").load()

    assert cfg.chat_interval == 44
    assert cfg.nickname == "user_nick"
    assert cfg.master == ["account_master"]
    assert _read(dd.user_config) == {"nickname": "user_nick"}
    assert _read(dd.account_cfg("bot1")) == {"master": ["account_master"]}


def test_canonical_rewrite_keeps_nested_user_and_account_layers_partial(dd):
    _write(dd.user_config, {"persona_ai": {"enabled": True}})
    _write(dd.account_cfg("bot1"), {"roll": {"enable": False}})

    cfg = dd.loader("bot1").load()

    assert cfg.persona_ai.enabled is True
    assert cfg.roll.enable is False
    assert _read(dd.user_config) == {"persona_ai": {"enabled": True}}
    assert _read(dd.account_cfg("bot1")) == {"roll": {"enable": False}}


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

    _write(dd.global_cfg, {"master": "bad-type"})
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
    assert loader._config is cfg
