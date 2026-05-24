"""管理后台的路径与配置常量。"""
import os
from pathlib import Path


def _project_root() -> Path:
    # __file__ = .../src/dicepp_admin/config.py → 上两级
    return Path(__file__).resolve().parent.parent.parent


class AdminPaths:
    PROJECT_ROOT: Path = _project_root()

    DATA_DIR:        Path = PROJECT_ROOT / "data"
    ADMIN_DIR:       Path = DATA_DIR / "admin"
    INSTANCES_DIR:   Path = DATA_DIR / "instances"
    LLONEBOT_DIR:    Path = DATA_DIR / "llonebot"

    AUTH_FILE:       Path = ADMIN_DIR / "auth.json"
    SESSION_FILE:    Path = ADMIN_DIR / "sessions.json"
    INSTANCES_FILE:  Path = ADMIN_DIR / "instances.json"
    AUDIT_DB:        Path = ADMIN_DIR / "audit.db"

    @classmethod
    def ensure_dirs(cls) -> None:
        for d in [cls.DATA_DIR, cls.ADMIN_DIR, cls.INSTANCES_DIR, cls.LLONEBOT_DIR]:
            d.mkdir(parents=True, exist_ok=True)

    @classmethod
    def instance_dir(cls, instance_id: str) -> Path:
        return cls.INSTANCES_DIR / instance_id


# 端口分配规则：第一个实例 8080，后续递增
INSTANCE_PORT_START = int(os.environ.get("DPP_INSTANCE_PORT_START", "8080"))
INSTANCE_PORT_END   = int(os.environ.get("DPP_INSTANCE_PORT_END",   "8180"))

# Session 超时：7 天
SESSION_TTL_SECONDS = 7 * 24 * 3600

# 默认用户名（首次需设置密码）
DEFAULT_USERNAME = "admin"
