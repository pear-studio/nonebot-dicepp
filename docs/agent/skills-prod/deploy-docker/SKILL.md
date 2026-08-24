---
name: deploy-docker
description: 在已确认的生产计划中操作 DicePP 单容器 Docker Compose，执行状态检查、日志读取、pull、up、restart、stop 和 start。
---

# Deploy Docker

处理 Linux 单容器 Docker/Compose 执行层，不选择版本、不改变 `version-deploy` 的目标和确认范围。当前部署只有 `dicepp` service，同时运行 Dashboard 和 Bot；不存在 Manager、4091、Docker Socket 或跨进程控制服务。

## 资源与确认

- 只操作已确认的 DicePP Compose project、`dicepp` service 和明确识别的 NapCat/LLOneBot 适配器。
- 版本相关的 pull、load、up 必须来自 `version-deploy` 已确认的计划；普通 stop/start/restart 也要先展示影响范围、命令和预期结果并等待确认。
- 禁止无关容器、`docker system prune`、删除 volume/数据库/配置/content、修改 Docker daemon、宿主机网络或防火墙。
- 不使用 Docker Socket，不执行本地生产 build，不输出 secret、token、cookie、session 或完整敏感配置。

## 固定 Compose 上下文

开始时确定 Compose 文件、project directory 和 project name；后续 `config`、`pull`、`up`、`ps`、`logs` 和验证全部复用同一上下文。

版本更新使用同一个镜像 tag：

```bash
DICEPP_IMAGE_TAG=<tag> docker compose config
DICEPP_IMAGE_TAG=<tag> docker compose pull
DICEPP_IMAGE_TAG=<tag> docker compose up -d
```

`<tag>` 可以是明确的 `vX.Y.Z` 或用户确认的 `latest`。不修改 `.env` 或 Compose 文件来保存 tag。

## 检查与验证

允许读取：

```bash
docker compose config --services
docker compose config
docker compose ps
docker compose logs --tail 200 dicepp
```

更新后检查实际镜像 tag、healthcheck、Dashboard `/api/health` 和关键启动日志。NapCat/LLOneBot 只在识别到其独立 Compose project 或容器后操作。

失败时停止并保留日志、Compose 文件和持久化目录，不删除现场、不恢复数据、不擅自切换其他版本；将当前状态交回 `version-deploy` 或用户处理。
