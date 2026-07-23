---
name: version-deploy
description: 在生产环境审计、部署或回退 DicePP 已发布版本。当用户要求部署、上线、更新代码或镜像、切换版本、回退、rollback、使用在线镜像或离线 Release 包，或只说“更新一下”但未指定版本时使用；消费 version-release 创建的正式版或用户明确指定的 RC Release。
---

# Version Deploy

作为生产版本变更的唯一编排器，完整持有目标版本、审计证据、部署计划、用户确认、执行状态、健康验证和回退判断。Docker/Compose 命令遵守 `deploy-docker`，但同一份已确认计划不得被重复确认。

## 不变约束

- 只在生产环境使用。
- 正式版必须形如 `vX.Y.Z`。只有用户明确指定时才允许 `vX.Y.ZrcN`；自动发现始终排除 draft 和 prerelease。
- 实际部署必须固定到明确 tag，禁止部署 `latest`、分支 HEAD、`master` 或 commit SHA。
- 目标 Release 必须由 `version-release` 创建。版本、Release metadata、目标 compose 和镜像 tag 必须相互一致。
- 运行时只使用 Release 镜像，不在生产执行本地 build。
- `DICEPP_IMAGE_TAG` 只通过命令环境变量传递，不写入配置文件。
- 不输出 secret、token、cookie、完整敏感配置或 Dashboard 会话凭据。
- 不自行制作数据备份、恢复数据或执行数据库操作。镜像回退不等于数据回退。
- 手工部署的条件式自动回退只覆盖当前不中断的 Agent 会话，不提供主机断电或 Agent 中断后的事务恢复。

## 单次确认

`version-deploy` 是版本变更的确认所有者。完成只读审计后，先展示一份完整计划，再等待一次包含目标 tag 的明确确认，例如：

- `确认部署 v3.1.0`
- `确认部署 v3.1.0rc2`
- `确认回退到 v3.0.0`

确认只授权展示过的目标、源码变更、compose 上下文、镜像路径、服务范围和回退策略。任一项发生实质变化时停止并重新展示计划；`deploy-docker` 不得自行扩大范围或沿用旧确认。

## 状态机

严格按以下状态推进，不跳过状态：

```text
Discover -> Audit -> Plan -> Confirm -> Execute -> Verify
                                      \-> Failure -> Rollback decision
```

确认前只允许生产规则列出的只读部署审计和受限目标 tag fetch。下载到部署目录、创建备份文件、改写 compose、pull/load 镜像、切换 Git 工作树和改变容器状态都只能在 Confirm 之后执行。

## Discover

1. 识别部署目录、Compose project 和预期 DicePP 服务，不操作无关容器。
2. 读取当前 compose 服务、运行容器、实际镜像引用和 image ID。
3. 判断部署目录是否包含 Git checkout。若存在，记录：
   - 当前 HEAD、branch/tag 和 detached/attached 状态；
   - tracked 文件是否干净，使用不包含 untracked 运行数据的状态检查；
   - 当前 `docker-compose.yml` 是否来自当前 Release。
4. 确定目标版本：
   - 用户给出明确 tag 时使用该 tag。
   - 用户只说“更新”“升级镜像”等模糊目标时，只读查询最新正式 GitHub Release，并把它作为候选；仍需用户确认具体 tag。
   - 不自动选择 RC，不把 `latest` 当作候选版本号。
5. 确定在线 GHCR 或用户提供的离线 Release 包路径。不要仅因 GHCR 暂时不可达就擅自改变路径。

## Audit

### Release 契约

按优先级读取目标 Release：

1. `gh release view <tag> --json ...` 或等价的只读 GitHub Release API。
2. 本地已有 tag 的 `git show <tag>:<path>`。
3. 生产目录有预期仓库 checkout 时，受限 fetch 目标 tag 后使用 `git show <tag>:<path>`。
4. 只读远端 tag 内容或已提供离线包内的文档。

读取并核对：

