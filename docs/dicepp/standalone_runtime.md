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

1. 创建 `DiceBot`，初始使用 `StandaloneClientProxy`（轻量级，无需认证）
2. 调用 `await bot.delay_init_command()`
3. 注入 Hub 相关配置
4. **自动注册**：如果配置了 `HUB_URL`，尝试向后端注册（带退避重试）
5. **自动启用 WebChat**（如果 `WEBCHAT_ENABLED=true`）：
   - 优先使用显式配置的 `WEBCHAT_API_KEY`
   - 若未配置，则从注册结果自动获取 `api_key`
   - 创建 `WebChatAdapter + WebChatProxy` 并替换默认代理
6. `bind_runtime(bot, proxy)` 绑定到 API
7. 退出时 `await bot.shutdown_async()`

### WebChat 启用条件

| WEBCHAT_ENABLED | WEBCHAT_API_KEY | 注册结果 | 行为 |
|-----------------|-----------------|----------|------|
| false | - | - | 纯 Standalone 模式，无 WebSocket |
| true | 有值 | - | 使用显式 `api_key` 启动 WebChat（不依赖注册） |
| true | 无值 | 成功 | 注册后自动获取 `api_key` 启动 WebChat |
| true | 无值 | 失败 | 记录警告，继续 Standalone 模式运行 |

**注意**：即使未配置 `HUB_URL`，只要提供 `WEBCHAT_API_KEY` 和 `WEBCHAT_HUB_URL`，仍可启用 WebChat。

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
