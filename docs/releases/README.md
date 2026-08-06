# DicePP 发版系统

## 架构概览

```
dev (开发环境)                         prod (生产环境)
─────────────                         ─────────────
version-release (skill)               version-deploy (skill)
  │                                     │
  ├─ bump version + release commit       ├─ gh release view vX.Y.Z
  ├─ write docs/releases/vX.Y.Z.md         → 从 Release body 读取风险元数据
  ├─ Final Candidate(run + artifact)      → 展示给用户确认
  │    → 构建/测试/封存全部最终字节       └─ deploy-docker → compose sync + pull/load + up
  └─ Promote(explicit run + artifact)
       → 校验 receipt / 每个文件 / manifest digest
       → 先建 draft Release，上传并复验原字节
       → 晋升 vX.Y.Z 镜像，发布时创建 tag；正式版最后更新 latest
```

## 关键文件

| 文件 | 用途 |
|------|------|
| `pyproject.toml` `[project].version` | 版本号唯一真相源 |
| `src/.../declare.py` `get_bot_version()` | 运行时版本读取（从 importlib.metadata） |
| `docs/releases/vX.Y.Z.md` | 每个 release 的 changelog 与风险摘要（数据变更 / 配置变更 / Risk Notes）；作为 GitHub Release body 提供 |
| `docker-compose.yml` | 部署入口；包含 bot、dashboard 与 manager 三个标准 service；生产默认使用 `image:` 发布镜像，`build:` 仅作开发/应急构建 |
| `docs/linux.md` | Linux Docker 部署说明；打入 Linux 发布包，也可从 tag 内容读取 |
| `Dockerfile` | 多阶段构建，第三方依赖层与源码层分离，`uv sync --frozen` 可复现 |
| `.github/workflows/candidate.yml` | 对精确的 master commit 构建、测试并封存最终发布字节，输出唯一 run/attempt artifact 与 receipt |
| `.github/workflows/release.yml` | 显式选择 candidate run ID + artifact ID，验证来源和摘要后原字节晋升；不重建或重新打包 |
| `dicepp-candidate.json` | 候选 receipt；绑定 workflow/run/attempt/commit、工具链、容器 identities 和全部 Release asset 的 size/SHA-256 |
| `dicepp-release.json` | Manager 消费的严格 machine contract；声明频道、兼容性、平台/架构和 artifact size/SHA-256 |
| `velopack.win-x64.zip` 内 `manifest.json` | Windows 更新层 contract；声明 DicePP/Velopack 版本、频道、目标及唯一 full nupkg 的 size/SHA-256 |
| Linux 包内 `dicepp-package.json` | Linux 安装层 contract；声明 Compose、image archive、镜像引用及内部文件摘要 |

## 版本号

- **唯一源**: `pyproject.toml` → `[project].version`
- **运行时**: `importlib.metadata.version("dicepp")` 返回 `"X.Y.Z"`
- **展示**: `get_bot_version()` 返回 `"vX.Y.Z"`
- **变更**: `uv run bump-my-version bump patch|minor|major` 或 version-release 技能
- **约束**: 不在代码中硬编码版本字符串

## 镜像

- **Registry**:
  - `ghcr.io/pear-studio/nonebot-dicepp`
  - `ghcr.io/pear-studio/dicepp-dashboard`
- **Tags**: Candidate 使用 `:candidate-{run_id}-{run_attempt}`；正式版打 `:vX.Y.Z`
  和 `:latest`；RC 只打同名 `:vX.Y.ZrcN`
- **构建触发**: 对精确 master commit 手工 dispatch Final Candidate；构建阶段不存在版本 tag 或 GitHub Release
- **构建方式**: `uv sync --no-dev --frozen`，依赖由 `uv.lock` 锁定
- **分发**: `docker-compose.yml`、Windows Portable/Setup、
  `velopack.win-x64.zip`、
  `DicePP-vX.Y.Z-linux-amd64.zip`、`dicepp-release.json` 和
  `dicepp-candidate.json` 作为 GitHub Release assets；未来允许自动升级时再条件附加
  `dicepp-upgrade-evidence.json`。`docs/releases/vX.Y.Z.md` 作为 Release body

## Docker Compose 模式

同一份 `docker-compose.yml` 同时包含 `image:` 和 `build:`。生产部署只使用发布镜像；源码构建仅用于开发或 GHCR 长期无法拉取时的应急验证。

对已处于标准拓扑、要升级兼容最新 Release 的用户，首选 Dashboard 中的 Manager 更新流程。下表和 `version-deploy` 处理的是首次部署、旧部署迁入、指定版本/回退、破坏性 Manager 交接变化（handoff 协议、Manager state、deployment schema、Compose 运行契约或安装布局不兼容）或 Compose/deployment schema 迁移等手工兜底情形；普通 Manager 代码变化随自动升级切换，不属于手工兜底。

