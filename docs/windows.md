# Windows 部署

本页面向想在 Windows 上部署 DicePP 的普通用户。

当前 Windows 正式部署将改为 exe 包；exe 发布前，本页先保留 DicePP 部署占位，并写清 LLOneBot 连接 DicePP 时必须配置的部分。

需要源码开发时，请看 [dev/guide.md](./dev/guide.md)。

## 当前状态

TODO：等待 Windows exe 发布后补充完整步骤。

计划中的小白部署流程会尽量保持为：

1. 下载 DicePP Windows 发布包。
2. 解压到固定目录。
3. 启动 DicePP 和 Dashboard（`DicePP.exe` + `DicePPDashboard.exe`）。
4. 等 LLOneBot 连接后生成账号配置。
5. 通过浏览器访问 `http://127.0.0.1:4090/dashboard` 或手动编辑 JSON 填写 master、昵称等。
6. 发送 `.help` 验证。

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

## DicePP 配置

TODO：exe 发布后补充配置文件位置。

推荐流程是：先启动 DicePP，等 LLOneBot 连接上来后，让 DicePP 根据机器人 QQ 号生成账号配置，再回来填写 master 和昵称。

默认配置会出现在类似位置：

```text
config/bots/{机器人QQ号}.json
```

如果没有生成，就手动创建这个文件。内容可以先写成：

```json
{
  "master": ["你的QQ号"],
  "admin": [],
  "friend_token": ["添加好友口令"],
  "persona": "default",
  "nickname": "骰娘"
}
```

保存后重启 DicePP。

配置字段说明见 [configuration.md](./configuration.md)。

## 验证

DicePP 和 LLOneBot 都启动后，给机器人发送：

```text
.help
```

收到帮助信息即基本部署成功。

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
3. 如果仍没有生成，按本文 “DicePP 配置” 手动创建账号配置。

### 修改配置后没有生效

重启 DicePP。

如果 JSON 写错，DicePP 可能启动失败。检查最近的启动日志，重点看 JSON 解析错误、字段名拼写和逗号。

### 想启用 Persona AI

先让 `.help` 正常，再按 [persona.md](./persona.md) 配置 Persona。
