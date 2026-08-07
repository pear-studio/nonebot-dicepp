# Windows 部署

本页面面向想在 Windows 上部署 DicePP 的骰主，只说明平台安装、QQ 接入、日常运行和 Windows 特有恢复操作。通用升级判断见 [版本更新](./updates.md)，内部事务设计见 [Manager、归档恢复与升级架构](./dev/manager-architecture.md)。

Windows 发布包采用单入口：普通用户只启动 `DicePP.exe`。它本身是常驻托盘 Manager，负责启动、停止和监控网页管理面板与实际机器人运行时；发布包里的 `DicePP-Runtime.exe` 只供 Manager 管理，不要手动双击或直接运行。

需要源码开发时，请回到项目仓库查看开发文档。

## 快速开始

推荐部署流程：

1. 默认下载 `DicePP-vX.Y.Z-win64-Portable.zip`，解压到固定目录，例如
   `D:\DicePP`。这是最直观、最符合 DicePP 自包含数据约定的部署形式。
2. `DicePP-vX.Y.Z-win64-Setup.exe` 是可选的 Velopack one-click 安装器，不依赖
   Portable zip。它没有自定义目录向导；要保持自包含，请从终端显式执行
   `.\DicePP-vX.Y.Z-win64-Setup.exe --installto D:\DicePP`。
3. 启动 `DicePP.exe`。
4. 在自动打开的网页管理面板中初始化管理员密码；也可以手动访问 `http://127.0.0.1:4090/dashboard`。
5. 配置 LLOneBot 连接 DicePP。
6. 等 LLOneBot 连接后，在网页管理面板中确认机器人状态，并填写账号配置。
7. 在 QQ 中向机器人发送 `.help` 验证。

`DicePP.exe` 启动后会留在托盘。托盘菜单可以打开网页管理面板、查看整体状态以及启动、停止或重启 Bot RuntimeUnit。需要退出时通过托盘菜单退出，Manager 会有序关闭网页管理面板和机器人运行时。

一个 Bot RuntimeUnit 可以同时承载多个 QQ 账号，因此托盘和网页管理面板里的启动、停止、重启作用于整个机器人进程，不是只操作某个 QQ 账号。

## 实例目录与登录自启动

Windows 部署保持自包含：配置、用户内容、运行数据、Dashboard 数据和 Manager 状态都放在 DicePP 根目录中，不写入 `%LocalAppData%` 作为隐藏的数据事实来源。复制或完整清理 DicePP 时，只需处理这个根目录。

如果直接双击 Setup 而不传 `--installto`，Velopack 会使用当前用户的默认
`%LocalAppData%` 安装位置；此时 DicePP 数据也会自包含在那个 install root
中，但不符合本项目推荐的固定可见目录习惯。需要使用 Setup 时，建议始终显式
指定 `--installto`。卸载 Setup 安装前先从 Dashboard 创建并导出归档；one-click
卸载行为不应被当作数据备份机制。

```text
DicePP/
├─ DicePP.exe
├─ config/
├─ content/
├─ data/
├─ dashboard/data/
└─ manager/
   ├─ state/
   ├─ control/        # 仅 Bot Runtime 与 Manager 使用的控制凭据
   ├─ packages/
   └─ backups/
```

`content/` 是同一用户的工作区：Bot 读取它，认证后的 Dashboard 仅通过 Persona
角色卡页面编辑 `content/characters/`，同机 Agent 也可直接编辑内容文件；Manager
不是其独占写入者。

Velopack 激活版本时，程序文件实际位于 `DicePP/current/`；Manager 会把
`DicePP/` 识别为稳定实例根，因此 `config/`、`content/`、`data/` 和 `manager/`
不会被写进 `current/`。启动器通过 `DICEPP_APP_DIR` 保留当前程序目录，并把
版本随附的 `config/global.json` 和 `config/bots/_template.json` 只在稳定实例
根缺失时以竞争安全方式复制一次；既存普通文件保持不变，符号链接或 reparse
目标会拒绝启动。`user.json`、账号配置和业务数据绝不覆盖。Velopack 生成的
Portable 和 Setup 都保留稳定根入口、`Update.exe` 与 `current/` 布局；区别只在
首次部署是否运行安装器，不会形成两套数据位置或更新协议。

