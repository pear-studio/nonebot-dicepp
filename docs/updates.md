# 版本更新

本页面向 DicePP 使用者，说明如何安全升级、何时必须手工处理，以及升级失败后如何恢复。发布流水线和内部升级协议属于维护者资料，见文末链接。

## 推荐方式

对于受支持的 Windows 安装和标准 Linux 三服务部署，优先使用 Dashboard 的“版本更新”页升级到兼容的最新版本。

Manager 以 GitHub Release 和其中的 `dicepp-release.json` 为版本来源，不根据文件名或 Release 正文猜测兼容性。默认行为是：

- 自动发现 stable 频道的新版本；prerelease 频道需要主动开启；
- 自动下载默认关闭；
- 检查和下载不会停止、替换或重启当前运行版本；
- 只有下载完整、校验通过且与当前部署兼容的版本才会显示安装入口；
- 安装始终需要再次确认。

Dashboard 会展示目标版本、变更范围、兼容性、下载校验状态和升级进度。关闭页面或轮询超时不会取消已经提交的后台操作，重新打开页面后可以继续查看。

## 更新前

开始安装或手工迁移前：

1. 在 Dashboard 创建并验证归档；重要实例建议同时保留一份导出的完整归档。
2. 阅读目标版本的 Release 说明，确认配置、数据和部署风险。
3. 确认没有未完成的归档、恢复或升级事务。
4. 确认磁盘空间足以保存目标包、旧程序或镜像以及升级前归档。

发现版本和下载发布包不改变当前运行状态。只有在用户确认安装后，Manager 才会进入受保护的升级事务。

## Dashboard 更新流程

兼容版本的标准流程是：

1. Manager 重新校验目标版本、平台、发布包和部署兼容性。
2. 创建并验证升级前归档。
3. 保留可恢复的旧程序或旧镜像。
4. 切换目标版本并执行必要的数据迁移。
5. 检查 Manager、Dashboard、Bot RuntimeUnit 和本地控制通道。
6. 健康检查通过后提交升级；失败时按平台恢复。

QQ 协议端、GitHub、LLM、语音或图片等外部服务暂时不可用只会产生警告，不会让一个本地已经健康的新版本被错误回退。

## 必须手工处理的情况

以下情况不走兼容自动安装：

- 第一次安装，或旧式部署迁入当前标准拓扑；
- 指定安装较旧版本、人工回退或灾难恢复；
- Release 明确标记为不兼容或不允许自动升级；
- Manager handoff、Manager state、deployment schema、Compose 运行契约或安装布局发生不兼容变化；
- Linux 的 service、volume、network、挂载或其他 Compose 拓扑需要迁移；
- Windows 当前目录不是受支持的安装布局，或发布包缺少所需更新资产；
- 校验、磁盘空间、旧版本保留或本地健康门槛未通过。

遇到拒绝安装时，不要绕过兼容检查。先阅读目标 Release 说明，再按对应平台文档完成手工迁移。

## Linux

兼容升级会使用已经下载并校验的 Linux 发布包，在本机导入目标镜像；安装阶段不依赖 `docker pull`。Manager 会保留旧镜像并在失败时自动恢复程序、升级前数据和原运行状态。

Manager 不会自动修改用户的 `docker-compose.yml`。如果目标版本调整了 Compose 拓扑或部署契约，必须先按 Release 说明同步完整三服务 Compose，再手工更新。具体命令见 [Linux 部署的手工更新](./linux.md#手工更新)。

自动升级正在执行时，不要并发运行 `docker compose up`、`down` 或人工清理相关容器。若宿主机或 Docker daemon 在 Manager 交接窗口中重启，先停止自行修改容器，并按 Dashboard 提示或架构文档进行人工恢复。

## Windows

兼容升级会使用 Release 中固定的 Windows 更新包。Manager 在切换前创建并验证升级前归档，并备份完整旧 `current/` 程序目录；准备失败时不会修改当前程序。

如果新版无法正常启动：

1. 关闭 DicePP；恢复脚本不会主动结束任何进程。
2. 在 DicePP 安装根目录运行升级事务生成的 `DicePP-Recover.cmd`。
3. 脚本会整体换回旧 `current/`，再由旧 Manager 恢复升级前数据和原运行状态。

如果目录仍被占用或移动失败，脚本会停止并保留恢复材料。关闭占用程序后再重试，不要手工拼接新旧 `current/`，也不要直接启动 `DicePP-Runtime.exe`。

第一次安装可以使用目标 Release 的 Portable 或 Setup。旧目录迁入、指定旧版本或不兼容
升级若要保留现有自包含目录，应使用 Portable；Setup 只能安装到新的空目录，再恢复归档。
详细步骤见 [Windows 部署的版本更新与旧版迁移](./windows.md#版本更新与旧版迁移)。

## 更新配置

版本发现配置位于 `config/global.json` 的 `update` 段，也可以在 `config/user.json` 中覆盖：

```json
{
  "update": {
    "discovery_enabled": true,
    "auto_download": false,
    "channel": "stable",
    "check_interval_hours": 24,
    "cache_versions": 2
  }
}
```

通常直接使用 Dashboard 配置编辑页即可。不了解预发布风险时保持 `stable`，不要开启 prerelease 频道。

## 相关资料

- 平台操作：[Windows 部署](./windows.md)、[Linux 部署](./linux.md)
- 版本变化和风险：[Release 记录](./releases/)
- 内部架构：[Manager、归档恢复与升级架构](./dev/manager-architecture.md)
- 维护者发版流程：[DicePP 发版系统](./releases/README.md)
