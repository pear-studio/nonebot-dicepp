# 版本更新

DicePP Manager 以 GitHub Release 为版本事实来源。每个 Release 必须携带
`dicepp-release.json`；Manager 不根据文件名猜测版本或平台，也不解析 Release
正文来决定能否升级。

## 优先使用 Manager，何时手动

对于当前的标准三服务 Linux 部署或受支持的 Windows 安装，Dashboard 的“版本更新”页是升级**兼容的最新版本**的首选入口。Manager 负责校验、pre-upgrade 归档、安装、本地健康检查和失败回退；用户只需确认安装。

以下情形必须使用手动部署或恢复流程：

- 第一次安装、旧式或不受支持的部署迁入标准拓扑；
- 指定安装较旧版本、人工回退或灾难恢复；
- 目标 Release 包含 Manager 自身升级；
- Linux Compose、RuntimeUnit、挂载、网络或 deployment schema 迁移；
- 发布被标记为不兼容，或 Manager 的校验、空间、旧版本保留或健康门槛未通过。

手工操作前先创建并验证归档。Linux 请按 [Linux 部署](./linux.md#手工更新) 同步完整三服务 Compose 并更新或回退镜像；Windows 请按 [Windows 部署](./windows.md#版本更新与旧版迁移) 使用目标发布包或已验证归档恢复。不要手工复制 Windows `current/`，也不要未经对比直接用 Release 内的 Compose 覆盖现有实例。

## 默认行为

- Manager 分页读取 GitHub Release 列表。`stable` 频道只比较非 draft、
  非 prerelease；`prerelease` 频道需要用户主动开启，只比较非 draft 的
  prerelease。每个候选独立校验，坏候选不会遮蔽其他合法版本。
- 合法候选按版本号比较，只展示严格高于当前 DicePP 的最高版本；不会把当前
  版本或旧版本当作更新。
- 自动发现默认开启；自动下载默认关闭。
- 发现只下载小型 JSON 清单，不下载发布包。
- 下载完成后先写入 `manager/packages/<version>/`。发现和下载本身不会停止、
  替换或重启当前 RuntimeUnit；只有用户在 Dashboard 再次确认安装后，Manager
  才会启动升级事务。
- 下载的软件包必须同时通过 manifest schema、平台/架构、GitHub asset
  size/digest 和本地 SHA-256 校验，才会标记为可安装。
- 默认只保留最近两个已完成下载的版本；仍处于运行、待恢复或回退失败事务中的
  目标版本不受此上限清理，直到事务得到明确处理。
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

Dashboard 的“版本更新”页展示频道、平台、可用版本、变更范围、兼容性、下载
校验状态和升级事务进度。只有已经完整下载、校验通过且与当前部署兼容的版本才
显示安装入口；点击后还要确认目标版本和回退说明。关闭页面或 Dashboard 轮询
超时不会取消后台事务，重新打开页面会从 Manager 持久化状态继续展示。

## 确认安装与回退

安装不是“解压覆盖”。Manager 在实例级维护锁内执行同一套可恢复事务：

1. 重新校验 Release contract、已下载 artifact、当前平台和部署兼容性。
2. 保存 RuntimeUnit 原状态，创建并验证常规 pre-upgrade 归档。
3. 保留可执行的旧程序或旧镜像，再切换目标程序。
4. 执行数据 migration 和本地硬性健康检查。
5. 成功后提交；程序切换、migration、启动或硬性健康检查失败时，自动恢复旧
   程序和 pre-upgrade 数据。

创建 pre-upgrade 归档或保留旧程序失败时，事务在切换前停止。Dashboard、配置、
schema、RuntimeUnit 启动和本地控制通道属于硬性健康检查；QQ 协议端、GitHub、
LLM、语音或图片等外部服务故障只产生警告，不会因此回退一个已经健康的新版本。

### Linux 边界

兼容 Release 的安装直接读取已经校验的 Linux bundle，校验包内
`dicepp-package.json` 与 `checksums.sha256`，解压其中的 image archive 并执行
本地 `docker load`。安装路径不会先尝试 `docker pull`，因此不依赖升级时能访问
GHCR。加载后会把镜像引用解析为包内声明的 immutable Image ID；不匹配就拒绝
切换。Manager 随后只用当前标准 Compose 已有的配置重建 Bot/Dashboard，并在
重建前比较旧/新 image defaults；第一阶段只要默认值变化就转为手工升级，避免把
“与旧默认值相同、但由 Compose 显式固定”的配置误判成未覆盖。不能证明可以无损
保留的 Config/HostConfig 同样拒绝自动升级。旧镜像以 immutable Image ID 保留用于回退。

如果 Release 要求升级 Manager 自身，或目标 `docker-compose.yml` 改变 service、
volume、network、部署 schema 等当前 Manager 不能安全迁移的拓扑，Dashboard
仍可展示该版本及不兼容原因，但 Manager 拒绝下载和自动安装，并提示按 Release
文档手工同步完整三服务部署。它不会修改 Compose 文件，也不会退回到无 Manager
部署。

### Windows 边界

Windows 后续更新只使用 Release 中固定命名的 `velopack.win-x64.zip`。Manager
先按外层 Release contract 校验整个 bundle，再安全解包并按内层 `manifest.json`
复核 DicePP/Velopack 版本、频道、平台、架构以及唯一 full nupkg 的名称、大小和
SHA-256，最后才把该 nupkg 交给 UpdateGuard。Velopack 切换版本化程序目录；
Manager 在切换前把独立 UpdateGuard
准备到版本目录之外并写入升级计划。新 Manager 只有在 migration、Dashboard、
RuntimeUnit 和本地控制通道全部通过后才写入成功健康标记。超时或失败时，
UpdateGuard 使用从旧版 bundle 校验解出的 full nupkg 请求降级，不手工
删除或替换 `current/`；再由旧 Manager 按同一事务恢复 pre-upgrade 数据。新程序
必须发布绑定事务、目标版本和真实进程身份的 started marker，并在带认证的本地
`/v1/health` 已可访问且硬性健康检查完成后发布 health marker。

实例数据始终留在稳定 DicePP 根目录，不随 `current/` 切换。Portable 和 Setup
都是首次部署入口，后续都使用同一更新事务；Setup 不依赖 Portable ZIP。若发布
产物缺少 UpdateGuard 或 Velopack bundle，或当前目录不是受支持的
Velopack 安装布局，Manager 会在修改程序或数据之前拒绝自动安装，并给出手工
升级提示。

## Release contract

`dicepp-release.json` 使用 contract version 2。顶层声明 DicePP 版本与频道、
部署 schema、最低 Manager 版本、DataAsset Catalog version/digest、变更范围和
`automatic_upgrade`；每个 artifact 分别声明 platform、arch、用途、文件名、
字节数和 SHA-256。GitHub Release API 返回的 machine-contract asset 本身也会
先按 HTTPS URL、size 和 digest 验证原始 bytes，随后才解析 JSON。

`automatic_upgrade`、最低 Manager 版本和 change scope 都从 tag 内
`docs/releases/vX.Y.Z.md` 的受校验字段生成。发布者只有确认 Manager 无需
自升级、部署拓扑兼容且 change scope 准确时，才把“自动升级”声明为 `yes`；
字段缺失或冲突会让发布失败。该标志、部署 schema 或最低 Manager 任一不兼容
时仍可展示 Release 和具体原因，但不能下载或进入可安装状态。

发布流程还会在公开提升容器 tag 和生成 Release 之前读取
`scripts/build/upgrade_matrix.json`，并要求 CI 产出 `dicepp-upgrade-evidence`。
矩阵必须为 Windows amd64 与 Linux amd64 固定每个仍受支持来源版本的 HTTPS
资产和 SHA-256；证据必须绑定目标版本、完整 Git commit、Runtime/Dashboard
容器 manifest 与 Windows 测试包目录摘要，并逐项通过健康提交、目标健康失败
回退、回退后重试、以及目标代码从未执行的 apply 失败四个场景。来源资产摘要、
候选身份、平台覆盖或任一场景不匹配都会拒绝 `automatic_upgrade: yes`。
`automatic_upgrade: no` 不需要这份证据。当前矩阵没有受支持来源，reusable
Quality Gate 也尚未包含 evidence producer；因此 `automatic_upgrade: yes`
目前有意不可达，而不会用手工或模拟结果冒充跨版本验收。未来只有真实
Windows/Linux runner 完成矩阵场景后，才能在 reusable quality workflow 中上传
同名 `dicepp-upgrade-evidence` artifact。Release workflow 复验通过后会把原始
证据以稳定名称 `dicepp-upgrade-evidence.json` 随 Release 发布，供后续审计目标
commit、三个候选身份、来源资产摘要与场景结果。

Windows amd64 Release 包含：

- `DicePP-vX.Y.Z-win64-Portable.zip`
- `DicePP-vX.Y.Z-win64-Setup.exe`
- `velopack.win-x64.zip`

`velopack.win-x64.zip` 恰好包含根目录 `manifest.json` 与一个 Velopack full
nupkg，不包含 feed；nupkg 不作为独立 Release asset。内层 manifest 严格声明
format version、DicePP/Velopack version、channel、platform、arch 和 nupkg
filename/size/SHA-256。bundle 拒绝路径穿越、POSIX/Windows 绝对路径、反斜杠、
符号链接/重解析点、重复或额外成员和超出成员数、解压大小、单文件大小、压缩比
上限的输入；nupkg 内部版本也必须与两层清单一致。Portable 与 Setup 是两个独立
的首次部署入口，Setup 不依赖 Portable zip。

GitHub Release 最终固定为六个 assets：上述三个 Windows/Linux 用户发行包、
`velopack.win-x64.zip`、`dicepp-release.json` 和 `docker-compose.yml`。

Linux amd64 Release 包含 `DicePP-vX.Y.Z-linux-amd64.zip`。外层
`dicepp-release.json` 验证整个 zip；zip 内的 `dicepp-package.json` 是第二层
安装契约，声明 deployment schema、最低 Manager、Catalog、是否允许自动升级、
Compose、压缩 image archive、镜像引用与 immutable Image ID、`change_scope`
及内部文件 size/SHA-256，包内
`checksums.sha256` 再覆盖所有分发文件。这样避免让外层 zip 自我哈希。GHCR
镜像引用只作为诊断和手工恢复信息；自动安装以包内 image archive 和加载后
校验的 immutable Image ID 为准，不执行
`docker pull`。

## 断点续传边界

Manager 只在已有 `.part` 同时具备 ETag/Last-Modified，服务器对 Range 返回
`206`，且 `Content-Range` 的 start/end/total 与本地偏移及 manifest size
完全一致时续传。任一条件不满足都会丢弃旧 partial 并从零下载。最终 size 或
SHA-256 不匹配时，partial 会被删除且不会标记为可安装。

下载缓存以实例根、`manager/packages` 和版本目录的固定身份作为安全边界，
逐级拒绝符号链接、Windows reparse/junction、目录替换及多链接文件。bundle 的
外层摘要与 ZIP 内容必须从同一个 no-follow 文件句柄校验；提取时重新打开
no-follow 句柄，先在该句柄复核已授权的整包摘要，再从同一句柄读取 payload。
因此路径在校验、发布或提取之间被替换时会失败关闭，不会把替换后的内容标记为
可安装。
