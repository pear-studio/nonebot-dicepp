# DicePP Production Rules

当前目录是 DicePP 生产环境。默认只读；先确认事实、整理风险，再请求用户确认任何写操作。

## 默认与确认

- 默认只读：检查版本、Compose、容器状态、健康检查和日志时不得输出 secret、token、cookie 或完整敏感配置。
- 默认不修改代码、配置、数据、数据库或服务状态。
- 发布、版本切换、pull、up、stop、restart、迁移、清理和远程写入都必须先说明影响范围、命令和回滚方式，等待用户明确确认。
- 需要代码修改时，使用 `prod-handoff-create` 交接到开发环境，不在生产目录改代码。

## 当前部署合同

- Linux 只有一个 `dicepp` Compose service，使用 `ghcr.io/pear-studio/nonebot-dicepp:<tag>`；`DICEPP_IMAGE_TAG=latest` 表示当前最新正式镜像。
- Compose 文件固定为目标 Release 提供的 `docker-compose.yml`，不得拼接隐藏 override、Docker Socket 或其他控制服务。
- 持久化目录是 `config/`、`data/`、`content/`、`dashboard/data/` 和兼容旧存档位置 `manager/backups/`。
- Windows 只使用 Portable ZIP：`DicePP.exe` 直接控制同目录的 `DicePP-Runtime.exe`，不使用 Setup、Velopack 或在线安装器。

## 版本更新

用户要求部署、更新、切换版本或回退时使用 `version-deploy`；实际 Docker/Compose 命令由 `deploy-docker` 执行。

- 目标使用明确的 `vX.Y.Z` tag，或用户明确选择的 `latest`；不得使用分支 HEAD 或本地 build。
- 更新前确认 Bot 已停止并导出重要存档；镜像回退不回退业务数据。
- Linux 正常更新使用同一 Compose 文件和明确 tag：

  ```bash
  DICEPP_IMAGE_TAG=<tag> docker compose config
  DICEPP_IMAGE_TAG=<tag> docker compose pull
  DICEPP_IMAGE_TAG=<tag> docker compose up -d
  ```

- Windows 更新是下载新的 Portable ZIP、停止旧实例、解压到新目录并按需导入配置/业务数据。
- 失败时停止并保留现场、日志和持久化目录；不自动恢复数据、不实现跨进程事务或自动回滚。

## Docker 操作

需要检查或操作 DicePP Docker/Compose、日志、NapCat/LLOneBot 适配器时使用 `deploy-docker`。只操作已确认的 DicePP Compose project 和明确的适配器资源；禁止 Docker Socket、无关容器、`docker system prune`、删除 volume 或宿主机全局网络修改。
