---
name: dicepp-shell
description: 使用 DicePP Shell 工具进行交互式机器人指令验收。涉及用户可见指令、骰子结果、会话状态、多步骤流程、私聊/群聊差异或需要确认机器人实际回复时使用；开发验证时可由 auto-test-run 配合调用。
license: MIT
metadata:
  author: DicePP
  version: "1.0"
---

# DicePP Shell

使用 `dicepp-shell` 做用户可见行为验收。它适合验证“用户发了什么，机器人回了什么”，不要用它替代单元测试覆盖纯内部逻辑。

## 基本流程

在项目根目录运行：

```bash
# 1. Create an isolated session workspace
uv run dicepp-shell init <session> [--group <group_id>]
# 2. Start the runtime (blocks the terminal; use a second terminal or background)
uv run dicepp-shell serve <session> [--tick]
# 3. Send messages to the running bot
uv run dicepp-shell send <session> --user <user_id> --msg "<message>" [--dice <seq>] [--json]
# 4. Stop the runtime and clean up
uv run dicepp-shell serve --stop <session>
uv run dicepp-shell rm <session>
```

多步骤流程使用同一个 session，在 serve 运行时反复 send。需要稳定骰子结果时使用 `--dice`，需要机器可读输出时使用 `--json`。send 必须依赖一个正在运行的 serve（不再自动创建临时 Bot）。

## 常用选项

- `--user <id>`：用户 ID，发送消息时必填
- `--msg <text>`：消息内容，发送消息时必填
- `--dice <seq>`：预设骰子序列，如 `20,18,15`
- `--json`：输出结构化结果，便于检查回复内容
- `--nick <name>`：设置用户昵称
- `--private`：使用私聊模式

## warp — 生活模拟时间加速

`warp` 推进虚构时间，驱动 persona 生活模拟运行指定天数（DM 叙事 → Character 反应 → Diary → SA 叙事规划）。用于调试 LLM prompt、生活模拟逻辑和叙事质量。

```bash
uv run dicepp-shell warp <session> --days <N> [--start <ISO>] [--dry-run] [--detach] [--json]
```

`warp` 由已启动的 `serve` Runtime 作为异步 job 执行。CLI 默认提交后轮询到完成；
使用 `--detach` 只返回 job ID，之后通过 `dicepp-shell job status/cancel` 管理。
执行 warp 的 Runtime 必须使用默认无 tick 模式；`serve --tick` 会被明确拒绝，
避免真实后台 tick 混入模拟时间线。

**执行前必须新建 session**（`dicepp-shell init <new-session>`），不要复用已有 session。复用会导致新旧 warp 的 `persona_story_deck`、`persona_daily_events`、`persona_sa_state` 等数据混在同一 DB 中，故事条目跨叙事污染，分析结果不可靠。

**常用选项：**

- `--days <N>`：模拟天数（≥1，必填）
- `--start <ISO>`：起始虚构时间（ISO 格式，如 `1351-10-26T08:00`）。默认随机生成 1000–1500 年间的虚构日期
- `--dry-run`：仅预估 LLM 调用次数和 token 量级，不实际执行
- `--detach`：提交后立即返回 job ID，不等待任务完成
- `--json`：输出结构化结果

**使用流程：**

```bash
# 1. 新建独立 session
uv run dicepp-shell init warp-qiqi-test

# 2. 在 session workspace 中配置 Persona、角色卡和 provider，然后启动 runtime
uv run dicepp-shell serve warp-qiqi-test

# 3. 在另一个终端先 dry-run 确认成本
uv run dicepp-shell warp warp-qiqi-test --days 2 --dry-run

# 4. 执行 warp
uv run dicepp-shell warp warp-qiqi-test --days 2

# 5. 停止 runtime，分析完成后清理
uv run dicepp-shell serve --stop warp-qiqi-test
uv run dicepp-shell rm warp-qiqi-test
```

**注意事项：**

- warp 使用真实 LLM，执行前先用 `--dry-run` 确认调用次数和 token 量级
- 每次 warp 消耗 50–200 次 LLM 调用，耗时 5–20 分钟
- warp 期间 send 和普通 stop 会返回 runtime_busy；可用 `job cancel` 取消
- Runtime 异常退出后，未完成 job 标记为 interrupted，不会自动续跑
- 虚构日期默认随机生成，避免与真实墙钟混淆
- warp 完成后，DM/Character 对话原文、SA 思考过程等原始数据在 `persona_llm_traces` 和 `persona_agent_events` 表中，可导出分析

## 验收原则

- 使用能表达真实用户行为的消息，不要只验证内部函数路径。
- 每个场景使用有意义的 session 名，完成后清理。
- 指令行为发生变化时，至少覆盖一个成功路径；风险较高时补充边界、失败或多用户场景。
- 验证掷骰、先攻、角色卡等受随机或状态影响的流程时，优先固定骰子结果和 session。
- 涉及外部 API、真实 LLM、生产数据或高成本场景时，先和用户确认。

## 示例

```bash
uv run dicepp-shell init roll-check
uv run dicepp-shell serve roll-check &
sleep 3
uv run dicepp-shell send roll-check --user player1 --msg ".r 1d20 攻击" --dice 20 --json
uv run dicepp-shell serve --stop roll-check
uv run dicepp-shell rm roll-check
```

如需绕过 `uv run`，可直接调用虚拟环境可执行文件：

```powershell
# Windows
.venv\Scripts\dicepp-shell.exe init <session>

# Unix/macOS/Linux
.venv/bin/dicepp-shell init <session>
```
