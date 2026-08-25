# 版本更新

DicePP 当前采用手工更新。Dashboard 只展示当前版本和 GitHub Releases 静态链接，不在线检查最新版本，也不下载、安装或回滚程序。

## Windows Portable

1. 在 Dashboard 导出重要存档并停止 Bot。
2. 从 [GitHub Releases](https://github.com/pear-studio/nonebot-dicepp/releases) 下载目标 `win64-Portable.zip`。
3. 解压到新的空目录，不覆盖正在使用的旧目录。
4. 按 [Windows 部署](./windows.md) 的导入说明迁移配置或业务数据。
5. 启动新目录的 `DicePP.exe`，确认 Dashboard 健康和 Bot 状态。

当前不提供 Windows Setup、Velopack、自动下载、自动安装或自动回滚。

## Linux 单容器

1. 在 Dashboard 导出重要存档并停止 Bot。
2. 备份 `config/`、`data/`、`content/` 和 `dashboard/data/`；存档位于 `data/backups/`。
3. 更新 `docker-compose.yml` 中的镜像 tag。
4. 执行 `docker compose down`、`docker compose pull`、`docker compose up -d`。
5. 检查 `docker compose ps`、`/api/health` 和 Dashboard 中的 Bot 状态。

数据迁移属于当前版本的正常启动路径。发生失败时保留目录和日志，停止容器后手工恢复；程序不会自动停启、回滚或裁决中间状态。

## 业务数据迁移

创建存档、清空实例和导入只接受已停止 Bot。导入目标必须为空实例。不会迁移旧的 Manager 状态、控制 token、NapCat 或 LLOneBot 状态。

更改配置后 Dashboard 会返回 `restart_required`，请按页面提示重启 Bot。不要期待配置保存通过隐式控制通道立即生效。

历史 Release 文档可能描述旧的 Manager、三服务 Compose 或自动升级行为；它们只记录当时版本事实，不是当前部署指引。
