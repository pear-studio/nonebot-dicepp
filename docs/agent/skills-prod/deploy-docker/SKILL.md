---
name: deploy-docker
description: 在 Linux 生产环境执行 DicePP 相关 Docker/Compose 运维动作。当用户要求查看服务状态、日志、pull/up/restart 容器、应用镜像或管理 DicePP/LLOneBot 相关容器时使用。
license: MIT
metadata:
  author: DicePP
  version: "1.0"
---

# Deploy Docker

在 Linux 生产环境执行 DicePP 相关 Docker/Compose 运维动作。该技能处理执行层面的服务状态、日志、镜像拉取、容器更新和重启规则。

## 适用场景

- 用户要求查看 Docker/Compose 服务状态、容器日志或健康状态。
- 用户要求 pull/load 镜像、up/restart/stop/start DicePP 服务。
- 用户要求管理 LLOneBot 相关容器。
- 'version-deploy' 已确认版本变更, 需要执行 Docker/Compose 更新。

## 操作范围

第一版只允许操作 DicePP 部署相关资源：

- 当前项目 Docker Compose 中声明的 DicePP 服务, 包括 `bot` 和独立 `dashboard` service。
- DicePP 生产链路明确依赖的 LLOneBot 相关容器/服务。

默认禁止：

- 操作无关容器或无关 compose project。
- 执行 'docker system prune'。
- 删除 volume、数据库、配置、content 或运行时数据。
- 修改 Docker daemon、宿主机全局网络、防火墙或系统服务。
- 在未确认影响范围时 stop/restart 生产服务。

## Default Mode

- 状态查看和日志读取默认允许, 但不得输出 secrets。
- 会改变服务状态的操作必须先说明影响范围、具体命令、预期结果和回滚方式, 等待用户明确确认。
- 当操作来自 'version-deploy' 时, 仍需遵守该技能的确认结果和目标版本。

## Preferred Entrypoints

优先使用明确的 Docker Compose 命令，不调用项目 shell wrapper 或 Makefile 部署入口：

- DicePP bot 服务优先使用当前项目的 'docker compose'。
- DicePP 在线版本更新使用 `DICEPP_IMAGE_TAG=vX.Y.Z docker compose pull/up`；离线镜像包更新使用 `docker load` 导入目标镜像后执行 `DICEPP_IMAGE_TAG=vX.Y.Z docker compose up -d --pull never`。两种方式都默认作用于当前 compose project 的 DicePP 服务整体，而不是只更新单个 `bot` service。
- LLOneBot 操作前必须先识别其 compose 目录或容器名；无法确认时只做只读检查并要求用户提供路径。
- 禁止使用 `git pull`、本地 build 或项目部署 wrapper 更新生产。

## Read-only Checks

常用只读检查包括：

- 'docker compose ps'
- 'docker compose config --services'
- 'docker compose logs --tail <N> bot'
- 'docker compose logs --tail <N> dashboard'
- 'docker ps' 仅用于识别 DicePP/LLOneBot 相关容器, 不对无关容器执行操作。

只读检查仍应避免输出 token、cookie、session、密钥、完整敏感配置或二维码敏感内容。

## Confirmed Operations

以下操作必须等待用户明确确认：

- 'docker compose pull'
- 'docker load -i <image tar>'
- 'docker compose up -d'
- 'docker compose restart'
- 'docker compose stop' / 'docker compose start'
- 对已确认 LLOneBot compose project 执行 'docker compose up/down/restart'
- 任何会改变容器、镜像、网络或服务状态的命令

确认前必须展示：

- 将操作的服务名或容器名。
- 完整命令。
- 预期影响, 如短暂断连、服务重启、WebSocket 重连。
- 基本回滚方式。

## Version Deploy Integration

当 'version-deploy' 要在线应用镜像版本时, 推荐执行序列为：

1. 确认环境变量 'DICEPP_IMAGE_TAG' 已设为目标版本。
2. 执行 'docker compose config --services', 确认目标 compose 包含预期 DicePP 服务；v3.0.0 起通常应包含 `bot` 和 `dashboard`。
3. 执行 'DICEPP_IMAGE_TAG=vX.Y.Z docker compose pull'。
4. 执行 'DICEPP_IMAGE_TAG=vX.Y.Z docker compose up -d'。
5. 执行 'docker compose ps'。
6. 查看 'docker compose logs --tail 100 bot'；如果存在 `dashboard` service, 同时查看 'docker compose logs --tail 100 dashboard'。
7. 如项目提供健康检查或机器人指令验收方式, 汇报可执行项或已执行结果。

当 'version-deploy' 要离线应用镜像包时, 推荐执行序列为：

1. 确认目标离线包来自目标 Release: `DicePP-vX.Y.Z-linux-amd64-offline.zip` 和 `DicePP-vX.Y.Z-linux-amd64-offline.zip.sha256`。
2. 执行 `sha256sum -c DicePP-vX.Y.Z-linux-amd64-offline.zip.sha256`。
3. 执行 `unzip -o DicePP-vX.Y.Z-linux-amd64-offline.zip`。
4. 进入解压目录并执行 `sha256sum -c checksums.sha256`。
5. 执行 `zstd -d -f images/DicePP-vX.Y.Z-linux-amd64-images.tar.zst`。
6. 执行 `docker load -i images/DicePP-vX.Y.Z-linux-amd64-images.tar`。
7. 确认 `docker load` 输出包含 `ghcr.io/pear-studio/nonebot-dicepp:vX.Y.Z` 和 `ghcr.io/pear-studio/dicepp-dashboard:vX.Y.Z`。
8. 将离线包内 `docker-compose.yml` 同步到部署目录后, 执行 'DICEPP_IMAGE_TAG=vX.Y.Z docker compose up -d --pull never'。
9. 执行 'docker compose ps' 并查看 bot/dashboard 日志。

## Important Notes

- 不把 Docker 运维操作和版本选择混在一起；版本选择由 'version-deploy' 处理。
- 不调用 Makefile 或 scripts/deploy wrapper 执行生产部署。
- 不自动执行 destructive cleanup。
- 不修改持久化数据；备份与恢复需要用户明确授权和专门流程。
- 如果命令需要 sudo, 先说明原因和范围。