“登录 Windows 后自动启动 DicePP”默认关闭。启用后，Manager 只为当前用户写入 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`，不安装 Windows Service，也不需要管理员权限。可以勾选托盘菜单中的“登录后自动启动”，也可以在 DicePP 根目录执行：

```powershell
.\DicePP.exe --autostart status
.\DicePP.exe --autostart enable
.\DicePP.exe --autostart disable
```

`status` 查询当前状态，`enable` 启用，`disable` 关闭并移除同一个当前用户注册项。

注册项始终指向 DicePP 根目录的稳定入口 `DicePP.exe` 并以托盘模式启动。移动整个 DicePP 目录后，应先关闭旧自启动项，再在新位置重新启用，避免 Windows 继续启动旧路径。

## 归档与恢复

网页管理面板中的归档由常驻 Manager 执行。普通归档保存配置和运行数据，不包含
可能达到数百 MB 或更多的 `content/`；只有显式选择完整归档才保存 `content/`。
创建或恢复时会暂停整个 Bot RuntimeUnit，完成后只恢复操作前原本正在运行的单元。

恢复失败时 Manager 会自动应用恢复前创建的安全归档；若程序在事务中途退出，
下次启动由 Manager 根据自身状态恢复未完成事务，Dashboard 的启动或健康不构成
前提。不要在 `manager/backups/` 中手动删除活跃事务引用的归档。

## 准备

需要：

- Windows 电脑或服务器
- QQNT
- LLOneBot
- 一个可登录的 QQ 机器人账号

LLOneBot 官方入口：

- [LLBot 文档站](https://luckylillia.com)
- [LuckyLilliaBot GitHub](https://github.com/LLOneBot/LuckyLilliaBot)

先确认 QQNT 能正常登录机器人 QQ，再安装 LLOneBot。

## 初始化网页管理面板

首次启动 `DicePP.exe` 后，会自动打开网页管理面板。通过以下任一地址设置管理员密码：

- 本机访问：`http://127.0.0.1:4090/dashboard`
- 同一局域网的其他电脑访问：`http://局域网IP:4090/dashboard`，例如 `http://192.168.1.20:4090/dashboard`

首次网页初始化只接受本机或局域网 IP 的直接访问，不接受公网 IP、公网域名或反向代理访问。请先完成初始化，再开放公网入口。

如果网页初始化不方便，也可以在 DicePP 所在目录运行：

```powershell
.\DicePP.exe admin init
```

管理员密码设置完成后，可以正常通过公网域名访问。直接使用 HTTP 会暴露登录密码和会话信息，建议通过反向代理开启 HTTPS。

## 配置 LLOneBot

打开 LLOneBot 设置界面，启用 OneBot 11。

在 OneBot 11 设置中，使用“反向 WebSocket”或“WebSocket 客户端”连接 DicePP。

关键配置：

| 项目 | 填写 |
|------|------|
| 协议 | OneBot 11 |
| 连接方式 | WebSocket 客户端 / 反向 WebSocket |
| WebSocket URL | `ws://127.0.0.1:8080/onebot/v11/ws` |
| Token | 留空，除非你额外配置了访问令牌 |

保存后重启 LLOneBot 或 QQNT。

如果 DicePP 还没启动，LLOneBot 日志里出现连接失败或重连是正常的。等 DicePP 启动后，它会自动重连。

DicePP 默认只监听本机回环地址（`127.0.0.1:8080`），同机的 LLOneBot 直接连接即可，不会触发 Windows 防火墙提示。如果 OneBot 客户端运行在另一台机器上，需要为 DicePP 设置环境变量 `DICEPP_ONEBOT_HOST=0.0.0.0` 使其监听全部网卡，并自行放行防火墙；LLOneBot 的 WebSocket URL 也要改成 DicePP 所在机器的实际 IP。

## 配置机器人账号

推荐流程是：先启动 DicePP，等 LLOneBot 连接上来后，让 DicePP 根据机器人 QQ 号生成账号配置，再回到网页管理面板填写主人、昵称等常用配置。

发布包解压目录就是 DicePP 项目目录。默认配置会出现在：

```text
config/bots/{机器人QQ号}.json
```

如果没有生成，可以手动创建这个文件。内容可以先写成：

```json
{
  "master": ["你的QQ号"],
  "admin": [],
  "friend_token": ["添加好友口令"],
  "persona": "default",
  "nickname": "DicePP"
}
```

保存后重启 DicePP，或在网页管理面板中保存配置并让机器人重新加载。

配置字段说明见 [configuration.md](./configuration.md)。

## 验证

DicePP 和 LLOneBot 都启动后，给机器人发送：

```text
.help
```

收到帮助信息即基本部署成功。具体群内指令以机器人内置 `.help` 为准。

## 运行日志和存档

运行日志默认写入：

```text
data/logs/dicepp-runtime.log
```

每次 `DicePP.exe` 启动时，会先把已有日志按时间戳轮转为 `dicepp-runtime-YYYYMMDD-HHMMSS.log`，再创建新的 `dicepp-runtime.log`。网页管理面板可以查看这份全局运行日志；它不是单个 bot 的业务日志。

