"""ImageCache — 图片下载、缓存、读取、删除

供 _inbound_message_recorder 和 process_msg / look_at_past_image executor 共用。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import time
from typing import List, Optional, Protocol

import httpx

from utils.logger import dice_log


class ImageCacheProtocol(Protocol):
    """ImageCache 接口契约，供 store.py 类型声明使用。"""

    async def download_and_cache(
        self, image_meta: List[dict], *, force_emoji: bool = False,
    ) -> None: ...

    def read_cache(self, cache_hash: str) -> Optional[str]: ...

    def delete_cache(self, cache_hash: str) -> None: ...


class ImageCache:
    IMAGE_DIR = "data/persona_images"
    MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10MB

    @staticmethod
    def is_emoji(sub_type: str) -> bool:
        return sub_type == "1"

    async def download_and_cache(
        self, image_meta: List[dict], *, force_emoji: bool = False,
    ) -> None:
        """下载图片到缓存，回填 cache_hash。已缓存的跳过。sub_type=1（表情）默认跳过，
        force_emoji=True 时按需下载（供 LLM 工具主动请求查看表情时使用）。"""
        os.makedirs(self.IMAGE_DIR, exist_ok=True)

        async def _download_one(client: httpx.AsyncClient, entry: dict) -> None:
            if entry.get("cache_hash"):
                return  # 已缓存
            if ImageCache.is_emoji(entry.get("sub_type", "")) and not force_emoji:
                entry["download_status"] = "skipped_emoji"
                dice_log(
                    f"[ImageResolve] 表情按设计跳过: url={entry.get('url', '')}"
                    f"（force_emoji=True 可强制下载）"
                )
                return
            url = entry.get("url", "")
            if not url:
                return
            try:
                r = await client.get(url, follow_redirects=True, timeout=10)
                if r.status_code != 200:
                    entry["download_attempted_at"] = int(time.time())
                    dice_log(f"[ImageResolve] HTTP {r.status_code}: {url}")
                    return
                if len(r.content) > self.MAX_IMAGE_SIZE:
                    entry["download_attempted_at"] = int(time.time())
                    dice_log(f"[ImageResolve] 图片超限 ({len(r.content)} bytes): {url}")
                    return
                mime = r.headers.get("content-type", "image/png").split(";")[0]
                b64 = base64.b64encode(r.content).decode()
                data_url = f"data:{mime};base64,{b64}"
                h = hashlib.sha256(r.content).hexdigest()[:8]
                fpath = os.path.join(self.IMAGE_DIR, f"{h}.b64")
                with open(fpath, "w") as f:
                    f.write(data_url)
                entry["cache_hash"] = h
                entry["size"] = len(r.content)
                entry["download_attempted_at"] = int(time.time())
            except Exception as e:
                entry["download_attempted_at"] = int(time.time())
                dice_log(f"[ImageResolve] 下载失败 {type(e).__name__}: {url}")

        async with httpx.AsyncClient() as client:
            await asyncio.gather(*[_download_one(client, e) for e in image_meta])

        # 统计下载结果
        cached = sum(1 for e in image_meta if e.get("cache_hash") and not e.get("download_attempted_at"))
        downloaded = sum(1 for e in image_meta if e.get("cache_hash") and e.get("download_attempted_at"))
        failed = sum(1 for e in image_meta if not e.get("cache_hash") and e.get("download_attempted_at"))
        skipped = sum(1 for e in image_meta if e.get("download_status") == "skipped_emoji")
        dice_log(
            f"[ImageCache] download_and_cache: total={len(image_meta)}"
            f" cached={cached} downloaded={downloaded} failed={failed} skipped={skipped}"
        )

    def read_cache(self, cache_hash: str) -> Optional[str]:
        """从缓存读取 data URL。"""
        fpath = os.path.join(self.IMAGE_DIR, f"{cache_hash}.b64")
        try:
            with open(fpath, "r") as f:
                return f.read()
        except FileNotFoundError:
            return None

    def delete_cache(self, cache_hash: str) -> None:
        """删除缓存文件（裁剪消息时调用）。"""
        fpath = os.path.join(self.IMAGE_DIR, f"{cache_hash}.b64")
        try:
            os.remove(fpath)
        except FileNotFoundError:
            pass
