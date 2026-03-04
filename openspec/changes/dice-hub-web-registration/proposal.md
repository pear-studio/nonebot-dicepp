## Why

现有的多机器人互联功能（dice_hub）依赖 QQ 消息进行机器人间的通信，存在账号被封禁的风险。需要重新设计为通过网站 API 进行机器人注册与发现，摆脱对 QQ 消息的依赖。

## What Changes

- **删除** 旧的基于 QQ 消息的 dice_hub 模块（hub_command.py, manager.py 等）
- **新增** 基于 HTTP API 的机器人注册与发现功能
- **新增** 机器人端配置项：网站 API 地址
- **新增** 机器人端指令：`.hub register`、`.hub key`、`.hub list`、`.hub online`
- **保留** 原有 `.hub` 指令的触发方式，但功能完全重写

## Capabilities

### New Capabilities
- `web-hub-registration`: 机器人通过 HTTP API 向网站注册，获取 API Key
- `web-hub-heartbeat`: 机器人定期向网站发送心跳，维持在线状态
- `web-hub-discovery`: 管理员通过指令查看在线机器人列表
- `web-hub-key-management`: 管理员查看/管理机器人 API Key

### Modified Capabilities
- `dice-hub`: 现有 dice_hub 功能完全重构，从 QQ 消息模式改为 HTTP API 模式

## Impact

- 代码变更：`module/dice_hub/` 模块完全重写
- 配置变更：新增 `dicehub_api_url` 配置项
- 依赖变更：可能需要新增 HTTP 请求库（如 aiohttp 或 httpx）
- 数据变更：现有本地存储的机器人好友数据不再需要，可清理
