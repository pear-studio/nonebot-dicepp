---
name: deploy-docker
description: 在 Linux 生产环境对 DicePP Docker/Compose 资源执行状态检查、日志读取、pull/load/up/restart/stop/start 和已确认的手工回退/首装版本更新命令。仅在 version-deploy 已判定 Manager 不适用并确认完整计划时作为受约束执行规范；普通容器运维仍由本技能独立展示影响并确认。
---

# Deploy Docker

处理 Linux Docker/Compose 执行层，不选择发布版本，不改变 `version-deploy` 的目标、Compose 上下文或确认范围。

## Manager 优先与适用范围（本节具有优先级）

本技能是两类场景的受约束执行器：

- `version-deploy` 已明确判定为手工回退的部署/回退；
- 没有可用 Manager 的首次安装或 legacy 拓扑。

正常版本更新先交给 `version-deploy`。如果 Manager 健康、API 兼容，并且目标是 Manager 当前选择的可安装候选，本技能不得以 Docker/Compose 命令绕过 Manager，也不得把“手工更新”包装成较窄的容器操作。Manager 的当前候选不是任意 tag 选择器；指定历史版本、回退版本或其他非当前候选由 `version-deploy` 决定是否进入手工路径。

只有收到同一会话内 `version-deploy` 已确认的手工计划，并且计划明确包含 `路径: 手工回退/首装`、Manager 不适用的原因、目标版本、服务范围、Compose 调用上下文、命令顺序和失败处理时，才可执行版本相关 Docker 操作。该计划是唯一确认来源，不得重复确认。

Manager 已接受 upgrade operation、Manager operation 仍活动、或其终态/恢复状态不明时，禁止执行任何与该版本变更相关的 Docker、Compose、镜像或回退命令；交还 `version-deploy` 监控持久 operation。不得为了“帮助恢复”而与 Manager 并行拉取、加载、重建服务或切换镜像。

## 资源边界

只允许操作：

- 当前 DicePP Compose project 中已识别的 `bot`、`dashboard` 及 Release 明确定义的 DicePP 服务；
- 已识别 compose project 或容器的 NapCat/LLOneBot 协议适配器。

禁止：

- 操作无关 container、service、network 或 volume；
- `docker system prune`；
- 删除 volume、数据库、配置、content 或运行时数据；
- 修改 Docker daemon、宿主机全局网络、防火墙或系统服务；
- 使用 `git pull`、本地 build 或部署 wrapper 更新生产；
- 未确认时改变容器或镜像状态。

## 确认所有权

- 任何选择、拉取、导入或应用 DicePP Release 镜像的请求都必须先进入 `version-deploy`，不得由本技能用一份较窄的 Docker 确认绕过版本审计。
- 不改变 DicePP 版本的独立 restart/stop/start 操作，以及已识别协议适配器的普通运维，由本技能展示服务范围、完整命令、影响和回滚方式，并等待明确确认。
- 从 `version-deploy` 进入时，若目标版本、服务范围、Compose 调用上下文、命令顺序和失败处理都与该会话中已确认的计划一致，直接执行，不重复确认。
- 任一参数或影响范围发生实质变化时，停止并交还 `version-deploy` 更新计划和确认。本技能不得自行扩大旧确认。

## Compose 调用上下文

开始时固定一个 Compose 调用前缀，后续命令全部复用。示例：

```bash
docker compose
docker compose -f docker-compose.yml -f docker-compose.prod.yml
```

使用同一前缀执行 `config`、`pull`、`up`、`ps` 和 `logs`，不得在某一步漏掉 `-f`、project directory、project name 或其他已确认参数。

版本更新时，对所有需要解析镜像的命令显式注入同一个 `DICEPP_IMAGE_TAG=<tag>`。不把它写入 `.env` 或 compose 文件。

## 只读检查

允许的常用检查：

- `<compose> config --services`
- `<compose> config`
- `<compose> ps`
- `<compose> logs --tail <N> <service>`
- `docker inspect`，仅用于已识别 DicePP/适配器容器的镜像和健康状态
- `docker image inspect`，仅用于计划涉及的明确镜像

输出 `config` 或日志前过滤 secret、token、cookie、session、二维码和完整敏感配置。`docker ps` 只用于识别范围，不对无关容器采取动作。

## 在线版本更新

收到 `version-deploy` 的已确认计划后：

1. 使用目标 tag 运行 `<compose> config --services` 和 `<compose> config`。
2. 确认服务和最终镜像与计划一致。
3. 执行 `DICEPP_IMAGE_TAG=<tag> <compose> pull`。
4. 执行 `DICEPP_IMAGE_TAG=<tag> <compose> up -d`。
5. 使用同一 `<compose>` 运行 `ps`、目标服务日志和健康检查。

默认作用于计划中的整个 DicePP Compose project，不擅自只更新单个 `bot` service。

## 离线版本更新

收到 `version-deploy` 的已确认计划后：

1. 只处理目标 Release 的明确离线包。
2. 在新临时目录解压，禁止覆盖式解压到部署目录。
3. 校验外层摘要（可用时）和包内 `checksums.sha256`。
4. 解压目标 image archive，执行已确认的 `docker load`。
5. 检查加载结果包含计划要求的 bot/dashboard 镜像。
6. 使用目标 tag 运行 `<compose> config`。
7. 执行 `DICEPP_IMAGE_TAG=<tag> <compose> up -d --pull never`。
8. 使用同一 `<compose>` 运行 `ps`、日志和健康检查。

离线路径禁止执行 `docker compose pull`。

## 普通容器运维

协议适配器操作前必须识别其 compose 目录或容器名。NapCat 通常位于独立 `napcat/` 目录，LLOneBot 位于 `llonebot/` 目录；无法确认时只做只读检查。

以下不涉及 DicePP 版本变更的操作必须在本技能独立展示计划并确认后执行：

- DicePP `restart`、`stop`、`start`；
- 已识别协议适配器的 `pull`、`load`、`up`、`restart`、`stop`、`start`；
- 任何改变已识别容器、镜像、网络或服务状态的命令。

如果需要 `sudo`，先说明原因和范围。

## 验证与回退

- 验证必须使用与变更相同的 Compose 上下文。
- 检查实际镜像 tag/image ID、Docker healthcheck、关键日志和 Release 定义的本地应用健康端点。
- 从 `version-deploy` 进入时，只执行其已确认的回退步骤；不得自行恢复数据。
- 禁止 destructive cleanup。失败时保留现场和必要日志，交还编排器判断。
