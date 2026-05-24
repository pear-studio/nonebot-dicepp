"""多 DicePP 实例的生命周期管理。

每个实例 = 独立子进程跑 bot.py + 独立 data 目录 + 独立端口。
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from dicepp_admin.config import (
    AdminPaths,
    INSTANCE_PORT_END,
    INSTANCE_PORT_START,
)
from dicepp_admin import llonebot_manager


_processes: Dict[str, subprocess.Popen] = {}


# ─── 持久化 ──────────────────────────────────────────────────────────────

def _load_instances() -> Dict[str, Dict]:
    if not AdminPaths.INSTANCES_FILE.exists():
        return {}
    try:
        return json.loads(AdminPaths.INSTANCES_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_instances(data: Dict[str, Dict]) -> None:
    AdminPaths.INSTANCES_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ─── 端口分配 ────────────────────────────────────────────────────────────

def _is_port_free(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _allocate_port(existing: List[int]) -> int:
    used = set(existing)
    for p in range(INSTANCE_PORT_START, INSTANCE_PORT_END + 1):
        if p in used:
            continue
        if _is_port_free(p):
            return p
    raise RuntimeError(f"端口 {INSTANCE_PORT_START}-{INSTANCE_PORT_END} 全部占用")


# ─── 实例 CRUD ───────────────────────────────────────────────────────────

def list_instances() -> List[Dict]:
    data = _load_instances()
    out = []
    for inst_id, inst in data.items():
        out.append({
            **inst,
            "id": inst_id,
            "running": is_running(inst_id),
        })
    return sorted(out, key=lambda x: x.get("created_at", 0))


def get_instance(instance_id: str) -> Optional[Dict]:
    inst = _load_instances().get(instance_id)
    if not inst:
        return None
    return {**inst, "id": instance_id, "running": is_running(instance_id)}


def create_instance(name: str, qq_id: Optional[str] = None,
                    master_qq: Optional[str] = None) -> Dict:
    if not name or not name.strip():
        raise ValueError("实例名不能为空")

    data = _load_instances()
    existing_ports = [v.get("port") for v in data.values() if v.get("port")]
    port = _allocate_port(existing_ports)

    inst_id = uuid.uuid4().hex[:12]
    inst_dir = AdminPaths.instance_dir(inst_id)
    inst_dir.mkdir(parents=True, exist_ok=True)
    (inst_dir / "bots").mkdir(exist_ok=True)
    (inst_dir / "logs").mkdir(exist_ok=True)
    (inst_dir / "config").mkdir(exist_ok=True)

    # 自动生成 13 位 access_token（跟 β README 约定一致）
    token = str(int(time.time() * 1000))[-13:]

    inst = {
        "name": name.strip(),
        "port": port,
        "qq_id": (qq_id or "").strip(),
        "master_qq": (master_qq or "").strip(),
        "data_dir": str(inst_dir),
        "created_at": int(time.time()),
        "auto_start": False,
        "access_token": token,
        "llonebot_pid": None,
    }
    data[inst_id] = inst
    _save_instances(data)

    # 如果绑定了 QQ 且 LLOneBot 已就绪，立刻预生成反向 WS 配置
    if inst["qq_id"] and llonebot_manager.is_acquired():
        try:
            llonebot_manager.generate_config(inst["qq_id"], port, token)
        except Exception:
            pass

    return {**inst, "id": inst_id, "running": False}


def update_instance(instance_id: str, patch: Dict) -> Dict:
    data = _load_instances()
    inst = data.get(instance_id)
    if not inst:
        raise KeyError(instance_id)
    old_qq = inst.get("qq_id", "")
    for k in ("name", "qq_id", "master_qq", "auto_start", "access_token"):
        if k in patch:
            inst[k] = patch[k]
    _save_instances(data)

    new_qq = inst.get("qq_id", "")
    # QQ 号变了：清旧的、写新的 LLOneBot 反向 WS 配置
    if llonebot_manager.is_acquired():
        try:
            if old_qq and old_qq != new_qq:
                llonebot_manager.clear_config(old_qq)
            if new_qq:
                llonebot_manager.generate_config(
                    new_qq, inst["port"], inst.get("access_token", "")
                )
        except Exception:
            pass

    return {**inst, "id": instance_id, "running": is_running(instance_id)}


def delete_instance(instance_id: str, remove_data: bool = False) -> None:
    stop_instance(instance_id)
    data = _load_instances()
    inst = data.pop(instance_id, None)
    _save_instances(data)
    # 清掉 LLOneBot 里对应的反向 WS 配置
    if inst and inst.get("qq_id") and llonebot_manager.is_acquired():
        try:
            llonebot_manager.clear_config(inst["qq_id"])
        except Exception:
            pass
    if remove_data and inst and inst.get("data_dir"):
        try:
            shutil.rmtree(inst["data_dir"], ignore_errors=True)
        except OSError:
            pass


# ─── 进程控制 ────────────────────────────────────────────────────────────

def is_running(instance_id: str) -> bool:
    p = _processes.get(instance_id)
    if not p:
        return False
    if p.poll() is None:
        return True
    # 进程已退出，清掉缓存
    _processes.pop(instance_id, None)
    return False


def _project_root() -> Path:
    return AdminPaths.PROJECT_ROOT


def _venv_python() -> Path:
    root = _project_root()
    candidates = [
        root / ".venv" / "Scripts" / "python.exe",   # Windows
        root / ".venv" / "bin" / "python",            # POSIX
    ]
    for p in candidates:
        if p.exists():
            return p
    # 退化用系统 python（开发环境）
    return Path(sys.executable)


def start_instance(instance_id: str) -> Dict:
    if is_running(instance_id):
        return get_instance(instance_id)  # type: ignore[return-value]

    data = _load_instances()
    inst = data.get(instance_id)
    if not inst:
        raise KeyError(instance_id)

    inst_dir = Path(inst["data_dir"])
    log_path = inst_dir / "logs" / "runtime.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["HOST"] = "127.0.0.1"
    env["PORT"] = str(inst["port"])
    env["DPP_INSTANCE_ID"] = instance_id
    env["DICEPP_DATA_DIR"] = str(inst_dir)
    if inst.get("access_token"):
        env["ACCESS_TOKEN"] = inst["access_token"]

    python = _venv_python()
    bot_py = _project_root() / "bot.py"

    # 用 stdout/stderr 重定向到日志文件
    log_fp = open(log_path, "a", encoding="utf-8", buffering=1)
    log_fp.write(f"\n=== [{time.strftime('%Y-%m-%d %H:%M:%S')}] 启动 ===\n")

    creationflags = 0
    if os.name == "nt":
        # CREATE_NEW_PROCESS_GROUP 让我们能精准 kill 这棵进程树
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

    proc = subprocess.Popen(
        [str(python), str(bot_py)],
        cwd=str(_project_root()),
        env=env,
        stdout=log_fp,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )
    _processes[instance_id] = proc

    return {**inst, "id": instance_id, "running": True}


def stop_instance(instance_id: str) -> None:
    p = _processes.pop(instance_id, None)
    if not p:
        return
    if p.poll() is not None:
        return
    try:
        if os.name == "nt":
            p.send_signal(subprocess.signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
        else:
            p.terminate()
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
    except (OSError, ValueError):
        try:
            p.kill()
        except OSError:
            pass


def stop_all() -> None:
    for inst_id in list(_processes.keys()):
        stop_instance(inst_id)


def read_runtime_log(instance_id: str, tail: int = 500) -> str:
    inst = _load_instances().get(instance_id)
    if not inst:
        return ""
    log_path = Path(inst["data_dir"]) / "logs" / "runtime.log"
    if not log_path.exists():
        return ""
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-tail:])
    except OSError:
        return ""
