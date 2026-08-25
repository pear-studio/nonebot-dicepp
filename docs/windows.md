# Windows 部署

Windows 版本只发布 Portable ZIP。解压后由 `DicePP.exe` 提供 Dashboard、托盘和登录自启动，并直接控制同目录的 `DicePP-Runtime.exe`。

## 快速开始

1. 下载 `DicePP-vX.Y.Z-win64-Portable.zip`，解压到一个新的固定目录，例如 `D:\DicePP`。
2. 启动 `DicePP.exe`。
3. 在 `http://127.0.0.1:4090/dashboard` 初始化管理员密码并配置 Bot。
4. 配置 LLOneBot 连接 `ws://127.0.0.1:8080/onebot/v11/ws`。
5. 在 QQ 中发送 `.help` 验证机器人回复。

Portable 目录中必须同时存在：

```text
DicePP/
├─ DicePP.exe
├─ DicePP-Runtime.exe
├─ config/
├─ content/
├─ data/
│  └─ backups/
└─ dashboard/data/
```

`DicePP-Runtime.exe` 是由 Dashboard 控制的 Bot 子进程，不需要单独双击。托盘和 Dashboard 的启动、停止、重启操作作用于这一个进程；退出 Dashboard 时会先停止 Bot。

## 数据与配置

配置、内容、运行数据库、日志、Dashboard 数据和存档 `data/backups/` 都在 Portable 根目录。程序不会自动发现或迁移旧目录中的存档库存；需要时请通过“导入 ZIP”独立恢复。

Dashboard 保存配置后会提示需要重启；不会通过隐藏服务热重载。手动备份、清空和空实例导入要求 Bot 已停止，并由 Dashboard 在本进程内串行执行。

## 登录自启动

托盘菜单可以启用“登录后自动启动”。也可以在 Portable 根目录执行：

```powershell
.\DicePP.exe --autostart status
.\DicePP.exe --autostart enable
.\DicePP.exe --autostart disable
```

自启动只写当前用户的 Run 注册项，不安装 Windows Service。移动整个目录后，先关闭旧位置的自启动，再在新位置重新启用。

## 手工更新

更新时先退出旧目录中的 `DicePP.exe`，停止旧 Bot 并释放 Dashboard 端口；不必从旧 Dashboard 导出存档。下载新的 Portable ZIP，解压到新的空目录并启动新目录中的 `DicePP.exe`。在新 Dashboard 停止 Bot，然后在存档页输入旧 Portable 根目录路径，点击“从旧目录导入”；导入完成后再启动 Bot。该入口会导入旧目录中受支持的配置、业务数据和内容，目标必须为空；旧存档库存不会自动迁移，需要时可用“导入 ZIP”独立恢复。当前版本不提供 Setup、Velopack、自动下载或自动回滚入口。

如果 Bot 正在运行，先在 Dashboard 停止它；不要覆盖正在使用的 Portable 目录，也不要手动替换运行中的 EXE。