- GitHub Release body 中的 `数据变更`、`配置变更` 和 `Risk Notes`。
- 目标 `docs/linux.md`。
- 目标 `docker-compose.yml`。
- Release asset 名称、大小和可用摘要。
- bot 镜像 `ghcr.io/pear-studio/nonebot-dicepp:<tag>`。
- 目标 compose 包含 Dashboard 时的 `ghcr.io/pear-studio/dicepp-dashboard:<tag>`。

metadata、compose、镜像或 tag 相互矛盾时停止。无法读取或解析的字段记为 `unknown`，不得默认为安全。

### 源码同步审计

生产目录有 Git checkout 时，计划在确认后先把源码切到目标 Release tag，再更新镜像：

- 禁止 `git pull master`。
- 确认目标 tag 和 `origin` 指向预期仓库后，允许在审计阶段执行精确目标 tag fetch，例如 `git fetch --no-tags origin tag <tag>`。
- 审计 fetch 不得使用 `--force`、`--prune`、分支 refspec 或通配 tag，不得 checkout、merge、rebase、reset，也不得改变当前 branch/HEAD 或工作树。
- 本地同名 tag 与远端不一致时停止，不强制覆盖。
- tracked 工作区不干净时停止，不自动 stash、reset、覆盖或清理。
- 记录旧 commit/tag，供回退使用。
- 切换 tag 只同步发布源码、compose 和文档，不得删除 `config/`、`data/`、`content/`、`dashboard/data/` 或其他持久化数据。

生产目录没有 Git checkout 时，继续使用 Release asset/离线包提供的目标 compose，不要求为了部署临时 clone 仓库。

### Compose 对比

比较当前生产 compose、当前版本标准 compose和目标版本 compose，至少核对：

- service、image、environment；
- port、volume、network；
- healthcheck、restart policy；
- 新增或删除的持久化路径。

不要手工发明目标部署拓扑，以目标 Release 的 compose 和 `docs/linux.md` 为准。

#### 条件式生产 override

只有发现生产 compose 存在确有必要的本地定制时，才计划使用独立生产 override：

- 标准 `docker-compose.yml` 保持与目标 tag/Release 一致。
- 本地端口、宿主机挂载路径、外部网络等生产差异可提取到独立文件。
- secret 不写入 override。
- override 不得改变目标镜像 tag、核心 service 身份或启动命令。
- 无法安全分离的定制必须停止并让用户裁决。

