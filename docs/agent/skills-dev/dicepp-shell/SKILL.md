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
uv run dicepp-shell start <session> [--group <group_id>]
uv run dicepp-shell send <session> --user <user_id> --msg "<message>" [--dice <seq>] [--json]
uv run dicepp-shell rm <session>
```

多步骤流程使用同一个 session 顺序发送消息。需要稳定骰子结果时使用 `--dice`，需要机器可读输出时使用 `--json`。

## 常用选项

- `--user <id>`：用户 ID，发送消息时必填
- `--msg <text>`：消息内容，发送消息时必填
- `--dice <seq>`：预设骰子序列，如 `20,18,15`
- `--json`：输出结构化结果，便于检查回复内容
- `--nick <name>`：设置用户昵称
- `--private`：使用私聊模式

## 验收原则

- 使用能表达真实用户行为的消息，不要只验证内部函数路径。
- 每个场景使用有意义的 session 名，完成后清理。
- 指令行为发生变化时，至少覆盖一个成功路径；风险较高时补充边界、失败或多用户场景。
- 验证掷骰、先攻、角色卡等受随机或状态影响的流程时，优先固定骰子结果和 session。
- 涉及外部 API、真实 LLM、生产数据或高成本场景时，先和用户确认。

## 示例

```bash
uv run dicepp-shell start roll-check
uv run dicepp-shell send roll-check --user player1 --msg ".r 1d20 攻击" --dice 20 --json
uv run dicepp-shell rm roll-check
```

如需绕过 `uv run`，可直接调用虚拟环境可执行文件：

```powershell
# Windows
.venv\Scripts\dicepp-shell.exe start <session>

# Unix/macOS/Linux
.venv/bin/dicepp-shell start <session>
```
