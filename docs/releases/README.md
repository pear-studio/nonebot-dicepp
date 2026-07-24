# DicePP 发版系统

## 架构概览

```
dev (开发环境)                         prod (生产环境)
─────────────                         ─────────────
version-release (skill)               version-deploy (skill)
  │                                     │
  ├─ bump version + tag vX.Y.Z          ├─ gh release view vX.Y.Z
  ├─ write docs/releases/vX.Y.Z.md        → 从 Release body 读取风险元数据
  ├─ create GitHub Release               → 展示给用户确认
  └─ tag triggers GHA → build images     └─ deploy-docker → compose sync + pull/load + up
       → nonebot-dicepp:vX.Y.Z
       → dicepp-dashboard:vX.Y.Z
       → latest（正式版）
       → linux-amd64 release zip
       → Windows Portable / Setup / Velopack feed
       → dicepp-release.json
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
| `.github/workflows/release.yml` | tag push 触发 GHCR、Windows Velopack、Linux 发布包和 machine contract 构建，并创建 GitHub Release |
| `dicepp-release.json` | Manager 消费的严格 machine contract；声明频道、兼容性、平台/架构和 artifact size/SHA-256 |
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
- **Tags**: 正式版打 `:vX.Y.Z` 和 `:latest`；RC 只打同名 `:vX.Y.ZrcN`
- **构建触发**: push `v*.*.*` tag
- **构建方式**: `uv sync --no-dev --frozen`，依赖由 `uv.lock` 锁定
- **分发**: `docker-compose.yml`、Windows Portable/Setup/Velopack feed、
  `DicePP-vX.Y.Z-linux-amd64.zip` 和 `dicepp-release.json` 作为 GitHub
  Release assets；`docs/releases/vX.Y.Z.md` 作为 Release body

## Docker Compose 模式

同一份 `docker-compose.yml` 同时包含 `image:` 和 `build:`。生产部署只使用发布镜像；源码构建仅用于开发或 GHCR 长期无法拉取时的应急验证。

对已处于标准拓扑、要升级兼容最新 Release 的用户，首选 Dashboard 中的 Manager 更新流程。下表和 `version-deploy` 处理的是首次部署、旧部署迁入、指定版本/回退、Manager 自身升级或 Compose/deployment schema 迁移等手工兜底情形。

| 场景 | 变量 | 行为 |
|------|------|------|
| 生产部署 | `DICEPP_IMAGE_TAG=v3.0.0` | `docker compose pull` → `up -d` |
| 回退到指定版本 | `DICEPP_IMAGE_TAG=v3.0.0` | `docker compose pull` → `up -d` |
| 离线部署/更新 | `DICEPP_IMAGE_TAG=v3.0.0` | `unzip` → `sha256sum -c checksums.sha256` → `zstd -d` → `docker load` → `up -d --pull never` |
| 临时使用其他 registry | `DICEPP_IMAGE=registry.example.com/ns/nonebot-dicepp:v3.0.0` | `docker compose pull` → `up -d` |
| 临时替换 Dashboard registry | `DASHBOARD_IMAGE=registry.example.com/ns/dicepp-dashboard:v3.0.0` | `docker compose pull` → `up -d` |
| 开发/应急源码构建 | 不设镜像变量 | `docker compose build` → `up -d` |

手工 Docker 更新前应先确认当前 `docker-compose.yml` 是否与目标 Release 的部署拓扑一致。新增 service、环境变量、volume 或端口映射时，必须先同步 Release 附带的完整三服务 `docker-compose.yml` 或按 `docs/linux.md` 的部署说明合并标准块，再执行 `pull` / `up -d`。Manager 不会自动改写这份文件。

## Release 流程

### 正常发布 (version-release 技能)

1. 确认工作区干净，在 master 分支
2. 选择递增级别 (patch/minor/major)
3. 创建 `docs/releases/vX.Y.Z.md`（风险元数据）
4. `bump-my-version` 递增版本号 + 自动 commit + tag
5. 在当前 HEAD 上运行完整回归 `uv run pytest`
6. `git push origin master --tags`
7. GHA 自动构建镜像、Windows Portable/Setup/Velopack feed、Linux amd64
   发布包和 `dicepp-release.json`，再创建 GitHub Release

### 基线建立

`pyproject.toml` 已有目标版本号时（如 `3.0.0`），不递增版本：

1. 确认 `docs/releases/vX.Y.Z.md` 就绪
2. 确认所有代码已 commit
3. 在当前 HEAD 上运行完整回归 `uv run pytest`
4. `git tag vX.Y.Z` → `git push origin master --tags`
5. 等待 GHA 完成

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