| 场景 | 变量 | 行为 |
|------|------|------|
| 生产部署 | `DICEPP_IMAGE_TAG=v3.0.0` | `docker compose pull` → `up -d` |
| 回退到指定版本 | `DICEPP_IMAGE_TAG=v3.0.0` | `docker compose pull` → `up -d` |
| 离线部署/更新 | `DICEPP_IMAGE_TAG=v3.0.0` | `unzip` → `sha256sum -c checksums.sha256` → `zstd -d` → `docker load` → `up -d --pull never` |
| 临时使用其他 registry | `DICEPP_IMAGE=registry.example.com/ns/nonebot-dicepp:v3.0.0` | `docker compose pull` → `up -d` |
| 临时替换 Dashboard registry | `DASHBOARD_IMAGE=registry.example.com/ns/dicepp-dashboard:v3.0.0` | `docker compose pull` → `up -d` |
| 开发/应急源码构建 | 不设镜像变量 | `docker compose build` → `up -d` |

手工 Docker 更新前应先确认当前 `docker-compose.yml` 是否与目标 Release 的部署拓扑一致。新增 service、环境变量、volume 或端口映射时，必须先同步 Release 附带的完整三服务 `docker-compose.yml` 或按 `docs/linux.md` 的部署说明合并标准块，再执行 `pull` / `up -d`。Manager 不会自动改写这份文件。

## 远端仓库启用契约

启用 Promotion 前，仓库管理员只需完成以下一次性配置：

- 启用 GitHub Immutable Releases，使公开后的 Release、assets 和关联 tag 不可变。
- 为 `refs/tags/v*` 建立 active tag ruleset，同时限制 creation、update 和 deletion，
  并允许 GitHub Actions 创建发布 tag。
- 分别为 `pear-studio/nonebot-dicepp` 和 `pear-studio/dicepp-dashboard` 两个 GHCR
  package 授予本仓库 Write access，使 workflow 的 `GITHUB_TOKEN` 可以推送候选镜像
  并按已验证 digest 添加正式 tag。

这些设置由管理员一次性启用，不在每次 Promotion 中重复读取管理配置。除 workflow
自动获得的 `GITHUB_TOKEN` 外，发布不要求额外凭据、管理员设置 ID 变量或人工审批门禁。
“本地实现与测试通过”不表示远端已经启用；首次真实晋升前仍应确认以上三项已经完成。
本次仓库代码变更不会创建或修改这些远端设置。

Workflow 内所有关键 actions 都固定完整 commit SHA。维护时先解析受信版本 tag、审查
release notes 与目标 commit，再更新 SHA 及相邻版本注释；不得退回浮动 major tag 或分支。

## Release 流程

### 正常发布 (version-release 技能)

1. 确认工作区干净，在 master 分支
2. 选择递增级别 (patch/minor/major)
3. 创建 `docs/releases/vX.Y.Z.md`（风险元数据）
4. 以 `--no-commit --no-tag` 运行 `bump-my-version`，同步 `uv.lock` 后创建一个
   不含 tag 的 release commit
5. 在当前 HEAD 上运行完整回归 `uv run pytest`，只推送 `master` commit
6. 对该 commit dispatch `.github/workflows/candidate.yml`；等待成功后记录
   `candidate_run_id`、`run_attempt`、`candidate_artifact_id` 和 artifact digest
7. 使用相同 version、显式 run ID 和 artifact ID dispatch `.github/workflows/release.yml`
8. Promotion 验证 run/workflow/event/conclusion/head SHA/attempt、artifact 归属与摘要、
   receipt、每个文件和容器 identity；然后先创建 draft Release，上传并复验候选原字节，
   晋升版本镜像，在 publish 紧邻时点重验 HEAD/draft/assets/tag/digests，最后
   发布 Release（此时创建 tag），并在正式版最后更新 `latest`

Candidate artifact 名为 `dicepp-final-candidate-{run_id}-{run_attempt}`，扁平包含
Windows Portable/Setup、Velopack bundle、Linux amd64 bundle、`docker-compose.yml`、
`dicepp-release.json`、receipt，以及 `自动升级: yes` 时与该候选绑定的
`dicepp-upgrade-evidence.json`。Promotion 不执行构建、`vpk`、`docker save` 或 zip。

在 backlog `B-260802-3e3e23` 完成并通过明确验收前，所有 release metadata 的
`自动升级` 必须填写 `no`；现有证据链能力不等于该 backlog 已完成。

`manager` 变更范围本身不再是一律手工的标志。变更 Manager 的 Linux 发布必须
在 release manifest 与 bundle 内层 manifest 中声明受支持的
`linux_manager_handoff_protocol`（当前 v1），并以真实 Linux 候选矩阵
（rc20 手工基线 → 下一候选的 `manager_handoff_*` 场景）和 Windows 自身矩阵
证据共同支撑 `自动升级: yes`；协议字段缺失或不受支持、破坏性交接变化，或
任一平台缺乏真实字节证据时保持 fail closed 手工迁移，全局 `automatic_upgrade`
保持 `no`。

Sealed candidate artifact 的 retention 是 30 天，因此 Promotion 必须在 candidate
run 完成后 30 天内执行；同时还要求 candidate `head_sha` 仍是当前 default branch
HEAD，所以 master 前进会让候选提前失效，必须重新生成 candidate。

