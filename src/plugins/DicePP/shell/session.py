"""Session 管理 - 创建、加载、列出、删除会话"""

import json
import shutil
import time
from pathlib import Path
from typing import List, Optional, Dict, Any

# 存储位置: 项目根目录下的 .dicepp-shell/
from utils.frozen import get_project_root

SHELL_DIR = Path(get_project_root()) / ".dicepp-shell"


def _validate_session_name(name: str) -> None:
    """验证会话名称合法性"""
    if not name:
        raise ValueError("Session name cannot be empty")
    if len(name) > 32:
        raise ValueError("Session name too long (max 32)")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
    if not all(c in allowed for c in name):
        raise ValueError("Session name contains invalid characters (allowed: a-z, A-Z, 0-9, _, -)")


def get_session_dir(name: str) -> Path:
    """获取会话目录路径"""
    _validate_session_name(name)
    return SHELL_DIR / name


def session_exists(name: str) -> bool:
    """检查会话是否存在"""
    return get_session_dir(name).exists()


def create_session(name: str, group_id: str = "test_group") -> Path:
    """创建新会话

    Args:
        name: 会话名称
        group_id: 默认群组ID

    Returns:
        会话目录路径
    """
    session_dir = get_session_dir(name)

    # 如果已存在，直接返回
    if session_dir.exists():
        return session_dir

    # 创建目录
    session_dir.mkdir(parents=True, exist_ok=True)

    # 写入元数据
    meta = {
        "name": name,
        "group_id": group_id,
        "created": time.time(),
        "last_used": time.time(),
    }
    (session_dir / "meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    return session_dir


def load_session(name: str) -> Optional[Dict[str, Any]]:
    """加载会话信息

    Returns:
        会话元数据字典，不存在返回 None
    """
    session_dir = get_session_dir(name)
    if not session_dir.exists():
        return None

    meta_path = session_dir / "meta.json"
    if not meta_path.exists():
        return None

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        # 更新最后使用时间
        meta["last_used"] = time.time()
        meta_path.write_text(
            json.dumps(meta, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
        return meta
    except (json.JSONDecodeError, IOError):
        return None


def list_sessions() -> List[Dict[str, Any]]:
    """列出所有会话

    Returns:
        会话信息列表，按最后使用时间排序
    """
    if not SHELL_DIR.exists():
        return []

    sessions = []
    for item in SHELL_DIR.iterdir():
        if not item.is_dir():
            continue

        meta_path = item / "meta.json"
        if not meta_path.exists():
            continue

        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            # 计算目录大小
            total_size = 0
            for f in item.rglob("*"):
                if f.is_file():
                    total_size += f.stat().st_size

            sessions.append({
                "name": meta.get("name", item.name),
                "group_id": meta.get("group_id", "unknown"),
                "created": meta.get("created", 0),
                "last_used": meta.get("last_used", 0),
                "size_bytes": total_size,
            })
        except (json.JSONDecodeError, IOError):
            continue

    # 按最后使用时间排序，最新的在前
    sessions.sort(key=lambda x: x["last_used"], reverse=True)
    return sessions


def delete_session(name: str) -> bool:
    """删除会话

    Args:
        name: 会话名称

    Returns:
        是否成功删除
    """
    session_dir = get_session_dir(name)
    if not session_dir.exists():
        return False

    try:
        shutil.rmtree(session_dir)
        return True
    except OSError:
        return False


def format_session_info(session: Dict[str, Any]) -> str:
    """格式化会话信息为可读字符串"""
    name = session["name"]
    group_id = session["group_id"]
    size = session["size_bytes"]

    # 格式化大小
    if size < 1024:
        size_str = f"{size}B"
    elif size < 1024 * 1024:
        size_str = f"{size / 1024:.1f}KB"
    else:
        size_str = f"{size / (1024 * 1024):.1f}MB"

    # 格式化时间
    last_used = session["last_used"]
    ago = time.time() - last_used
    if ago < 60:
        time_str = "just now"
    elif ago < 3600:
        time_str = f"{int(ago / 60)}m ago"
    elif ago < 86400:
        time_str = f"{int(ago / 3600)}h ago"
    else:
        time_str = f"{int(ago / 86400)}d ago"

    return f"{name:16} {group_id:16} {size_str:>8} {time_str:>10}"
