# DicePP 发布说明

DicePP 发布采用直接构建、测试和发布的简单路径。Release workflow 只产生正常部署所需的字节，不建立候选 promotion、receipt、升级 evidence、跨版本矩阵或回滚裁决链。

## 发布资产

每个版本发布：

- `DicePP-vX.Y.Z-win64-Portable.zip`：包含 `DicePP.exe` 和 `DicePP-Runtime.exe`；
- `docker-compose.yml`：单一 `dicepp` service，使用单一 GHCR 镜像；
- `checksums.sha256`：Portable ZIP 和 Compose 文件的 SHA-256。

镜像名为 `ghcr.io/pear-studio/nonebot-dicepp:vX.Y.Z`。镜像内以 `python -m dashboard` 为入口，由 Dashboard 启动同一镜像内的 Bot 子进程，暴露 Dashboard 4090 和 OneBot 8080。

历史 `docs/releases/v*.md` 可以保留当时的资产和部署事实；新版本说明应只描述当前 Portable、单镜像和手工更新路径。

## 发布前检查

Release workflow 会：

1. 复用普通 CI 的 quick、单镜像 smoke 和 Windows Portable 检查；
2. 构建并推送带版本 tag 的单一 GHCR 镜像；
3. 执行 fresh-start Dashboard 健康检查；
4. 使用真实临时目录运行空实例导入 fixture；
5. 上传 Portable、Compose 和 checksum 到 GitHub Release。

构建过程不访问 GitHub Release 发现最新版本，也不调用真实 LLM、QQ 或 OneBot 服务。

## 使用者更新

使用者请阅读 [Windows 部署](../windows.md)、[Linux 部署](../linux.md) 和[版本更新](../updates.md)。更新前停止 Bot、保留存档和数据目录；新版本的兼容性由正常启动和当前 schema migration 路径保证。需要人工处理时停止服务并保留日志，不使用旧 Manager 或自动升级工具。
