# 版本发现与下载

DicePP Manager 以 GitHub Release 为版本事实来源。每个 Release 必须携带
`dicepp-release.json`；Manager 不根据文件名猜测版本或平台，也不解析 Release
正文来决定能否升级。

## 默认行为

- Manager 分页读取 GitHub Release 列表。`stable` 频道只比较非 draft、
  非 prerelease；`prerelease` 频道需要用户主动开启，只比较非 draft 的
  prerelease。每个候选独立校验，坏候选不会遮蔽其他合法版本。
- 合法候选按版本号比较，只展示严格高于当前 DicePP 的最高版本；不会把当前
  版本或旧版本当作更新。
- 自动发现默认开启；自动下载默认关闭。
- 发现只下载小型 JSON 清单，不下载发布包。
- 下载完成后只写入 `manager/packages/<version>/`，不会停止、替换或重启当前
  RuntimeUnit，也不会执行安装。
- 下载的软件包必须同时通过 manifest schema、平台/架构、GitHub asset
  size/digest 和本地 SHA-256 校验，才会标记为可安装。
- 默认只保留最近两个已完成下载的版本。
- 检查与下载使用同一互斥队列。检查请求立即返回，Dashboard 根据持久化状态
  轮询；Manager 重启后仍可看到最后检查时间、频道、结果或错误。

这些选项位于 `config/global.json` 的 `update` 段，也可以在
`config/user.json` 覆盖，因此可直接使用 Dashboard 的配置编辑页管理：

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

Dashboard 的“版本更新”页展示频道、平台、可用版本、变更范围、兼容性和下载
校验状态，只提供“检查更新”和“下载并校验”。本阶段没有安装入口。

## Release contract

`dicepp-release.json` 使用 contract version 1。顶层声明 DicePP 版本与频道、
部署 schema、最低 Manager 版本、DataAsset Catalog version/digest、变更范围和
`automatic_upgrade`；每个 artifact 分别声明 platform、arch、用途、文件名、
字节数和 SHA-256。GitHub Release API 返回的 machine-contract asset 本身也会
先按 HTTPS URL、size 和 digest 验证原始 bytes，随后才解析 JSON。

`automatic_upgrade`、最低 Manager 版本和 change scope 都从 tag 内
`docs/releases/vX.Y.Z.md` 的受校验字段生成。发布者只有确认 Manager 无需
自升级、部署拓扑兼容且 change scope 准确时，才把“自动升级”声明为 `yes`；
字段缺失或冲突会让发布失败。该标志、部署 schema 或最低 Manager 任一不兼容
时仍可展示 Release，但不能进入下载后的可安装状态。

Windows amd64 Release 包含：

- `DicePP-vX.Y.Z-win64-Portable.zip`
- `DicePP-vX.Y.Z-win64-Setup.exe`
- Velopack full package 和按架构、频道隔离的 feed，例如
  `releases.win-x64-stable.json` / `assets.win-x64-stable.json`

Portable 与 Setup 是两个独立的首次部署入口，Setup 不依赖 Portable zip。

Linux amd64 Release 包含 `DicePP-vX.Y.Z-linux-amd64.zip`。外层
`dicepp-release.json` 验证整个 zip；zip 内的 `dicepp-package.json` 是第二层
安装契约，声明 deployment schema、最低 Manager、Catalog、是否允许自动升级、
Compose、压缩 image archive、镜像引用及内部文件 size/SHA-256，包内
`checksums.sha256` 再覆盖所有分发文件。这样避免让外层 zip 自我哈希。GHCR
镜像引用保留为后续安装事务的 fallback；本阶段不会自动 `docker pull` 或切换
现有容器。

## 断点续传边界

Manager 只在已有 `.part` 同时具备 ETag/Last-Modified，服务器对 Range 返回
`206`，且 `Content-Range` 的 start/end/total 与本地偏移及 manifest size
完全一致时续传。任一条件不满足都会丢弃旧 partial 并从零下载。最终 size 或
SHA-256 不匹配时，partial 会被删除且不会标记为可安装。

下载缓存以实例根、`manager/packages` 和版本目录的固定身份作为轻量安全边界，
并拒绝符号链接、目录替换及多链接文件。能够以同一系统用户权限在最后一次身份
检查与单个文件系统调用之间持续制造纳秒级竞态的本地进程不属于本项目威胁模型；
若未来需要抵御该场景，应改用各平台的目录句柄 API，而不是继续增加路径检查。
