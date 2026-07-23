---
name: full-offline-bundle
description: 生成 DicePP 群分发整合离线包。Linux 整合包包含 NapCat (Docker 模板 + NapCat.Shell.zip)，Windows 整合包包含 LLOneBot 桌面包。当用户要求制作、更新或检查整合离线包，或提到 full offline bundle、群文件整合包时使用。
---

# Full Offline Bundle

为维护者生成群分发用整合离线包。整合包不重新组装 DicePP 内容，而是直接包含 GitHub Release 上的 DicePP 官方发布 zip，并额外加入 LLOneBot 离线资产、简短 `使用说明.md`、`manifest.json` 和 `checksums.sha256`。

## 核心约定

- DicePP 版本默认使用 GitHub 最新 Release；用户明确指定版本时才覆盖。
- **Linux 整合包**使用 NapCat 方案：包含 Docker 模板文件（`docker-compose.yml`、`Dockerfile`、`entrypoint.sh`）和预下载的 NapCat.Shell.zip（约 28MB）。QQNT 由用户构建时从腾讯 CDN 在线下载（首次 ~170MB，Docker 缓存后不再重复），避免打包 500MB+ 的 Docker 镜像。
- **Windows 整合包**使用 LLOneBot Release 的 `LLBot-Desktop-win-x64.zip`，与 `docs/windows.md` 的桌面部署路线一致。
- NapCat 版本默认复用本 skill `assets/napcat/` 下已有的最新资产；用户通过 `--napcat-version` 指定版本时才重新下载。
- Windows LLOneBot 版本默认复用本 skill `assets/llonebot/` 下已有的同平台最新资产。
- 大文件缓存放在本 skill 的 `assets/`，输出放在 `out/`；两者由 `.gitignore` 排除，不提交进 git。
- Linux 包不需要 WSL2 或 Docker（仅下载 NapCat.Shell.zip + 复制模板，不构建 Docker 镜像）。
- Windows 包不需要 WSL2 或 Docker；缺少 LLOneBot 桌面包缓存时直接从 LLOneBot Release 下载。

## 使用脚本

从仓库根目录运行：

```bash
python docs/agent/skills-dev/full-offline-bundle/scripts/build_bundle.py
```

无参数默认同时输出 Linux 和 Windows 整合包。

常用参数：

```bash
# 同时输出 Linux 和 Windows 整合包
python docs/agent/skills-dev/full-offline-bundle/scripts/build_bundle.py --platform all

# 只输出 Windows 整合包
python docs/agent/skills-dev/full-offline-bundle/scripts/build_bundle.py --platform windows

# 指定 DicePP 版本，NapCat 仍复用已有资产
python docs/agent/skills-dev/full-offline-bundle/scripts/build_bundle.py --dicepp-version v3.0.0

# 明确升级 Linux NapCat 资产
python docs/agent/skills-dev/full-offline-bundle/scripts/build_bundle.py --napcat-version v4.18.9

# Windows 相关参数保持不变
python docs/agent/skills-dev/full-offline-bundle/scripts/build_bundle.py --platform windows --llonebot-version v7.12.15
```

脚本会生成：

```text
docs/agent/skills-dev/full-offline-bundle/out/
  DicePP-vX.Y.Z-linux-amd64-with-napcat.zip
  DicePP-vX.Y.Z-linux-amd64-with-napcat.zip.sha256
  DicePP-vX.Y.Z-win64-with-llonebot.zip
  DicePP-vX.Y.Z-win64-with-llonebot.zip.sha256
```

## 输出结构

Linux 整合包内容：

```text
DicePP-vX.Y.Z-linux-amd64-with-napcat/
  使用说明.md
  manifest.json
  checksums.sha256

  dicepp/
    DicePP-vX.Y.Z-linux-amd64.zip

  napcat/
    docker-compose.yml
    Dockerfile
    entrypoint.sh
    NapCat.Shell-vA.B.C.zip
    NapCat.Shell-vA.B.C.zip.sha256
    source.txt
```

Windows 整合包内容（不变）：

```text
DicePP-vX.Y.Z-win64-with-llonebot/
  使用说明.md
  manifest.json
  checksums.sha256

  dicepp/
    DicePP-vX.Y.Z-win64-Portable.zip

  llonebot/
    LLBot-Desktop-win-x64-vA.B.C.zip
    LLBot-Desktop-win-x64-vA.B.C.zip.sha256
    source.txt
```

`使用说明.md` 只负责指向 DicePP 官方发布包内的文档，并给出 NapCat/LLOneBot 资产来源。安装、升级、回滚仍以 DicePP 发布包内的 `使用说明.md`、`docs/linux.md` 或 `docs/windows.md` 为准。
