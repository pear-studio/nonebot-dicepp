"""LLOneBot 协议端（LuckyLilliaBot）管理。

设计要点：
1. LLOneBot 是 **单例进程**，多个 QQ 号共享同一个 LLOneBot；每个 QQ 号在
   `LLONEBOT/data/config_<qq>.json` 里有独立配置。
2. 自动获取整合包：扫描常见位置（β 整合包/桌面/Documents/项目同级），
   找到后复制到项目内 `bin/llonebot/`；找不到则提示用户去下载页。
3. 反向 WS 配置自动生成：实例与 QQ 绑定后，admin 调 generate_config()
   写入 `data/config_<qq>.json`，LLOneBot 启动后自动连上对应 DicePP 端口。
4. 端口冲突：DicePP 实例端口由 admin 自动分配；LLOneBot 本身的 WebUI 等端口
   由整合包默认值（3080/3010 等）固定，不与 DicePP 实例冲突。
"""
import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from dicepp_admin.config import AdminPaths


_LL_CONFIG_FILE = AdminPaths.LLONEBOT_DIR / "config.json"
_LL_PROCESS: Optional[subprocess.Popen] = None

# 项目内整合包默认位置
_BUNDLE_DIR_DEFAULT = AdminPaths.PROJECT_ROOT / "bin" / "llonebot"

# 自动扫描时考虑的"已有整合包"位置
_SCAN_CANDIDATES: List[Path] = [
    # 用户从 β 整合包复制过来
    AdminPaths.PROJECT_ROOT / "LLONEBOT",
    AdminPaths.PROJECT_ROOT.parent / "LLONEBOT",
    AdminPaths.PROJECT_ROOT.parent / "DicePPBOT" / "LLONEBOT",
    # 常见用户目录
    Path(os.path.expanduser("~")) / "Desktop" / "LLONEBOT",
    Path(os.path.expanduser("~")) / "Documents" / "LLONEBOT",
    Path(os.path.expanduser("~")) / "LLONEBOT",
    # Program Files
    Path("C:/Program Files/LLONEBOT"),
    Path("C:/Program Files (x86)/LLONEBOT"),
]


# ─── 持久化设置 ──────────────────────────────────────────────────────────

def _load_config() -> Dict:
    if not _LL_CONFIG_FILE.exists():
        return {"llbot_path": "", "bundle_dir": ""}
    try:
        return json.loads(_LL_CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"llbot_path": "", "bundle_dir": ""}


def _save_config(cfg: Dict) -> None:
    AdminPaths.LLONEBOT_DIR.mkdir(parents=True, exist_ok=True)
    _LL_CONFIG_FILE.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_config() -> Dict:
    return _load_config()


def set_llbot_path(path: str) -> Dict:
    """手动指定 llbot.exe 路径（也会更新 bundle_dir）。"""
    cfg = _load_config()
    cfg["llbot_path"] = path.strip()
    if cfg["llbot_path"]:
        bundle = Path(cfg["llbot_path"]).parent
        cfg["bundle_dir"] = str(bundle)
    _save_config(cfg)
    return cfg


# ─── 路径解析 ────────────────────────────────────────────────────────────

def bundle_dir() -> Optional[Path]:
    """当前可用的 LLONEBOT 整合包目录。"""
    cfg = _load_config()
    if cfg.get("bundle_dir"):
        p = Path(cfg["bundle_dir"])
        if (p / "llbot.exe").exists():
            return p
    if (_BUNDLE_DIR_DEFAULT / "llbot.exe").exists():
        return _BUNDLE_DIR_DEFAULT
    return None


def llbot_exe() -> Optional[Path]:
    bd = bundle_dir()
    if bd:
        exe = bd / "llbot.exe"
        if exe.exists():
            return exe
    cfg = _load_config()
    if cfg.get("llbot_path") and Path(cfg["llbot_path"]).exists():
        return Path(cfg["llbot_path"])
    return None


def is_acquired() -> bool:
    return llbot_exe() is not None


def scan_existing() -> List[str]:
    """扫描常见位置返回找到的整合包路径。"""
    found = []
    for p in _SCAN_CANDIDATES:
        if (p / "llbot.exe").exists():
            try:
                found.append(str(p.resolve()))
            except OSError:
                found.append(str(p))
    return found


# ─── 自动获取 ────────────────────────────────────────────────────────────

