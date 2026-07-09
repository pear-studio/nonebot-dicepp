---
name: full-offline-bundle
description: 生成 DicePP 群分发整合离线包。当用户要求制作、更新或检查包含 DicePP 官方发布 zip 与 LLOneBot 离线资产的 Linux/Windows 整合包，或提到 full offline bundle、群文件整合包、LLOneBot 离线镜像/桌面包复用时使用。
---

# Full Offline Bundle

为维护者生成群分发用整合离线包。整合包不重新组装 DicePP 内容，而是直接包含 GitHub Release 上的 DicePP 官方发布 zip，并额外加入 LLOneBot 离线资产、简短 `使用说明.md`、`manifest.json` 和 `checksums.sha256`。

## 核心约定

- DicePP 版本默认使用 GitHub 最新 Release；用户明确指定版本时才覆盖。
- Linux 整合包使用 LLOneBot Docker 镜像形态，不使用 CLI zip，避免和 `docs/linux.md` 的 Docker 部署路线冲突。
- Windows 整合包使用 LLOneBot Release 的 `LLBot-Desktop-win-x64.zip`，与 `docs/windows.md` 的桌面部署路线一致。
- LLOneBot 版本默认复用本 skill `assets/llonebot/` 下已有的同平台最新资产；用户明确指定版本时才重新匹配、下载或生成。
- 大文件缓存放在本 skill 的 `assets/`，输出放在 `out/`；两者由 `.gitignore` 排除，不提交进 git。
- 如果在 Windows 上构建 Linux 包且缺少 LLOneBot 镜像资产，脚本会优先尝试 WSL2 中的 `docker` 和 `zstd`；不可用时提示用户先准备 WSL2/Docker 环境或手动提供资产。
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

# 指定 DicePP 版本，LLOneBot 仍复用已有资产
python docs/agent/skills-dev/full-offline-bundle/scripts/build_bundle.py --dicepp-version v3.0.0

# 明确升级 Linux LLOneBot 镜像资产
python docs/agent/skills-dev/full-offline-bundle/scripts/build_bundle.py --llonebot-version v7.12.15 --pmhq-version 7.3.2

# 已经手动准备好 Linux LLOneBot 镜像包时直接指定
python docs/agent/skills-dev/full-offline-bundle/scripts/build_bundle.py --llonebot-asset path/to/llonebot-v7.12.15-pmhq-v7.3.2-docker-images.tar.zst

# 已经手动准备好 Windows LLOneBot 桌面包时直接指定
python docs/agent/skills-dev/full-offline-bundle/scripts/build_bundle.py --platform windows --llonebot-windows-asset path/to/LLBot-Desktop-win-x64-v7.12.15.zip
```

脚本会生成：

```text
docs/agent/skills-dev/full-offline-bundle/out/
  DicePP-vX.Y.Z-linux-amd64-with-llonebot.zip
  DicePP-vX.Y.Z-linux-amd64-with-llonebot.zip.sha256
  DicePP-vX.Y.Z-win64-with-llonebot.zip
  DicePP-vX.Y.Z-win64-with-llonebot.zip.sha256
```

## 输出结构

Linux 整合包内容：

```text
DicePP-vX.Y.Z-linux-amd64-with-llonebot/
  使用说明.md
  manifest.json
  checksums.sha256

  dicepp/
    DicePP-vX.Y.Z-linux-amd64-offline.zip

  llonebot/
    llonebot-vA.B.C-pmhq-vD.E.F-docker-images.tar.zst
    llonebot-vA.B.C-pmhq-vD.E.F-docker-images.tar.zst.sha256
    source.txt
```

Windows 整合包内容：

```text
DicePP-vX.Y.Z-win64-with-llonebot/
  使用说明.md
  manifest.json
  checksums.sha256

  dicepp/
    DicePP-vX.Y.Z-win64.zip

  llonebot/
    LLBot-Desktop-win-x64-vA.B.C.zip
    LLBot-Desktop-win-x64-vA.B.C.zip.sha256
    source.txt
```

`使用说明.md` 只负责指向 DicePP 官方发布包内的文档，并给出 LLOneBot 资产来源。安装、升级、回滚仍以 DicePP 发布包内的 `使用说明.md`、`docs/linux.md` 或 `docs/windows.md` 为准。
