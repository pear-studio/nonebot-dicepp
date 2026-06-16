# DicePP 发版系统

## 架构概览

```
dev (开发环境)                         prod (生产环境)
─────────────                         ─────────────
version-release (skill)               version-deploy (skill)
  │                                     │
  ├─ bump version + tag vX.Y.Z          ├─ gh release view vX.Y.Z
  ├─ write docs/releases/vX.Y.Z.md        → 读取风险元数据
  ├─ create GitHub Release               → 展示给用户确认
  └─ tag triggers GHA → build image      └─ deploy-docker → pull + up
       → ghcr.io:...:vX.Y.Z
       → ghcr.io:...:latest
```

## 关键文件

| 文件 | 用途 |
|------|------|
| `pyproject.toml` `[project].version` | 版本号唯一真相源 |
| `src/.../declare.py` `get_bot_version()` | 运行时版本读取（从 importlib.metadata） |
| `docs/releases/vX.Y.Z.md` | 每个 release 的 changelog 与风险摘要（数据变更 / 配置变更 / Risk Notes） |
| `docker-compose.yml` | 部署入口，同时支持 `image:`（镜像模式）和 `build:`（源码模式） |
| `Dockerfile` | 多阶段构建，`uv sync --frozen` 可复现 |
| `.github/workflows/release.yml` | tag push 触发 GHCR 镜像构建 + GitHub Release 创建 |

## 版本号

- **唯一源**: `pyproject.toml` → `[project].version`
- **运行时**: `importlib.metadata.version("dicepp")` 返回 `"X.Y.Z"`
- **展示**: `get_bot_version()` 返回 `"vX.Y.Z"`
- **变更**: `uv run bump-my-version bump patch|minor|major` 或 version-release 技能
- **约束**: 不在代码中硬编码版本字符串

## 镜像

- **Registry**: `ghcr.io/pear-studio/nonebot-dicepp`
- **Tags**: 每个 release 打 `:vX.Y.Z` 和 `:latest`
- **构建触发**: push `v*.*.*` tag
- **构建方式**: `uv sync --no-dev --frozen`，依赖由 `uv.lock` 锁定
- **分发**: `docker-compose.yml` 作为 GitHub Release asset 下载

## Docker Compose 模式

同一份 `docker-compose.yml` 同时包含 `image:` 和 `build:`：

| 场景 | `DICEPP_IMAGE_TAG` | 行为 |
|------|-------------------|------|
| 生产部署 | `v3.0.0` | `docker compose pull` → `up -d` |
| 开发构建 | 不设（默认 `latest`） | `docker compose build` → `up -d` |
| 更新到最新 | `latest` | `DICEPP_IMAGE_TAG=latest docker compose pull` → `up -d` |
| 回退到指定版本 | `v3.0.0` | `DICEPP_IMAGE_TAG=v3.0.0 docker compose pull` → `up -d` |

## Release 流程

### 正常发布 (version-release 技能)

1. 确认工作区干净，在 master 分支
2. 选择递增级别 (patch/minor/major)
3. 创建 `docs/releases/vX.Y.Z.md`（风险元数据）
4. `bump-my-version` 递增版本号 + 自动 commit + tag
5. `git push origin master --tags`
6. GHA 自动构建镜像 + 创建 GitHub Release + 上传 compose 文件

### 基线建立

`pyproject.toml` 已有目标版本号时（如 `3.0.0`），不递增版本：

1. 确认 `docs/releases/vX.Y.Z.md` 就绪
2. 确认所有代码已 commit
3. `git tag vX.Y.Z` → `git push origin master --tags`
4. 等待 GHA 完成

### 生产部署 (version-deploy 技能)

1. 读取目标版本 `vX.Y.Z`
2. 通过 `gh release view vX.Y.Z --json body` 或 `git show` 读取风险元数据
3. 展示影响范围，等待用户确认
4. 注入 `DICEPP_IMAGE_TAG=vX.Y.Z`，调用 deploy-docker 执行 pull + up

## 用户操作速查

```bash
# 生产环境（镜像部署）
DICEPP_IMAGE_TAG=v3.0.0 docker compose pull
DICEPP_IMAGE_TAG=v3.0.0 docker compose up -d
DICEPP_IMAGE_TAG=v3.1.0 docker compose pull && DICEPP_IMAGE_TAG=v3.1.0 docker compose up -d  # 更新

# 小白部署（从零开始）
# 1. 浏览器打开 https://github.com/pear-studio/nonebot-dicepp/releases/latest
# 2. 下载 docker-compose.yml
# 3. docker network create dice-net
# 4. docker compose up -d
```

## 约束

- DicePP 不依赖根目录 `.env`；NoneBot 监听参数由 `bot.py` 默认值提供
- Prod 由 agent skill 保证不执行 build 命令
- 镜像构建使用官方源，国内开发者通过 compose build args 自动用清华源
- Release metadata 不进 Docker 镜像
