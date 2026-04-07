from typing import Optional, Dict, Any, List
import aiohttp
import asyncio

from utils.logger import dice_log

DEFAULT_TIMEOUT = 10
DEFAULT_RETRY = 3


class HubAPIError(Exception):
    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class HubAPIClient:
    def __init__(self, base_url: str, api_key: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT)
        dice_log(f"[DiceHub] 初始化 API 客户端: base_url={self.base_url}, api_key={'已设置' if api_key else '未设置'}")

    def _get_headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    async def _request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict[str, Any]] = None,
        retry: int = DEFAULT_RETRY,
    ) -> Dict[str, Any]:
        url = f"{self.base_url}{endpoint}"
        last_error = None

        dice_log(f"[DiceHub] 发起请求: {method} {url}, data={data}")

        for attempt in range(retry):
            try:
                async with aiohttp.ClientSession(timeout=self.timeout) as session:
                    async with session.request(
                        method, url, json=data, headers=self._get_headers()
                    ) as response:
                        content = await response.json()
                        dice_log(f"[DiceHub] 响应: status={response.status}, content={content}")
                        if response.status >= 400:
                            raise HubAPIError(
                                content.get("error", "Unknown error"),
                                status_code=response.status,
                            )
                        return content
            except aiohttp.ClientError as e:
                last_error = e
                dice_log(f"[DiceHub] 请求失败 (尝试 {attempt + 1}/{retry}): {e}")
                if attempt < retry - 1:
                    await asyncio.sleep(1 * (attempt + 1))
                continue

        dice_log(f"[DiceHub] 请求最终失败: {last_error}")
        raise HubAPIError(f"Request failed: {last_error}")

    async def register(
        self,
        bot_id: str,
        nickname: str,
        master_id: str,
        version: str,
    ) -> Dict[str, Any]:
        data = {
            "bot_id": bot_id,
            "nickname": nickname,
            "master_id": master_id,
            "version": version,
        }
        dice_log(f"[DiceHub] 注册机器人: bot_id={bot_id}, nickname={nickname}, master_id={master_id}, version={version}")
        return await self._request("POST", "/api/bots/register/", data)

    async def get_robots(self, online_only: bool = False) -> List[Dict[str, Any]]:
        params = ""
        if online_only:
            params = "?online_only=true"
        dice_log(f"[DiceHub] 获取机器人列表: online_only={online_only}")
        result = await self._request("GET", f"/api/bots/{params}")
        bots = result.get("results", [])
        if not bots:
            bots = result.get("bots", [])
        dice_log(f"[DiceHub] 获取到 {len(bots)} 个机器人")
        return bots

    async def get_my_info(self) -> Dict[str, Any]:
        dice_log(f"[DiceHub] 获取自身信息")
        return await self._request("GET", "/api/bots/me/")

    async def unregister(self) -> Dict[str, Any]:
        dice_log(f"[DiceHub] 注销机器人")
        return await self._request("DELETE", "/api/bots/me/")
