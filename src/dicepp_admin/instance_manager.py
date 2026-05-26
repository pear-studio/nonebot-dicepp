"""多 DicePP 实例的生命周期管理。

每个实例 = 独立子进程跑 bot.py + 独立 data 目录 + 独立端口。

进程状态持久化策略
─────────────────
admin 后台自身重启后，原本由它 spawn 的 DicePP 子进程可能仍在跑（如果
是 detached / daemon-like 启动）。为避免管理员重启 admin 后所有实例
都被错误标为「已停止」，本模块：

1. 每次 start_instance() 把 pid 写入 instances.json
2. admin 启动调 scan_existing_processes()：用 psutil 检查 pid 是否还
   是活着的 python+bot.py 进程，是 → 重建 ProcessRef 到内存
3. is_running() 优先看 Popen.poll()，回退查 psutil
"""
import json
import logging
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("dicepp.admin.instance")

try:
    import psutil  # 用于持久化检测；α 主依赖里已有
except ImportError:  # pragma: no cover
    psutil = None  # type: ignore[assignment]

from dicepp_admin.config import (
    AdminPaths,
    INSTANCE_PORT_END,
    INSTANCE_PORT_START,
)
from dicepp_admin import llonebot_manager


@dataclass
class ProcessRef:
    """对子进程的引用：可能是当前进程 spawn 的（有 Popen），
    也可能是 admin 重启前留下的（只有 pid）。"""
    pid: int
    popen: Optional[subprocess.Popen] = None

    def is_alive(self) -> bool:
        if self.popen is not None:
            return self.popen.poll() is None
        if psutil is not None:
            try:
                return psutil.pid_exists(self.pid)
            except (OSError, RuntimeError):
                return False
        # 没 psutil 时只能信赖 Popen；裸 pid 视为不可知 → False
        return False


# 全局子进程引用表。FastAPI 同步 def 端点跑在线程池里，多客户端
# 并发访问（启动/停止/查询同一实例）会读写这个 dict，必须用 Lock
# 串行化所有读写路径以避免 KeyError 和状态不一致。
# 锁仅保护字典本身（put/pop/lookup），不包含真正的 subprocess 调用 ——
# 那些 IO 操作放在 finally 里执行以减少 hold lock 时间。
_processes: Dict[str, ProcessRef] = {}
_processes_lock = threading.RLock()


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


def _patch_inst_field(instance_id: str, **fields) -> None:
    data = _load_instances()
    if instance_id not in data:
        return
    inst = data[instance_id]
    for k, v in fields.items():
        if v is None:
            inst.pop(k, None)
        else:
            inst[k] = v
    _save_instances(data)


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

    # 自动生成 access_token：32 hex chars，CSPRNG 不可预测
    # （旧实现用 time.time()*1000 后 13 位，可凭时间戳推算 — pear #45 Q2）
    token = secrets.token_hex(16)

    inst = {
        "name": name.strip(),
        "port": port,
        "qq_id": (qq_id or "").strip(),
        "master_qq": (master_qq or "").strip(),
        "data_dir": str(inst_dir),
        "created_at": int(time.time()),
        "auto_start": False,
        "access_token": token,
    }
    data[inst_id] = inst
    _save_instances(data)

    # 如果绑定了 QQ 且 LLOneBot 已就绪，立刻预生成反向 WS 配置
    if inst["qq_id"] and llonebot_manager.is_acquired():
        try:
            llonebot_manager.generate_config(inst["qq_id"], port, token)
        except (OSError, ValueError):
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
        except (OSError, ValueError):
            pass

    return {**inst, "id": instance_id, "running": is_running(instance_id)}


def delete_instance(instance_id: str, remove_data: bool = False) -> None:
    """删除实例。按 pear #45 S2：先操作磁盘成功，再写 instances.json，保证原子性。

    旧实现是 pop+save 之后才 rmtree —— 若 rmtree 失败，instances.json
    已经把实例移除，但数据目录残留磁盘，下次创建同名实例会撞已有目录。
    """
    stop_instance(instance_id)

    data = _load_instances()
    inst = data.get(instance_id)
    if not inst:
        return

    # 1. 先清 LLOneBot config（独立资源，失败仅 warning，不阻塞主流程）
    if inst.get("qq_id") and llonebot_manager.is_acquired():
        try:
            llonebot_manager.clear_config(inst["qq_id"])
        except OSError as e:
            logger.warning(
                "delete_instance(%s): clear llonebot config for qq %s failed: %s",
                instance_id, inst.get("qq_id"), e,
            )

    # 2. 如需清磁盘数据，先做 rmtree（用 ignore_errors=True，部分失败也能继续）。
    #    rmtree 完整失败时记录详细日志，但仍继续 pop —— 因为部分文件可能已删，
    #    保留 instances.json 条目会让用户看到死实例，反而更糟
    if remove_data and inst.get("data_dir"):
        try:
            shutil.rmtree(inst["data_dir"], ignore_errors=True)
        except OSError as e:
            logger.warning(
                "delete_instance(%s): rmtree(%s) failed, manual cleanup needed: %s",
                instance_id, inst["data_dir"], e,
            )

    # 3. 最后从 instances.json 移除（在磁盘清理已完成或已尝试之后）
    data.pop(instance_id, None)
    _save_instances(data)


