"""Configuration foundation contracts."""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from plugins.DicePP.core.config.loader import (
    ConfigLoader,
    ConfigValidationError,
    canonicalize_config_layer,
    save_config_file,
)
from plugins.DicePP.core.config.pydantic_models import BotConfig, UserConfig


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class _DataDir:
    def __init__(self, tmp: Path):
        self.root = tmp
        (tmp / "bots").mkdir(parents=True, exist_ok=True)

    @property
    def user_config(self) -> Path:
        return self.root / "user.json"

    def account_cfg(self, account: str) -> Path:
        return self.root / "bots" / f"{account}.json"

    def loader(self, account: str = "test_account") -> ConfigLoader:
        return ConfigLoader(str(self.root), account)


@pytest.fixture
def dd(tmp_path):
    return _DataDir(tmp_path)


def test_load_empty_dir_uses_defaults_without_creating_config_files(dd):
    loader = dd.loader()
    cfg = loader.load()

    assert cfg.master == BotConfig().master
    assert cfg.friend_request_token == BotConfig().friend_request_token
    assert cfg.accept_group_invites is BotConfig().accept_group_invites
    assert loader.user_config == UserConfig()
    assert not dd.user_config.exists()
    assert not dd.account_cfg("test_account").exists()


def test_user_json_is_independent_and_empty_for_this_batch(dd):
    _write(dd.user_config, {})
    _write(dd.account_cfg("bot1"), {"master": "account"})

    loader = dd.loader("bot1")

    assert loader.load().master == "account"
    assert loader.user_config == UserConfig()


@pytest.mark.parametrize(
    "path_kind, payload",
    [
        ("user", {"nickname": "bot field"}),
        ("user", {"persona_ai": {"enabled": True}}),
        ("bot", {"unknown": True}),
        ("bot", {"master": ["old-master"]}),
        ("bot", {"admin": ["old-admin"]}),
        ("bot", {"friend_token": "old-token"}),
        ("bot", {"group_invite": False}),
        ("bot", {"nickname": "old-name"}),
        ("bot", {"agreement": "old-agreement"}),
        ("bot", {"command_split": "\n"}),
        ("bot", {"bot_default_enable": False}),
        ("bot", {"white_list_user": []}),
        ("bot", {"white_list_group": []}),
        ("bot", {"data_expire": False}),
        ("bot", {"user_expire_day": 60}),
        ("bot", {"group_expire_day": 14}),
        ("bot", {"group_expire_warning_time": 1}),
    ],
)
def test_each_config_file_rejects_unknown_fields_and_wrong_types(
    dd, path_kind, payload
):
    path = dd.user_config if path_kind == "user" else dd.account_cfg("bot1")
    _write(path, payload)

    with pytest.raises(ConfigValidationError):
        dd.loader("bot1").load()


def test_account_sparse_nested_mapping_preserves_default_siblings(dd):
    _write(
        dd.account_cfg("bot1"),
        {"persona_ai": {"providers": {"minimax": {"api_key": "test-key"}}}},
    )

    cfg = dd.loader("bot1").load()

    assert cfg.persona_ai.providers["minimax"].api_key == "test-key"
    assert cfg.persona_ai.providers["minimax"].base_url == "https://api.minimaxi.com/v1"
    assert cfg.persona_ai.providers["deepseek"].models


def test_explicit_empty_mapping_overrides_non_empty_default_and_roundtrips(dd):
    path = dd.account_cfg("bot1")
    save_config_file(
        path,
        {"persona_ai": {"providers": {}}},
        model_type=BotConfig,
    )

    assert _read(path) == {"persona_ai": {"providers": {}}}
    assert dd.loader("bot1").load().persona_ai.providers == {}


def test_saving_default_values_removes_nested_overrides(dd):
    path = dd.account_cfg("bot1")
    save_config_file(
        path,
        {"persona_ai": {"providers": {"deepseek": {"api_key": "secret"}}}},
        model_type=BotConfig,
    )
    assert _read(path) == {
        "persona_ai": {"providers": {"deepseek": {"api_key": "secret"}}}
    }

    save_config_file(
        path,
        {"persona_ai": {"providers": {"deepseek": {"api_key": ""}}}},
        model_type=BotConfig,
    )
    assert _read(path) == {}


def test_identity_environment_overrides_are_ignored(dd):
    _write(dd.account_cfg("bot1"), {"master": "file-master"})

    with patch.dict(
        os.environ,
        {
            "DICE_MASTER": "env-master",
            "DICE_ADMIN": "env-admin",
            "DICE_NICKNAME": "env-name",
        },
    ):
        cfg = dd.loader("bot1").load()

    assert cfg.master == "file-master"
    assert _read(dd.account_cfg("bot1")) == {"master": "file-master"}


def test_environment_master_override_does_not_populate_master(dd):
    with patch.dict(os.environ, {"DICE_MASTER": "one,two"}):
        cfg = dd.loader("bot1").load()

    assert cfg.master == ""


def test_command_split_environment_override_is_ignored(dd):
    with patch.dict(os.environ, {"DICE_COMMAND_SPLIT": "\n"}):
        cfg = dd.loader("bot1").load()

    assert "command_split" not in BotConfig.model_fields
    assert cfg == BotConfig()


def test_malformed_json_is_rejected_without_rewriting(dd):
    dd.account_cfg("bot1").write_text("BAD JSON", encoding="utf-8")

    with pytest.raises(ConfigValidationError):
        dd.loader("bot1").load()

    assert dd.account_cfg("bot1").read_text(encoding="utf-8") == "BAD JSON"


def test_config_property_lazy_loads(dd):
    _write(dd.account_cfg("bot1"), {"master": "lazy"})
    loader = dd.loader("bot1")

    assert loader._config is None
    cfg = loader.config

    assert cfg.master == "lazy"
    assert loader.config is cfg


def test_canonicalize_requires_an_explicit_schema_model():
    with pytest.raises(TypeError):
        canonicalize_config_layer({})
