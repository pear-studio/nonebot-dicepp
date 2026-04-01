# Standalone 模式

DicePP 支持独立运行模式（Standalone），无需 OneBot 或 QQ 客户端即可启动并提供 HTTP 接口。

开发实现细节见：`docs/dicepp/standalone_runtime.md`

## 启动方式

配置优先级如下（从高到低）：

1. CLI 参数
2. 环境变量
3. 项目根目录 `config.json`
4. 默认值

> 运行模型说明：Standalone 当前仅保证 **single-worker** 模式下的正确性。

### CLI 启动

```bash
python standalone_bot.py --bot-id 123456 --hub-url http://localhost:8000 --port 8080
```

### 环境变量启动

```bash
BOT_ID=123456 HUB_URL=http://localhost:8000 MASTER_ID=10001 NICKNAME=DicePP PORT=8080 python standalone_bot.py
```

可选环境变量：
- `HUB_HEARTBEAT_INTERVAL`（单位秒，默认 `180`，最小值 `5`）
- `WEBCHAT_ENABLED`（`true/false`，默认 `false`）
- `WEBCHAT_HUB_URL`（Web Chat 网关地址，例如 `wss://example.com/ws/bot/`）
- `WEBCHAT_API_KEY`（Web Chat 鉴权密钥）

> 生产环境启用 Web Chat 时应使用 `wss://` 并开启证书校验（默认开启）。
> Web Chat 保活以 **协议层 ping/pong** 为准；若网页端短期保留应用层 JSON ping，请勿将其作为主健康指标，避免双重保活导致监控误判。

### 配置文件

将 `config.template.json` 复制为 `config.json`，并设置 `hub_url`。

## HTTP 接口

- `GET /dpp/`：健康检查
- `POST /dpp/heartbeat`：触发 DiceHub 心跳
- `POST /dpp/command`：执行一条 Bot 命令

`POST /dpp/command` 在单进程内采用串行执行，以保证请求级输出隔离。
当 `WEBCHAT_ENABLED=true` 且 Web Chat 配置完整时，`POST /dpp/command` 返回 `503`（WebSocket 模式下不再通过 `_outputs` 拉取 Bot 回复）。

### 命令请求示例

```json
{
  "text": ".help",
  "user_id": "10001",
  "group_id": "",
  "nickname": "StandaloneUser",
  "to_me": false
}
```

### 命令响应示例

```json
{
  "ok": true,
  "error": null,
  "messages": ["..."],
  "raw_command_count": 1
}
```

## Mock ID

- 默认 Mock 群号：`10000`
- 默认 Mock 用户号：`10001`

### Web Chat 群组 API 说明（重要）

当启用 Web Chat 时，`WebChatProxy` 仍实现 `ClientProxy` 的群组查询接口，但返回的是**确定性 Mock 数据**，不是网站或 QQ 的真实群状态。  
这仅用于保持命令运行稳定（避免 `NotImplemented`），不能用于需要真实群信息的一致性判断。

## 自动注册重试策略

当配置了 `hub_url` 时，启动阶段会自动尝试注册：
- 最大尝试次数：`6`
- 失败等待序列：`2s, 4s, 8s, 10s, 10s`
- 日志策略：每次失败记录 WARN；最终失败记录 ERROR（包含 `hub_url`、`bot_id`、`attempt`、`error_type`）

