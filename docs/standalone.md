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

### 配置文件

将 `config.template.json` 复制为 `config.json`，并设置 `hub_url`。

## HTTP 接口

- `GET /dpp/`：健康检查
- `POST /dpp/heartbeat`：触发 DiceHub 心跳
- `POST /dpp/command`：执行一条 Bot 命令

`POST /dpp/command` 在单进程内采用串行执行，以保证请求级输出隔离。

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

## 自动注册重试策略

当配置了 `hub_url` 时，启动阶段会自动尝试注册：
- 最大尝试次数：`6`
- 失败等待序列：`2s, 4s, 8s, 10s, 10s`
- 日志策略：每次失败记录 WARN；最终失败记录 ERROR（包含 `hub_url`、`bot_id`、`attempt`、`error_type`）

