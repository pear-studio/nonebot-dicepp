from __future__ import annotations

import json
from pathlib import Path

from plugins.DicePP.core.config.loader import ConfigLoader


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_current_log_web_config_loads_and_persists(tmp_path: Path):
    path = tmp_path / "bots" / "bot1.json"
    _write(
        path,
        {
            "log": {
                "web": {
                    "provider": "dice_log_v105",
                    "endpoint": "https://current.test/api/log",
                    "token": "current-token",
                    "timeout_seconds": 9,
                }
            }
        },
    )

    config = ConfigLoader(str(tmp_path), "bot1").load()
    saved = json.loads(path.read_text(encoding="utf-8"))

    assert config.log.web.provider == "dice_log_v105"
    assert config.log.web.endpoint == "https://current.test/api/log"
    assert config.log.web.token == "current-token"
    assert config.log.web.timeout_seconds == 9
    assert saved["log"]["web"]["endpoint"] == "https://current.test/api/log"