升级或迁移前，建议先在网页管理面板中创建存档。

Manager 的 HTTP API token、operation 和维护状态保存在：

```text
manager/state/
```

Bot↔Manager 控制凭据单独位于 `manager/control/control-token`，与 HTTP API token
完全独立，且不会写入 `data/dicepp.db`。

网页管理面板通过本机 Manager API 执行运行控制。若 Manager 不可用，面板会明确显示运行管理不受支持，不会直接接管子进程。

## 版本更新与旧版迁移

日常升级优先使用 Dashboard 的“版本更新”页。检查和下载不会改变当前版本；只有
发布包校验通过、与当前安装布局兼容的版本才会显示安装入口，安装仍需再次确认。
完整的升级条件、手工处理情形和平台差异见 [版本更新](./updates.md)。

确认安装后，Manager 会创建并验证升级前归档，备份完整旧 `current/`，并在安装根目录
准备一次性 `DicePP-Recover.cmd`。准备失败时不会切换当前程序；新版本通过本地健康
检查后才会提交升级。

如果新版无法正常启动，Windows 不会启动独立守护进程无人值守回退。请按以下步骤恢复：

1. 关闭 DicePP；恢复脚本不会主动结束任何进程。
2. 在 DicePP 安装目录根部运行 `DicePP-Recover.cmd`。
3. 脚本将故障 `current/` 整体隔离，并将备份的旧 `current/` 整体移回；不会逐文件混合两个版本。
4. 旧 Manager 启动后恢复这次升级前的数据和 RuntimeUnit 原状态。

如果 `current/` 仍被占用或目录移动失败，脚本会停止并保留全部恢复材料。关闭占用程序后
再重试，不要手工复制、删除或拼接新旧程序树。

第一次安装可以把 Portable 或 Setup 安装到新的空目录。旧目录迁入、指定安装较旧版本、
人工回退、灾难恢复或 Dashboard 明确提示不兼容时，先创建并验证归档，退出旧
`DicePP.exe`：需要保留现有自包含目录时，只使用 Portable 覆盖解压；Setup 会重建目标
安装目录，只能安装到新的空目录，再按下方流程恢复归档和自定义内容。普通升级和恢复
完成后都只启动 `DicePP.exe`，不要手动启动 `DicePP-Runtime.exe`。

从不具备精确归档能力的旧目录迁移时按手动流程处理：

1. 在旧版网页管理面板中创建存档。
2. 将旧目录的 `data/backups/*.zip` 复制到新目录的 `manager/backups/`。
3. 启动新目录的 `DicePP.exe`，进入网页管理面板后从存档恢复。
4. `dashboard/data` 可以按需复制；如果不复制，需要重新初始化管理员密码。
5. 如果旧目录里有自定义 `content/` 内容，请手动复制到新目录。

普通升级不需要手动启动 `DicePP-Runtime.exe`；恢复完成后仍由 `DicePP.exe` 管理运行时。

## 常见问题

### LLOneBot 一直连接失败

常见现象：

- LLOneBot 日志反复出现 WebSocket 连接失败
- DicePP 日志没有收到连接
- QQ 发 `.help` 没反应

检查：

- DicePP 是否已经启动
- WebSocket URL 是否完全一致
- DicePP 端口是否仍是 `8080`

Windows 本机部署时使用：

```text
ws://127.0.0.1:8080/onebot/v11/ws
```

不要填 Linux Docker 文档里的 `ws://dicepp:8080/onebot/v11/ws`。

### 端口被占用

常见现象：

- DicePP 启动失败
- 日志里出现 `address already in use`
- LLOneBot 连接不上 `8080`

默认端口是 `8080`。如果其他程序占用了这个端口，需要在 DicePP 配置中修改端口，并同步修改 LLOneBot 的 WebSocket URL。

例如 DicePP 改成 `8090` 后，LLOneBot 也要改成：

```text
ws://127.0.0.1:8090/onebot/v11/ws
```

### 账号配置没有生成

常见现象：

- 找不到 `config/bots/{机器人QQ号}.json`
- 日志提示没有账号配置或模板

处理：

1. 确认 LLOneBot 已经成功连接 DicePP。
2. 确认 DicePP 发布包里有配置模板。
3. 如果仍没有生成，按本文“配置机器人账号”手动创建账号配置。

### 修改配置后没有生效

优先在网页管理面板中保存配置并让机器人重新加载。手动编辑 JSON 后，可以重启 DicePP。

如果 JSON 写错，DicePP 可能启动失败。检查最近的启动日志，重点看 JSON 解析错误、字段名拼写和逗号。

### 想启用 Persona AI

先让 `.help` 正常，再按 [persona.md](./persona.md) 配置 Persona。
