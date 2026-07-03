# Windows 部署

本页面面向想在 Windows 上部署 DicePP 的骰主。

Windows 发布包采用单入口：普通用户只启动 `DicePP.exe`。它会启动网页管理面板、托盘和实际机器人运行时；发布包里的 `DicePP-Runtime.exe` 只供 `DicePP.exe` 管理，不要手动双击或直接运行。

需要源码开发时，请回到项目仓库查看开发文档。

## 快速开始

推荐部署流程：

1. 下载 DicePP Windows 发布包。安装包可以从发布页或交流群获取。
2. 解压到固定目录。
3. 启动 `DicePP.exe`。
4. 在自动打开的网页管理面板中初始化管理员密码；也可以手动访问 `http://127.0.0.1:4090/dashboard`。
5. 配置 LLOneBot 连接 DicePP。
6. 等 LLOneBot 连接后，在网页管理面板中确认机器人状态，并填写账号配置。
7. 在 QQ 中向机器人发送 `.help` 验证。

`DicePP.exe` 启动后会留在托盘。需要退出时通过托盘菜单退出，退出会关闭网页管理面板和机器人运行时。

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

## 从旧版手动升级

Windows 自动 update/rollback 目前不支持。迁移旧目录时按手动流程处理：

1. 在旧版网页管理面板中创建存档。
2. 将旧目录的 `data/backups/*.zip` 复制到新目录的 `data/backups/`。
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
