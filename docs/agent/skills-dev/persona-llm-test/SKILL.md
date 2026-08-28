---
name: persona-llm-test
description: 运行 DicePP Persona 真实 LLM 功能回归，客观验收生活模拟、主动消息、私聊多轮、群聊上下文、持久化和 trace。仅在用户显式调用本技能或明确要求 Persona 真实 LLM 回归时使用；不得自动触发，也不得由 subagent 自行调用。
---

# Persona 真实 LLM 回归

只验证功能和可核对的业务语义，不评价文风、角色表现或内容质量。真实 LLM 回归不能替代 fake/unit/integration tests。

## 准备

1. 完整阅读 [references/scenarios.md](references/scenarios.md)。
2. 向用户列出全部固定场景，结合回归范围标注推荐项，由用户选择。固定场景覆盖不足时可提出附加场景；不得为构造场景修改源码，数据库默认只读。
3. 确认 skill 同目录存在 `test_llm.local.json`。它只作为离线脚本读取的本地凭据输入，只填写 `deepseek_api_key`；脚本会把 Persona 覆盖写入临时 Bot JSON，并把 API Key 写入临时 session 的 `config/user.json`。
4. 运行离线准备脚本：

```bash
uv run python docs/agent/skills-dev/persona-llm-test/scripts/prepare_session.py \
  --scenarios <warp|private|group> [<warp|private|group> ...]
```

脚本只创建隔离 session、合并并验证配置、复制测试角色卡、输出 Agent Run 估算；不会启动 Runtime 或调用 LLM。临时 session 使用 `config/user.json` 中的 DeepSeek 模型配置。不得显示或复述凭据。

## 启动确认

在任何 `serve` 前，只向用户展示本次场景与 Agent Run 总数/分布：

```text
启动真实 LLM 测试前请确认：

- 场景：<场景>
- Agent Run：共 <总数> 次
  - <非零 Agent 类型>：<数量> 次

确认开始？
```

- 总数优先，分布只列非零类型。
- 多场景按场景分组，每个场景在同一行紧凑列出 Agent 类型分布。
- 确定数量写“共 N 次”；包含 warp 等动态上界时写“预计最多 N 次”。
- 若只执行固定场景的一部分，以实际计划为准缩减脚本给出的完整场景估算。
- session、凭据路径、模型、max rounds、fallback、执行步骤和收尾规则属于内部准备或结果汇报信息，不写入启动确认。

用户确认前不得启动 `serve` 或发出任何真实 LLM 请求。

## 执行

1. 启动默认无 tick 的 `dicepp-shell serve`。
2. 按用户选择执行场景；选择多个时必须按 `warp → private → group` 顺序，共用同一 session。
3. warp 先运行 `--dry-run` 核对当前 Runtime 给出的估算，再用 `--detach` 提交并轮询 `job status`。
4. 使用只读 SQLite 查询检查聚合状态、Agent Run 和 LLM trace。多个 scope 共用 session 时查看实际 prompt，检查是否混入其他 scope 的对话上下文。
5. 模型调用失败但重试后完成时，功能结论可通过，但必须标记警告。job 完成与场景通过分开判断。

## 收尾

- 每个场景汇报：通过 / 通过但有警告 / 失败、关键步骤、客观验收项、实际模型与调用量、重试/超时/completion code、session 处置。
- 不生成落盘报告，不粘贴完整 prompt、trace 或数据库内容；失败时只摘取必要证据。
- 全部通过：停止 Runtime 并删除 session。
- 失败或警告：停止 Runtime，保留 session 供检查。
- 只有用户明确批准，才可修改临时 session 的 SQLite 数据。