def auto_acquire() -> Dict:
    """扫描本地常见位置，找到 LLONEBOT 整合包就复制到项目内。

    返回：
      {status, message, bundle_dir?, copied_from?}
    """
    if is_acquired():
        return {
            "status": "already_acquired",
            "message": "LLOneBot 整合包已就绪",
            "bundle_dir": str(bundle_dir()),
        }

    found = scan_existing()
    if not found:
        return {
            "status": "not_found",
            "message": "本地未找到 LLONEBOT 整合包，请到下载页获取后复制到项目同级目录",
            "download_url": "https://github.com/LLOneBot/LuckyLilliaBot/releases",
            "suggested_paths": [str(p) for p in _SCAN_CANDIDATES[:6]],
        }

    src = Path(found[0])
    dst = _BUNDLE_DIR_DEFAULT

    # 若目标已存在不完整文件，清掉重来
    if dst.exists():
        try:
            shutil.rmtree(dst)
        except OSError as e:
            return {"status": "error", "message": f"清理旧目录失败: {e}"}

    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        # 用 copytree（保留 data/ 里已登录账号的配置 → 不复制 data 反而更安全；
        # 这里采用完整复制，data 里的旧账号也带过来，省得用户重登）
        shutil.copytree(src, dst, symlinks=False, ignore_dangling_symlinks=True)
    except OSError as e:
        return {"status": "error", "message": f"复制失败: {e}"}

    cfg = _load_config()
    cfg["bundle_dir"] = str(dst)
    cfg["llbot_path"] = str(dst / "llbot.exe")
    _save_config(cfg)

    return {
        "status": "acquired",
        "message": "已从本地复制 LLONEBOT 整合包",
        "bundle_dir": str(dst),
        "copied_from": str(src),
    }


# ─── 反向 WS 配置预生成 ─────────────────────────────────────────────────

