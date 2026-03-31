#!/usr/bin/env python3
import argparse
import asyncio
from contextlib import asynccontextmanager
import json
import os
import sys
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI

PROJECT_ROOT = Path(__file__).resolve().parent
PLUGIN_ROOT = PROJECT_ROOT / "src" / "plugins" / "DicePP"

if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from core.bot import Bot as DiceBot  # noqa: E402
from adapter.standalone_proxy import StandaloneClientProxy  # noqa: E402
from module.fastapi.api import dpp_api, bind_runtime  # noqa: E402
from utils.logger import dice_log  # noqa: E402


def _read_config_json() -> dict[str, Any]:
    config_path = PROJECT_ROOT / "config.json"
    if not config_path.exists():
        return {}
    try:
        return json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _resolve_value(cli_value: str | None, env_name: str, cfg: dict[str, Any], cfg_key: str, default: str = "") -> str:
    if cli_value:
        return str(cli_value).strip()
    env_val = os.getenv(env_name, "").strip()
    if env_val:
        return env_val
    cfg_val = str(cfg.get(cfg_key, "")).strip()
    if cfg_val:
        return cfg_val
    return default


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DicePP standalone runtime")
    parser.add_argument("--bot-id", dest="bot_id", default=None)
    parser.add_argument("--hub-url", dest="hub_url", default=None)
    parser.add_argument("--master-id", dest="master_id", default=None)
    parser.add_argument("--nickname", dest="nickname", default=None)
    parser.add_argument("--port", dest="port", type=int, default=None)
    return parser.parse_args()


def resolve_runtime_config(args: argparse.Namespace) -> tuple[dict[str, str], int]:
    config_json = _read_config_json()
    bot_id = _resolve_value(args.bot_id, "BOT_ID", config_json, "bot_id", "999999999")
    hub_url = _resolve_value(args.hub_url, "HUB_URL", config_json, "hub_url", "")
    master_id = _resolve_value(args.master_id, "MASTER_ID", config_json, "master_id", "")
    nickname = _resolve_value(args.nickname, "NICKNAME", config_json, "nickname", "")
    port_str = _resolve_value(str(args.port) if args.port else None, "PORT", config_json, "port", "8080")
    port = int(port_str)
    heartbeat_interval = _resolve_value(None, "HUB_HEARTBEAT_INTERVAL", config_json, "hub_heartbeat_interval", "180")
    runtime_config = {
        "bot_id": bot_id,
        "hub_url": hub_url,
        "master_id": master_id,
        "nickname": nickname,
        "heartbeat_interval": heartbeat_interval,
    }
    return runtime_config, port


def create_app(runtime_cfg: dict[str, str]) -> FastAPI:
    bot_id = runtime_cfg["bot_id"]
    hub_url = runtime_cfg["hub_url"]
    master_id = runtime_cfg["master_id"]
    nickname = runtime_cfg["nickname"]
    heartbeat_interval = runtime_cfg["heartbeat_interval"]

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        bot = DiceBot(bot_id)
        proxy = StandaloneClientProxy()
        bot.set_client_proxy(proxy)
        await bot.delay_init_command()

        if hub_url:
            await bot.hub_manager.set_api_url(hub_url)
        if master_id:
            await bot.hub_manager.set_master_id(master_id)
        if nickname:
            await bot.hub_manager.set_nickname(nickname)
        if heartbeat_interval:
            await bot.db.hub_set("heartbeat_interval", str(heartbeat_interval))
            await bot.hub_manager.load_config()

        # Optional auto-registration with exponential backoff.
        # Unified policy: 6 attempts, wait sequence 2s/4s/8s/10s/10s (total wait 34s).
        if hub_url:
            waits = [2, 4, 8, 10, 10]
            max_attempts = len(waits) + 1
            for attempt in range(max_attempts):
                try:
                    await bot.hub_manager.register()
                    break
                except Exception as exc:
                    error_type = exc.__class__.__name__
                    if attempt >= max_attempts - 1:
                        dice_log(
                            f"[Standalone][HubRegister][ERROR] hub_url={hub_url} bot_id={bot_id} "
                            f"attempt={attempt + 1}/{max_attempts} error_type={error_type} detail={exc}"
                        )
                        break
                    wait_s = waits[attempt]
                    dice_log(
                        f"[Standalone][HubRegister][WARN] hub_url={hub_url} bot_id={bot_id} "
                        f"attempt={attempt + 1}/{max_attempts} error_type={error_type} next_retry_in={wait_s}s detail={exc}"
                    )
                    await asyncio.sleep(wait_s)

        bind_runtime(bot, proxy)
        try:
            yield
        finally:
            await bot.shutdown_async()

    app = FastAPI(lifespan=lifespan)
    app.mount("/dpp", dpp_api)
    return app


def main() -> None:
    args = parse_args()
    runtime_cfg, port = resolve_runtime_config(args)
    app = create_app(runtime_cfg)
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()

