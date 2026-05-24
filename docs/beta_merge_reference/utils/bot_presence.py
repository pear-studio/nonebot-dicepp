import json
import os
import threading
import time
from typing import Any, Dict

from core.config import DATA_PATH


_ADMIN_DIR = os.path.join(DATA_PATH, "Admin")
_STATUS_FILE = os.path.join(_ADMIN_DIR, "bot_presence.json")
_LOCK = threading.Lock()


def _ensure_admin_dir() -> None:
    if not os.path.isdir(_ADMIN_DIR):
        os.makedirs(_ADMIN_DIR, exist_ok=True)


def _read_all_unlocked() -> Dict[str, Any]:
    if not os.path.exists(_STATUS_FILE):
        return {"bots": {}}
    try:
        with open(_STATUS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {"bots": {}}
    if not isinstance(data, dict):
        return {"bots": {}}
    bots = data.get("bots")
    if not isinstance(bots, dict):
        data["bots"] = {}
    return data


def _write_all_unlocked(data: Dict[str, Any]) -> None:
    _ensure_admin_dir()
    tmp = _STATUS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _STATUS_FILE)


def update_presence(
    account: str,
    *,
    state: str,
    nickname: str = "",
    reason: str = "",
) -> Dict[str, Any]:
    now = int(time.time())
    aid = str(account or "").strip()
    if not aid:
        return {}

    with _LOCK:
        data = _read_all_unlocked()
        bots = data.setdefault("bots", {})
        item = bots.get(aid)
        if not isinstance(item, dict):
            item = {}
        if nickname:
            item["nickname"] = str(nickname)
        item["account"] = aid
        item["state"] = state
        item["reason"] = reason
        item["heartbeat_ts"] = now
        item["updated_at"] = now
        bots[aid] = item
        data["updated_at"] = now
        _write_all_unlocked(data)
        return item


def heartbeat(account: str, nickname: str = "") -> Dict[str, Any]:
    return update_presence(account, state="online", nickname=nickname, reason="heartbeat")


def mark_online(account: str, nickname: str = "") -> Dict[str, Any]:
    return update_presence(account, state="online", nickname=nickname, reason="connect")


def mark_offline(account: str, reason: str = "disconnect") -> Dict[str, Any]:
    return update_presence(account, state="offline", reason=reason)


def get_presence_snapshot() -> Dict[str, Any]:
    with _LOCK:
        return _read_all_unlocked()