当前不自动清理候选 GHCR 镜像。

已有 tag、Release 或 GHCR tag 的身份、目标 commit、metadata/assets 或 digest 任一
不一致时立即停止。精确同身份的中断 draft、已上传资产或同 digest 镜像仅为失败恢复
允许幂等续传或复核成功，不覆盖或改写已发布资源。

Promotion 全局串行，不按版本并发。GitHub Release 与 GHCR 是当前唯一发布目标。
当前不做 Gitee 镜像同步，恢复需单独设计并经用户确认。

### 基线建立

`pyproject.toml` 已有目标版本号时（如 `3.0.0`），不递增版本：

1. 确认 `docs/releases/vX.Y.Z.md` 就绪
2. 确认所有代码已 commit
3. 在当前 HEAD 上运行完整回归 `uv run pytest`
4. 只推送 baseline commit，按正常发布步骤 6–8 生成并显式晋升候选
5. 不手工创建或推送 tag；tag 只由 Promotion 在完整验证后创建

### 手工部署与回退（version-deploy 技能）

1. 读取目标版本 `vX.Y.Z`
2. 通过 `gh release view vX.Y.Z --json body`、Release asset 或 `git show` 读取风险元数据，作为人工部署和回滚前的风险检查材料
3. 读取目标版本的 `docs/linux.md` / Linux 发布包内置部署说明
4. 对比生产 `docker-compose.yml` 与目标 Release 的 compose 拓扑，必要时先计划同步 compose
5. 展示影响范围，等待用户确认
6. 在线路径注入 `DICEPP_IMAGE_TAG=vX.Y.Z`，调用 deploy-docker 执行 compose sync + pull + up；离线路径先 `docker load` 目标离线包，再执行 `up --pull never`

## 用户操作速查

兼容最新版本的日常升级先使用 Dashboard 的 Manager 更新页。下面命令仅用于已经确认需要手工操作的场景；执行前创建并验证归档。

```bash
# 手工兜底：生产环境（镜像部署）
DICEPP_IMAGE_TAG=v3.0.0 docker compose pull
DICEPP_IMAGE_TAG=v3.0.0 docker compose up -d
DICEPP_IMAGE_TAG=v3.1.0 docker compose pull && DICEPP_IMAGE_TAG=v3.1.0 docker compose up -d  # 更新

# 生产环境（离线包）
VERSION=v3.0.0
unzip -o DicePP-${VERSION}-linux-amd64.zip
cd DicePP-${VERSION}-linux-amd64
sha256sum -c checksums.sha256
zstd -d -f images/DicePP-${VERSION}-linux-amd64-images.tar.zst
docker load -i images/DicePP-${VERSION}-linux-amd64-images.tar
cd ..
DICEPP_IMAGE_TAG=${VERSION} docker compose up -d --pull never

# 小白首次离线部署（从零开始）
# 先确认服务器已有 Docker Compose、unzip 和 zstd；安装方法见下方完整 Linux 文档。
VERSION=v3.0.0

# 二选一取得发布包：服务器可访问 GitHub 时执行这一段。
BASE_URL="https://github.com/pear-studio/nonebot-dicepp/releases/download/${VERSION}"
curl -L -O "${BASE_URL}/DicePP-${VERSION}-linux-amd64.zip"

# 服务器不能访问 GitHub 时，不执行上面的 curl；在有网设备下载同名文件后传入当前目录：
# scp "DicePP-${VERSION}-linux-amd64.zip" 用户名@服务器IP:~/dicepp/

# 发布包包含与该版本匹配的 Compose、镜像和校验清单。
PACKAGE_DIR="DicePP-${VERSION}-linux-amd64"
unzip -o "DicePP-${VERSION}-linux-amd64.zip" -d "${PACKAGE_DIR}"
cd "${PACKAGE_DIR}"
sha256sum -c checksums.sha256
cp docker-compose.yml ..
zstd -d -f "images/DicePP-${VERSION}-linux-amd64-images.tar.zst"
docker load -i "images/DicePP-${VERSION}-linux-amd64-images.tar"
cd ..
docker network inspect dice-net >/dev/null 2>&1 || docker network create dice-net
DICEPP_IMAGE_TAG=${VERSION} docker compose up -d --pull never
```

`dicepp-release.json` 不需要在首次部署时手工下载；它仅供 Manager 在标准部署启动后发现和校验可用版本，不是 Compose 文件或安装脚本。完整的在线、离线传输和初始化说明见[完整 Linux 部署说明](../linux.md)。

## 约束

- DicePP 不依赖根目录 `.env`；NoneBot 监听参数由 `bot.py` 默认值提供
- Prod 由 agent skill 保证不执行 build 命令
- 生产主路径是发布镜像；源码构建只作为开发/应急 fallback
- 镜像构建使用官方源，国内开发者通过 compose build args 可覆盖为清华源
- Release body 不进 Docker 镜像，继续供人工阅读；Manager 只消费
  `dicepp-release.json`，发现和下载不会修改当前 runtime
