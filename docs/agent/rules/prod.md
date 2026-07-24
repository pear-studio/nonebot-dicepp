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
- 调用外部 API、LLM 或付费服务；下述版本部署只读审计例外除外。

### Version Audit Read Exception

用户已经要求部署、回退、升级或核对发布版本时，允许在部署确认前执行必要的审计。`version-deploy` 必须先作出 Manager 优先的路由判断：

- 读取本地 Git/Docker/Compose 状态。
- 通过获批准、经认证的 Manager 客户端或 API 读取 Manager 健康/API 兼容性、`/v1/releases/status`、当前 channel 和当前候选；不得输出认证凭据或敏感值。
- 仅当需要刷新当前候选时，允许调用一次 `/v1/releases/check`。该调用会访问发布源并持久化 Manager discovery 状态，是本规则对默认“无外部调用/无写入”的窄范围例外；调用后只能重新读取状态。
- `/v1/releases/check` 不得下载发布包、创建升级记录、调用 `/v1/releases/download`、`/v1/upgrades/preview` 或 `/v1/upgrades/confirm`，也不得拉取/加载镜像、修改部署或服务状态。
- 只有 Manager 路由不适用、已明确转入手工回退审计时，才可使用 `gh release view` 或等价的只读 GitHub GET 请求读取公开 Release、tag、产物元数据和目标版本文件，或查询最新正式 Release 作为候选。
- 确认手工目标 tag 和 origin 指向预期仓库后，允许执行 `git fetch --no-tags origin tag <tag>` 获取该精确 Release tag，用于读取目标版本文件和验证 commit。

目标 tag fetch 不得使用 `--force`、`--prune`、分支 refspec 或通配 tag，不得改变当前 branch/HEAD、checkout/reset/merge 工作树，也不得覆盖冲突的本地 tag。上述审计例外不允许下载文件到部署目录、远程写入、修改生产认证状态或调用无关 API。不得输出认证凭据或其他敏感值。

## Version Deployment

当用户要求部署、上线、更新代码、切换版本、回退、rollback、pull 新镜像、使用离线镜像包或应用某个 release 时，必须使用 version-deploy。version-deploy 是完整版本变更计划和确认的唯一所有者；同一份已确认计划交给 deploy-docker 执行时不得重复确认。

- 必须先判断 Manager 是否健康、API 兼容，且目标是否为 Manager 当前选择的兼容候选。条件满足时，Manager 是持久事务所有者；`version-deploy` 只在获得一次用户明确确认后，按 Manager 的 download → preview → confirm → status 生命周期发起并监控 operation，不得并行执行 Docker、Compose、Git 切换或镜像回退。
- Manager 正常路径不承诺任意历史 tag 安装或任意版本回退。指定目标/回退不在当前候选、首次安装或旧式拓扑、Manager 自升级或最低版本不匹配、Compose/部署架构/配置人工迁移、契约不兼容、`install_supported` 为 false、preview 不通过、或 Manager 不可用/失败时，才进入手工回退/首装路径。
- 手工路径由 version-deploy 展示完整计划并取得唯一确认；确认后才把相同的目标、服务范围、Compose 上下文、命令顺序和失败处理交给 deploy-docker。Manager 已接受 operation、operation 仍活动或其恢复状态不明时，禁止手工 Docker 接管。

- Manager 路径只消费 Manager 当前选择的兼容候选（通常是当前 channel 的最新合格版本），不把它当作任意 tag 选择器；手工路径以明确 Release tag 为单位。自动发现只选择正式版 `vX.Y.Z`；只有用户明确指定时才允许 `vX.Y.ZrcN`。不部署浮动的 `latest`、分支 HEAD 或“最新代码”。
- 目标 release 必须读取 docs/releases/vX.Y.Z.md 或 GitHub Release body 作为生产更新风险摘要；缺失时按未知风险处理。
- DICEPP_IMAGE_TAG 通过命令环境变量传递, 不写入配置文件。不得输出 secrets。
- 镜像回退不等于数据回退；涉及数据、配置、迁移或风险未知时，必须确认已有可靠备份，缺少备份时停止部署。
- 注入 DICEPP_IMAGE_TAG、pull/load 镜像、更新容器或重启服务前，必须展示计划并等待用户明确确认。
- 生产目录存在 Git checkout 时，审计阶段可受限 fetch 精确目标 tag；确认后切换到该 Release tag，再更新同 tag 镜像。tracked 工作区不干净时停止，不自动 stash/reset。禁止 `git pull master` 和生产本地 build。
- 生产 compose 存在本地定制时，可由 version-deploy 计划条件式 production override；所有 config/pull/up/ps/logs/验证和回退命令必须复用同一 Compose 文件列表与 project 上下文。
- 健康检查失败时只有满足 version-deploy 明示的自动回退资格才可恢复旧源码、compose 和镜像；不得自动恢复数据。

## Docker Deployment Ops

当用户要求查看或操作 Docker/Compose 服务、DicePP bot 容器、协议适配器（NapCat / LLOneBot）容器、服务日志、pull/load/up/restart/stop/start 时，必须使用 deploy-docker。

- 与版本变更有关的 Docker/Compose 操作只允许执行 `version-deploy` 已确认的手工回退/首装计划；可用 Manager 的正常更新不得由 deploy-docker 绕过。Manager operation 已被接受、仍活动或状态不明时，停止 Docker 操作并交还 version-deploy 监控持久状态。

- 第一版只允许操作 DicePP 部署相关资源：当前项目 compose 服务、DicePP bot、协议适配器（NapCat / LLOneBot）和项目文档明确关联的运维入口。
- 协议适配器操作只允许在识别到明确的 compose project（NapCat 在 `napcat/` 目录，LLOneBot 在 `llonebot/` 目录）或容器后执行；不要依赖项目内安装脚本或其他 wrapper。
- 默认禁止操作无关容器、执行 docker system prune、删除 volume、修改 Docker daemon 或宿主机全局网络/防火墙。
- 改变服务状态的 Docker/Compose/协议适配器操作必须先说明影响范围、命令、预期结果和回滚方式，等待用户明确确认。

## Development Handoff

生产环境发现需要代码修改、测试或 backlog 管理时，不在当前目录执行；使用 `prod-handoff-create` 交接到开发环境。

如本地 `docs/agent/.agent-env.json` 配置了开发环境 peer 路径，可由该技能将交接写入开发环境的 `.temp/prod-handoff/`。
