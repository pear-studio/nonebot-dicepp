#!/usr/bin/env python3
"""
DicePP standalone runtime.

Configuration priority (high → low):
  1. CLI arguments  (--hub-url, --master-id, --nickname, --port)
  2. Environment variables  (BOT_ID, HUB_URL, MASTER_ID, NICKNAME, PORT, DICE_*)
  3. config/bots/{bot_id}.json
  4. config/secrets.json
  5. config/global.json

bot_id MUST be provided via --bot-id or BOT_ID env var; it is not read from JSON.
port   is standalone-only and is never stored in JSON config.
"""
import argparse
import asyncio
from contextlib import asynccontextmanager
import os
import sys
from pathlib import Path

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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DicePP standalone runtime")
    parser.add_argument("--bot-id", dest="bot_id", default=None,
                        help="Bot account ID (required; also via BOT_ID env var)")
    parser.add_argument("--hub-url", dest="hub_url", default=None,
                        help="DiceHub API URL override")
    parser.add_argument("--master-id", dest="master_id", default=None,
                        help="Master user ID override (added to master list)")
    parser.add_argument("--nickname", dest="nickname", default=None,
                        help="Bot nickname override")
    parser.add_argument("--port", dest="port", type=int, default=None,
                        help="HTTP listen port (default: 8080)")
    return parser.parse_args()


def _resolve(cli_value, env_name: str, default: str = "") -> str:
    if cli_value:
        return str(cli_value).strip()
    env_val = os.getenv(env_name, "").strip()
    if env_val:
        return env_val
    return default


def inject_cli_env_overrides(args: argparse.Namespace) -> None:
    """
    Push CLI / env-var overrides into environment so ConfigLoader picks them up
    via the DICE_* env-var layer.
    """
    if args.hub_url:
        os.environ.setdefault("DICE_DICEHUB_API_URL", args.hub_url.strip())
    hub_url_env = os.getenv("HUB_URL", "").strip()
    if hub_url_env:
        os.environ.setdefault("DICE_DICEHUB_API_URL", hub_url_env)

    if args.nickname:
        os.environ.setdefault("DICE_NICKNAME", args.nickname.strip())
    nickname_env = os.getenv("NICKNAME", "").strip()
    if nickname_env:
        os.environ.setdefault("DICE_NICKNAME", nickname_env)

    # Master ID from CLI or env
    if args.master_id:
        os.environ.setdefault("DICE_MASTER", args.master_id.strip())
    master_env = os.getenv("MASTER_ID", "").strip()
    if master_env:
        os.environ.setdefault("DICE_MASTER", master_env)

    # WebChat Hub URL from env (for WebSocket connection)
    webchat_url_env = os.getenv("WEBCHAT_HUB_URL", "").strip()
    if webchat_url_env:
        os.environ.setdefault("DICE_DICEHUB_WEBCHAT_URL", webchat_url_env)


def create_app(bot_id: str) -> FastAPI:

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        bot = DiceBot(bot_id)
        standalone_proxy = StandaloneClientProxy()
        active_proxy = standalone_proxy
        webchat_adapter = None
        webchat_active = False

        bot.set_client_proxy(active_proxy)
        await bot.delay_init_command()

        cfg = bot.config

        # Apply hub config from BotConfig (already loaded by ConfigLoader)
        if cfg.dicehub.api_url:
            await bot.hub_manager.set_api_url(cfg.dicehub.api_url)
        if cfg.master:
            await bot.hub_manager.set_master_id(cfg.master[0])
        if cfg.nickname:
            await bot.hub_manager.set_nickname(cfg.nickname)
        await bot.hub_manager.load_config()

        # Optional hub registration with exponential backoff
        registration_success = False
        if cfg.dicehub.api_url:
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
                            f"[Standalone][HubRegister][ERROR] hub_url={cfg.dicehub.api_url} bot_id={bot_id} "
                            f"attempt={attempt + 1}/{max_attempts} error_type={error_type} detail={exc}"
                        )
                        break
                    wait_s = waits[attempt]
                    dice_log(
                        f"[Standalone][HubRegister][WARN] hub_url={cfg.dicehub.api_url} bot_id={bot_id} "
                        f"attempt={attempt + 1}/{max_attempts} error_type={error_type} next_retry_in={wait_s}s detail={exc}"
                    )
                    await asyncio.sleep(wait_s)

        # Optional WebChat via DiceHub - connect in background, do not block startup
        # Priority: WEBCHAT_HUB_URL env var > cfg.dicehub.webchat_url > cfg.dicehub.api_url
        webchat_hub_url = os.getenv("WEBCHAT_HUB_URL", "").strip()
        if not webchat_hub_url:
            webchat_hub_url = cfg.dicehub.webchat_url or cfg.dicehub.api_url
        if webchat_hub_url:
            final_api_key = cfg.dicehub.api_key
            if not final_api_key and registration_success:
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
                    dice_log(f"[Standalone] WebChat started for hub={webchat_hub_url}")
                except Exception as exc:
                    dice_log(f"[Standalone][WARN] WebChat initialization failed, running without WebChat: {exc}")
                    active_proxy = standalone_proxy
                    webchat_active = False
            else:
                dice_log("[Standalone][WARN] DiceHub URL configured but no api_key available, WebChat disabled")
        else:
            dice_log("[Standalone] No DiceHub URL configured, running in standalone mode")

        bind_runtime(bot, active_proxy, webchat_enabled=webchat_active)
        try:
            yield
        finally:
            if webchat_adapter is not None:
                await webchat_adapter.close()
            await bot.shutdown_async()

    app = FastAPI(lifespan=lifespan)

    # 过滤健康检查访问日志, 只在出错时记录
    @app.middleware("http")
    async def filter_health_logs(request, call_next):
        response = await call_next(request)
        # 对 /dpp/ 健康检查端点, 200 响应不打日志
        if request.url.path == "/dpp/" and request.method == "GET" and response.status_code == 200:
            return response
        return response

    app.mount("/dpp", dpp_api)
    return app


def main() -> None:
    args = parse_args()

    bot_id = _resolve(args.bot_id, "BOT_ID", "999999999")
    port = int(_resolve(str(args.port) if args.port else None, "PORT", "8080"))
    master_id = _resolve(args.master_id, "MASTER_ID", "")

    # Push CLI overrides into env so ConfigLoader sees them via DICE_* layer
    inject_cli_env_overrides(args)

    app = create_app(bot_id)
    uvicorn.run(app, host="0.0.0.0", port=port, access_log=False)


if __name__ == "__main__":
    main()
