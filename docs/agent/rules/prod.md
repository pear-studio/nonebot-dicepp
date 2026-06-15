# DicePP Production Rules

当前目录是 DicePP 生产环境。默认只读，优先诊断、确认事实、整理风险和交接开发环境。

## Default Mode

- 默认只读：诊断、状态确认、日志分析、版本确认和风险说明。
- 默认不修改当前项目下的仓库文件、运行配置、数据文件、数据库或服务状态。
- 默认不在当前项目下创建报告文件。
- 可读取非敏感配置模板和运行证据；敏感值只报告是否存在，不输出内容。

## Confirmation Required

以下操作必须先说明影响范围、回滚方式和具体命令，等待用户明确确认：

- 改写当前项目、运行配置、依赖、数据文件或数据库。
- 改变生产服务状态，如启动、停止、重启、迁移、清理或修复。
- 执行发布、版本、git 写操作或远程写入。
- 调用外部 API、LLM 或付费服务。

## Version Deployment

当用户要求部署、上线、更新代码、切换版本、回退、rollback、pull 新镜像或应用某个 release 时，必须使用 version-deploy。

- 生产发布/回退以 vX.Y.Z release 为单位，不默认部署分支 HEAD 或“最新代码”。
- 目标 release 必须读取 docs/releases/vX.Y.Z.md 作为生产更新风险摘要；缺失时按未知风险处理。
- 生产 .env 中只允许按白名单读取/修改 DICEPP_IMAGE_TAG，不得整段输出 .env。
- 镜像回退不等于数据回退；涉及数据、配置、迁移或风险未知时，必须确认备份状态或明确接受风险。
- 修改 DICEPP_IMAGE_TAG、pull 镜像、更新容器或重启服务前，必须展示计划并等待用户明确确认。

## Docker Deployment Ops

当用户要求查看或操作 Docker/Compose 服务、DicePP bot 容器、LLOneBot 容器、服务日志、pull/up/restart/stop/start 时，必须使用 deploy-docker。

- 第一版只允许操作 DicePP 部署相关资源：当前项目 compose 服务、DicePP bot、LLOneBot 相关容器和项目文档明确关联的运维入口。
- LLOneBot 操作优先使用项目已有 Makefile 或 scripts/deploy/linux/llonebot/ 入口，不临场自由拼接无边界 Docker 命令。
- 默认禁止操作无关容器、执行 docker system prune、删除 volume、修改 Docker daemon 或宿主机全局网络/防火墙。
- 改变服务状态的 Docker/Compose/LLOneBot 操作必须先说明影响范围、命令、预期结果和回滚方式，等待用户明确确认。

## Development Handoff

生产环境发现需要代码修改、测试或 backlog 管理时，不在当前目录执行；使用 `prod-handoff-create` 交接到开发环境。

如本地 `docs/agent/.agent-env.json` 配置了开发环境 peer 路径，可由该技能将交接写入开发环境的 `.temp/prod-handoff/`。
