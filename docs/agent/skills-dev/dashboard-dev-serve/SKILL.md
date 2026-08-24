---
name: dashboard-dev-serve
description: 用当前源码在本地起一个隔离 Dashboard，并由 Dashboard 的 BotProcessController 启动同一工作区的 Bot，进行真实页面和启停联调。
license: MIT
metadata:
  author: DicePP
  version: "2.0"
---

# Dashboard Dev Serve

这个技能只启动一个本地 Dashboard 进程。Dashboard lifespan 持有唯一的 `BotProcessController`，负责启动、停止和关闭同一隔离工作区中的 Bot 子进程；脚本本身不再启动额外服务或控制通道。

脚本位置：`docs/agent/skills-dev/dashboard-dev-serve/scripts/dev_dashboard.py`

## 适用场景

- Dashboard 前后端需要真实 Bot 状态和生命周期联调。
- 验证当前 `/api/bot/status`、`/api/bot/logs` 及同步 start/stop/restart 页面行为。
- 在本机浏览器查看当前源码 Dashboard；不连接生产 Compose 或生产数据。

## 启动与端口

默认使用 `127.0.0.1:5090` 和 `.dicepp-shell/dashboard-dev/` 隔离工作区。脚本会准备最小 config/data/content/dashboard 目录，并复制当前 `bot.py` 到工作区；Dashboard 进程通过当前源码的 BotProcessController 启动它。

```bash
uv run python docs/agent/skills-dev/dashboard-dev-serve/scripts/dev_dashboard.py start
uv run python docs/agent/skills-dev/dashboard-dev-serve/scripts/dev_dashboard.py status
uv run python docs/agent/skills-dev/dashboard-dev-serve/scripts/dev_dashboard.py stop
```

访问 `http://127.0.0.1:5090/dashboard`。需要局域网临时查看时才使用 `start --expose`，它绑定 `0.0.0.0`；用完立即执行 `stop`。

可用 `start --dashboard-port <port>` 避让本地端口，或用 `--json` 输出脚本状态。默认等待 20 秒，调整时使用 `--timeout <seconds>`。

## 生命周期与数据边界

- 仅 Dashboard 入口显式 auto-start Bot；导入 Python 模块或运行测试不会启动 Bot。
- `stop` 终止 Dashboard，Dashboard lifespan 随后调用 controller.shutdown，确保 Bot 一起停止。
- Dashboard 和 Bot 日志位于 `.dicepp-shell/dashboard-dev/data/logs/`。
- 不访问生产资源、远程服务或跨进程协调层，也不使用生产 `config/`、`data/`、`content/`。
- 不调用真实 LLM；需要消息或协议适配器时，请使用专门的本地测试工具并保持本技能范围不变。