使用 override 时，生成一次明确的 Compose 调用上下文，例如：

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml
```

后续 `config`、`pull`、`up`、`ps`、`logs`、验证和回退必须复用完全相同的文件列表与 project 上下文。先用目标 `DICEPP_IMAGE_TAG` 运行 `config`，检查合成后的服务、镜像、端口、volume 和 network。

无本地定制时不创建 override，不增加常规部署步骤。

### 备份门槛

以下任一成立时，确认前必须要求用户明确说明可靠备份已经完成；没有备份则停止：

- `数据变更: yes`；
- `配置变更: yes`；
- 任一字段为 `unknown`；
- Risk Notes 要求迁移、备份或人工恢复步骤。

`version-deploy` 不用在线压缩正在写入的数据冒充一致性备份。当前手工路径只接受已有备份作为前置条件。

### 自动回退资格

计划中必须给出 `自动回退资格: yes/no` 及原因。只有同时满足以下条件才为 `yes`：

- 没有数据迁移或数据格式变更；
- 没有不可逆配置变更，且配置/compose 变更有已验证的旧版本副本；
- 生产目录有 Git checkout 时已记录旧源码 commit/ref 及 detached/attached 状态；没有 checkout 时源码恢复为 `n/a`；
- 已记录旧 compose 调用上下文及最终配置；
- 已记录旧运行镜像的明确 tag 和 image ID；
- 旧镜像仍在本机可用；
- Release metadata 完整且未声明禁止降级。

风险未知、需要恢复数据、旧镜像不可用或无法恢复旧 compose 时一律为 `no`。自动回退不得恢复数据备份。

## Plan

确认前展示以下内容：

```text
动作: 部署/回退
当前源码: <commit/tag 或 none>
当前运行镜像: <bot；dashboard 如适用>
目标版本: <明确 tag，并标记 stable/RC>
目标源码: <同一 tag 或 none>
路径: 在线 GHCR / 离线 Release 包
当前服务: <list>
目标服务: <list>
Compose 同步: no / 替换标准文件 / 标准文件 + production override
Compose 调用上下文: <完整命令前缀>
数据变更: yes/no/unknown
配置变更: yes/no/unknown
备份状态: 不需要 / 用户已确认 / 缺失
Risk Notes: <摘要>
预计影响: <重建服务、短暂断连等>
健康检查: <必需检查>
自动回退资格: yes/no
失败处理: <自动回退步骤或停止并保留现场>
将执行的命令: <按顺序列出，敏感值脱敏>
```

Compose 同步、源码切换、镜像操作和服务更新必须合并在这一份计划中，不拆成重复确认。

## Execute

用户确认后按顺序执行。任一步失败都停止前进并进入 Failure：

1. 若有源码 checkout：
   - 再次确认 tracked 工作区干净；
   - 必要时重复已展示的精确目标 tag fetch，并重新验证目标 commit；
   - 验证本地 tag 与审计到的目标 Release commit 一致；
   - 切换到目标 tag 的 detached HEAD；
   - 确认 HEAD、版本文件和目标 compose 均属于该 tag。
2. 准备并验证目标 compose：
   - 无源码时从已验证的 Release 输入同步标准 compose；
   - 需要 override 时只应用计划中展示的生产差异；
   - 保留可恢复的旧 compose 文件；
   - 使用完整 Compose 上下文和目标 `DICEPP_IMAGE_TAG` 运行 `config`。
3. 在线路径：
   - 使用完整 Compose 上下文执行 `DICEPP_IMAGE_TAG=<tag> ... pull`；
   - 执行 `DICEPP_IMAGE_TAG=<tag> ... up -d`。
4. 离线路径：
   - 校验外层 Release asset 摘要（可用时）；
   - 解压到新的临时目录，不对部署目录执行覆盖式 `unzip -o`；
   - 校验包内 `checksums.sha256`；
   - 解压并 `docker load` 镜像 archive；
   - 确认输出包含目标 bot/dashboard 镜像；
   - 执行 `DICEPP_IMAGE_TAG=<tag> ... up -d --pull never`，禁止 `docker compose pull`。

所有 Docker 操作遵守 `deploy-docker` 的资源边界。不要操作无关 service、container、network 或 volume。

## Verify

部署后执行并汇报：

1. 使用同一 Compose 上下文运行 `config` 和 `ps`。
2. 检查实际运行镜像 tag/image ID 与目标一致。
3. 等待 Release 定义的 Docker healthcheck 通过；没有 healthcheck 时明确标记 `n/a`。
4. 检查 bot 和 dashboard 关键启动日志，不输出敏感内容。
5. 调用目标 Release 明确定义的本地 Bot/Dashboard 健康端点。目标未定义某端点时不要猜测 URL，标记 `n/a` 并使用持续运行状态与关键日志作为降级证据。
6. 在较慢的应用探针结束后再次确认容器仍持续运行。

QQ 协议端、GitHub、LLM、语音或图片等外部依赖故障只作为警告，不单独判定本地部署失败。真实机器人指令验收仅在环境支持且用户要求时执行。

## Failure 与回退

- 写操作开始前失败：不需要回退，汇报阻塞点。
- 写操作开始后失败且 `自动回退资格: yes`：立即按计划恢复旧源码 commit/ref 及 detached/attached 状态、旧标准 compose/override 上下文和旧镜像，使用 `--pull never` 重建旧服务，再执行同等级本地健康检查。
- 自动回退成功：明确汇报目标部署失败、旧版本已恢复和剩余证据。
- 自动回退失败：停止进一步变更，保留日志和现场，不反复重试。
- `自动回退资格: no`：不擅自切回镜像或恢复数据；停止并向用户展示失败阶段、当前实际状态和人工恢复选项。

## 最终汇报

始终汇报：

- 目标版本与 stable/RC；
- 源码切换结果；
- 最终 compose 上下文；
- 最终运行镜像和服务健康；
- 备份前置条件；
- 自动回退是否触发及结果；
- 警告、未执行项和需要人工处理的事项。
