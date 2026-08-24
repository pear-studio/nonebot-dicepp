# DicePP Runtime 架构

DicePP 的运行时由 Dashboard、一个 Bot controller 和一个 Bot 子进程组成。项目不再包含 Standalone Manager、4091 HTTP 服务、RuntimeUnit、多实例 operation journal、跨进程控制 token 或 Docker Socket 控制层。

## 进程关系

```text
Dashboard / Windows launcher
        │ owns one controller
        ▼
  one Bot subprocess
        │ stdout + stderr
        ▼
  data/logs/dicepp-runtime.log
```

`BotProcessController` 只接收 command、cwd、environment 和日志路径。它提供 start、stop、restart、status、tail logs 和 shutdown；操作由一个进程内锁串行化。stop 先 terminate，短超时后 kill。Bot 自然退出时，status 返回 stopped 和 returncode，不自动拉起。

## 入口

- 源码/Linux：当前 Python 执行 `bot.py`；
- Windows Portable：`DicePP.exe` 直接控制同目录 `DicePP-Runtime.exe`；
- Linux Docker：`python -m dashboard` 作为容器 CMD，正常入口显式 auto-start Bot；
- 导入 ASGI app、测试或普通模块不会隐式启动 Bot。

Dashboard lifespan 持有 controller，并在 lifespan 结束时 shutdown。Bot API 同步返回最终状态，不创建 operation ID。Windows tray 使用同一个 controller，不另起控制者。

## 数据边界

Dashboard 管理数据库、session、审计和运行日志独立于业务数据。配置、查询、存档、清空和空实例导入都在 Dashboard 本地调用 `dicepp_data`；业务维护使用一个进程内 `data_maintenance_lock`，并要求 Bot 已停止。导入目标必须为空实例，in-progress marker 存在时拒绝启动。

存档物理位置保持为 `manager/backups/`，这是历史数据目录名称，不代表仍存在 Manager 服务。Docker Compose 只挂载该目录和普通 config/data/content/dashboard 数据目录。

## 部署

Linux 使用一个 `dicepp` Compose service、一个 GHCR 镜像、4090 Dashboard 端口和内部 8080 OneBot 端口。Windows 只发布 Portable ZIP。版本发现、在线下载、安装、自动更新和自动回滚不属于运行时职责；用户通过 GitHub Releases 手工选择版本。

## 测试边界

测试优先保护正常路径：真实本地短生命周期 Bot、Dashboard 同步控制、停止后数据维护、空实例导入、单镜像 fresh start 和 Windows Portable smoke。异常 Docker、断电、极端磁盘或跨主机恢复不构成当前运行时协议。