# ─── 进程控制 ────────────────────────────────────────────────────────────

def is_running(instance_id: str) -> bool:
    # pear #45 S1：FastAPI 同步 def 端点在线程池里跑，并发读写 _processes
    # 必须串行化。锁内只做字典读取 + is_alive 检查（不做 IO）
    with _processes_lock:
        ref = _processes.get(instance_id)
        if ref is None:
            return False
        if ref.is_alive():
            return True
        # 进程已退出，清掉缓存
        _processes.pop(instance_id, None)
    # 持久化 pid=None 走自己的 IO，无需 hold 锁
    _patch_inst_field(instance_id, pid=None)
    return False


def scan_existing_processes() -> int:
    """admin 启动时调用：扫描 instances.json 里有 pid 字段的实例，
    重建 _processes 缓存。返回成功恢复的实例数。

    被 admin app 的 startup 事件调用。
    """
    if psutil is None:
        return 0

    data = _load_instances()
    dirty = False
    recovered = 0
    for inst_id, inst in data.items():
        pid = inst.get("pid")
        if not pid:
            continue
        alive = False
        try:
            if psutil.pid_exists(pid):
                # 进一步验证：进程 cmdline 应该指向本项目 bot.py
                proc = psutil.Process(pid)
                cmdline = " ".join(proc.cmdline()).lower()
                if "bot.py" in cmdline:
                    alive = True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
            alive = False

        if alive:
            with _processes_lock:
                _processes[inst_id] = ProcessRef(pid=pid)
            recovered += 1
        else:
            inst.pop("pid", None)
            dirty = True

    if dirty:
        _save_instances(data)
    return recovered


def autostart_marked_instances() -> List[str]:
    """启动所有 auto_start=True 但当前未运行的实例。返回启动的实例 id 列表。

    被 admin app 的 startup 事件在 scan_existing_processes 之后调用。
    """
    started: List[str] = []
    for inst in list_instances():
        if inst.get("auto_start") and not inst.get("running"):
            try:
                start_instance(inst["id"])
                started.append(inst["id"])
            except (KeyError, OSError):
                continue
    return started


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
    # pear #45 S1: 用锁保护 _processes 写入
    with _processes_lock:
        _processes[instance_id] = ProcessRef(pid=proc.pid, popen=proc)
    # 持久化 pid：admin 重启后能识别并恢复状态
    _patch_inst_field(instance_id, pid=proc.pid)

    return {**inst, "id": instance_id, "pid": proc.pid, "running": True}


def stop_instance(instance_id: str) -> None:
    # pear #45 S1: 用锁原子地 pop 引用；后续 kill IO 操作在锁外执行
    # 以避免长时间 hold 锁阻塞其他端点
    with _processes_lock:
        ref = _processes.pop(instance_id, None)

    if ref is None:
        _patch_inst_field(instance_id, pid=None)
        return

    if not ref.is_alive():
        _patch_inst_field(instance_id, pid=None)
        return

    try:
        if ref.popen is not None:
            # 当前进程 spawn 的，用 Popen API 干净 kill
            if os.name == "nt":
                ref.popen.send_signal(subprocess.signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
            else:
                ref.popen.terminate()
            try:
                ref.popen.wait(timeout=10)
            except subprocess.TimeoutExpired:
                ref.popen.kill()
        elif psutil is not None:
            # admin 重启前留下的 pid，用 psutil 终止
            try:
                proc = psutil.Process(ref.pid)
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except psutil.TimeoutExpired:
                    proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except (OSError, ValueError) as outer_e:
        # 主路径终止失败（如 send_signal 后 wait 时管道已关闭），尝试 fallback kill
        if ref.popen is not None:
            try:
                ref.popen.kill()
            except OSError as kill_e:
                # fallback kill 也失败：进程可能已被 OS 回收或权限问题
                logger.warning(
                    "stop_instance(%s): fallback kill failed (outer=%s, kill=%s)",
                    instance_id, outer_e, kill_e,
                )

    _patch_inst_field(instance_id, pid=None)


def stop_all() -> None:
    # pear #45 S1: 锁内取 ID 快照，避免迭代中被其他线程修改
    with _processes_lock:
        ids = list(_processes.keys())
    for inst_id in ids:
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
