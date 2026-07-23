from __future__ import annotations

import json
from pathlib import Path

import pytest

from plugins.DicePP.core.config.loader import ConfigLoader


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("layer", ["global.json", "user.json", "bots/bot1.json"])
def test_legacy_log_upload_config_migrates_and_rewrites_each_layer(
    tmp_path: Path,
    layer: str,
):
    path = tmp_path / layer
    _write(
        path,
        {
            "log": {
                "upload_enable": False,
                "upload_endpoint": "https://legacy.test/api/log",
                "upload_token": "legacy-token",
            }
        },
    )
    loader = ConfigLoader(str(tmp_path), "bot1")

    config = loader.load()
    first_rewrite = path.read_bytes()
    loader.reload()

    assert config.log.web.endpoint == "https://legacy.test/api/log"
    assert config.log.web.token == "legacy-token"
    assert not hasattr(config.log, "upload_enable")
    saved_log = _read(path)["log"]
    assert saved_log["web"]["endpoint"] == "https://legacy.test/api/log"
    assert saved_log["web"]["token"] == "legacy-token"
    assert not {"upload_enable", "upload_endpoint", "upload_token"}.intersection(saved_log)
    assert path.read_bytes() == first_rewrite


def test_new_web_config_wins_over_legacy_and_defaults_are_opt_in(tmp_path: Path):
    path = tmp_path / "global.json"
    _write(
        path,
        {
            "log": {
                "upload_endpoint": "https://legacy.test/api/log",
                "upload_token": "legacy-token",
                "web": {
                    "provider": "dice_log_v105",
                    "endpoint": "https://new.test/api/log",
                    "token": "new-token",
                    "timeout_seconds": 9,
                },
            }
        },
    )

    config = ConfigLoader(str(tmp_path), "bot1").load()

    assert config.log.web.endpoint == "https://new.test/api/log"
    assert config.log.web.token == "new-token"
    assert config.log.web.timeout_seconds == 9
    empty_config = ConfigLoader(str(tmp_path / "empty"), "bot2").load()
    assert empty_config.log.web.provider == "dice_log_v105"
    assert empty_config.log.web.endpoint == ""
    assert empty_config.log.web.token == ""
    assert empty_config.log.web.timeout_seconds == 15