def _data_dir() -> Optional[Path]:
    bd = bundle_dir()
    if not bd:
        return None
    d = bd / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _template_config() -> Optional[Dict]:
    bd = bundle_dir()
    if not bd:
        return None
    # 优先用整合包自带的 default_config.json
    candidates = [
        bd / "bin" / "llbot" / "default_config.json",
        bd / "default_config.json",
    ]
    for c in candidates:
        if c.exists():
            try:
                return json.loads(c.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
    return None


def _config_file_for(qq_id: str) -> Optional[Path]:
    d = _data_dir()
    if not d:
        return None
    return d / f"config_{qq_id}.json"


def generate_config(qq_id: str, instance_port: int,
                    access_token: str = "") -> Dict:
    """为指定 QQ 号生成 LLOneBot 的 config_<qq>.json，
    把反向 WS 自动指向 ws://127.0.0.1:<port>/onebot/v11/ws。
    """
    qq_id = (qq_id or "").strip()
    if not qq_id or not qq_id.isdigit():
        return {"status": "invalid_qq", "message": "QQ 号必须是纯数字"}

    cf = _config_file_for(qq_id)
    if cf is None:
        return {"status": "no_bundle", "message": "未找到 LLONEBOT 整合包"}

    # 读已存在的配置（保留用户其他设置），否则用模板
    base: Dict[str, Any]
    if cf.exists():
        try:
            base = json.loads(cf.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            base = _template_config() or _minimal_template()
    else:
        base = _template_config() or _minimal_template()

    # 修改 ob11 反向 WS 配置：找到 ws-reverse 项就改，没有就插入
    ob11 = base.setdefault("ob11", {})
    ob11["enable"] = True
    connects = ob11.setdefault("connect", [])
    target_url = f"ws://127.0.0.1:{instance_port}/onebot/v11/ws"

    reverse_idx = None
    for i, c in enumerate(connects):
        if isinstance(c, dict) and c.get("type") == "ws-reverse":
            reverse_idx = i
            break
    reverse_cfg = {
        "type": "ws-reverse",
        "enable": True,
        "url": target_url,
        "heartInterval": 60000,
        "token": access_token,
        "reportSelfMessage": False,
        "reportOfflineMessage": False,
        "messageFormat": "array",
        "debug": False,
    }
    if reverse_idx is None:
        connects.append(reverse_cfg)
    else:
        connects[reverse_idx] = reverse_cfg

    cf.write_text(json.dumps(base, ensure_ascii=False, indent=4), encoding="utf-8")
    return {
        "status": "ok",
        "config_file": str(cf),
        "reverse_ws_url": target_url,
        "qq_id": qq_id,
    }


def clear_config(qq_id: str) -> bool:
    cf = _config_file_for(qq_id)
    if cf and cf.exists():
        try:
            cf.unlink()
            return True
        except OSError:
            return False
    return False


def list_configured_qqs() -> List[Dict]:
    """列出 LLONEBOT/data/ 下已有的 config_*.json。"""
    d = _data_dir()
    if not d:
        return []
    out = []
    for p in d.iterdir():
        if not p.is_file():
            continue
        if not (p.name.startswith("config_") and p.name.endswith(".json")):
            continue
        qq = p.stem.removeprefix("config_")
        if not qq.isdigit():
            continue
        # 解析出反向 WS 信息
        reverse_url = ""
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            for c in data.get("ob11", {}).get("connect", []):
                if isinstance(c, dict) and c.get("type") == "ws-reverse" and c.get("enable"):
                    reverse_url = c.get("url", "")
                    break
        except (OSError, json.JSONDecodeError):
            pass
        out.append({"qq_id": qq, "reverse_ws_url": reverse_url, "config_file": str(p)})
    return sorted(out, key=lambda x: x["qq_id"])


def _minimal_template() -> Dict:
    """整合包没有 default_config 时的兜底模板。"""
    return {
        "webui": {"enable": True, "host": "127.0.0.1", "port": 3080},
        "ob11": {
            "enable": True,
            "connect": [
                {
                    "type": "ws-reverse", "enable": True, "url": "",
                    "heartInterval": 60000, "token": "",
                    "reportSelfMessage": False, "reportOfflineMessage": False,
                    "messageFormat": "array", "debug": False,
                }
            ],
        },
        "enableLocalFile2Url": False,
        "log": True,
        "msgCacheExpire": 120,
    }


# ─── 进程管理（单例） ───────────────────────────────────────────────────

def is_running() -> bool:
    global _LL_PROCESS
    if _LL_PROCESS is None:
        return False
    if _LL_PROCESS.poll() is None:
        return True
    _LL_PROCESS = None
    return False


def start() -> Dict:
    """启动 LLOneBot 单例进程（用户在弹出窗口中扫码登录 QQ）。"""
    global _LL_PROCESS
    if is_running():
        return {"status": "already_running", "pid": _LL_PROCESS.pid}

    exe = llbot_exe()
    if not exe:
        return {
            "status": "not_found",
            "message": "未找到 LLOneBot。点击「自动获取」或手动指定 llbot.exe 路径。",
        }

    log_path = AdminPaths.LLONEBOT_DIR / "llbot.log"
    AdminPaths.LLONEBOT_DIR.mkdir(parents=True, exist_ok=True)
    log_fp = open(log_path, "a", encoding="utf-8", buffering=1)
    log_fp.write(f"\n=== [{time.strftime('%Y-%m-%d %H:%M:%S')}] 启动 LLOneBot ===\n")

    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

    try:
        _LL_PROCESS = subprocess.Popen(
            [str(exe)],
            cwd=str(exe.parent),
            stdout=log_fp,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )
        return {
            "status": "started",
            "pid": _LL_PROCESS.pid,
            "note": "请在 LLOneBot 窗口中扫码登录 QQ。已配置的 QQ 号会自动连接到对应实例。",
        }
    except OSError as e:
        return {"status": "error", "message": str(e)}


def stop() -> Dict:
    global _LL_PROCESS
    if not is_running():
        return {"status": "not_running"}
    try:
        _LL_PROCESS.terminate()
        try:
            _LL_PROCESS.wait(timeout=8)
        except subprocess.TimeoutExpired:
            _LL_PROCESS.kill()
        _LL_PROCESS = None
        return {"status": "stopped"}
    except (OSError, ValueError) as e:
        return {"status": "error", "message": str(e)}


# ─── 连接探测 ────────────────────────────────────────────────────────────

def is_instance_port_listening(instance_port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        s.connect(("127.0.0.1", instance_port))
        return True
    except (OSError, socket.timeout):
        return False
    finally:
        s.close()


# ─── 综合状态 ────────────────────────────────────────────────────────────

def status_snapshot() -> Dict:
    """供 admin 一次拿全信息。"""
    return {
        "acquired": is_acquired(),
        "bundle_dir": str(bundle_dir()) if bundle_dir() else None,
        "llbot_exe": str(llbot_exe()) if llbot_exe() else None,
        "running": is_running(),
        "scan_results": scan_existing(),
        "configured_qqs": list_configured_qqs(),
        "config": _load_config(),
    }
