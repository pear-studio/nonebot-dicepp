---
name: version-deploy
description: 在生产环境审计、确认和部署 DicePP 已发布版本。用户要求更新、上线、切换版本、回退、pull 镜像、使用 Portable 或 Compose Release 时使用。
---

# Version Deploy

作为生产版本变更的唯一编排器，负责选择明确 tag、展示计划、取得一次确认、调用固定部署命令并验证结果。当前没有 Standalone Manager、远程 API、持久 operation 或自动升级路径。

## Discover

1. 读取当前 `docker-compose.yml`、`docker compose ps`、实际镜像 tag、健康状态和关键日志。
2. 识别当前版本和持久化目录，不读取或输出 secret、token、cookie 或完整配置。
3. 目标使用用户指定的 `vX.Y.Z`；用户明确要求跟随最新正式版时使用 `latest`。不要部署分支 HEAD 或本地 build。
4. 读取目标 Release 的发布说明、`docker-compose.yml` 和 Windows Portable 资产；不执行下载或服务写操作。

## Plan and Confirm

展示一次完整计划：当前版本、目标 tag、Compose 文件、唯一 `dicepp` service、持久化目录、预计断连、健康检查、备份状态和失败处理。目标 tag、服务范围或 Compose 上下文发生变化时重新确认。

更新前要求用户确认重要存档已导出，并停止 Bot。镜像切换不回滚业务数据；异常时停止并保留现场，不自动恢复数据或构造复杂回滚链路。

## Execute

确认后，Linux 始终复用同一个 Compose 文件和项目上下文：

```bash
DICEPP_IMAGE_TAG=<tag> docker compose config
DICEPP_IMAGE_TAG=<tag> docker compose pull
DICEPP_IMAGE_TAG=<tag> docker compose up -d
```

跟随最新正式镜像时将 `<tag>` 替换为 `latest`。不添加 Manager service、4091、Docker Socket、控制 token 或隐藏 override。

Windows 更新只处理 Portable ZIP：停止旧目录中的 DicePP，下载并校验 ZIP，解压到新目录，保留或按需导入用户数据，再启动 `DicePP.exe`。

## Verify

使用同一 Compose 上下文检查 `config`、`ps`、healthcheck、Dashboard `/api/health` 和关键日志；确认镜像引用与目标 tag 一致。外部 QQ、LLM、语音或图片依赖故障只作为警告。

失败时停止前进，报告失败命令和当前实际状态，保留持久化目录与日志，交给用户决定人工处理。
