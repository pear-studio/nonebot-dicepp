"""WebUI 后台入口：python -m dicepp_admin"""
import os
import sys
from pathlib import Path

# 确保 src 在 sys.path 里，让 `dicepp_admin.*` 可导入
_SRC_DIR = Path(__file__).resolve().parent.parent
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

import uvicorn  # noqa: E402

from dicepp_admin.config import AdminPaths  # noqa: E402


def main() -> None:
    AdminPaths.ensure_dirs()
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
