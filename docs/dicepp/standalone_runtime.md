# Standalone 运行说明（开发视角）

本文档聚焦 Standalone 的代码运行机制。部署步骤见 `../standalone.md` 与 `../deploy.md`。

## 入口与配置优先级

入口文件：`standalone_bot.py`

配置优先级（高到低）：

1. CLI 参数（`--bot-id`、`--hub-url`、`--master-id`、`--nickname`、`--port`）
2. 环境变量（`BOT_ID`、`HUB_URL`、`MASTER_ID`、`NICKNAME`、`PORT`）
3. 根目录 `config.json`
4. 默认值

## 生命周期

`create_app()` 使用 FastAPI lifespan：

1. 创建 `DiceBot` 与 `StandaloneClientProxy`
2. 调用 `await bot.delay_init_command()`
3. 注入 Hub 相关配置并可选自动注册（带退避重试）
4. `bind_runtime(bot, proxy)` 绑定到 API
5. 退出时 `await bot.shutdown_async()`

当 `webchat_enabled=true` 且 `webchat_hub_url/webchat_api_key` 完整时：

1. 生命周期会创建 `WebChatAdapter + WebChatProxy`
2. `bot.set_client_proxy(WebChatProxy)` 替换默认 `StandaloneClientProxy`
3. 启动 WebSocket 客户端任务并在退出时关闭

## HTTP 接口

由 `module/fastapi/api.py` 提供并挂载在 `/dpp`：

- `GET /dpp/`：健康检查
- `POST /dpp/heartbeat`：触发 Hub 心跳
- `POST /dpp/command`：执行单条命令

### 命令执行并发约束

`/dpp/command` 在单进程内使用 `_command_lock` 串行化，保证同进程输出缓冲隔离。
因此当前语义以 single-worker 为准。

当 Web Chat 启用时，`/dpp/command` 返回 `503`，避免与 WS 输出通道并存造成语义冲突。

## Web Chat 补充约束

- **保活口径**：以 WebSocket 协议层 ping/pong 为准（客户端使用 `ping_interval/ping_timeout`）。
- **群信息语义**：Web Chat 代理实现的群组查询接口返回确定性 mock，不代表真实群状态。

## 调试建议

- 先用 `GET /dpp/` 确认运行时已绑定
- 再调用 `POST /dpp/command` 验证命令链
- Hub 问题优先看注册重试日志（`[Standalone][HubRegister]`）
