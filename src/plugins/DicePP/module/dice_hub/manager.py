from typing import Dict, List, Optional, Any
import datetime

from core.bot import Bot
from core.config import BOT_VERSION
from utils.time import get_current_date_raw

from module.dice_hub.api_client import HubAPIClient, HubAPIError

LIST_REFRESH_INTERVAL = 600
HUB_KEY_API_URL = "api_url"
HUB_KEY_API_KEY = "api_key"
HUB_KEY_NICKNAME = "nickname"
HUB_KEY_MASTER_ID = "master_id"


class HubManager:
    def __init__(self, bot: Bot):
        self.bot = bot
        self.api_client: Optional[HubAPIClient] = None
        self._is_online = False
        self._online_robots_cache: List[Dict[str, Any]] = []
        self._last_list_refresh: Optional[datetime.datetime] = None
        self._config_cache: Dict[str, str] = {}

    async def load_config(self) -> None:
        api_url = await self.bot.db.hub_get(HUB_KEY_API_URL) or ""
        api_key = await self.bot.db.hub_get(HUB_KEY_API_KEY) or ""
        nickname = await self.bot.db.hub_get(HUB_KEY_NICKNAME) or ""
        master_id = await self.bot.db.hub_get(HUB_KEY_MASTER_ID) or ""
        self._config_cache = {
            HUB_KEY_API_URL: api_url,
            HUB_KEY_API_KEY: api_key,
            HUB_KEY_NICKNAME: nickname,
            HUB_KEY_MASTER_ID: master_id,
        }

    async def set_api_url(self, api_url: str) -> None:
        self._config_cache[HUB_KEY_API_URL] = (api_url or "").strip()
        await self.bot.db.hub_set(HUB_KEY_API_URL, self._config_cache[HUB_KEY_API_URL])

    async def set_api_key(self, api_key: str) -> None:
        self._config_cache[HUB_KEY_API_KEY] = (api_key or "").strip()
        await self.bot.db.hub_set(HUB_KEY_API_KEY, self._config_cache[HUB_KEY_API_KEY])

    async def set_nickname(self, nickname: str) -> None:
        self._config_cache[HUB_KEY_NICKNAME] = (nickname or "").strip()
        await self.bot.db.hub_set(HUB_KEY_NICKNAME, self._config_cache[HUB_KEY_NICKNAME])

    async def set_master_id(self, master_id: str) -> None:
        self._config_cache[HUB_KEY_MASTER_ID] = (master_id or "").strip()
        await self.bot.db.hub_set(HUB_KEY_MASTER_ID, self._config_cache[HUB_KEY_MASTER_ID])

    def get_api_url(self) -> str:
        return self._config_cache.get(HUB_KEY_API_URL, "")

    def get_api_key(self) -> str:
        return self._config_cache.get(HUB_KEY_API_KEY, "")

    def get_nickname(self) -> str:
        nickname = self._config_cache.get(HUB_KEY_NICKNAME, "")
        return nickname or f"Bot_{self.bot.account}"

    def get_master_id(self) -> str:
        master_id = self._config_cache.get(HUB_KEY_MASTER_ID, "")
        if master_id:
            return master_id
        masters = self.bot.config.master
        return masters[0] if masters else ""

    def is_configured(self) -> bool:
        return bool(self.get_api_url())

    def is_registered(self) -> bool:
        return bool(self.get_api_key())

    def get_client(self) -> Optional[HubAPIClient]:
        if not self.is_configured():
            return None
        return HubAPIClient(self.get_api_url(), self.get_api_key())

    async def register(self) -> Dict[str, Any]:
        if not self.is_configured():
            raise HubAPIError("请先配置 DiceHub API 地址")

        client = HubAPIClient(
            self.get_api_url(),
            None,
        )
        result = await client.register(
            bot_id=self.bot.account,
            nickname=self.get_nickname(),
            master_id=self.get_master_id(),
            version=BOT_VERSION,
        )

        api_key = result.get("api_key")
        if api_key:
            await self.set_api_key(api_key)

        self.api_client = HubAPIClient(self.get_api_url(), api_key)
        return result

    async def get_online_robots(self) -> List[Dict[str, Any]]:
        if not self.is_registered():
            return []

        current_time = get_current_date_raw()
        if (
            self._online_robots_cache
            and self._last_list_refresh
            and (current_time - self._last_list_refresh).total_seconds()
            < LIST_REFRESH_INTERVAL
        ):
            return self._online_robots_cache

        client = self.get_client()
        if not client:
            return []

        try:
            robots = await client.get_robots(online_only=True)
            self._online_robots_cache = robots
            self._last_list_refresh = current_time
            return robots
        except HubAPIError:
            return self._online_robots_cache

    def generate_list_message(self) -> str:
        if not self._online_robots_cache:
            return "暂无在线机器人"

        lines = []
        for bot_info in self._online_robots_cache:
            nickname = bot_info.get("nickname", "未知")
            bot_id = bot_info.get("bot_id", "")
            version = bot_info.get("version", "")
            is_online = bot_info.get("is_online", False)
            status = "在线" if is_online else "离线"
            lines.append(f"- {nickname} (账号: {bot_id}, 版本: {version}) [{status}]")

        return "\n".join(lines)

    def generate_status_message(self) -> str:
        if not self.is_registered():
            return "状态: 未注册\n请使用 .hub register 注册"

        api_key = self.get_api_key()
        masked_key = api_key[:8] + "..." if len(api_key) > 8 else "***"

        lines = [
            f"状态: {'已注册' if self.is_registered() else '未注册'}",
            f"API Key: {masked_key}",
            f"在线状态: {'在线' if self._is_online else '离线'}",
        ]

        return "\n".join(lines)

