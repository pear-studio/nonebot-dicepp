"""WebUI 后台入口：python -m dicepp_admin"""
import os
import sys
import urllib.request
from pathlib import Path

# 确保 src 在 sys.path 里，让 `dicepp_admin.*` 可导入
_SRC_DIR = Path(__file__).resolve().parent.parent
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

import uvicorn  # noqa: E402

from dicepp_admin.config import AdminPaths  # noqa: E402


# S5: 解决内网部署 / CDN 不可达问题
# admin 启动时把 Tailwind + Alpine 下载到 static/vendor/，HTML 引用本地路径
_VENDOR_ASSETS = [
    ("tailwind.min.js",
     "https://cdn.tailwindcss.com"),
    ("alpine.min.js",
     "https://cdn.jsdelivr.net/npm/alpinejs@3.13.5/dist/cdn.min.js"),
]


def ensure_vendor_assets() -> None:
    """首次启动从公共 CDN 下载前端依赖到 static/vendor/，之后用本地副本。

    下载失败会打印警告但不阻塞启动；HTML 中也保留 CDN fallback。
    """
    vendor_dir = Path(__file__).resolve().parent / "static" / "vendor"
    vendor_dir.mkdir(parents=True, exist_ok=True)
    for filename, url in _VENDOR_ASSETS:
        target = vendor_dir / filename
        if target.exists() and target.stat().st_size > 1024:
            continue
        try:
            print(f"[admin] downloading vendor asset: {filename} ...")
            req = urllib.request.Request(url, headers={"User-Agent": "DicePP-Admin/0.1"})
            with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 (trusted CDN)
                data = resp.read()
            target.write_bytes(data)
            print(f"[admin] saved: {target} ({len(data)} bytes)")
        except (OSError, ValueError) as e:
            print(f"[admin] WARN: failed to download {filename} ({e}); "
                  f"WebUI will fall back to CDN at runtime")


def main() -> None:
    AdminPaths.ensure_dirs()
    ensure_vendor_assets()
    host = os.environ.get("DPP_ADMIN_HOST", "127.0.0.1")
    port = int(os.environ.get("DPP_ADMIN_PORT", "2333"))
    uvicorn.run(
        "dicepp_admin.app:app",
        host=host,
        port=port,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()
