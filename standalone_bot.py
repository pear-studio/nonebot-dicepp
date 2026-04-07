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
from adapter.web_chat_adapter import WebChatAdapter  # noqa: E402
from adapter.web_chat_proxy import WebChatProxy  # noqa: E402
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


def _resolve_bool(cli_value: str | None, env_name: str, cfg: dict[str, Any], cfg_key: str, default: bool = False) -> bool:
    raw = _resolve_value(cli_value, env_name, cfg, cfg_key, str(default).lower())
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


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
    webchat_hub_url = _resolve_value(None, "WEBCHAT_HUB_URL", config_json, "webchat_hub_url", "")
    webchat_api_key = _resolve_value(None, "WEBCHAT_API_KEY", config_json, "webchat_api_key", "")
    webchat_enabled = _resolve_bool(None, "WEBCHAT_ENABLED", config_json, "webchat_enabled", False)
    runtime_config = {
        "bot_id": bot_id,
        "hub_url": hub_url,
        "master_id": master_id,
        "nickname": nickname,
        "heartbeat_interval": heartbeat_interval,
        "webchat_hub_url": webchat_hub_url,
        "webchat_api_key": webchat_api_key,
        "webchat_enabled": "true" if webchat_enabled else "false",
    }
    return runtime_config, port


def create_app(runtime_cfg: dict[str, str]) -> FastAPI:
    bot_id = runtime_cfg["bot_id"]
    hub_url = runtime_cfg["hub_url"]
    master_id = runtime_cfg["master_id"]
    nickname = runtime_cfg["nickname"]
    heartbeat_interval = runtime_cfg["heartbeat_interval"]
    webchat_hub_url = runtime_cfg.get("webchat_hub_url", "").strip()
    webchat_api_key = runtime_cfg.get("webchat_api_key", "").strip()
    webchat_enabled = runtime_cfg.get("webchat_enabled", "false").lower() == "true"

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        bot = DiceBot(bot_id)
        # 初始始终创建 StandaloneClientProxy（轻量级，无需认证）
        standalone_proxy = StandaloneClientProxy()
        active_proxy = standalone_proxy
        webchat_adapter = None
        webchat_active = False

        bot.set_client_proxy(active_proxy)
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
        registration_success = False
        if hub_url:
            waits = [2, 4, 8, 10, 10]
            max_attempts = len(waits) + 1
            for attempt in range(max_attempts):
                try:
                    await bot.hub_manager.register()
                    registration_success = True
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

        # 注册完成后，根据配置启用 WebChat
        if webchat_enabled and webchat_hub_url:
            # 确定 api_key 来源（按优先级）
            final_api_key = ""
            if webchat_api_key:
                # 优先使用显式提供的 WEBCHAT_API_KEY
                final_api_key = webchat_api_key
                dice_log("[Standalone] Using explicit WEBCHAT_API_KEY for WebChat")
            elif registration_success:
                # 注册成功，从 hub_manager 获取 api_key
                final_api_key = bot.hub_manager.get_api_key()
                if final_api_key:
                    dice_log("[Standalone] Using api_key from hub registration for WebChat")

            if final_api_key:
                try:
                    webchat_adapter = WebChatAdapter(webchat_hub_url, final_api_key)
                    active_proxy = WebChatProxy(webchat_adapter)
                    webchat_active = True
                    await webchat_adapter.start(bot)
                    bot.set_client_proxy(active_proxy)
                    dice_log(f"[Standalone] WebChat enabled for hub={webchat_hub_url}")
                except Exception as exc:
                    dice_log(f"[Standalone][WARN] WebChat initialization failed: {exc}")
                    dice_log("[Standalone] Continuing in standalone mode")
                    active_proxy = standalone_proxy
                    webchat_active = False
            else:
                dice_log("[Standalone][WARN] WEBCHAT_ENABLED is true but no api_key available")
                dice_log("[Standalone] Provide WEBCHAT_API_KEY or ensure hub registration succeeds")

        bind_runtime(bot, active_proxy, webchat_enabled=webchat_active)
        try:
            yield
        finally:
            if webchat_adapter is not None:
                await webchat_adapter.close()
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

