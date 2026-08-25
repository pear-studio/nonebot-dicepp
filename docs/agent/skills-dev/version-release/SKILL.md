---
name: version-release
description: 在开发环境创建 DicePP 可部署版本。产出 Windows Portable ZIP、单一 GHCR 镜像、docker-compose.yml 与 checksum，并创建 GitHub Release。
---

# DicePP 发版

只支持手工选择版本、直接构建和直接发布。不要实现候选 promotion、receipt、upgrade evidence、跨版本升级矩阵、Velopack/Setup、双镜像或自动回滚。

## 发布前确认

1. 工作树干净，目标版本已写入 `pyproject.toml` 和 `uv.lock`。
2. 更新说明描述当前 Portable、单 Compose service 和手工更新路径。
3. 运行窄范围测试和 `git diff --check`；不要调用真实 LLM、QQ、OneBot 或在线版本发现。
4. 发布 tag 使用 `vX.Y.Z`，tag 指向要发布的 commit。

## 构建契约

Windows：

- `scripts/build/dicepp.spec` 生成 `DicePP-Runtime.exe`；
- `scripts/build/dashboard.spec` 生成 `DicePP.exe`；
- `scripts/build/assemble_windows_package.ps1` 将两者放入同一 `dist/DicePP/`；
- `Compress-Archive` 生成 `DicePP-vX.Y.Z-win64-Portable.zip`；
- 目录中不应有 Setup、Velopack、UpdateGuard、nupkg 或第二个 launcher。

Linux：

- `Dockerfile` 是唯一官方镜像构建文件；
- `docker-compose.yml` 只有 `dicepp` 一个 service，且只引用 `ghcr.io/pear-studio/nonebot-dicepp:${DICEPP_IMAGE_TAG:-latest}`，不包含 `build` 段；
- 镜像 CMD 是 `python -m dashboard`，Dashboard 正常入口 auto-start 一个 Bot 子进程；
- 镜像发布到 `ghcr.io/pear-studio/nonebot-dicepp:vX.Y.Z`。

## 本地验证

```bash
uv sync --frozen --group dev
uv run pytest -m quick -n0
uv run pytest tests/integration/dashboard/test_instance_data.py -n0 -q
docker compose config --quiet
docker build -f Dockerfile -t dicepp:local .
```

Windows 环境额外运行：

```powershell
uv run pyinstaller scripts/build/dicepp.spec --clean --noconfirm
uv run pyinstaller scripts/build/dashboard.spec --clean --noconfirm
./scripts/build/assemble_windows_package.ps1
```

运行 `scripts/build/verify_windows_package.ps1 -DistDir dist/DicePP`，通过正常
Dashboard + Bot 启动路径验收后再压缩 Portable 目录。

## GitHub Release

Release workflow 复用普通 CI 后：

1. 构建并通过 Windows Portable 正常启动验收；
2. 构建、fresh-start 验收并推送单一 GHCR 镜像；
3. 用真实临时目录运行空实例导入 fixture；
4. 上传 `DicePP-vX.Y.Z-win64-Portable.zip`、`docker-compose.yml` 和 `checksums.sha256`。

GitHub Release 是用户选择版本的静态目录。Dashboard 不查询最新版本，不从 Release 下载或安装程序。

## 版本说明

面向用户的版本说明只写：功能变化、配置/数据迁移、Windows Portable、Linux 单镜像手工更新和已知风险。历史 `docs/releases/v*.md` 可保留历史事实，但不要把它们当作当前协议。
